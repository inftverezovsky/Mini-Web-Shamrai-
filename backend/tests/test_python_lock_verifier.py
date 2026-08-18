import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify-python-lock.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_python_lock", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Python lock verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_requirement_versions_resolves_pinned_requirement_and_lock_includes(tmp_path: Path):
    verifier = load_verifier()
    (tmp_path / "runtime.lock").write_text(
        "runtime-package==1.2.3 \\\n+    --hash=sha256:abc\n",
        encoding="utf-8",
    )
    (tmp_path / "audit.txt").write_text("audit-package==4.5.6\n", encoding="utf-8")
    requirements = tmp_path / "dev.txt"
    requirements.write_text(
        "-r runtime.lock\n-r audit.txt\ntest-package==7.8.9\n",
        encoding="utf-8",
    )

    assert verifier.requirement_versions(requirements) == {
        "runtime-package": "1.2.3",
        "audit-package": "4.5.6",
        "test-package": "7.8.9",
    }


def test_requirement_versions_rejects_unpinned_nested_requirement(tmp_path: Path):
    verifier = load_verifier()
    (tmp_path / "nested.txt").write_text("unsafe-package>=1\n", encoding="utf-8")
    requirements = tmp_path / "dev.txt"
    requirements.write_text("--requirement nested.txt\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="not exactly pinned"):
        verifier.requirement_versions(requirements)


def test_requirement_versions_rejects_conflicting_includes(tmp_path: Path):
    verifier = load_verifier()
    (tmp_path / "one.txt").write_text("shared-package==1\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("shared-package==2\n", encoding="utf-8")
    requirements = tmp_path / "dev.txt"
    requirements.write_text("-r one.txt\n-r two.txt\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="conflicting pinned versions"):
        verifier.requirement_versions(requirements)
