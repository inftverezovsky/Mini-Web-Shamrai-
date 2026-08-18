import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci_alembic_fresh_db.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("ci_alembic_fresh_db", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load CI migration bootstrap module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CiAlembicBootstrapTests(unittest.TestCase):
    def test_fresh_database_runner_uses_only_the_shipped_migration_chain(self):
        module = load_script_module()
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertTrue(callable(module.run_fresh_database_migration))
        self.assertIn('LEGACY_FLAT_REVISION = "20260802_0041"', source)
        self.assertIn('arguments=["upgrade", LEGACY_FLAT_REVISION]', source)
        self.assertIn('"check"', source)
        self.assertIn("ScriptDirectory", source)
        self.assertIn("SELECT version_num FROM alembic_version", source)
        self.assertIn("database_readiness_probe", source)
        self.assertNotIn("BASELINE_COMMIT", source)
        self.assertNotIn("git archive", source)

    def test_disposable_schema_reset_is_restricted_to_explicit_local_ci_database(self):
        module = load_script_module()

        with self.assertRaisesRegex(RuntimeError, "explicitly enabled"):
            module._validate_disposable_database_url(
                "postgresql://postgres:postgres@localhost:5432/shamrai_ci",
                allow_schema_reset=False,
            )
        with self.assertRaisesRegex(RuntimeError, "loopback"):
            module._validate_disposable_database_url(
                "postgresql://postgres:postgres@db.internal:5432/shamrai_ci",
                allow_schema_reset=True,
            )
        with self.assertRaisesRegex(RuntimeError, "shamrai_ci"):
            module._validate_disposable_database_url(
                "postgresql://postgres:postgres@localhost:5432/shamrai",
                allow_schema_reset=True,
            )
        with self.assertRaisesRegex(RuntimeError, "connection overrides"):
            module._validate_disposable_database_url(
                "postgresql://postgres:postgres@localhost:5432/shamrai_ci?host=db.internal",
                allow_schema_reset=True,
            )

        module._validate_disposable_database_url(
            "postgresql://postgres:postgres@127.0.0.1:5432/shamrai_ci",
            allow_schema_reset=True,
        )
        self.assertTrue(module._is_allowed_ci_server_address("127.0.0.1"))
        self.assertTrue(module._is_allowed_ci_server_address("172.17.0.2"))
        self.assertTrue(module._is_allowed_ci_server_address("172.17.0.2/32"))
        self.assertTrue(module._is_allowed_ci_server_address("::1/128"))
        self.assertTrue(module._is_allowed_ci_server_address("::ffff:172.17.0.2"))
        self.assertTrue(module._is_allowed_ci_server_address("10.10.0.8"))
        self.assertFalse(module._is_allowed_ci_server_address("8.8.8.8"))
        self.assertFalse(module._is_allowed_ci_server_address(None))

    def test_gate_runs_fresh_and_seeded_upgrade_paths_in_order(self):
        module = load_script_module()
        events: list[str] = []

        with (
            patch.dict("os.environ", {module.SCHEMA_RESET_ENV: "1"}, clear=False),
            patch.object(module, "_reset_public_schema", side_effect=lambda *_: events.append("reset")),
            patch.object(
                module,
                "_run_alembic",
                side_effect=lambda *_, arguments, **__: events.append("alembic:" + " ".join(arguments)),
            ),
            patch.object(module, "_assert_database_heads", side_effect=lambda *_: events.append("heads")),
            patch.object(module, "_run_app_database_readiness", side_effect=lambda *_: events.append("ready")),
            patch.object(module, "_seed_pre_checkout_safety_rows", side_effect=lambda *_: events.append("seed")),
            patch.object(module, "_assert_seeded_upgrade", side_effect=lambda *_: events.append("assert-seed")),
        ):
            module.run_fresh_database_migration(
                "postgresql://postgres:postgres@localhost:5432/shamrai_ci",
                SCRIPT_PATH.parents[1],
            )

        self.assertEqual(
            events,
            [
                "reset",
                "alembic:upgrade head",
                "heads",
                "alembic:check",
                "ready",
                "reset",
                "alembic:upgrade 20260802_0041",
                "seed",
                "alembic:upgrade head",
                "heads",
                "alembic:check",
                "assert-seed",
                "ready",
            ],
        )

    def test_baseline_revision_is_frozen_and_model_independent(self):
        baseline = SCRIPT_PATH.parents[1] / "backend" / "alembic" / "versions" / "20260605_0001_baseline.py"
        source = baseline.read_text(encoding="utf-8")

        self.assertNotIn("Base.metadata", source)
        self.assertNotIn("src.models", source)
        self.assertNotIn("create_all", source)
        self.assertIn('revision = "20260605_0001"', source)
        self.assertIn("CREATE TABLE users", source)

    def test_ci_explicitly_authorizes_only_the_disposable_schema_reset(self):
        workflow = (SCRIPT_PATH.parents[1] / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python ../scripts/ci_alembic_fresh_db.py", workflow)
        self.assertIn('SHAMRAI_ALLOW_CI_SCHEMA_RESET: "1"', workflow)


if __name__ == "__main__":
    unittest.main()
