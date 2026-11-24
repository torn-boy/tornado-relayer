# Tornado Cash Classic relayer for Ethereum mainnet

Version **4.1.7** lowers the priority fee floor for Classic withdrawals from 3 to **0.3 gwei**. For both withdrawal fee validation and initial transaction pricing, the worker uses the higher of the floor and the network priority fee estimate accepted by the oracle. If the oracle rejects an extreme estimate, its existing fallback applies. The relayer service fee is configured separately. Clients can identify the running release from the `version` field on `/status` or `/v1/status`.

## Get the 4.1.7 source

From a directory separate from your running installation, check out the release branch. Keep your current Compose project, environment file and Redis volume in place if upgrading. The commands below use Docker Engine, Docker Compose v2, Git, curl and jq.

```sh
git clone --single-branch --branch feat/mainnet-withdrawal-priority-floor \
  https://github.com/torn-boy/tornado-relayer.git tornado-relayer-4.1.7
cd tornado-relayer-4.1.7
export RELEASE_DIR="$(pwd -P)"
jq -e '.version == "4.1.7"' package.json
git rev-parse HEAD
```

Continue once the version check prints `true`. Record the commit shown for your deployment.

## Upgrade a running 4.1.6 relayer

The 4.1.6 and 4.1.7 releases use the same dependency lockfile. You can build the new code on the same Docker Engine using the **image ID of your running 4.1.6 worker**. This path works without fetching the historical npm packages. The upgrade Dockerfile checks the base version, compares its lockfile byte for byte, and checks the installed fee oracle and transaction manager versions before copying the new code.

1. In the same shell, change back to the directory you normally use for `docker compose` on the running relayer. Use its usual project name, Compose files and profiles, so the commands address the existing workers and queue. The examples use the supplied worker name `eth-worker1`.

2. Build and check the candidate image. The tag ending in `4.1.6` retains the original image for rollback.

   ```sh
   (
     set -eu
     worker_id="$(docker compose ps -q eth-worker1)"
     test -n "$worker_id"
     base_image="$(docker inspect --format '{{.Image}}' "$worker_id")"
     base_tag="localhost/tornado-relayer:base-${base_image#sha256:}"
     docker image tag "$base_image" "$base_tag"
     docker build --pull=false --network=none \
       --build-arg RELAYER_BASE_IMAGE="$base_tag" \
       -f "$RELEASE_DIR/Dockerfile.upgrade" \
       -t localhost/tornado-relayer:4.1.7 "$RELEASE_DIR"
     docker run --rm --network none --entrypoint node localhost/tornado-relayer:4.1.7 \
       -e 'require("assert").strictEqual(require("./package.json").version, "4.1.7")'
     docker image tag "$base_image" localhost/tornado-relayer:4.1.6
   )
   ```

3. Once the build and checks succeed, let active withdrawals finish. The supplied Compose file uses one local image tag for all Ethereum services. Recreate the workers and watchers first, keeping Redis and the API running. Include **every** worker consuming this queue if you run more than one. For workers on other hosts, transfer the same image to each host first.

   ```sh
   docker image tag localhost/tornado-relayer:4.1.7 tornadocash/relayer:mainnet-v4
   docker compose up -d --no-build --pull never --no-deps --force-recreate \
     eth-treeWatcher eth-priceWatcher eth-healthWatcher eth-worker1
   docker compose logs --tail=50 eth-worker1
   ```

4. Wait for `Worker started` in each recreated worker's logs. Then switch the API so it advertises 4.1.7 only after the workers can accept the new fees.

   ```sh
   docker compose up -d --no-build --pull never --no-deps --force-recreate eth-server
   ```

## Start on a new host

Use an image built by your deployment team from the checked-out 4.1.7 source. To transfer the verified image from the upgrade above, run `docker save -o relayer-4.1.7.tar localhost/tornado-relayer:4.1.7` on the build host. Transfer the archive to a host of the same architecture, verify its SHA-256 against the build host's copy, and run `docker load -i relayer-4.1.7.tar`.

For a build from scratch, the standard Dockerfile uses Node 16 and Yarn's frozen lockfile. It needs the exact package archives pinned in `yarn.lock`. The historical package host `git.tornado.ws` returned HTTP 521 during release preparation. A clean build requires that source to be reachable from the builder or an approved build environment that already has the checksum-matching packages. When those dependencies are available, build with `docker build -t localhost/tornado-relayer:4.1.7 "$RELEASE_DIR"`.

In the release directory, create `.env` once and fill it in before starting the services.

```sh
cd "$RELEASE_DIR"
test -e .env || (umask 077 && cp .env.example .env)
```

| Setting                            | Value for Ethereum mainnet                                                        |
| ---------------------------------- | --------------------------------------------------------------------------------- |
| `HTTP_RPC_URL`, `ORACLE_RPC_URL`   | Ethereum mainnet HTTP endpoints for chain access and gas estimation               |
| `WS_RPC_URL`                       | Ethereum mainnet WebSocket endpoint with event subscriptions for the tree watcher |
| `PRIVATE_KEY`                      | Funded worker signing key, without the `0x` prefix                                |
| `REWARD_ACCOUNT`                   | The registered relayer reward address                                             |
| `REGULAR_TORNADO_WITHDRAW_FEE`     | Service fee percentage. For example, `0.4` means 0.4%                             |
| `VIRTUAL_HOST`, `LETSENCRYPT_HOST` | The public hostname pointing to this server, for the bundled HTTPS proxy          |

The Compose services set `NET_ID=1` and use their shared Redis service. Keep `APP_PORT=8000` for the supplied image and proxy. For this standalone stack, the bundled proxy needs ports 80 and 443 available. If you already operate a proxy, route it to the API using your existing deployment configuration.

```sh
docker image tag localhost/tornado-relayer:4.1.7 tornadocash/relayer:mainnet-v4
docker compose config --quiet
docker compose pull redis nginx dockergen letsencrypt
docker compose up -d --no-build --pull never
docker compose ps
docker compose logs --tail=50 eth-worker1
```

## Verify the running service

Run this against the public hostname used by the frontend. Replace the example hostname with your own.

```sh
RELAYER_URL=https://relayer.example.org
curl -fsS "$RELAYER_URL/status" | jq -e '
  select(.netId == 1 and .version == "4.1.7")
  | {netId, version, health, currentQueue}'
```

A match prints the version, network, health and queue information. Check `health` and the worker logs before accepting traffic.

## Roll back an upgrade

If you need to restore the saved image, switch the HTTP server first to withdraw the 4.1.7 advertisement. Allow the upgraded workers to finish outstanding low-fee jobs. Then recreate all workers and watchers with the saved image, using the same Compose project and Redis volume.

```sh
docker image tag localhost/tornado-relayer:4.1.6 tornadocash/relayer:mainnet-v4
docker compose up -d --no-build --pull never --no-deps --force-recreate eth-server
# After the outstanding jobs have finished:
docker compose up -d --no-build --pull never --no-deps --force-recreate \
  eth-treeWatcher eth-priceWatcher eth-healthWatcher eth-worker1
```

## API

`GET /status` and `GET /v1/status` return the same JSON. A valid `POST /v1/tornadoWithdraw` request queues the withdrawal and returns a job `id`. Use `GET /v1/jobs/:id` to check its progress. See the [routes](./src/router.js), [request validator](./src/modules/validator.js) and [relayer configuration](./src/config.js).

## Run from source for development

With Node 16, Yarn Classic, the locked packages available, and a separate development `.env`, you can run the processes directly. For local Redis, set `REDIS_URL=redis://127.0.0.1:6379`. Use a separate Compose project for development.

```sh
yarn install --frozen-lockfile
docker compose up -d redis
yarn concurrently "yarn server" "yarn treeWatcher" "yarn priceWatcher" "yarn healthWatcher" "yarn worker"
```

The local API listens at `http://127.0.0.1:8000/status` with the example port.

## Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
