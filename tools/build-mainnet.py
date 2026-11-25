#!/usr/bin/env python3
"""Build the 4.1.7 development image with verified historical dependency sources."""
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

TOOLS = Path(__file__).resolve().parent
REVISION = "4c0ef4b2c0c91831c9d7f3ab24871e93966a6443"
LOCK_SHA256 = "f12ad7595b40997d91c01ac7daf1a746c8ffee351c52ce88e6d22c7afe7214f7"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tornado-relayer-417"
SOURCES = json.loads((TOOLS / "mainnet-sources.json").read_text())
TYPESCRIPT_SHA256 = "39247cdcfec56456849449813197cd96b4b19b81b783e746bb8e5f9f5ec87311"
IMAGE = "localhost/tornado-relayer:4.1.7-dev"


def fetch(url, algorithm, digest):
    """Keep verified downloads across builds, including interrupted attempts."""
    cached = CACHE / (digest + ".tgz")
    if cached.exists() and hashlib.new(algorithm, cached.read_bytes()).hexdigest() == digest:
        return cached
    fd, filename = tempfile.mkstemp(dir=CACHE)
    os.close(fd)
    incoming = Path(filename)
    try:
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                        "--http1.1", "--retry", "3", "--retry-all-errors", "--connect-timeout", "15", "--max-time", "600",
                        "--output", str(incoming), url], check=True)
        if hashlib.new(algorithm, incoming.read_bytes()).hexdigest() != digest:
            raise ValueError("Checksum mismatch for " + url)
        incoming.replace(cached)
    finally:
        incoming.unlink(missing_ok=True)
    return cached


def export_source(context):
    # A shallow clone of main may not contain the worker's development commit.
    repository = TOOLS.parent
    lookup = subprocess.run(["git", "-C", str(repository), "cat-file", "-e", REVISION + "^{commit}"],
                            stderr=subprocess.DEVNULL)
    if lookup.returncode:
        repository = context.parent / "git"
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "fetch", "--quiet", "--depth", "1",
                        "https://github.com/torn-boy/tornado-relayer.git", REVISION], check=True)
    archive = context.parent / "worker.tar"
    subprocess.run(["git", "-C", str(repository), "archive", "--output", str(archive), REVISION,
                    "abis", "cache", "keys", "src", "test", "package.json", "yarn.lock"], check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(context)], check=True)


def recovered_archive(lock_block, url):
    version = re.search(r'^  version "([^"]+)"$', lock_block, re.M)[1]
    name = re.match(r'^"?((?:@[^/]+/)?[^@"]+)@', lock_block)[1]
    source = SOURCES[name + "@" + version]
    path = fetch(source["url"], "sha256", source["sha256"])
    with tarfile.open(path) as archive:
        manifest_file, = (item for item in archive.getmembers()
                          if item.isfile() and item.name.split("/")[1:] == ["package.json"])
        package = json.load(archive.extractfile(manifest_file))
    dependencies = dict((key.strip('"'), value) for key, value in
                        re.findall(r'^    (\S+) "([^"]+)"$', lock_block, re.M))
    if (package["name"], package["version"], package.get("dependencies", {})) != (name, version, dependencies):
        raise ValueError("Recovered package does not match the locked dependency graph: " + url)
    return path


def stage_entry(context, lock_block):
    match = re.search(r'^  resolved "(https://[^"#]+)#([a-f0-9]{40})"$', lock_block, re.M)
    if not match:
        return lock_block
    url, original_digest = match.groups()
    if url.startswith("https://registry.yarnpkg.com/"):
        path = fetch(url.replace("registry.yarnpkg.com", "registry.npmjs.org"), "sha1", original_digest)
        payload = path.read_bytes()
        algorithm, expected = re.search(r"^  integrity (\S+)$", lock_block, re.M)[1].split("-", 1)
        if base64.b64encode(hashlib.new(algorithm, payload).digest()).decode() != expected:
            raise ValueError("Yarn integrity mismatch for " + url)
    else:
        path = recovered_archive(lock_block, url)
        payload = path.read_bytes()
    destination = context / "archives" / path.name
    shutil.copyfile(path, destination)
    in_image = "file:/app/archives/" + path.name
    new_resolution = '  resolved "{}#{}"'.format(in_image, hashlib.sha1(payload).hexdigest())
    result = lock_block.replace(match.group(), new_resolution)
    if url.startswith("https://github.com/jbaylina/blake2b-wasm.git"):
        result = '"blake2b-wasm@' + in_image + '", ' + result
    integrity = "  integrity sha512-" + base64.b64encode(hashlib.sha512(payload).digest()).decode()
    if re.search(r"^  integrity ", result, re.M):
        return re.sub(r"^  integrity .*$", integrity, result, flags=re.M)
    return result.replace(new_resolution, new_resolution + "\n" + integrity)


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tornado-relayer-build-") as temp:
        context = Path(temp) / "context"
        (context / "archives").mkdir(parents=True)
        export_source(context)
        lock = (context / "yarn.lock").read_bytes()
        if hashlib.sha256(lock).hexdigest() != LOCK_SHA256:
            raise ValueError("Unexpected worker yarn.lock")
        print("Verifying dependency archives for " + REVISION, flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            entries = pool.map(lambda block: stage_entry(context, block), lock.decode().split("\n\n"))
            (context / "yarn.lock").write_text("\n\n".join(entries))

        # Yarn 1 resolves a literal Git dependency before looking at its offline cache.
        # Only snarkjs uses the Git copy; blake2b keeps its separately locked npm version.
        package_path = context / "package.json"
        package = json.loads(package_path.read_text())
        git_package = SOURCES["blake2b-wasm@2.1.0"]["sha256"] + ".tgz"
        package["resolutions"] = {"**/snarkjs/blake2b-wasm": "file:/app/archives/" + git_package}
        package_path.write_text(json.dumps(package, indent=2) + "\n")
        compiler = fetch("https://registry.npmjs.org/typescript/-/typescript-5.1.6.tgz",
                         "sha256", TYPESCRIPT_SHA256)
        shutil.copyfile(compiler, context / "typescript.tgz")
        shutil.copyfile(TOOLS / "Dockerfile.mainnet-dev", context / "Dockerfile")
        subprocess.run(["docker", "build", "--network=none", "-t", IMAGE, str(context)], check=True)
        print("Built " + IMAGE)


if __name__ == "__main__":
    main()
