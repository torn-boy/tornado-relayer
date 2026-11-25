#!/usr/bin/env python3
"""Prepare a Docker build context from a committed release and its verified Yarn archives."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RESOLUTION = re.compile(r'^  resolved "([^"]+)"$', re.M)
INTEGRITY = re.compile(r"^  integrity (\S+)$", re.M)
INPUTS = ["package.json", "yarn.lock", "Dockerfile.offline", "vendor",
          "scripts/offline-build-sources.json", "src", "abis", "keys", "cache", "test"]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def verified(data, sha1, integrity):
    algorithm, expected = integrity.split("-", 1)
    checksum = base64.b64encode(hashlib.new(algorithm, data).digest()).decode()
    return hashlib.sha1(data).hexdigest() == sha1 and checksum == expected


def download(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def stage_archive(resolution, integrity, context, reuse, recovered):
    url, sha1 = resolution.rsplit("#", 1)
    if not re.fullmatch("[0-9a-f]{40}", sha1):
        raise ValueError(f"Missing or invalid locked SHA-1 for {url}")

    if url.startswith("file:vendor/"):
        name = Path(url.removeprefix("file:"))
        if name.parent != Path("vendor"):
            raise ValueError(f"Expected a checked-in vendor archive: {url}")
        data = (context / name).read_bytes()
        staged_resolution = resolution
    elif url.startswith("https://"):
        path = context / "public" / f"{sha1}.tgz"
        data = None
        if reuse:
            candidates = [reuse / "public" / path.name]
            if recovered:
                candidates.append(reuse / "vendor" / url.rsplit("/", 1)[1])
            for candidate in candidates:
                if candidate.is_file():
                    cached = candidate.read_bytes()
                    if verified(cached, sha1, integrity):
                        data = cached
                        break
        if data is None:
            data = download(url.replace("https://registry.yarnpkg.com/", "https://registry.npmjs.org/", 1))
        staged_resolution = f"file:/app/build-deps/public/{path.name}#{sha1}"
    else:
        raise ValueError(f"Unsupported Yarn resolution: {url}")

    if not verified(data, sha1, integrity):
        raise ValueError(f"Archive does not match yarn.lock: {url}")
    if recovered and sha256(data) != recovered["archiveSha256"]:
        raise ValueError(f"Archive does not match recovery manifest: {url}")
    if url.startswith("https://"):
        path.write_bytes(data)
    return resolution, staged_resolution


def prepare(context, reuse):
    # Read every build input from one commit, including the lockfile and vendored archives.
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with tempfile.TemporaryFile() as snapshot:
        subprocess.run(["git", "archive", revision, "--", *INPUTS], cwd=ROOT, stdout=snapshot, check=True)
        snapshot.seek(0)
        subprocess.run(["tar", "-xf", "-", "-C", str(context)], stdin=snapshot, check=True)
    (context / "Dockerfile.offline").rename(context / "Dockerfile")
    (context / "public").mkdir()
    manifest_path = context / "scripts/offline-build-sources.json"
    manifest = json.loads(manifest_path.read_text())["packages"]
    recovered = {package["installResolution"]: {**package, "originalResolution": original}
                 for original, package in manifest.items()}

    lock_path = context / "yarn.lock"
    lock = lock_path.read_bytes()
    checksums = {}
    for stanza in lock.decode().split("\n\n"):
        if not re.search(r"^  version ", stanza, re.M):
            continue
        resolution = RESOLUTION.search(stanza)
        integrity = INTEGRITY.search(stanza)
        if not resolution or not integrity:
            raise ValueError("Missing resolution or integrity for: " + stanza.splitlines()[0])
        if checksums.setdefault(resolution[1], integrity[1]) != integrity[1]:
            raise ValueError("Conflicting integrity values for: " + resolution[1])
    if not checksums or recovered.keys() - checksums.keys():
        raise ValueError("Release lockfile and recovery manifest do not agree")

    print(f"Verifying {len(checksums)} archives for {revision}", flush=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        tasks = [pool.submit(stage_archive, res, check, context, reuse, recovered.get(res))
                 for res, check in checksums.items()]
        replacements = dict(task.result() for task in tasks)
    staged = RESOLUTION.sub(lambda m: f'  resolved "{replacements[m[1]]}"', lock.decode()).encode()
    lock_path.write_bytes(staged)
    provenance = {
        "relayerGitCommit": revision,
        "releaseYarnLockSha256": sha256(lock),
        "localYarnLockSha256": sha256(staged),
        "archivesVerifiedAgainstReleaseLock": len(checksums),
        "installLifecycleScripts": False,
        "recoveredPackages": list(recovered.values()),
    }
    (context / "build-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Prepared {context}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path, help="New Docker build context directory")
    parser.add_argument("--reuse-archives", type=Path, help="Read and verify archives from a previous context")
    args = parser.parse_args()
    context = args.context.resolve()
    if context.exists():
        parser.error("context must be a new directory")
    context.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".relayer-staging-", dir=context.parent) as staging:
        staged_context = Path(staging) / "context"
        staged_context.mkdir()
        prepare(staged_context, args.reuse_archives)
        staged_context.rename(context)
    print(f"Docker context ready: {context}")
