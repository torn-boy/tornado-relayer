# Tornado Cash sidechain relayer 5.2.3

For the U.S. Treasury’s designation history, see its [March 2025 Tornado Cash delisting notice](https://ofac.treasury.gov/recent-actions/20250321).

**It is recommended to run your Relayer on a VPS instance ([Virtual Private Server](https://njal.la/)). Ensure SSH configuration is enabled for security, you can find information about SSH keygen and management [here](https://www.ssh.com/academy/ssh/keygen).**

## Build the 5.2.3 image

Use Git, Docker Engine and the Compose v2 plugin. Start in a directory separate from your running deployment. The image tag below is the one used by the supplied Compose file.

```sh
git clone --single-branch --branch sidechain-v5 https://github.com/torn-boy/tornado-relayer.git tornado-relayer-bsc
cd tornado-relayer-bsc
docker build -t tornadocash/relayer:sidechain-v5 .
```

The build uses the versions and checksums in `yarn.lock`. For a clean rebuild, add `--no-cache` to the Docker build command.

## Upgrade an existing BSC relayer

Build the image above on the host running the relayer. Once active jobs have finished, return to your existing deployment directory. Use its usual Compose project name and files so the services keep their current `.env.bsc`, Redis queue and volumes. The following command updates the three standard BSC services using the locally built image.

```sh
docker compose --profile bsc up -d --no-deps --pull never bsc-server bsc-healthWatcher bsc-worker1
```

On your public BSC relayer URL, check `/status` or `/v1/status` for `"netId": 56`, `"version": "5.2.3"` and a healthy worker. If your deployment runs additional BSC workers, recreate those with the same image.

## Start BSC on a new host

From the freshly cloned directory, run `cp .env.example .env.bsc` and fill in your settings.

| Setting          | Value                                               |
| ---------------- | --------------------------------------------------- |
| `HTTP_RPC_URL`   | Your BSC RPC endpoint                               |
| `ORACLE_RPC_URL` | Your Ethereum mainnet RPC endpoint                  |
| `PRIVATE_KEY`    | Your funded BSC worker key, without the `0x` prefix |
| `REWARD_ACCOUNT` | Your relayer reward address                         |
| `RELAYER_FEE`    | Your service fee percentage (`0.1` means 0.1%)      |

To serve the API through a reverse proxy on the host, create `docker-compose.override.yml` in the new checkout with the following port mapping. This publishes the API on loopback at the default `APP_PORT` of 8000.

```yaml
services:
  bsc-server:
    ports:
      - '127.0.0.1:8000:8000'
```

```sh
docker compose --profile bsc up -d redis bsc-server bsc-healthWatcher bsc-worker1
curl -fsS http://127.0.0.1:8000/status
```

Configure your BSC hostname and HTTPS reverse proxy to forward to `http://127.0.0.1:8000`. Check the same status response over HTTPS before advertising the relayer. The supplied Compose file also defines profiles for Polygon, Gnosis, Arbitrum, Optimism and Avalanche. Each profile reads its own `.env.<network>` file. Deployments using the bundled proxy services should set `VIRTUAL_HOST` and `LETSENCRYPT_HOST` in each network’s environment. [Compose services](docker-compose.yml) · [nginx template](tornado.conf) · [stream template](tornado-stream.conf)

## Run locally for one chain

1. `yarn install --frozen-lockfile`
2. `cp .env.example .env`
3. Modify `.env` as needed (described above)
4. Start Redis with `docker compose up -d redis`, then run `yarn concurrently "yarn server" "yarn worker" "yarn healthWatcher"`.
5. Go to `http://127.0.0.1:8000`
6. In order to execute withdraw request, you can run following command

```bash
curl -X POST -H 'content-type:application/json' --data '<input data>' http://127.0.0.1:8000/relay
```

The relayer returns a job ID. Query `/v1/jobs/<id>` to follow the withdrawal.

In that case you will need to add https termination yourself because browsers with default settings will prevent https
tornado.cash UI from submitting your request over http connection

## Run own RPC nodes

It is strongly recommended that you use your own RPC nodes. Instruction on how to run full nodes can be found [here](https://github.com/feshchenkod/rpc-nodes).

## Architecture

1. TreeWatcher module keeps track of Account Tree changes and automatically caches the actual state in Redis and emits `treeUpdate` event to redis pub/sub channel
2. Server module is Express.js instance that accepts http requests
3. Controller contains handlers for the Server endpoints. It validates input data and adds a Job to Queue.
4. Queue module is used by Controller to put and get Job from queue (bull wrapper)
5. Status module contains handler to get a Job status. It's used by UI for pull updates
6. Validate contains validation logic for all endpoints
7. Worker is the main module that gets a Job from queue and processes it

Disclaimer:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
