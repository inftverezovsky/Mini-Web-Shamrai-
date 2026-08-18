#!/usr/bin/env python3
"""Fail-closed PostgreSQL migration gate for the Shamrai release chain.

The script is intentionally PostgreSQL-only.  SQLite remains useful for ORM
unit tests, but it cannot exercise the project's ALTER CONSTRAINT migrations,
PostgreSQL JSON operators, row locking, or partial-index behavior.
"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

LEGACY_FLAT_REVISION = "20260802_0041"
SCHEMA_RESET_ENV = "SHAMRAI_ALLOW_CI_SCHEMA_RESET"
CI_DATABASE_NAME = "shamrai_ci"
CI_USER_ID = 9_910_001
CI_PLAN_ID = 9_911
CI_FLAT_SUBSCRIPTION_ID = UUID("00000000-0000-0000-0000-000000009911")
CI_BET_IDS = {
    "ambiguous": UUID("10000000-0000-0000-0000-000000009911"),
    "singleton": UUID("20000000-0000-0000-0000-000000009911"),
    "duplicates": UUID("30000000-0000-0000-0000-000000009911"),
    "unknown": UUID("40000000-0000-0000-0000-000000009911"),
    "index_guard": UUID("50000000-0000-0000-0000-000000009911"),
    "crowd": UUID("80000000-0000-0000-0000-000000009911"),
}
CI_ATTEMPT_IDS = {
    "linked_plan": UUID("00000000-0000-0000-0001-000000009911"),
    "orphan": UUID("00000000-0000-0000-0002-000000009911"),
    "ambiguous": UUID("00000000-0000-0000-0003-000000009911"),
    "stars_singleton": UUID("00000000-0000-0000-0004-000000009911"),
    "stars_duplicate_one": UUID("00000000-0000-0000-0005-000000009911"),
    "stars_duplicate_two": UUID("00000000-0000-0000-0006-000000009911"),
    "stars_unknown": UUID("00000000-0000-0000-0007-000000009911"),
    "stars_overlong": UUID("00000000-0000-0000-0008-000000009911"),
    "oversized_discount": UUID("00000000-0000-0000-0009-000000009911"),
    "malformed_discount": UUID("00000000-0000-0000-0010-000000009911"),
    "boolean_discount": UUID("00000000-0000-0000-0011-000000009911"),
    "stars_crowd_valid": UUID("00000000-0000-0000-0012-000000009911"),
    "stars_crowd_invalid": UUID("00000000-0000-0000-0013-000000009911"),
}
CI_SERVER_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")
)

READINESS_PROBE_CODE = r"""
import asyncio
import json

from src.main import app, database_readiness_probe
from src.models.database import engine

async def verify() -> None:
    try:
        result = await database_readiness_probe()
        if result.get("database") != "ok" or result.get("schema") != "ok":
            raise RuntimeError(f"database readiness failed: {result!r}")
        if not getattr(app, "routes", None):
            raise RuntimeError("FastAPI application imported without routes")
        print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()

asyncio.run(verify())
"""

def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=capture,
    )

def _migration_env(database_url: str, backend_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "local",
            "DATABASE_URL": database_url,
            "ENABLE_BACKGROUND_TASKS": "false",
            "PYTHONPATH": str(backend_path),
            "REDIS_CACHE_ENABLED": "false",
        }
    )
    return env

def _validate_disposable_database_url(
    database_url: str,
    *,
    allow_schema_reset: bool,
) -> None:
    if not allow_schema_reset:
        raise RuntimeError(
            f"Disposable schema reset must be explicitly enabled with {SCHEMA_RESET_ENV}=1"
        )
    parsed = make_url(database_url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg2"}:
        raise RuntimeError("Migration qualification requires PostgreSQL with psycopg2")
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Disposable migration database must use a loopback host")
    if parsed.database != CI_DATABASE_NAME:
        raise RuntimeError(f"Disposable migration database must be named {CI_DATABASE_NAME}")
    if parsed.query:
        raise RuntimeError("Disposable migration database URL must not contain connection overrides")

def _is_allowed_ci_server_address(server_address: str | None) -> bool:
    if not server_address:
        return False
    try:
        server_ip = ipaddress.ip_interface(server_address).ip
    except ValueError:
        return False
    if isinstance(server_ip, ipaddress.IPv6Address) and server_ip.ipv4_mapped:
        server_ip = server_ip.ipv4_mapped
    return any(server_ip in network for network in CI_SERVER_NETWORKS)

def _reset_public_schema(database_url: str) -> None:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            database_name, server_address = connection.execute(
                text("SELECT current_database(), inet_server_addr()::text")
            ).one()
            if str(database_name) != CI_DATABASE_NAME:
                raise RuntimeError(
                    f"Connected database must be {CI_DATABASE_NAME}, got {database_name!r}"
                )
            if not _is_allowed_ci_server_address(
                str(server_address) if server_address is not None else None
            ):
                raise RuntimeError(
                    "Connected PostgreSQL server must use a loopback or private CI-container "
                    f"address, got {server_address!r}"
                )
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public AUTHORIZATION CURRENT_USER"))
    finally:
        engine.dispose()

def _run_alembic(
    backend_path: Path,
    env: dict[str, str],
    *,
    arguments: list[str],
) -> None:
    _run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=backend_path,
        env=env,
    )

def _expected_heads(backend_path: Path) -> tuple[str, ...]:
    config = AlembicConfig(str(backend_path / "alembic.ini"))
    config.set_main_option("script_location", str(backend_path / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def _assert_database_heads(database_url: str, backend_path: Path) -> None:
    expected_heads = _expected_heads(backend_path)
    if not expected_heads:
        raise RuntimeError("Alembic migration directory has no head")

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            current_heads = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).all()
                )
            )
    finally:
        engine.dispose()

    if set(current_heads) != set(expected_heads):
        raise RuntimeError(
            f"Database heads do not match migration heads: current={current_heads!r} "
            f"expected={expected_heads!r}"
        )


def _legacy_attempt(
    name: str,
    *,
    provider: str,
    provider_payment_id: str | None = None,
    plan_id: int | None = None,
    bet_id: UUID | None = None,
    status: str = "pending",
    amount: str = "700.00",
    currency: str = "RUB",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": CI_ATTEMPT_IDS[name],
        "plan_id": plan_id,
        "bet_id": bet_id,
        "provider": provider,
        "provider_payment_id": provider_payment_id,
        "status": status,
        "amount": Decimal(amount),
        "currency": currency,
        "metadata": dict(metadata or {}),
    }


def _seed_pre_checkout_safety_rows(database_url: str) -> None:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        telegram_id, is_onboarded, favorite_sports,
                        currency_preference, purchased_bets_balance, free_bets_available,
                        matches_remaining, guarantee_active, tg_chat_joined, has_used_shield,
                        alert_min_coef, is_night_mode, preferred_sports
                    ) VALUES (
                        :user_id, false, CAST(:empty_json AS JSON),
                        'RUB', 0, 0, 0, false, false, false,
                        1.0, false, CAST(:empty_json AS JSON)
                    )
                    """
                ),
                {"user_id": CI_USER_ID, "empty_json": "[]"},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO subscription_plans (
                        id, name, duration_days, match_count, entitlement_type,
                        target_flats, price, price_stars, currency, is_active
                    ) VALUES (
                        :plan_id, :name, 0, 7, 'legacy_match',
                        NULL, 700.00, 70, 'RUB', true
                    )
                    """
                ),
                {"plan_id": CI_PLAN_ID, "name": "Legacy CI plan"},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO bets (
                        id, event_name, coefficient, delivery_mode,
                        category, bookmaker_links, publication_type
                    ) VALUES (
                        :id, :event_name, 1.70, 'feed',
                        'prematch', CAST(:bookmaker_links AS JSON), 'forecast'
                    )
                    """
                ),
                [
                    {
                        "id": bet_id,
                        "event_name": f"CI migration bet {name}",
                        "bookmaker_links": "[]",
                    }
                    for name, bet_id in CI_BET_IDS.items()
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO flat_subscriptions (
                        id, user_id, status, flat_amount_rub, target_flats
                    ) VALUES (:id, :user_id, 'pending_setup', NULL, 3.00)
                    """
                ),
                {"id": CI_FLAT_SUBSCRIPTION_ID, "user_id": CI_USER_ID},
            )

            attempts = [
                _legacy_attempt(
                    "linked_plan",
                    provider="yookassa",
                    provider_payment_id="ci-linked-provider-payment",
                    plan_id=CI_PLAN_ID,
                    amount="525.00",
                    metadata={"discount_percent": 25},
                ),
                _legacy_attempt(
                    "orphan", provider="tegro", provider_payment_id="ci-orphan-provider-payment"
                ),
                _legacy_attempt(
                    "ambiguous",
                    provider="yookassa",
                    provider_payment_id="ci-ambiguous-provider-payment",
                    plan_id=CI_PLAN_ID,
                    bet_id=CI_BET_IDS["ambiguous"],
                    status="processing",
                    metadata={"purchase_type": "single_bet"},
                ),
                *[
                    _legacy_attempt(
                        name,
                        provider="telegram_stars",
                        bet_id=CI_BET_IDS[bet_name],
                        status=status,
                        amount="50.00",
                        currency="XTR",
                        metadata=metadata,
                    )
                    for name, bet_name, status, metadata in (
                        ("stars_singleton", "singleton", "pending", {}),
                        ("stars_duplicate_one", "duplicates", "pending", {"purchase_type": "single_bet"}),
                        ("stars_duplicate_two", "duplicates", "processing", {"purchase_type": "single_bet"}),
                        ("stars_unknown", "unknown", "pending", {"purchase_type": "unexpected_purchase_type"}),
                        ("stars_overlong", "unknown", "pending", {"purchase_type": "x" * 128}),
                        (
                            "stars_crowd_valid",
                            "crowd",
                            "pending",
                            {"purchase_type": "crowd_bet", "crowd_bet_id": 123},
                        ),
                        (
                            "stars_crowd_invalid",
                            "crowd",
                            "pending",
                            {"purchase_type": "crowd_bet", "crowd_bet_id": "20abc"},
                        ),
                    )
                ],
                *[
                    _legacy_attempt(
                        name,
                        provider=provider,
                        provider_payment_id=provider_payment_id,
                        plan_id=CI_PLAN_ID,
                        metadata={"discount_percent": discount},
                    )
                    for name, provider, provider_payment_id, discount in (
                        ("oversized_discount", "tegro", "ci-oversized-discount-payment", "9" * 128),
                        ("malformed_discount", "tegro", "ci-malformed-discount-payment", "20abc"),
                        ("boolean_discount", "yookassa", "ci-boolean-discount-payment", True),
                    )
                ],
            ]
            connection.execute(
                text(
                    """
                    INSERT INTO payment_attempts (
                        id, user_id, plan_id, bet_id, provider,
                        provider_payment_id, status, amount, currency, metadata_json
                    ) VALUES (
                        :id, :user_id, :plan_id, :bet_id, :provider,
                        :provider_payment_id, :status, :amount, :currency,
                        CAST(:metadata_json AS JSON)
                    )
                    """
                ),
                [
                    {
                        **{
                            key: value
                            for key, value in attempt.items()
                            if key != "metadata"
                        },
                        "user_id": CI_USER_ID,
                        "metadata_json": json.dumps(attempt["metadata"], sort_keys=True),
                    }
                    for attempt in attempts
                ],
            )
    finally:
        engine.dispose()


def _post_migration_attempt_values(
    *,
    attempt_id: UUID,
    checkout_intent_id: UUID,
    provider: str,
    status: str,
    checkout_state: str,
    plan_id: int | None = None,
    bet_id: UUID | None = None,
    purchase_type_snapshot: str | None = None,
    crowd_bet_id_snapshot: int | None = None,
    telegram_pre_checkout_query_id: str | None = None,
) -> dict[str, Any]:
    is_plan = plan_id is not None
    return {
        "id": attempt_id,
        "user_id": CI_USER_ID,
        "plan_id": plan_id,
        "bet_id": bet_id,
        "provider": provider,
        "status": status,
        "amount": Decimal("700.00") if is_plan else Decimal("50.00"),
        "currency": "RUB" if is_plan else "XTR",
        "metadata_json": json.dumps(
            {"purchase_type": purchase_type_snapshot}
            if purchase_type_snapshot is not None
            else {},
            sort_keys=True,
        ),
        "checkout_intent_id": checkout_intent_id,
        "checkout_state": checkout_state,
        "telegram_pre_checkout_query_id": telegram_pre_checkout_query_id,
        "purchase_type_snapshot": purchase_type_snapshot,
        "crowd_bet_id_snapshot": crowd_bet_id_snapshot,
        "plan_name_snapshot": "Legacy CI plan" if is_plan else None,
        "entitlement_type_snapshot": "legacy_match" if is_plan else None,
        "match_count_snapshot": 7 if is_plan else None,
    }


POST_MIGRATION_ATTEMPT_INSERT = text(
    """
    INSERT INTO payment_attempts (
        id, user_id, plan_id, bet_id, provider, status,
        amount, currency, metadata_json, checkout_intent_id, checkout_state,
        telegram_pre_checkout_query_id, purchase_type_snapshot, crowd_bet_id_snapshot,
        plan_name_snapshot, entitlement_type_snapshot, match_count_snapshot,
        discount_percent_snapshot
    ) VALUES (
        :id, :user_id, :plan_id, :bet_id, :provider, :status,
        :amount, :currency, CAST(:metadata_json AS JSON),
        :checkout_intent_id, :checkout_state, :telegram_pre_checkout_query_id,
        :purchase_type_snapshot, :crowd_bet_id_snapshot,
        :plan_name_snapshot, :entitlement_type_snapshot, :match_count_snapshot,
        0
    )
    """
)


def _insert_post_migration_attempt(database_url: str, values: dict[str, Any]) -> None:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(POST_MIGRATION_ATTEMPT_INSERT, values)
    finally:
        engine.dispose()


def _assert_insert_rejected(database_url: str, values: dict[str, Any], *, label: str) -> None:
    try:
        _insert_post_migration_attempt(database_url, values)
    except IntegrityError:
        return
    raise RuntimeError(f"Expected PostgreSQL uniqueness rejection for {label}")


def _assert_payment_indexes(database_url: str) -> None:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            index_rows = {
                str(row.name): row
                for row in connection.execute(
                    text(
                        """
                        SELECT
                            index_class.relname AS name,
                            index_info.indisunique AS is_unique,
                            pg_get_expr(index_info.indpred, index_info.indrelid) AS predicate
                        FROM pg_index AS index_info
                        JOIN pg_class AS table_class
                          ON table_class.oid = index_info.indrelid
                        JOIN pg_class AS index_class
                          ON index_class.oid = index_info.indexrelid
                        JOIN pg_namespace AS namespace
                          ON namespace.oid = table_class.relnamespace
                        WHERE namespace.nspname = 'public'
                          AND table_class.relname = 'payment_attempts'
                          AND index_class.relname IN (
                              'uq_payment_attempts_user_provider_checkout_intent',
                              'uq_payment_attempts_telegram_pre_checkout_query',
                              'uq_payment_attempts_active_telegram_purchase',
                              'uq_payment_attempts_active_telegram_crowd_purchase'
                          )
                        """
                    )
                ).all()
            }
    finally:
        engine.dispose()

    required = {
        "uq_payment_attempts_user_provider_checkout_intent",
        "uq_payment_attempts_telegram_pre_checkout_query",
        "uq_payment_attempts_active_telegram_purchase",
        "uq_payment_attempts_active_telegram_crowd_purchase",
    }
    if set(index_rows) != required:
        raise RuntimeError(f"Payment safety indexes differ from the required set: {set(index_rows)!r}")
    if not all(bool(row.is_unique) for row in index_rows.values()):
        raise RuntimeError("Every payment safety index must be unique")

    active_predicate = str(
        index_rows["uq_payment_attempts_active_telegram_purchase"].predicate or ""
    ).lower()
    for fragment in (
        "telegram_stars",
        "single_bet",
        "bet_hint",
        "pending",
        "processing",
        "requires_reconciliation",
    ):
        if fragment not in active_predicate:
            raise RuntimeError(f"Active Stars index predicate is missing {fragment!r}")

    crowd_predicate = str(
        index_rows["uq_payment_attempts_active_telegram_crowd_purchase"].predicate or ""
    ).lower()
    for fragment in (
        "telegram_stars",
        "crowd_bet_id_snapshot",
        "crowd_bet",
        "pending",
        "processing",
        "requires_reconciliation",
    ):
        if fragment not in crowd_predicate:
            raise RuntimeError(f"Active crowd index predicate is missing {fragment!r}")


def _assert_payment_index_behavior(database_url: str) -> None:
    shared_intent = UUID("60000000-0000-0000-0000-000000009911")
    first_idempotent = _post_migration_attempt_values(
        attempt_id=UUID("60000000-0000-0000-0001-000000009911"),
        checkout_intent_id=shared_intent,
        provider="yookassa",
        status="failed",
        checkout_state="failed",
        plan_id=CI_PLAN_ID,
    )
    duplicate_idempotent = {
        **first_idempotent,
        "id": UUID("60000000-0000-0000-0002-000000009911"),
    }
    _insert_post_migration_attempt(database_url, first_idempotent)
    _assert_insert_rejected(
        database_url,
        duplicate_idempotent,
        label="checkout intent idempotency",
    )

    first_pre_checkout = _post_migration_attempt_values(
        attempt_id=UUID("65000000-0000-0000-0001-000000009911"),
        checkout_intent_id=UUID("65000000-0000-0000-0011-000000009911"),
        provider="telegram_stars",
        status="failed",
        checkout_state="failed",
        bet_id=CI_BET_IDS["unknown"],
        purchase_type_snapshot="bet_hint",
        telegram_pre_checkout_query_id="ci-shared-pre-checkout-query",
    )
    duplicate_pre_checkout = {
        **first_pre_checkout,
        "id": UUID("65000000-0000-0000-0002-000000009911"),
        "checkout_intent_id": UUID("65000000-0000-0000-0012-000000009911"),
    }
    _insert_post_migration_attempt(database_url, first_pre_checkout)
    _assert_insert_rejected(
        database_url,
        duplicate_pre_checkout,
        label="Telegram pre-checkout query replay",
    )

    first_active = _post_migration_attempt_values(
        attempt_id=UUID("70000000-0000-0000-0001-000000009911"),
        checkout_intent_id=UUID("70000000-0000-0000-0011-000000009911"),
        provider="telegram_stars",
        status="pending",
        checkout_state="ready_to_create",
        bet_id=CI_BET_IDS["index_guard"],
        purchase_type_snapshot="bet_hint",
    )
    duplicate_active = {
        **first_active,
        "id": UUID("70000000-0000-0000-0002-000000009911"),
        "checkout_intent_id": UUID("70000000-0000-0000-0012-000000009911"),
    }
    reconciled_attempt = {
        **first_active,
        "id": UUID("70000000-0000-0000-0003-000000009911"),
        "checkout_intent_id": UUID("70000000-0000-0000-0013-000000009911"),
        "checkout_state": "requires_reconciliation",
    }
    _insert_post_migration_attempt(database_url, first_active)
    _assert_insert_rejected(
        database_url,
        duplicate_active,
        label="one active Telegram Stars purchase",
    )
    _insert_post_migration_attempt(database_url, reconciled_attempt)

    first_crowd = _post_migration_attempt_values(
        attempt_id=UUID("75000000-0000-0000-0001-000000009911"),
        checkout_intent_id=UUID("75000000-0000-0000-0011-000000009911"),
        provider="telegram_stars",
        status="pending",
        checkout_state="ready_to_create",
        bet_id=CI_BET_IDS["crowd"],
        purchase_type_snapshot="crowd_bet",
        crowd_bet_id_snapshot=456,
    )
    duplicate_crowd = {
        **first_crowd,
        "id": UUID("75000000-0000-0000-0002-000000009911"),
        "checkout_intent_id": UUID("75000000-0000-0000-0012-000000009911"),
    }
    reconciled_crowd = {
        **first_crowd,
        "id": UUID("75000000-0000-0000-0003-000000009911"),
        "checkout_intent_id": UUID("75000000-0000-0000-0013-000000009911"),
        "checkout_state": "requires_reconciliation",
    }
    _insert_post_migration_attempt(database_url, first_crowd)
    _assert_insert_rejected(
        database_url,
        duplicate_crowd,
        label="one active Telegram Stars crowd contribution",
    )
    _insert_post_migration_attempt(database_url, reconciled_crowd)


def _assert_seeded_upgrade(database_url: str) -> None:
    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE subscription_plans
                    SET name = 'Mutated plan after checkout', match_count = 99
                    WHERE id = :plan_id
                    """
                ),
                {"plan_id": CI_PLAN_ID},
            )
            rows = connection.execute(
                text(
                    """
                    SELECT
                        id,
                        checkout_state,
                        purchase_type_snapshot,
                        crowd_bet_id_snapshot,
                        plan_name_snapshot,
                        entitlement_type_snapshot,
                        target_flats_snapshot,
                        match_count_snapshot,
                        discount_percent_snapshot
                    FROM payment_attempts
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": CI_USER_ID},
            ).mappings().all()
            revision = connection.execute(
                text("SELECT revision FROM flat_subscriptions WHERE id = :id"),
                {"id": CI_FLAT_SUBSCRIPTION_ID},
            ).scalar_one()
    finally:
        engine.dispose()

    attempts = {UUID(str(row["id"])): row for row in rows}
    if set(attempts) != set(CI_ATTEMPT_IDS.values()):
        raise RuntimeError("Seeded migration attempts were lost during upgrade")

    linked = attempts[CI_ATTEMPT_IDS["linked_plan"]]
    if (
        linked["plan_name_snapshot"] != "Legacy CI plan"
        or linked["entitlement_type_snapshot"] != "legacy_match"
        or linked["target_flats_snapshot"] is not None
        or int(linked["match_count_snapshot"] or 0) != 7
        or int(linked["discount_percent_snapshot"] or 0) != 25
    ):
        raise RuntimeError(f"Linked payment snapshot was not frozen correctly: {dict(linked)!r}")

    reconciliation_names = {
        "orphan",
        "ambiguous",
        "stars_singleton",
        "stars_duplicate_one",
        "stars_duplicate_two",
        "stars_unknown",
        "stars_overlong",
        "stars_crowd_valid",
        "stars_crowd_invalid",
    }
    for name in reconciliation_names:
        state = attempts[CI_ATTEMPT_IDS[name]]["checkout_state"]
        if state != "requires_reconciliation":
            raise RuntimeError(f"Adversarial attempt {name!r} was not quarantined: {state!r}")

    for name in ("stars_singleton", "stars_duplicate_one", "stars_duplicate_two"):
        snapshot = attempts[CI_ATTEMPT_IDS[name]]["purchase_type_snapshot"]
        if snapshot != "single_bet":
            raise RuntimeError(f"Known Stars purchase type was not canonicalized: {name}={snapshot!r}")

    for name in ("stars_unknown", "stars_overlong"):
        snapshot = attempts[CI_ATTEMPT_IDS[name]]["purchase_type_snapshot"]
        if snapshot not in {None, "unknown"}:
            raise RuntimeError(f"Unknown purchase type was retained as executable data: {snapshot!r}")

    valid_crowd = attempts[CI_ATTEMPT_IDS["stars_crowd_valid"]]
    if valid_crowd["purchase_type_snapshot"] != "crowd_bet" or int(
        valid_crowd["crowd_bet_id_snapshot"] or 0
    ) != 123:
        raise RuntimeError(f"Valid historical crowd purchase was not frozen: {dict(valid_crowd)!r}")
    invalid_crowd = attempts[CI_ATTEMPT_IDS["stars_crowd_invalid"]]
    if (
        invalid_crowd["purchase_type_snapshot"] != "crowd_bet"
        or invalid_crowd["crowd_bet_id_snapshot"] is not None
    ):
        raise RuntimeError(f"Invalid historical crowd purchase was not quarantined: {dict(invalid_crowd)!r}")

    for name in ("oversized_discount", "malformed_discount", "boolean_discount"):
        discount = int(attempts[CI_ATTEMPT_IDS[name]]["discount_percent_snapshot"])
        if discount != 0:
            raise RuntimeError(f"Invalid discount {name!r} must migrate to 0, got {discount}")
    if int(revision) != 1:
        raise RuntimeError(f"Existing flat subscription revision must start at 1, got {revision!r}")

    _assert_payment_indexes(database_url)
    _assert_payment_index_behavior(database_url)


def _run_app_database_readiness(backend_path: Path, env: dict[str, str]) -> None:
    _run(
        [sys.executable, "-c", READINESS_PROBE_CODE],
        cwd=backend_path,
        env=env,
    )


def run_fresh_database_migration(database_url: str, repository: Path) -> None:
    _validate_disposable_database_url(
        database_url,
        allow_schema_reset=os.getenv(SCHEMA_RESET_ENV, "").strip() == "1",
    )
    backend_path = repository / "backend"
    migration_env = _migration_env(database_url, backend_path)

    print("Qualifying fresh PostgreSQL migration chain")
    _reset_public_schema(database_url)
    _run_alembic(backend_path, migration_env, arguments=["upgrade", "head"])
    _assert_database_heads(database_url, backend_path)
    _run_alembic(backend_path, migration_env, arguments=["check"])
    _run_app_database_readiness(backend_path, migration_env)

    print(f"Qualifying seeded PostgreSQL upgrade from {LEGACY_FLAT_REVISION}")
    _reset_public_schema(database_url)
    _run_alembic(
        backend_path,
        migration_env,
        arguments=["upgrade", LEGACY_FLAT_REVISION],
    )
    _seed_pre_checkout_safety_rows(database_url)
    _run_alembic(backend_path, migration_env, arguments=["upgrade", "head"])
    _assert_database_heads(database_url, backend_path)
    _run_alembic(backend_path, migration_env, arguments=["check"])
    _assert_seeded_upgrade(database_url)
    _run_app_database_readiness(backend_path, migration_env)


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    repository = Path(__file__).resolve().parents[1]
    run_fresh_database_migration(database_url, repository)
    print("alembic_postgresql_qualification_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
