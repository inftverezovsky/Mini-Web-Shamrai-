from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Mapping


@dataclass(frozen=True)
class EnvPatchResult:
    replaced_counts: dict[str, int]
    appended_keys: list[str]


def _env_key_from_line(line: str) -> str | None:
    if "=" not in line or line.lstrip().startswith("#"):
        return None
    return line.split("=", 1)[0]


def patch_env_file(path: str | Path, values: Mapping[str, str]) -> EnvPatchResult:
    env_path = Path(path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining_values = {key: str(value) for key, value in values.items()}
    replaced_counts = {key: 0 for key in remaining_values}
    out: list[str] = []
    seen: set[str] = set()

    for line in lines:
        key = _env_key_from_line(line)
        if key in remaining_values:
            if key not in seen:
                out.append(f"{key}={remaining_values[key]}")
                seen.add(key)
            replaced_counts[key] += 1
            continue
        out.append(line)

    appended_keys: list[str] = []
    for key, value in remaining_values.items():
        if key in seen:
            continue
        out.append(f"{key}={value}")
        appended_keys.append(key)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return EnvPatchResult(replaced_counts=replaced_counts, appended_keys=appended_keys)


def read_env_value(path: str | Path, key: str) -> str:
    env_path = Path(path)
    if not env_path.exists():
        return ""
    value = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
    return value


def _main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: python -m src.scripts.env_patch <env-path> KEY=VALUE [...]", file=sys.stderr)
        return 2

    values: dict[str, str] = {}
    for item in argv[2:]:
        if "=" not in item:
            print(f"invalid env assignment: {item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        if not key:
            print("empty env key is not allowed", file=sys.stderr)
            return 2
        values[key] = value

    result = patch_env_file(argv[1], values)
    for key, value in values.items():
        print(f"{key} replaced_count={result.replaced_counts.get(key, 0)} final_len={len(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
