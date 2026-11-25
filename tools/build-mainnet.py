#!/usr/bin/env python3
"""Build and test the pinned 4.1.7 worker image for the local preview."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REVISION = "6adbb587a0ff5b743c056cd6ed75e47042b369ea"
REMOTE = "https://github.com/torn-boy/tornado-relayer.git"
REPOSITORY = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "tornado-relayer-417"
IMAGE = os.environ.get("RELAYER_DEV_IMAGE", "localhost/tornado-relayer:4.1.7-dev")


def checkout_worker(source):
    # Prefer an existing local Git object. Single-branch deployment clones fetch the pin.
    local = subprocess.run(["git", "-C", str(REPOSITORY), "cat-file", "-e", REVISION + "^{commit}"],
                           stderr=subprocess.DEVNULL)
    repository = str(REPOSITORY) if local.returncode == 0 else REMOTE
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "fetch", "--quiet", "--depth=1", repository, REVISION],
                   check=True)
    subprocess.run(["git", "-C", str(source), "checkout", "--quiet", "--detach", REVISION], check=True)


def main():
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9 or later is required to prepare the worker's build context.")
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-", dir=CACHE) as temporary:
        work = Path(temporary)
        source = work / "worker"
        context = work / "context"
        checkout_worker(source)

        # The release preparer verifies public/<sha1>.tgz. Keep using the existing flat cache.
        reuse = work / "reuse"
        reuse.mkdir()
        (reuse / "public").symlink_to(CACHE.resolve(), target_is_directory=True)
        subprocess.run([sys.executable, str(source / "scripts" / "prepare-offline-build.py"),
                        str(context), "--reuse-archives", str(reuse)], check=True)

        image_id = work / "image-id"
        try:
            subprocess.run(["docker", "build", "--network=none", "--iidfile", str(image_id),
                            "--label", "org.opencontainers.image.revision=" + REVISION, str(context)], check=True)
        finally:
            for archive in (context / "public").glob("*.tgz"):
                archive.replace(CACHE / archive.name)
        image = image_id.read_text().strip()
        subprocess.run(["docker", "run", "--rm", "--network=none", image, "test"], check=True)
        subprocess.run(["docker", "tag", image, IMAGE], check=True)
        print("Built and tested " + IMAGE)


if __name__ == "__main__":
    main()
