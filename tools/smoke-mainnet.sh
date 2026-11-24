#!/usr/bin/env bash
# Start the 4.1.7 mainnet services against a private chain fixture.
set -euo pipefail

image=${1:-localhost/tornado-relayer:4.1.7-dev}
script_dir=$(cd "$(dirname "$0")" && pwd)
if ! docker image inspect "$image" >/dev/null; then
  echo "Build or load $image in the current Docker context first." >&2
  exit 1
fi
if ! docker image inspect redis:7-alpine >/dev/null 2>&1; then
  docker pull redis:7-alpine
fi

prefix="relayer-smoke-$$-$RANDOM"
docker network create --internal "$prefix" >/dev/null
cleanup() {
  local service
  for service in worker health price tree api rpc redis; do
    docker rm -fv "$prefix-$service" >/dev/null 2>&1 || true
  done
  docker network rm "$prefix" >/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

wait_for_log() {
  local service=$1 message=$2 attempt
  for ((attempt=0; attempt<120; attempt++)); do
    if docker logs "$prefix-$service" 2>&1 | grep -F "$message" >/dev/null; then return; fi
    if [[ $(docker inspect --format '{{.State.Running}}' "$prefix-$service") != true ]]; then break; fi
    sleep 1
  done
  echo "Startup failed for $service while waiting for '$message'." >&2
  docker logs --tail 12 "$prefix-$service" >&2
  exit 1
}

docker run --rm --network none --entrypoint node "$image" -e '
  const assert = require("node:assert/strict")
  assert.equal(require("./package.json").version, "4.1.7")
  assert.equal(require("./src/config").withdrawalMinPriorityFeeWei, "300000000")
'
docker run -d --name "$prefix-redis" --network "$prefix" --network-alias redis redis:7-alpine >/dev/null
docker create --name "$prefix-rpc" --network "$prefix" --network-alias rpc \
  --entrypoint node "$image" /app/smoke-rpc.cjs >/dev/null
docker cp "$script_dir/smoke-rpc.cjs" "$prefix-rpc:/app/smoke-rpc.cjs"
docker start "$prefix-rpc" >/dev/null
wait_for_log rpc "Test RPC listening"

# A fresh throwaway signing key exists only for this isolated run.
PRIVATE_KEY=$(docker run --rm --network none --entrypoint node "$image" \
  -e 'process.stdout.write(require("crypto").randomBytes(32).toString("hex"))')
export PRIVATE_KEY
start_service() {
  local name=$1 command=$2
  docker run -d --name "$prefix-$name" --network "$prefix" \
    -e NET_ID=1 -e PRIVATE_KEY -e REDIS_URL=redis://redis/0 \
    -e HTTP_RPC_URL=http://rpc:8545 -e WS_RPC_URL=ws://rpc:8545 -e ORACLE_RPC_URL=http://rpc:8545 \
    -e REWARD_ACCOUNT=0x0000000000000000000000000000000000000001 \
    -e AGGREGATOR=0x0000000000000000000000000000000000000010 \
    -e REGULAR_TORNADO_WITHDRAW_FEE=0.4 -e MINING_SERVICE_FEE=0.05 \
    -e BASE_FEE_RESERVE_PERCENTAGE=25 -e MAX_GAS_PRICE=1000 -e CONFIRMATIONS=4 \
    "$image" "$command" >/dev/null
}

start_service tree treeWatcher
wait_for_log tree "Rebuilt tree with"
start_service worker worker
start_service price priceWatcher
start_service health healthWatcher
start_service api server
unset PRIVATE_KEY
wait_for_log worker "Worker started"
wait_for_log price "Wrote following prices to redis"
wait_for_log api "Relayer 4.1.7 started"

docker exec -i "$prefix-api" node <<'JS'
const assert = require('node:assert/strict')
const http = require('node:http')
const { setTimeout: delay } = require('node:timers/promises')

function readStatus(path) {
  return new Promise((resolve, reject) => {
    const req = http.get('http://127.0.0.1:8000' + path, { timeout: 3000 }, (res) => {
      let text = ''
      res.setEncoding('utf8')
      res.on('data', chunk => { text += chunk })
      res.on('end', () => {
        try {
          assert.equal(res.statusCode, 200)
          resolve(JSON.parse(text))
        } catch (error) { reject(error) }
      })
      res.on('error', reject)
    })
    req.on('timeout', () => req.destroy(new Error('Status request timed out')))
    req.on('error', reject)
  })
}

async function check(path) {
  for (let attempt = 0; attempt < 60; attempt++) {
    const status = await readStatus(path)
    assert.equal(status.netId, 1)
    assert.equal(status.version, '4.1.7')
    assert.equal(Object.hasOwn(status, 'withdrawalMinPriorityFeeWei'), false)
    assert.equal(status.currentQueue, 0)
    if (status.health.status === 'true' && status.ethPrices.dai === '1000000000000000000') {
      console.log('PASS', path, 'version', status.version, 'health', status.health.status)
      return
    }
    await delay(1000)
  }
  throw new Error(path + ' never received the fixture health and price updates')
}
Promise.all(['/status', '/v1/status'].map(check)).catch(error => {
  console.error(error.message)
  process.exitCode = 1
})
JS
