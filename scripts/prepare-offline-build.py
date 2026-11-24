#!/usr/bin/env python3
"""Prepare a staged Docker context using checked local dependency archives."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RESOLUTION = re.compile(r'^  resolved "(https?://[^"]+)"$', re.M)
INTEGRITY = re.compile(r"^  integrity (\S+)$", re.M)
GIT_DEPENDENCY = "blake2b-wasm"
ORACLE = "@tornado/tornado-oracles"
COMPILER_DEPS = {
    "@ethersproject/abstract-provider": "5.7.0",
    "@ethersproject/abstract-signer": "5.7.0",
    "@tornado/gas-price-oracle": "^0.5.3",
    "@tornado/tornado-config": "^2.0.0",
    "@types/node": "^20.5.1",
    "bignumber.js": "^9.1.1",
    "ethers": "5.7",
}


def digest(data, algorithm="sha256"):
    return hashlib.new(algorithm, data).hexdigest()


def sri(data, algorithm="sha512"):
    checksum = base64.b64encode(hashlib.new(algorithm, data).digest()).decode("ascii")
    return f"{algorithm}-{checksum}"


def matches(data, algorithm, expected, integrity):
    return digest(data, algorithm) == expected and (
        not integrity or sri(data, integrity.split("-", 1)[0]) == integrity
    )


def download(url, path, algorithm, expected, integrity=None, reuse=None):
    if reuse:
        cached = reuse / path.parent.name / path.name
        if cached.is_file():
            data = cached.read_bytes()
            if matches(data, algorithm, expected, integrity):
                path.write_bytes(data)
                return

    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    if not matches(data, algorithm, expected, integrity):
        raise ValueError(f"Checksum mismatch for {url}")
    path.write_bytes(data)


def unpack(data, directory):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        members = [member for member in archive.getmembers() if member.isfile() or member.isdir()]
        archive.extractall(directory, members=members, filter="data")
    (root,) = directory.iterdir()
    return root


def package_filename(package):
    return f'{package["name"].split("/")[-1]}-{package["version"]}.tgz'


def pack(directory, destination):
    result = subprocess.check_output(
        ["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(destination.parent)],
        cwd=directory,
        text=True,
    )
    packed_name = json.loads(result)[0]["filename"]
    (destination.parent / packed_name).replace(destination)


def check_package(stanza, package, archive_path):
    with tarfile.open(archive_path) as archive:
        manifest = json.load(archive.extractfile("package/package.json"))
    dependencies = {
        name.strip('"'): version
        for name, version in re.findall(r'^    (\S+) "([^"]+)"$', stanza, re.M)
    }
    version = re.search(r'^  version "([^"]+)"$', stanza, re.M)[1]
    if (
        manifest["name"] != package["name"]
        or manifest["version"] != version
        or manifest.get("dependencies", {}) != dependencies
    ):
        raise ValueError(f"Locked metadata differs for {archive_path.name}")


def resolved_stanza(stanza, path, file_ref):
    data = path.read_bytes()
    resolved = f'  resolved "file:{file_ref}#{digest(data, "sha1")}"'
    stanza = RESOLUTION.sub(lambda _: resolved, stanza, count=1)
    integrity = f"  integrity {sri(data)}"
    if INTEGRITY.search(stanza):
        return INTEGRITY.sub(lambda _: integrity, stanza, count=1)
    return stanza.replace(resolved, f"{resolved}\n{integrity}", 1)


def render_lock(stanzas, packages, build_dir, reference_dir):
    result = []
    for stanza in stanzas:
        match = RESOLUTION.search(stanza)
        if not match or match[1] not in packages:
            result.append(stanza)
            continue
        path = packages[match[1]]
        file_ref = reference_dir / path.relative_to(build_dir)
        stanza = resolved_stanza(stanza, path, file_ref)
        if match[1].startswith("https://github.com/jbaylina/blake2b-wasm.git#"):
            stanza = f'"{GIT_DEPENDENCY}@file:{file_ref}", {stanza}'
        result.append(stanza)
    return "\n\n".join(result)


def compile_oracle(source_dir, temporary, stanzas, packages, build_dir, compiler_source):
    compiler_archive = temporary / "typescript.tgz"
    download(compiler_source["url"], compiler_archive, "sha256", compiler_source["sha256"])
    compiler = unpack(compiler_archive.read_bytes(), temporary / "compiler")
    dependency_dir = temporary / "compiler-dependencies"
    dependency_dir.mkdir()
    (dependency_dir / "package.json").write_text(
        json.dumps({"private": True, "dependencies": COMPILER_DEPS}), encoding="utf-8"
    )
    (dependency_dir / "yarn.lock").write_text(
        render_lock(stanzas, packages, build_dir, build_dir), encoding="utf-8"
    )
    subprocess.run(
        ["yarn", "install", "--offline", "--frozen-lockfile", "--ignore-scripts", "--non-interactive"],
        cwd=dependency_dir,
        check=True,
    )
    (source_dir / "node_modules").symlink_to(dependency_dir / "node_modules", target_is_directory=True)
    for config in ("tsconfig.json", "tsconfig.esm.json"):
        subprocess.run(["node", str(compiler / "lib/tsc.js"), "-p", config], cwd=source_dir, check=True)


def prepare(build_dir, reuse):
    sources = json.loads((ROOT / "scripts/offline-build-sources.json").read_text(encoding="utf-8"))
    recovered = sources["packages"]
    lock = (ROOT / "yarn.lock").read_bytes()
    stanzas = lock.decode("utf-8").split("\n\n")
    for name in ("public", "vendor"):
        (build_dir / name).mkdir(parents=True)

    packages = {}
    queued = []
    source_stanzas = {}
    for stanza in stanzas:
        match = RESOLUTION.search(stanza)
        if not match:
            continue
        resolution = match[1]
        url, sha1 = resolution.rsplit("#", 1)
        if url.startswith("https://registry.yarnpkg.com/"):
            path = build_dir / "public" / f"{sha1}.tgz"
            npm_url = url.replace("https://registry.yarnpkg.com/", "https://registry.npmjs.org/", 1)
            queued.append((npm_url, path, "sha1", sha1, INTEGRITY.search(stanza)[1]))
            packages[resolution] = path
        else:
            package = recovered[resolution]
            source_stanzas[resolution] = stanza
            if package["format"] == "npm":
                path = build_dir / "vendor" / package_filename(package)
                queued.append((package["url"], path, "sha256", package["sha256"]))
                packages[resolution] = path

    print(f"Checking {len(queued)} package archives", flush=True)
    with ThreadPoolExecutor(max_workers=16) as executor:
        for future in [executor.submit(download, *args, reuse=reuse) for args in queued]:
            future.result()

    with tempfile.TemporaryDirectory(prefix="relayer-offline-sources-") as work:
        temporary = Path(work)
        for resolution in source_stanzas:
            package = recovered[resolution]
            if package["format"] != "source":
                continue
            path = build_dir / "vendor" / package_filename(package)
            print(f"Rebuilding {path.name}", flush=True)
            archive_path = temporary / path.name
            download(package["url"], archive_path, "sha256", package["sha256"])
            source_dir = unpack(archive_path.read_bytes(), temporary / package["name"].split("/")[-1] / package["version"])
            if package["name"] == ORACLE:
                compile_oracle(source_dir, temporary, stanzas, packages, build_dir, sources["typescript"])
            pack(source_dir, path)
            packages[resolution] = path

    for resolution, stanza in source_stanzas.items():
        check_package(stanza, recovered[resolution], packages[resolution])

    files = ["package.json", "src", "abis", "keys", "cache", "test"]
    checkout = subprocess.check_output(["git", "archive", "HEAD", "--", *files], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(checkout)) as archive:
        archive.extractall(build_dir, filter="data")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    shutil.copyfile(ROOT / "Dockerfile.offline", build_dir / "Dockerfile")
    reference_dir = Path("/app/build-deps")
    (build_dir / "yarn.lock").write_text(
        render_lock(stanzas, packages, build_dir, reference_dir), encoding="utf-8"
    )
    manifest_path = build_dir / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("resolutions", {})[GIT_DEPENDENCY] = (
        f"file:{reference_dir}/vendor/blake2b-wasm-2.1.0.tgz"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    provenance = {
        "relayerGitCommit": commit,
        "originalYarnLockSha256": digest(lock),
        "localYarnLockSha256": digest((build_dir / "yarn.lock").read_bytes()),
        "publicTarballsVerifiedAgainstOriginalLock": len(packages) - len(source_stanzas),
        "installLifecycleScripts": False,
        "recoveredPackages": [
            {
                **recovered[resolution],
                "originalResolution": resolution,
                "archiveSha256": digest(packages[resolution].read_bytes()),
            }
            for resolution in source_stanzas
        ],
        "typescript": sources["typescript"],
    }
    (build_dir / "build-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {build_dir} from commit {commit}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path, help="Path for the new Docker build context")
    parser.add_argument("--reuse-archives", type=Path, help="Previous context with checked npm archives")
    args = parser.parse_args()
    context = args.context.resolve()
    if context.exists():
        parser.error("context must be a new directory")
    prepare(context, args.reuse_archives)
