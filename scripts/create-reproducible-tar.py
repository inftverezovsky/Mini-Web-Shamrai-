from __future__ import annotations

import argparse
import gzip
import os
import tarfile
import tempfile
from pathlib import Path


def _tar_info(path: Path, arcname: str, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(arcname)
    stat = path.stat()
    info.size = stat.st_size
    info.mode = 0o644
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info


def create_archive(source: Path, destination: Path, epoch: int) -> None:
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(path for path in source.rglob("*") if path.is_file())
    if not files:
        raise ValueError("source directory has no files")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("release archives must not contain symbolic links")

    fd, raw_name = tempfile.mkstemp(prefix="shamrai-release-", suffix=".tar", dir=destination.parent)
    os.close(fd)
    raw_path = Path(raw_name)
    try:
        with tarfile.open(raw_path, "w", format=tarfile.PAX_FORMAT) as archive:
            for path in files:
                relative = path.relative_to(source).as_posix()
                info = _tar_info(path, relative, epoch)
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        with raw_path.open("rb") as source_stream, destination.open("wb") as destination_stream:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination_stream, mtime=epoch, compresslevel=9) as compressed:
                while chunk := source_stream.read(1024 * 1024):
                    compressed.write(chunk)
    finally:
        raw_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a deterministic tar.gz from release files.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("epoch", type=int)
    args = parser.parse_args()
    create_archive(args.source, args.destination, args.epoch)


if __name__ == "__main__":
    main()
