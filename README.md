# Tornado Cash Classic relayer

Run the Ethereum mainnet **4.1.7** software locally, inspect its API, or test a worker against a simulated Ethereum node. The local steps below require **no wallet, ETH, domain, or Ethereum RPC subscription**.

The mainnet worker uses a **0.3 gwei minimum priority fee** for Classic withdrawal quotes and initial transaction pricing. Higher network estimates accepted by the fee oracle take precedence. The relayer's percentage service fee is a separate setting. Both status endpoints identify this version as `4.1.7`.

## Run it locally

Install Git, Python 3.9 or later, tar, curl, and Docker with a running Linux container engine and Compose v2. The first build fetches the pinned worker source and its dependencies. Subsequent builds reuse verified downloads from `$XDG_CACHE_HOME/tornado-relayer-417` (default `~/.cache/tornado-relayer-417`).

```sh
git clone --branch main --single-branch https://github.com/torn-boy/tornado-relayer.git
cd tornado-relayer
python3 tools/build-mainnet.py
docker compose -p tornboy-relayer417-preview -f compose.preview.yml up -d --wait
curl -fsS --retry 10 --retry-connrefused http://127.0.0.1:18000/status
```

Open **http://127.0.0.1:18000/status** or **http://127.0.0.1:18000/v1/status**. Both return JSON with `"netId":1` and `"version":"4.1.7"`. This preview runs the real HTTP API and Redis, with the API bound to your computer's loopback address. The worker and watchers run in the separate test below. The preview has no worker health or token prices to report.

```sh
# View startup logs
docker compose -p tornboy-relayer417-preview -f compose.preview.yml logs --tail=30 api

# Stop the preview and remove its temporary Redis data
docker compose -p tornboy-relayer417-preview -f compose.preview.yml down -v
```

If you already have a development image built from the source revision below, set `RELAYER_DEV_IMAGE` to its local image tag before starting the preview. Port `18000` must be available.

### Test the worker and both status endpoints

From the same checkout, run the integration test with Bash and Docker.

```sh
./tools/smoke-mainnet.sh
```

This starts Redis, the API, the worker, and the tree, price, and health watchers on a temporary internal Docker network. A simulated Ethereum RPC supplies the chain data and test balance. The check waits for the tree and worker, verifies both status routes, and removes the test containers on exit. A healthy result here describes the local simulation. The simulated RPC only implements reads and subscriptions. To test a particular image, pass its tag as the first argument.

### What the development image contains

The [4.1.7 worker source](https://github.com/torn-boy/tornado-relayer/tree/6adbb587a0ff5b743c056cd6ed75e47042b369ea) lives on the [mainnet-v4 branch](https://github.com/torn-boy/tornado-relayer/tree/mainnet-v4). The `main` branch contains deployment files. The local builder checks out the pinned worker commit and runs its offline preparation script. The release lockfile pins public registry archives, Codeberg archives and the historical packages included in the worker checkout. The script verifies their hashes. Docker installs the verified packages offline and the builder runs the worker tests before tagging `localhost/tornado-relayer:4.1.7-dev`. Set `RELAYER_DEV_IMAGE` to build and preview a different local tag. The source revision and lockfile hashes are recorded in `/app/build-provenance.json` inside the image.

## Operate an Ethereum mainnet relayer

An operator needs Ethereum HTTP and WebSocket RPC endpoints, an oracle RPC, a funded worker signer, and a reward address. To appear in the registered relayer list, the worker and reward address must match the operator's registration. Store the private key in the operator's private environment file. For the bundled public HTTPS route, point the hostname at the server and make ports 80 and 443 available.

- **Existing 4.1.6 installation.** Follow the [4.1.7 upgrade and rollback guide](https://github.com/torn-boy/tornado-relayer/blob/6adbb587a0ff5b743c056cd6ed75e47042b369ea/README.md#upgrade-a-running-416-relayer). It builds on your running image, preserving its original dependencies. Upgrade every worker consuming the queue before switching the API to advertise 4.1.7.
- **New installation.** Use the [4.1.7 source checkout and environment template](https://github.com/torn-boy/tornado-relayer/blob/6adbb587a0ff5b743c056cd6ed75e47042b369ea/.env.example) and the [new-host image instructions](https://github.com/torn-boy/tornado-relayer/blob/6adbb587a0ff5b743c056cd6ed75e47042b369ea/README.md#start-on-a-new-host). The release lockfile and bundled archives support clean builds. The source guide also covers preparing an offline Docker build or transferring a verified operator image.

On a fresh deployment, start the services in this order from the **4.1.7 source checkout**, after tagging the operator image for Compose, pulling the infrastructure images and configuring `.env`.

```sh
docker compose up -d --no-build --pull never redis
docker compose up -d --no-build --pull never --no-deps eth-treeWatcher eth-priceWatcher eth-healthWatcher
docker compose exec -T redis redis-cli EXISTS tree:elements tree:root
```

The Redis check must print `2` before starting the worker. If the tree is still syncing, check `docker compose logs --tail=50 eth-treeWatcher` and repeat the Redis check. The tree watcher requires historical logs and WebSocket subscriptions.

```sh
docker compose up -d --no-build --pull never --no-deps eth-worker1
docker compose logs --tail=50 eth-worker1
```

Wait for `Worker started` in the current worker's logs. If the worker had already been started with an empty Redis, restart it after the tree check returns `2`. Once the worker is ready, start the API. If you use the bundled HTTPS proxy, start its services as well.

```sh
docker compose up -d --no-build --pull never eth-server
# For the bundled HTTPS proxy
docker compose up -d --no-build --pull never nginx dockergen letsencrypt
```

Read `/status` and `/v1/status` at the operator's HTTPS hostname. Check the network, version, configured reward address, populated token prices, and `health.status` equal to `"true"`. The worker log confirms it is consuming jobs. The queue count reports waiting jobs only.

## Existing multi-network deployments

The [deployment Compose file](./docker-compose.yml) on `main` uses profiles and network-specific files such as `.env.eth`, `.env.bsc` and `.env.arb`. The standalone 4.1.7 source checkout uses `.env`. For existing configured sidechains, keep their matching images and configuration files. For example, in an existing BSC deployment use `docker compose --profile bsc up -d --no-build`. Nova uses its own image and `.env.nova`. The 4.1.7 withdrawal fee change applies to Ethereum mainnet.

The historical [install.sh](./install.sh) targets the former upstream host and changes the host firewall and nginx configuration. Use the local build above for this checkout.

## Disclaimer

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
