"""add immutable payment checkout snapshots and flat revisions

Revision ID: 20260802_0042
Revises: 20260802_0041
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_0042"
down_revision = "20260802_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("checkout_intent_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("payment_attempts", sa.Column("checkout_payload_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "payment_attempts",
        sa.Column("checkout_state", sa.String(length=24), nullable=False, server_default="ready_to_create"),
    )
    op.add_column("payment_attempts", sa.Column("checkout_url", sa.Text(), nullable=True))
    op.add_column("payment_attempts", sa.Column("checkout_creation_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_attempts", sa.Column("telegram_pre_checkout_query_id", sa.String(length=255), nullable=True))
    op.add_column("payment_attempts", sa.Column("telegram_pre_checkout_user_id", sa.BigInteger(), nullable=True))
    op.add_column("payment_attempts", sa.Column("telegram_pre_checkout_reserved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payment_attempts", sa.Column("purchase_type_snapshot", sa.String(length=32), nullable=True))
    op.add_column("payment_attempts", sa.Column("crowd_bet_id_snapshot", sa.Integer(), nullable=True))
    op.add_column("payment_attempts", sa.Column("plan_name_snapshot", sa.String(), nullable=True))
    op.add_column("payment_attempts", sa.Column("entitlement_type_snapshot", sa.String(length=24), nullable=True))
    op.add_column("payment_attempts", sa.Column("target_flats_snapshot", sa.Numeric(12, 2), nullable=True))
    op.add_column("payment_attempts", sa.Column("match_count_snapshot", sa.Integer(), nullable=True))
    op.add_column(
        "payment_attempts",
        sa.Column("discount_percent_snapshot", sa.Integer(), nullable=False, server_default="0"),
    )
    # Keep the entire expand migration in Alembic's transaction.  Flat checkout
    # intents do not exist before this revision, so a regular unique index does
    # not need a long-running concurrent build and avoids a partially-applied
    # schema if a later backfill fails.
    op.create_index(
        "uq_payment_attempts_user_provider_checkout_intent",
        "payment_attempts",
        ["user_id", "provider", "checkout_intent_id"],
        unique=True,
    )
    op.create_index(
        "uq_payment_attempts_telegram_pre_checkout_query",
        "payment_attempts",
        ["telegram_pre_checkout_query_id"],
        unique=True,
    )

    # Freeze the current entitlement for historical attempts that still have a plan.
    # New attempts always populate these columns before contacting a provider.
    op.execute(sa.text("""
        UPDATE payment_attempts
        SET plan_name_snapshot = (
                SELECT subscription_plans.name
                FROM subscription_plans
                WHERE subscription_plans.id = payment_attempts.plan_id
            ),
            entitlement_type_snapshot = (
                SELECT subscription_plans.entitlement_type
                FROM subscription_plans
                WHERE subscription_plans.id = payment_attempts.plan_id
            ),
            target_flats_snapshot = (
                SELECT subscription_plans.target_flats
                FROM subscription_plans
                WHERE subscription_plans.id = payment_attempts.plan_id
            ),
            match_count_snapshot = (
                SELECT subscription_plans.match_count
                FROM subscription_plans
                WHERE subscription_plans.id = payment_attempts.plan_id
            )
        WHERE plan_id IS NOT NULL
    """))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET discount_percent_snapshot = CASE
                WHEN COALESCE(metadata_json ->> 'discount_percent', '') ~ '^[0-9]{1,3}$'
                    THEN LEAST(100, GREATEST(0, (metadata_json ->> 'discount_percent')::integer))
                ELSE 0
            END
        """))
    elif bind.dialect.name == "sqlite":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET discount_percent_snapshot = CASE
                WHEN json_type(metadata_json, '$.discount_percent') = 'integer'
                     AND json_extract(metadata_json, '$.discount_percent') BETWEEN 0 AND 999
                    THEN MIN(100, CAST(json_extract(metadata_json, '$.discount_percent') AS INTEGER))
                WHEN json_type(metadata_json, '$.discount_percent') = 'text'
                     AND length(json_extract(metadata_json, '$.discount_percent')) BETWEEN 1 AND 3
                     AND json_extract(metadata_json, '$.discount_percent') NOT GLOB '*[^0-9]*'
                    THEN MIN(100, CAST(json_extract(metadata_json, '$.discount_percent') AS INTEGER))
                ELSE 0
            END
        """))
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET purchase_type_snapshot = CASE
                WHEN bet_id IS NULL THEN NULL
                WHEN COALESCE(NULLIF(metadata_json ->> 'purchase_type', ''), 'single_bet')
                     IN ('single_bet', 'bet_hint', 'crowd_bet')
                    THEN COALESCE(NULLIF(metadata_json ->> 'purchase_type', ''), 'single_bet')
                ELSE NULL
            END
        """))
    elif bind.dialect.name == "sqlite":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET purchase_type_snapshot = CASE
                WHEN bet_id IS NULL THEN NULL
                WHEN COALESCE(NULLIF(json_extract(metadata_json, '$.purchase_type'), ''), 'single_bet')
                     IN ('single_bet', 'bet_hint', 'crowd_bet')
                    THEN COALESCE(NULLIF(json_extract(metadata_json, '$.purchase_type'), ''), 'single_bet')
                ELSE NULL
            END
        """))
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET crowd_bet_id_snapshot = CASE
                WHEN purchase_type_snapshot = 'crowd_bet'
                     AND COALESCE(metadata_json ->> 'crowd_bet_id', '') ~ '^[1-9][0-9]{0,9}$'
                     AND (metadata_json ->> 'crowd_bet_id')::bigint <= 2147483647
                    THEN (metadata_json ->> 'crowd_bet_id')::integer
                ELSE NULL
            END
        """))
    elif bind.dialect.name == "sqlite":
        op.execute(sa.text("""
            UPDATE payment_attempts
            SET crowd_bet_id_snapshot = CASE
                WHEN purchase_type_snapshot = 'crowd_bet'
                     AND json_type(metadata_json, '$.crowd_bet_id') = 'integer'
                     AND json_extract(metadata_json, '$.crowd_bet_id') BETWEEN 1 AND 2147483647
                    THEN CAST(json_extract(metadata_json, '$.crowd_bet_id') AS INTEGER)
                WHEN purchase_type_snapshot = 'crowd_bet'
                     AND json_type(metadata_json, '$.crowd_bet_id') = 'text'
                     AND length(json_extract(metadata_json, '$.crowd_bet_id')) BETWEEN 1 AND 10
                     AND json_extract(metadata_json, '$.crowd_bet_id') NOT GLOB '*[^0-9]*'
                     AND CAST(json_extract(metadata_json, '$.crowd_bet_id') AS INTEGER) BETWEEN 1 AND 2147483647
                    THEN CAST(json_extract(metadata_json, '$.crowd_bet_id') AS INTEGER)
                ELSE NULL
            END
        """))
    # Historical rows never stored their checkout URL.  Any unfinished Stars
    # invoice is therefore unrecoverable after the deploy and must be reconciled
    # manually.  Ambiguous item links, unknown purchase types, and incomplete
    # plan snapshots also fail closed instead of consulting mutable live data.
    op.execute(sa.text("""
        UPDATE payment_attempts
        SET checkout_state = CASE
            WHEN status = 'succeeded' THEN 'ready'
            WHEN status IN ('pending', 'processing') AND (
                provider = 'telegram_stars'
                OR (plan_id IS NULL AND bet_id IS NULL)
                OR (plan_id IS NOT NULL AND bet_id IS NOT NULL)
                OR (bet_id IS NOT NULL AND purchase_type_snapshot IS NULL)
                OR (purchase_type_snapshot = 'crowd_bet' AND crowd_bet_id_snapshot IS NULL)
                OR (
                    plan_id IS NOT NULL
                    AND (
                        plan_name_snapshot IS NULL
                        OR entitlement_type_snapshot IS NULL
                        OR entitlement_type_snapshot NOT IN ('flat', 'legacy_match')
                        OR (entitlement_type_snapshot = 'flat' AND target_flats_snapshot IS NULL)
                        OR (entitlement_type_snapshot = 'legacy_match' AND match_count_snapshot IS NULL)
                    )
                )
            ) THEN 'requires_reconciliation'
            WHEN provider_payment_id IS NOT NULL THEN 'ready'
            ELSE 'legacy'
        END
    """))
    # Existing duplicate active links cannot be assigned a trusted winner.
    # Quarantine every member before creating the one-active-purchase guard.
    op.execute(sa.text("""
        UPDATE payment_attempts
        SET checkout_state = 'requires_reconciliation'
        WHERE id IN (
            SELECT candidate.id
            FROM payment_attempts AS candidate
            JOIN (
                SELECT user_id, provider, bet_id, purchase_type_snapshot
                FROM payment_attempts
                WHERE provider = 'telegram_stars'
                  AND bet_id IS NOT NULL
                  AND purchase_type_snapshot IN ('single_bet', 'bet_hint')
                  AND status IN ('pending', 'processing')
                  AND checkout_state <> 'requires_reconciliation'
                GROUP BY user_id, provider, bet_id, purchase_type_snapshot
                HAVING COUNT(*) > 1
            ) AS duplicate_group
              ON duplicate_group.user_id = candidate.user_id
             AND duplicate_group.provider = candidate.provider
             AND duplicate_group.bet_id = candidate.bet_id
             AND duplicate_group.purchase_type_snapshot = candidate.purchase_type_snapshot
            WHERE candidate.status IN ('pending', 'processing')
              AND candidate.checkout_state <> 'requires_reconciliation'
        )
    """))
    op.create_index(
        "uq_payment_attempts_active_telegram_purchase",
        "payment_attempts",
        ["user_id", "provider", "bet_id", "purchase_type_snapshot"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'telegram_stars' AND bet_id IS NOT NULL "
            "AND purchase_type_snapshot IN ('single_bet', 'bet_hint') "
            "AND status IN ('pending', 'processing') "
            "AND checkout_state <> 'requires_reconciliation'"
        ),
        sqlite_where=sa.text(
            "provider = 'telegram_stars' AND bet_id IS NOT NULL "
            "AND purchase_type_snapshot IN ('single_bet', 'bet_hint') "
            "AND status IN ('pending', 'processing') "
            "AND checkout_state <> 'requires_reconciliation'"
        ),
    )
    op.create_index(
        "uq_payment_attempts_active_telegram_crowd_purchase",
        "payment_attempts",
        ["user_id", "provider", "crowd_bet_id_snapshot"],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'telegram_stars' AND crowd_bet_id_snapshot IS NOT NULL "
            "AND purchase_type_snapshot = 'crowd_bet' "
            "AND status IN ('pending', 'processing') "
            "AND checkout_state <> 'requires_reconciliation'"
        ),
        sqlite_where=sa.text(
            "provider = 'telegram_stars' AND crowd_bet_id_snapshot IS NOT NULL "
            "AND purchase_type_snapshot = 'crowd_bet' "
            "AND status IN ('pending', 'processing') "
            "AND checkout_state <> 'requires_reconciliation'"
        ),
    )

    op.add_column(
        "flat_subscriptions",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("flat_subscriptions", "revision")
    op.drop_index(
        "uq_payment_attempts_active_telegram_crowd_purchase",
        table_name="payment_attempts",
    )
    op.drop_index(
        "uq_payment_attempts_active_telegram_purchase",
        table_name="payment_attempts",
    )
    op.drop_index(
        "uq_payment_attempts_telegram_pre_checkout_query",
        table_name="payment_attempts",
    )
    op.drop_index(
        "uq_payment_attempts_user_provider_checkout_intent",
        table_name="payment_attempts",
    )
    op.drop_column("payment_attempts", "discount_percent_snapshot")
    op.drop_column("payment_attempts", "match_count_snapshot")
    op.drop_column("payment_attempts", "target_flats_snapshot")
    op.drop_column("payment_attempts", "entitlement_type_snapshot")
    op.drop_column("payment_attempts", "plan_name_snapshot")
    op.drop_column("payment_attempts", "checkout_url")
    op.drop_column("payment_attempts", "telegram_pre_checkout_reserved_at")
    op.drop_column("payment_attempts", "telegram_pre_checkout_user_id")
    op.drop_column("payment_attempts", "telegram_pre_checkout_query_id")
    op.drop_column("payment_attempts", "checkout_creation_started_at")
    op.drop_column("payment_attempts", "crowd_bet_id_snapshot")
    op.drop_column("payment_attempts", "purchase_type_snapshot")
    op.drop_column("payment_attempts", "checkout_state")
    op.drop_column("payment_attempts", "checkout_payload_hash")
    op.drop_column("payment_attempts", "checkout_intent_id")
