from __future__ import annotations

import re
import sys
from pathlib import Path


PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;]+)$")
LOCK_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)\s*\\?$")
INCLUDE_RE = re.compile(r"^(?:-r|--requirement(?:=|\s+))\s*(\S+)$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def lock_versions(path: Path) -> tuple[dict[str, str], str]:
    lock_text = path.read_text(encoding="utf-8")
    locked: dict[str, str] = {}
    for raw in lock_text.splitlines():
        match = LOCK_RE.fullmatch(raw.strip())
        if match is not None:
            locked[canonical_name(match.group(1))] = match.group(2)
    return locked, lock_text


def requirement_versions(path: Path, *, seen: set[Path] | None = None) -> dict[str, str]:
    resolved = path.resolve()
    visited = seen if seen is not None else set()
    if resolved in visited:
        return {}
    visited.add(resolved)

    if resolved.suffix == ".lock":
        return lock_versions(resolved)[0]

    direct: dict[str, str] = {}
    for raw in resolved.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        include = INCLUDE_RE.fullmatch(line)
        if include is not None:
            included = requirement_versions(resolved.parent / include.group(1), seen=visited)
            for name, version in included.items():
                existing = direct.get(name)
                if existing is not None and existing != version:
                    raise SystemExit(
                        f"conflicting pinned versions for {name}: {existing} and {version}"
                    )
                direct[name] = version
            continue
        match = PIN_RE.fullmatch(line)
        if match is None:
            raise SystemExit(f"runtime dependency is not exactly pinned: {line}")
        name = canonical_name(match.group(1))
        version = match.group(2)
        existing = direct.get(name)
        if existing is not None and existing != version:
            raise SystemExit(f"conflicting pinned versions for {name}: {existing} and {version}")
        direct[name] = version
    return direct


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify-python-lock.py REQUIREMENTS LOCK")

    requirements_path = Path(sys.argv[1])
    lock_path = Path(sys.argv[2])
    direct = requirement_versions(requirements_path)
    locked, lock_text = lock_versions(lock_path)

    for name, version in direct.items():
        if locked.get(name) != version:
            raise SystemExit(
                f"hash lock is stale for {name}: expected {version}, found {locked.get(name)!r}"
            )
    if "--hash=sha256:" not in lock_text:
        raise SystemExit("hash lock contains no SHA-256 package hashes")
    print(f"python_lock_ok direct={len(direct)} locked={len(locked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
