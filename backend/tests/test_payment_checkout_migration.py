import importlib.util
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260802_0042_payment_checkout_safety.py"
)


def load_migration_module():
    spec = importlib.util.spec_from_file_location("payment_checkout_safety_0042", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load payment checkout migration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaymentCheckoutMigrationTests(unittest.TestCase):
    def test_upgrade_is_transactional_and_does_not_open_autocommit_block(self):
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertNotIn("autocommit_block", source)
        self.assertNotIn("CONCURRENTLY", source)

    def test_pending_orphan_with_provider_payment_id_requires_reconciliation(self):
        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        subscription_plans = sa.Table(
            "subscription_plans",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String),
            sa.Column("entitlement_type", sa.String(24)),
            sa.Column("target_flats", sa.Numeric(12, 2)),
            sa.Column("match_count", sa.Integer),
        )
        payment_attempts = sa.Table(
            "payment_attempts",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("provider", sa.String, nullable=False),
            sa.Column("provider_payment_id", sa.String),
            sa.Column("plan_id", sa.Integer),
            sa.Column("bet_id", sa.String),
            sa.Column("status", sa.String, nullable=False),
            sa.Column("metadata_json", sa.JSON, nullable=False),
        )
        sa.Table(
            "flat_subscriptions",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
        )
        metadata.create_all(engine)

        with engine.begin() as connection:
            connection.execute(
                subscription_plans.insert(),
                {
                    "id": 7,
                    "name": "Legacy five",
                    "entitlement_type": "legacy_match",
                    "target_flats": None,
                    "match_count": 5,
                },
            )
            connection.execute(
                payment_attempts.insert(),
                [
                    {
                        "id": 1,
                        "user_id": 10,
                        "provider": "yookassa",
                        "provider_payment_id": "provider-payment",
                        "plan_id": None,
                        "bet_id": None,
                        "status": "pending",
                        "metadata_json": {},
                    },
                    {
                        "id": 2,
                        "user_id": 11,
                        "provider": "yookassa",
                        "provider_payment_id": "linked-payment",
                        "plan_id": 7,
                        "bet_id": None,
                        "status": "pending",
                        "metadata_json": {},
                    },
                    {
                        "id": 3,
                        "user_id": 12,
                        "provider": "tegro",
                        "provider_payment_id": "processing-orphan",
                        "plan_id": None,
                        "bet_id": None,
                        "status": "processing",
                        "metadata_json": {},
                    },
                    {
                        "id": 4,
                        "user_id": 13,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "shared-bet",
                        "status": "pending",
                        "metadata_json": {"purchase_type": "single_bet"},
                    },
                    {
                        "id": 5,
                        "user_id": 13,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "shared-bet",
                        "status": "processing",
                        "metadata_json": {"purchase_type": "single_bet"},
                    },
                    {
                        "id": 6,
                        "user_id": 14,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "shared-hint-bet",
                        "status": "pending",
                        "metadata_json": {"purchase_type": "bet_hint"},
                    },
                    {
                        "id": 7,
                        "user_id": 14,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "shared-hint-bet",
                        "status": "pending",
                        "metadata_json": {"purchase_type": "bet_hint"},
                    },
                    {
                        "id": 8,
                        "user_id": 15,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "singleton-bet",
                        "status": "pending",
                        "metadata_json": {"purchase_type": "single_bet"},
                    },
                    {
                        "id": 9,
                        "user_id": 16,
                        "provider": "yookassa",
                        "provider_payment_id": "ambiguous-payment",
                        "plan_id": 7,
                        "bet_id": "ambiguous-bet",
                        "status": "pending",
                        "metadata_json": {},
                    },
                    {
                        "id": 10,
                        "user_id": 17,
                        "provider": "tegro",
                        "provider_payment_id": "unknown-purchase",
                        "plan_id": None,
                        "bet_id": "unknown-bet",
                        "status": "pending",
                        "metadata_json": {"purchase_type": "x" * 200},
                    },
                    {
                        "id": 11,
                        "user_id": 18,
                        "provider": "tegro",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "discount-bet-1",
                        "status": "failed",
                        "metadata_json": {
                            "purchase_type": "single_bet",
                            "discount_percent": "9" * 200,
                        },
                    },
                    {
                        "id": 12,
                        "user_id": 19,
                        "provider": "tegro",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "discount-bet-2",
                        "status": "failed",
                        "metadata_json": {
                            "purchase_type": "single_bet",
                            "discount_percent": "20abc",
                        },
                    },
                    {
                        "id": 13,
                        "user_id": 20,
                        "provider": "tegro",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "discount-bet-3",
                        "status": "failed",
                        "metadata_json": {
                            "purchase_type": "single_bet",
                            "discount_percent": True,
                        },
                    },
                    {
                        "id": 14,
                        "user_id": 21,
                        "provider": "tegro",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "discount-bet-4",
                        "status": "failed",
                        "metadata_json": {
                            "purchase_type": "single_bet",
                            "discount_percent": "100",
                        },
                    },
                    {
                        "id": 15,
                        "user_id": 22,
                        "provider": "telegram_stars",
                        "provider_payment_id": None,
                        "plan_id": None,
                        "bet_id": "crowd-bet",
                        "status": "pending",
                        "metadata_json": {
                            "purchase_type": "crowd_bet",
                            "crowd_bet_id": "42",
                        },
                    },
                ],
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                load_migration_module().upgrade()

            rows = connection.execute(
                sa.text(
                    "SELECT id, checkout_state, purchase_type_snapshot, discount_percent_snapshot, "
                    "crowd_bet_id_snapshot "
                    "FROM payment_attempts ORDER BY id"
                )
            ).all()
            indexes = sa.inspect(connection).get_indexes("payment_attempts")
            payment_columns = {
                item["name"] for item in sa.inspect(connection).get_columns("payment_attempts")
            }
            flat_columns = {
                item["name"] for item in sa.inspect(connection).get_columns("flat_subscriptions")
            }

        self.assertEqual(
            rows,
            [
                (1, "requires_reconciliation", None, 0, None),
                (2, "ready", None, 0, None),
                (3, "requires_reconciliation", None, 0, None),
                (4, "requires_reconciliation", "single_bet", 0, None),
                (5, "requires_reconciliation", "single_bet", 0, None),
                (6, "requires_reconciliation", "bet_hint", 0, None),
                (7, "requires_reconciliation", "bet_hint", 0, None),
                (8, "requires_reconciliation", "single_bet", 0, None),
                (9, "requires_reconciliation", "single_bet", 0, None),
                (10, "requires_reconciliation", None, 0, None),
                (11, "legacy", "single_bet", 0, None),
                (12, "legacy", "single_bet", 0, None),
                (13, "legacy", "single_bet", 0, None),
                (14, "legacy", "single_bet", 100, None),
                (15, "requires_reconciliation", "crowd_bet", 0, 42),
            ],
        )
        checkout_index = next(
            item
            for item in indexes
            if item["name"] == "uq_payment_attempts_user_provider_checkout_intent"
        )
        self.assertTrue(checkout_index["unique"])
        pre_checkout_index = next(
            item
            for item in indexes
            if item["name"] == "uq_payment_attempts_telegram_pre_checkout_query"
        )
        active_purchase_index = next(
            item
            for item in indexes
            if item["name"] == "uq_payment_attempts_active_telegram_purchase"
        )
        active_crowd_purchase_index = next(
            item
            for item in indexes
            if item["name"] == "uq_payment_attempts_active_telegram_crowd_purchase"
        )
        self.assertTrue(pre_checkout_index["unique"])
        self.assertTrue(active_purchase_index["unique"])
        self.assertTrue(active_crowd_purchase_index["unique"])
        migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("purchase_type_snapshot IN ('single_bet', 'bet_hint')", migration_source)
        self.assertTrue({
            "checkout_creation_started_at",
            "telegram_pre_checkout_query_id",
            "telegram_pre_checkout_user_id",
            "telegram_pre_checkout_reserved_at",
            "purchase_type_snapshot",
            "crowd_bet_id_snapshot",
        }.issubset(payment_columns))
        self.assertIn("revision", flat_columns)


if __name__ == "__main__":
    unittest.main()
