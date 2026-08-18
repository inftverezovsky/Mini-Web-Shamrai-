import json
import inspect
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import payments, subscriptions
from src.models.database import Base
from src.models.models import (
    ABTestConfig,
    Bet,
    FlatSubscription,
    FlatSubscriptionCredit,
    PaymentAttempt,
    Subscription,
    SubscriptionPlan,
    User,
    user_bets,
)
from src.schemas.schemas import FlatSubscriptionConfigureRequest


class _FakeProviderResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class PaymentCheckoutSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    @staticmethod
    def _user(user_id: int = 7001) -> User:
        return User(
            telegram_id=user_id,
            role="user",
            purchased_bets_balance=0,
            matches_remaining=0,
        )

    @staticmethod
    def _flat_plan(*, name: str = "Цель +3", target: str = "3.00") -> SubscriptionPlan:
        return SubscriptionPlan(
            name=name,
            duration_days=0,
            match_count=1,
            entitlement_type="flat",
            target_flats=Decimal(target),
            price=Decimal("1490.00"),
            price_stars=0,
            currency="RUB",
            is_active=True,
        )

    def test_discount_uses_half_up_money_rounding(self):
        self.assertEqual(
            payments._apply_percent_discount(
                Decimal("1.50"),
                1,
                minimum=Decimal("1.00"),
            ),
            Decimal("1.49"),
        )
        self.assertEqual(
            payments._apply_percent_discount(
                Decimal("50"),
                15,
                minimum=Decimal("1"),
                quantum=Decimal("1"),
            ),
            Decimal("43"),
        )

    async def test_discounted_stars_invoice_and_precheckout_use_same_integer_amount(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()
            intent_id = uuid.uuid4()

            with (
                patch.object(payments, "_validate_promo", new=AsyncMock(return_value=("SALE15", 15))),
                patch.object(
                    payments,
                    "create_telegram_stars_invoice_link",
                    new=AsyncMock(return_value="https://invoice.invalid/discounted"),
                ),
            ):
                invoice = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id, promo_code="SALE15"),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            attempt = await session.get(PaymentAttempt, uuid.UUID(invoice["attempt_id"]))
            telegram_api = AsyncMock(return_value={"ok": True})
            with patch.object(payments, "call_telegram_api_async", new=telegram_api):
                result = await payments.process_telegram_payment_update(
                    {
                        "pre_checkout_query": {
                            "id": "discounted-query",
                            "from": {"id": user.telegram_id},
                            "invoice_payload": str(attempt.id),
                            "total_amount": 43,
                            "currency": "XTR",
                        }
                    },
                    session,
                )

            self.assertEqual(attempt.amount, Decimal("43.00"))
            self.assertEqual(result["status"], "pre_checkout_answered")
            self.assertTrue(telegram_api.await_args.args[1]["ok"])
            await session.refresh(attempt)
            self.assertEqual(attempt.status, "processing")
            self.assertEqual(attempt.telegram_pre_checkout_query_id, "discounted-query")
            self.assertEqual(attempt.telegram_pre_checkout_user_id, user.telegram_id)

    async def test_stars_precheckout_reserves_before_ok_and_is_query_idempotent(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_state="ready",
                checkout_url="https://invoice.invalid/reserved",
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                metadata_json={"purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id
            user_id = user.telegram_id

            observed_at_answer: list[tuple[str, str | None, int | None]] = []

            async def answer_precheckout(_method, payload, *_args):
                async with self.Session() as verifier:
                    persisted = await verifier.get(PaymentAttempt, attempt_id)
                    observed_at_answer.append((
                        persisted.status,
                        persisted.telegram_pre_checkout_query_id,
                        persisted.telegram_pre_checkout_user_id,
                    ))
                return {"ok": True, "payload": payload}

            def precheckout(query_id: str) -> dict:
                return {
                    "pre_checkout_query": {
                        "id": query_id,
                        "from": {"id": user_id},
                        "invoice_payload": str(attempt_id),
                        "total_amount": 50,
                        "currency": "XTR",
                    }
                }

            telegram_api = AsyncMock(side_effect=answer_precheckout)
            with patch.object(payments, "call_telegram_api_async", new=telegram_api):
                first = await payments.process_telegram_payment_update(precheckout("query-one"), session)
                replay = await payments.process_telegram_payment_update(precheckout("query-one"), session)
                competing = await payments.process_telegram_payment_update(precheckout("query-two"), session)

            attempt = await session.get(PaymentAttempt, attempt_id)
            self.assertEqual(first["status"], "pre_checkout_answered")
            self.assertEqual(replay["status"], "pre_checkout_answered")
            self.assertEqual(competing["status"], "pre_checkout_rejected")
            self.assertEqual(observed_at_answer[0], ("processing", "query-one", user_id))
            self.assertTrue(telegram_api.await_args_list[0].args[1]["ok"])
            self.assertTrue(telegram_api.await_args_list[1].args[1]["ok"])
            self.assertFalse(telegram_api.await_args_list[2].args[1]["ok"])
            self.assertEqual(attempt.status, "processing")
            self.assertEqual(attempt.telegram_pre_checkout_query_id, "query-one")

    async def test_stars_foreign_precheckout_cannot_reserve_owner_attempt(self):
        async with self.Session() as session:
            owner = self._user()
            foreign_user = self._user(7002)
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([owner, foreign_user, bet])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=owner.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_state="ready",
                checkout_url="https://invoice.invalid/owner",
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                metadata_json={"purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id
            owner_id = owner.telegram_id
            foreign_user_id = foreign_user.telegram_id
            bet_id = bet.id

            telegram_api = AsyncMock(return_value={"ok": True})
            with patch.object(payments, "call_telegram_api_async", new=telegram_api):
                result = await payments.process_telegram_payment_update(
                    {
                        "pre_checkout_query": {
                            "id": "foreign-query",
                            "from": {"id": foreign_user_id},
                            "invoice_payload": str(attempt_id),
                            "total_amount": 50,
                            "currency": "XTR",
                        }
                    },
                    session,
                )

            await session.refresh(attempt)
            self.assertEqual(result["status"], "pre_checkout_rejected")
            self.assertFalse(telegram_api.await_args.args[1]["ok"])
            self.assertEqual(attempt.status, "pending")
            self.assertIsNone(attempt.telegram_pre_checkout_query_id)
            self.assertIsNone(attempt.telegram_pre_checkout_user_id)

    async def test_stars_success_continues_reserved_attempt_and_rejects_foreign_or_second_charge(self):
        async with self.Session() as session:
            owner = self._user()
            foreign_user = self._user(7002)
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([owner, foreign_user, bet])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=owner.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_state="ready",
                checkout_url="https://invoice.invalid/owner",
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                metadata_json={"purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id
            owner_id = owner.telegram_id
            foreign_user_id = foreign_user.telegram_id
            bet_id = bet.id

            with patch.object(
                payments,
                "call_telegram_api_async",
                new=AsyncMock(return_value={"ok": True}),
            ):
                await payments.process_telegram_payment_update(
                    {
                        "pre_checkout_query": {
                            "id": "owner-query",
                            "from": {"id": owner_id},
                            "invoice_payload": str(attempt_id),
                            "total_amount": 50,
                            "currency": "XTR",
                        }
                    },
                    session,
                )

            def successful_payment(sender_id: int, charge_id: str) -> dict:
                return {
                    "message": {
                        "from": {"id": sender_id},
                        "successful_payment": {
                            "invoice_payload": str(attempt_id),
                            "total_amount": 50,
                            "currency": "XTR",
                            "telegram_payment_charge_id": charge_id,
                        },
                    }
                }

            with self.assertRaises(HTTPException) as foreign:
                await payments.process_telegram_payment_update(
                    successful_payment(foreign_user_id, "charge-owner"),
                    session,
                )
            await session.rollback()
            reserved = await session.get(PaymentAttempt, attempt_id)
            self.assertEqual(foreign.exception.status_code, 403)
            self.assertEqual(reserved.status, "processing")

            success = await payments.process_telegram_payment_update(
                successful_payment(owner_id, "charge-owner"),
                session,
            )
            duplicate = await payments.process_telegram_payment_update(
                successful_payment(owner_id, "charge-owner"),
                session,
            )
            with self.assertRaises(HTTPException) as second_charge:
                await payments.process_telegram_payment_update(
                    successful_payment(owner_id, "charge-two"),
                    session,
                )
            await session.rollback()

            access_count = await session.scalar(
                select(func.count()).select_from(user_bets).where(
                    user_bets.c.user_id == owner_id,
                    user_bets.c.bet_id == bet_id,
                )
            )
            persisted = await session.get(PaymentAttempt, attempt_id)
            self.assertEqual(success["status"], "success")
            self.assertEqual(duplicate["status"], "already_processed")
            self.assertEqual(second_charge.exception.status_code, 403)
            self.assertEqual(access_count, 1)
            self.assertEqual(persisted.status, "succeeded")
            self.assertEqual(persisted.provider_payment_id, "charge-owner")

    async def test_fulfillment_uses_immutable_purchase_type_snapshot_and_quarantines_unknown(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.flush()
            hint_attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="tegro",
                amount=Decimal("50.00"),
                currency="RUB",
                checkout_state="ready",
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_BET_HINT,
                metadata_json={"purchase_type": payments.PAYMENT_PURCHASE_SINGLE_BET},
                status="pending",
            )
            unknown_attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="tegro",
                amount=Decimal("50.00"),
                currency="RUB",
                checkout_state="ready",
                purchase_type_snapshot=None,
                metadata_json={"purchase_type": payments.PAYMENT_PURCHASE_SINGLE_BET},
                status="pending",
            )
            session.add_all([hint_attempt, unknown_attempt])
            await session.commit()

            hint_result = await payments._process_payment_attempt(
                session,
                attempt_id=hint_attempt.id,
                provider="tegro",
                provider_payment_id="immutable-hint-charge",
                amount=Decimal("50.00"),
                currency="RUB",
            )
            await session.commit()
            unknown_result = await payments._process_payment_attempt(
                session,
                attempt_id=unknown_attempt.id,
                provider="tegro",
                provider_payment_id="unknown-snapshot-charge",
                amount=Decimal("50.00"),
                currency="RUB",
            )
            await session.commit()

            access_count = await session.scalar(
                select(func.count()).select_from(user_bets).where(
                    user_bets.c.user_id == user.telegram_id,
                    user_bets.c.bet_id == bet.id,
                )
            )
            await session.refresh(unknown_attempt)
            self.assertEqual(hint_result["purchase_type"], payments.PAYMENT_PURCHASE_BET_HINT)
            self.assertTrue(hint_result["hint_ready"])
            self.assertEqual(unknown_result["status"], "purchase_snapshot_missing")
            self.assertEqual(unknown_attempt.status, "processing")
            self.assertEqual(unknown_attempt.checkout_state, "requires_reconciliation")
            self.assertEqual(access_count, 0)

    async def test_yookassa_reuses_same_uuid_intent_and_rejects_changed_payload(self):
        async with self.Session() as session:
            user = self._user()
            first_plan = self._flat_plan()
            second_plan = self._flat_plan(name="Цель +5", target="5.00")
            session.add_all([user, first_plan, second_plan])
            await session.commit()
            intent_id = uuid.uuid4()

            with (
                patch.object(payments.settings, "DEBUG_MODE", True),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
            ):
                first = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=first_plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )
                repeated = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=first_plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )
                with self.assertRaises(HTTPException) as conflict:
                    await payments.create_yookassa_payment(
                        payments.YooKassaPaymentRequest(plan_id=second_plan.id),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )

            attempts = (await session.execute(select(PaymentAttempt))).scalars().all()
            self.assertEqual(first["attempt_id"], repeated["attempt_id"])
            self.assertEqual(first["confirmation_url"], repeated["confirmation_url"])
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].checkout_intent_id, intent_id)
            self.assertEqual(attempts[0].checkout_state, "ready")
            self.assertEqual(attempts[0].plan_name_snapshot, "Цель +3")
            self.assertEqual(attempts[0].entitlement_type_snapshot, "flat")
            self.assertEqual(attempts[0].target_flats_snapshot, Decimal("3.00"))
            self.assertEqual(attempts[0].match_count_snapshot, 1)
            self.assertEqual(conflict.exception.status_code, 409)

    async def test_ready_checkout_replays_after_live_plan_is_archived(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._flat_plan()
            session.add_all([user, plan])
            await session.commit()

            for create_checkout, request_type, url_field in (
                (payments.create_yookassa_payment, payments.YooKassaPaymentRequest, "confirmation_url"),
                (payments.create_tegro_payment, payments.TegroPaymentRequest, "confirmation_url"),
            ):
                intent_id = uuid.uuid4()
                with (
                    patch.object(payments.settings, "DEBUG_MODE", True),
                    patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
                ):
                    created = await create_checkout(
                        request_type(plan_id=plan.id),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )

                plan.is_active = False
                await session.commit()
                forbidden_live_gate = AsyncMock(side_effect=AssertionError("live plan gate must not run"))
                with (
                    patch.object(payments.settings, "DEBUG_MODE", True),
                    patch.object(payments, "_ensure_subscription_purchases_enabled", new=forbidden_live_gate),
                ):
                    replay = await create_checkout(
                        request_type(plan_id=plan.id),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )

                self.assertEqual(replay["attempt_id"], created["attempt_id"])
                self.assertEqual(replay[url_field], created[url_field])
                self.assertTrue(replay["idempotent_replay"])
                forbidden_live_gate.assert_not_awaited()
                plan.is_active = True
                await session.commit()

    async def test_hidden_allowlisted_flat_checkout_bypasses_disabled_public_sales(self):
        async with self.Session() as session:
            allowed = self._user()
            denied = self._user(7002)
            plan = self._flat_plan(name="Скрытая проверка", target="1.00")
            plan.is_hidden = True
            plan.allowed_checkout_users = [allowed]
            session.add_all([allowed, denied, plan])
            await session.commit()

            with patch.object(payments.settings, "DEBUG_MODE", True):
                checkout = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=plan.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=allowed,
                    db=session,
                )
                with self.assertRaises(HTTPException) as hidden:
                    await payments.create_yookassa_payment(
                        payments.YooKassaPaymentRequest(plan_id=plan.id),
                        idempotency_key=uuid.uuid4(),
                        current_user=denied,
                        db=session,
                    )

            attempt = await session.get(PaymentAttempt, uuid.UUID(checkout["attempt_id"]))
            self.assertEqual(attempt.amount, Decimal("1490.00"))
            self.assertEqual(hidden.exception.status_code, 404)

    async def test_ab_effective_rub_price_matches_list_yookassa_and_tegro_snapshots(self):
        async with self.Session() as session:
            user = self._user()
            user.ab_group = "B"
            plan = self._flat_plan()
            session.add_all([user, plan])
            await session.flush()
            session.add(
                ABTestConfig(
                    plan_id=plan.id,
                    price_group_a=100,
                    price_group_b=125,
                    is_active=True,
                )
            )
            await session.commit()

            listed = await subscriptions.list_plans(
                include_inactive=False,
                db=session,
                current_user=user,
            )
            listed_plan = next(item for item in listed if item.id == plan.id)

            with (
                patch.object(payments.settings, "DEBUG_MODE", True),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
            ):
                yookassa = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=plan.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                tegro = await payments.create_tegro_payment(
                    payments.TegroPaymentRequest(plan_id=plan.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = {
                attempt.id: attempt
                for attempt in (await session.execute(select(PaymentAttempt))).scalars().all()
            }
            yookassa_attempt = attempts[uuid.UUID(yookassa["attempt_id"])]
            tegro_attempt = attempts[uuid.UUID(tegro["attempt_id"])]
            self.assertEqual(listed_plan.price, Decimal("1250.00"))
            self.assertEqual(listed_plan.price_stars, 125)
            self.assertEqual(yookassa_attempt.amount, Decimal("1250.00"))
            self.assertEqual(tegro_attempt.amount, Decimal("1250.00"))
            self.assertEqual(yookassa_attempt.metadata_json["ab_group"], "B")
            self.assertEqual(tegro_attempt.metadata_json["effective_amount_rub"], "1250.00")
            self.assertEqual(plan.price, Decimal("1490.00"))

    async def test_yookassa_timeout_is_quarantined_without_second_provider_request(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._flat_plan()
            session.add_all([user, plan])
            await session.commit()
            intent_id = uuid.uuid4()
            provider_keys = []

            def provider_request(request, *, timeout):
                self.assertEqual(timeout, 15)
                provider_keys.append(request.get_header("Idempotence-key"))
                if len(provider_keys) == 1:
                    raise TimeoutError("response lost after provider acceptance")
                return _FakeProviderResponse(
                    {
                        "id": "same-provider-payment",
                        "status": "pending",
                        "confirmation": {"confirmation_url": "https://pay.invalid/recovered"},
                    }
                )

            with (
                patch.object(payments.settings, "DEBUG_MODE", False),
                patch.object(payments.settings, "YOOKASSA_SHOP_ID", "test-shop"),
                patch.object(payments.settings, "YOOKASSA_SECRET_KEY", "test-secret"),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
                patch.object(payments, "_open_payment_provider_request", side_effect=provider_request),
            ):
                with self.assertRaises(HTTPException) as first_error:
                    await payments.create_yookassa_payment(
                        payments.YooKassaPaymentRequest(plan_id=plan.id),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )

                first_attempt = (await session.execute(select(PaymentAttempt))).scalars().one()
                first_attempt_id = first_attempt.id
                self.assertEqual(first_error.exception.status_code, 502)
                self.assertEqual(first_attempt.status, "pending")
                self.assertEqual(first_attempt.checkout_state, "requires_reconciliation")

                replay = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(uuid.UUID(replay["attempt_id"]), first_attempt_id)
            self.assertIsNone(replay["confirmation_url"])
            self.assertEqual(replay["checkout_state"], "requires_reconciliation")
            self.assertEqual(provider_keys, [str(first_attempt_id)])

    async def test_tegro_and_stars_reuse_checkout_urls_for_same_intent(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._flat_plan()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, plan, bet])
            await session.commit()

            tegro_intent = uuid.uuid4()
            with (
                patch.object(payments.settings, "DEBUG_MODE", True),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
            ):
                first_tegro = await payments.create_tegro_payment(
                    payments.TegroPaymentRequest(plan_id=plan.id),
                    idempotency_key=tegro_intent,
                    current_user=user,
                    db=session,
                )
                second_tegro = await payments.create_tegro_payment(
                    payments.TegroPaymentRequest(plan_id=plan.id),
                    idempotency_key=tegro_intent,
                    current_user=user,
                    db=session,
                )

            stars_intent = uuid.uuid4()
            invoice_link = AsyncMock(return_value="https://invoice.invalid/idempotent")
            with patch.object(payments, "create_telegram_stars_invoice_link", new=invoice_link):
                first_stars = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=stars_intent,
                    current_user=user,
                    db=session,
                )
                second_stars = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=stars_intent,
                    current_user=user,
                    db=session,
                )

            self.assertEqual(first_tegro["attempt_id"], second_tegro["attempt_id"])
            self.assertEqual(first_tegro["confirmation_url"], second_tegro["confirmation_url"])
            self.assertEqual(first_stars["attempt_id"], second_stars["attempt_id"])
            self.assertEqual(first_stars["invoice_url"], second_stars["invoice_url"])
            self.assertEqual(invoice_link.await_count, 1)

    async def test_stars_new_client_key_recovers_owner_active_attempt_by_canonical_params(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()

            invoice_link = AsyncMock(return_value="https://invoice.invalid/owner-recovery")
            with patch.object(payments, "create_telegram_stars_invoice_link", new=invoice_link):
                first = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                recovered = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(recovered["attempt_id"], first["attempt_id"])
            self.assertEqual(recovered["invoice_url"], first["invoice_url"])
            self.assertTrue(recovered["idempotent_replay"])
            invoice_link.assert_awaited_once()

    async def test_stars_new_client_key_rejects_changed_canonical_params(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()

            with patch.object(
                payments,
                "create_telegram_stars_invoice_link",
                new=AsyncMock(return_value="https://invoice.invalid/original"),
            ):
                await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                with self.assertRaises(HTTPException) as conflict:
                    await payments.create_stars_invoice(
                        payments.InvoiceRequest(bet_id=bet.id, promo_code="OTHER"),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )

            self.assertEqual(conflict.exception.status_code, 409)

    async def test_stars_multiple_owner_active_attempts_fail_closed(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.flush()
            payload_hash = payments._checkout_request_hash(
                provider="telegram_stars",
                user_id=user.telegram_id,
                bet_id=bet.id,
            )
            for _ in range(2):
                session.add(PaymentAttempt(
                    user_id=user.telegram_id,
                    bet_id=bet.id,
                    provider="telegram_stars",
                    amount=Decimal("50.00"),
                    currency="XTR",
                    checkout_intent_id=uuid.uuid4(),
                    checkout_payload_hash=payload_hash,
                    checkout_state="requires_reconciliation",
                    purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                    discount_percent_snapshot=0,
                    metadata_json={"discount_percent": 0, "purchase_type": "single_bet"},
                    status="pending",
                ))
            await session.commit()

            with self.assertRaises(HTTPException) as conflict:
                await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(conflict.exception.status_code, 409)
            self.assertEqual(len(attempts), 2)
            self.assertTrue(all(item.checkout_state == "requires_reconciliation" for item in attempts))

    def test_stars_checkout_lock_order_is_attempt_scope_before_bet(self):
        source = inspect.getsource(payments.create_stars_invoice)
        scope_lock = source.index("await _lock_telegram_purchase_scope")
        attempt_lock = source.index("await _load_checkout_attempt")
        bet_lock = source.index("await load_locked_bet_for_user_access")
        self.assertLess(scope_lock, attempt_lock)
        self.assertLess(attempt_lock, bet_lock)

    async def test_stars_quarantines_survived_creating_attempt_without_provider_retry(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()
            intent_id = uuid.uuid4()
            payload_hash = payments._checkout_request_hash(
                provider="telegram_stars",
                user_id=user.telegram_id,
                bet_id=bet.id,
            )
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_intent_id=intent_id,
                checkout_payload_hash=payload_hash,
                checkout_state="creating",
                checkout_url=None,
                checkout_creation_started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                discount_percent_snapshot=0,
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                metadata_json={"discount_percent": 0, "purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()

            invoice_link = AsyncMock(return_value="https://invoice.invalid/must-not-be-created")
            with patch.object(payments, "create_telegram_stars_invoice_link", new=invoice_link):
                quarantined = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )
                replayed = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(quarantined["attempt_id"], str(attempt.id))
            self.assertEqual(replayed["attempt_id"], str(attempt.id))
            self.assertIsNone(quarantined["invoice_url"])
            self.assertEqual(quarantined["checkout_state"], "requires_reconciliation")
            self.assertEqual(replayed["checkout_state"], "requires_reconciliation")
            invoice_link.assert_not_awaited()

    async def test_stars_fresh_creating_claim_returns_425_without_provider_retry(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()
            intent_id = uuid.uuid4()
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_intent_id=intent_id,
                checkout_payload_hash=payments._checkout_request_hash(
                    provider="telegram_stars",
                    user_id=user.telegram_id,
                    bet_id=bet.id,
                ),
                checkout_state="creating",
                checkout_creation_started_at=datetime.now(timezone.utc),
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                discount_percent_snapshot=0,
                metadata_json={"discount_percent": 0, "purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()

            invoice_link = AsyncMock(return_value="https://invoice.invalid/must-not-be-created")
            with (
                patch.object(payments, "create_telegram_stars_invoice_link", new=invoice_link),
                self.assertRaises(HTTPException) as too_early,
            ):
                await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            await session.refresh(attempt)
            self.assertEqual(too_early.exception.status_code, 425)
            self.assertEqual(too_early.exception.headers.get("Retry-After"), "3")
            self.assertEqual(attempt.checkout_state, "creating")
            invoice_link.assert_not_awaited()

    async def test_stars_ambiguous_creation_is_quarantined_and_new_key_replays_it(self):
        async with self.Session() as session:
            user = self._user()
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()
            first_intent = uuid.uuid4()

            create_link = AsyncMock(side_effect=TimeoutError("response lost"))
            with patch.object(payments, "create_telegram_stars_invoice_link", new=create_link):
                with self.assertRaises(HTTPException) as ambiguous:
                    await payments.create_stars_invoice(
                        payments.InvoiceRequest(bet_id=bet.id),
                        idempotency_key=first_intent,
                        current_user=user,
                        db=session,
                    )
                replay = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=first_intent,
                    current_user=user,
                    db=session,
                )
                recovered_with_new_key = await payments.create_stars_invoice(
                    payments.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempt = (await session.execute(select(PaymentAttempt))).scalars().one()
            self.assertEqual(ambiguous.exception.status_code, 502)
            self.assertEqual(attempt.status, "pending")
            self.assertEqual(attempt.checkout_state, "requires_reconciliation")
            self.assertEqual(replay["checkout_state"], "requires_reconciliation")
            self.assertIsNone(replay["invoice_url"])
            self.assertEqual(recovered_with_new_key["attempt_id"], str(attempt.id))
            self.assertEqual(recovered_with_new_key["checkout_state"], "requires_reconciliation")
            self.assertIsNone(recovered_with_new_key["invoice_url"])
            create_link.assert_awaited_once()

    async def test_tegro_recovers_creating_attempt_from_immutable_snapshot(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._flat_plan()
            session.add_all([user, plan])
            await session.commit()
            intent_id = uuid.uuid4()
            payload_hash = payments._checkout_request_hash(
                provider="tegro",
                user_id=user.telegram_id,
                plan_id=plan.id,
            )
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                plan_id=plan.id,
                provider="tegro",
                amount=Decimal("1490.00"),
                currency="RUB",
                checkout_intent_id=intent_id,
                checkout_payload_hash=payload_hash,
                checkout_state="creating",
                checkout_url=None,
                plan_name_snapshot="Цель +3",
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=1,
                discount_percent_snapshot=0,
                metadata_json={"discount_percent": 0},
                status="pending",
            )
            session.add(attempt)
            await session.commit()

            plan.name = "Архивный изменённый тариф"
            plan.target_flats = Decimal("99.00")
            plan.is_active = False
            await session.commit()

            payment_url = "https://tegro.invalid/recovered"
            with (
                patch.object(payments.settings, "DEBUG_MODE", False),
                patch.object(payments.settings, "TEGRO_SHOP_ID", "test-shop"),
                patch.object(payments.settings, "TEGRO_SECRET_KEY", "test-secret"),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
                patch.object(payments, "_create_tegro_payment_url", return_value=payment_url) as create_url,
            ):
                recovered = await payments.create_tegro_payment(
                    payments.TegroPaymentRequest(plan_id=plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )
                replayed = await payments.create_tegro_payment(
                    payments.TegroPaymentRequest(plan_id=plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(recovered["attempt_id"], str(attempt.id))
            self.assertEqual(replayed["attempt_id"], str(attempt.id))
            self.assertEqual(recovered["confirmation_url"], payment_url)
            self.assertEqual(replayed["confirmation_url"], payment_url)
            create_url.assert_called_once()
            payload = create_url.call_args.args[0]
            self.assertEqual(payload["order_id"], str(attempt.id))
            self.assertEqual(payload["amount"], "1490.00")
            self.assertIn("Цель +3", payload["receipt"]["items"][0]["name"])
            self.assertIn("+3.00", payload["receipt"]["items"][0]["name"])

    async def test_webhook_entitlement_uses_purchase_snapshot_after_plan_edit(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._flat_plan()
            session.add_all([user, plan])
            await session.commit()

            intent_id = uuid.uuid4()
            with (
                patch.object(payments.settings, "DEBUG_MODE", True),
                patch.object(payments, "_ensure_subscription_purchases_enabled", new=AsyncMock()),
            ):
                checkout = await payments.create_yookassa_payment(
                    payments.YooKassaPaymentRequest(plan_id=plan.id),
                    idempotency_key=intent_id,
                    current_user=user,
                    db=session,
                )

            plan.name = "Изменённый тариф"
            plan.entitlement_type = "legacy_match"
            plan.target_flats = None
            plan.match_count = 99
            await session.commit()

            result = await payments._process_payment_attempt(
                session,
                attempt_id=uuid.UUID(checkout["attempt_id"]),
                provider="yookassa_debug",
                provider_payment_id="immutable-snapshot-payment",
                amount=Decimal("1490.00"),
                currency="RUB",
                raw_payload={"status": "succeeded"},
            )
            await session.commit()

            subscription = (await session.execute(select(Subscription))).scalars().one()
            credit = (await session.execute(select(FlatSubscriptionCredit))).scalars().one()
            self.assertEqual(result["target_flats_added"], "3.00")
            self.assertEqual(subscription.target_flats_snapshot, Decimal("3.00"))
            self.assertEqual(credit.delta_target_flats, Decimal("3.00"))

    async def test_attempt_status_is_owner_only_and_returns_flat_progress(self):
        async with self.Session() as session:
            owner = self._user()
            stranger = self._user(7002)
            plan = self._flat_plan()
            session.add_all([owner, stranger, plan])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=owner.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("1490.00"),
                currency="RUB",
                status="pending",
                checkout_state="ready",
                checkout_url="https://checkout.invalid/owner",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=1,
                discount_percent_snapshot=0,
            )
            progress = FlatSubscription(
                user_id=owner.telegram_id,
                status="pending_setup",
                target_flats=Decimal("3.00"),
                profit_rub=Decimal("0.00"),
                profit_flats=Decimal("0.000000"),
            )
            session.add_all([attempt, progress])
            await session.commit()

            response = await payments.get_payment_attempt_status(
                attempt.id,
                current_user=owner,
                db=session,
            )
            with self.assertRaises(HTTPException) as hidden:
                await payments.get_payment_attempt_status(
                    attempt.id,
                    current_user=stranger,
                    db=session,
                )

            self.assertEqual(response.attempt_id, attempt.id)
            self.assertTrue(response.flat_setup_required)
            self.assertEqual(response.flat_subscription.target_flats, Decimal("3.00"))
            self.assertEqual(hidden.exception.status_code, 404)

    async def test_attempt_status_does_not_require_setup_when_flat_amount_is_already_configured(self):
        async with self.Session() as session:
            owner = self._user()
            plan = self._flat_plan()
            session.add_all([owner, plan])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=owner.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("1490.00"),
                currency="RUB",
                status="succeeded",
                checkout_state="ready",
                checkout_url="https://checkout.invalid/owner",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=1,
                discount_percent_snapshot=0,
            )
            progress = FlatSubscription(
                user_id=owner.telegram_id,
                status="pending_setup",
                flat_amount_rub=Decimal("10000.00"),
                target_flats=Decimal("3.00"),
                profit_rub=Decimal("0.00"),
                profit_flats=Decimal("0.000000"),
            )
            session.add_all([attempt, progress])
            await session.commit()

            response = await payments.get_payment_attempt_status(
                attempt.id,
                current_user=owner,
                db=session,
            )

            self.assertFalse(response.flat_setup_required)
            self.assertEqual(response.flat_subscription.status, "pending_setup")
            self.assertEqual(response.flat_subscription.flat_amount_rub, Decimal("10000.00"))

    async def test_payment_result_does_not_require_setup_while_configured_flat_waits_for_legacy(self):
        async with self.Session() as session:
            owner = self._user()
            owner.purchased_bets_balance = 1
            owner.matches_remaining = 1
            plan = self._flat_plan()
            session.add_all([owner, plan])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=owner.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("1490.00"),
                currency="RUB",
                status="pending",
                checkout_state="ready",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=1,
                discount_percent_snapshot=0,
            )
            progress = FlatSubscription(
                user_id=owner.telegram_id,
                status="pending_setup",
                flat_amount_rub=Decimal("10000.00"),
                target_flats=Decimal("1.00"),
                profit_rub=Decimal("0.00"),
                profit_flats=Decimal("0.000000"),
            )
            session.add_all([attempt, progress])
            await session.commit()

            result = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="yookassa",
                provider_payment_id="configured-legacy-wait",
                amount=Decimal("1490.00"),
                currency="RUB",
                raw_payload={"status": "succeeded"},
            )

            self.assertEqual(result["status"], "success")
            self.assertFalse(result["flat_setup_required"])
            await session.refresh(progress)
            self.assertEqual(progress.status, "pending_setup")
            self.assertEqual(progress.flat_amount_rub, Decimal("10000.00"))

    async def test_client_can_configure_flat_amount_only_once(self):
        async with self.Session() as session:
            user = self._user()
            flat_subscription = FlatSubscription(
                user_id=user.telegram_id,
                status="pending_setup",
                target_flats=Decimal("3.00"),
                profit_rub=Decimal("0.00"),
                profit_flats=Decimal("0.000000"),
            )
            session.add_all([user, flat_subscription])
            await session.commit()

            await subscriptions.configure_current_flat_subscription(
                FlatSubscriptionConfigureRequest(
                    flat_amount_rub=Decimal("10000.00"),
                    expected_revision=1,
                ),
                current_user=user,
                db=session,
            )
            with self.assertRaises(HTTPException) as repeated:
                await subscriptions.configure_current_flat_subscription(
                    FlatSubscriptionConfigureRequest(
                        flat_amount_rub=Decimal("1.00"),
                        expected_revision=2,
                    ),
                    current_user=user,
                    db=session,
                )

            await session.refresh(flat_subscription)
            self.assertEqual(repeated.exception.status_code, 409)
            self.assertEqual(flat_subscription.flat_amount_rub, Decimal("10000.00"))


if __name__ == "__main__":
    unittest.main()
