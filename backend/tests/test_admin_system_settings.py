import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import admin as admin_api, auth as auth_api
from src.main import is_maintenance_exempt_path
from src.models.database import Base
from src.models.models import SystemSetting
from src.services import presence, system_settings


class MaintenanceModeRoutingTests(unittest.TestCase):
    def test_maintenance_mode_keeps_service_routes_open_and_blocks_user_api(self):
        self.assertTrue(is_maintenance_exempt_path("/api/admin/settings"))
        self.assertTrue(is_maintenance_exempt_path("/api/health"))
        self.assertTrue(is_maintenance_exempt_path("/api/payments/yookassa/webhook"))
        self.assertTrue(is_maintenance_exempt_path("/api/payments/tegro/webhook"))
        self.assertTrue(is_maintenance_exempt_path("/api/payments/telegram-webhook"))
        self.assertTrue(is_maintenance_exempt_path("/api/settings/theme"))
        self.assertTrue(is_maintenance_exempt_path("/api/vk/callback"))
        self.assertFalse(is_maintenance_exempt_path("/api/bets"))
        self.assertFalse(is_maintenance_exempt_path("/api/payments/invoice"))


class AdminSystemSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_secret_values_are_masked_in_admin_response(self):
        async with self.Session() as session:
            session.add(SystemSetting(key="TELEGRAM_BOT_TOKEN", value="real-token"))
            await session.flush()

            with (
                patch.object(system_settings, "cache_get_json", new=AsyncMock(return_value=None)),
                patch.object(system_settings, "cache_set_json", new=AsyncMock()) as cache_set,
            ):
                payload = await system_settings.get_admin_system_settings(session)

        token_setting = next(item for item in payload["settings"] if item["key"] == "TELEGRAM_BOT_TOKEN")
        self.assertEqual(token_setting["value"], "")
        self.assertTrue(token_setting["is_secret"])
        self.assertTrue(token_setting["is_configured"])
        cache_set.assert_awaited_once()
        cached_payload = cache_set.await_args.args[1]
        cached_token = next(item for item in cached_payload["settings"] if item["key"] == "TELEGRAM_BOT_TOKEN")
        self.assertEqual(cached_token["value"], "")

    async def test_update_persists_settings_and_invalidates_cache(self):
        async with self.Session() as session:
            with patch.object(system_settings, "cache_delete", new=AsyncMock()) as cache_delete:
                payload = await system_settings.update_admin_system_settings(
                    session,
                    [
                        {"key": "MAINTENANCE_MODE", "value": "true"},
                        {"key": "SUPPORT_URL", "value": " https://support.example.com "},
                    ],
                )
                await session.flush()

            cache_delete.assert_awaited_once_with(system_settings.SYSTEM_SETTINGS_CACHE_KEY)
            settings_by_key = {item["key"]: item for item in payload["settings"]}
            self.assertEqual(settings_by_key["MAINTENANCE_MODE"]["value"], "true")
            self.assertEqual(settings_by_key["SUPPORT_URL"]["value"], "https://support.example.com")

            result = await session.execute(select(SystemSetting).where(SystemSetting.key == "SUPPORT_URL"))
            self.assertEqual(result.scalar_one().value, "https://support.example.com")

    async def test_empty_secret_update_does_not_overwrite_existing_token(self):
        async with self.Session() as session:
            session.add(SystemSetting(key="VK_ACCESS_TOKEN", value="existing-token"))
            await session.flush()

            with patch.object(system_settings, "cache_delete", new=AsyncMock()):
                await system_settings.update_admin_system_settings(
                    session,
                    [{"key": "VK_ACCESS_TOKEN", "value": ""}],
                )
                await session.flush()

            result = await session.execute(select(SystemSetting).where(SystemSetting.key == "VK_ACCESS_TOKEN"))
            self.assertEqual(result.scalar_one().value, "existing-token")

    async def test_empty_secret_update_does_not_create_blank_runtime_override(self):
        async with self.Session() as session:
            with patch.object(system_settings, "cache_delete", new=AsyncMock()):
                await system_settings.update_admin_system_settings(
                    session,
                    [{"key": "TELEGRAM_BOT_TOKEN", "value": ""}],
                )
                await session.flush()

            result = await session.execute(select(SystemSetting).where(SystemSetting.key == "TELEGRAM_BOT_TOKEN"))
            self.assertIsNone(result.scalar_one_or_none())

    async def test_unlocked_integrations_reveal_runtime_and_stored_secret_values(self):
        async with self.Session() as session:
            session.add(SystemSetting(key="YOOKASSA_SECRET_KEY", value="stored-yookassa-secret", is_secret=True))
            await session.flush()

            with patch.object(system_settings.settings, "TELEGRAM_BOT_TOKEN", "runtime-telegram-token"):
                payload = await system_settings.get_unlocked_integration_settings(session)

        settings_by_key = {item["key"]: item for item in payload["settings"]}
        self.assertEqual(settings_by_key["TELEGRAM_BOT_TOKEN"]["value"], "runtime-telegram-token")
        self.assertEqual(settings_by_key["YOOKASSA_SECRET_KEY"]["value"], "stored-yookassa-secret")
        self.assertTrue(settings_by_key["TELEGRAM_BOT_TOKEN"]["is_secret"])

    async def test_integrations_password_uses_runtime_setting(self):
        with patch.object(system_settings.settings, "ADMIN_INTEGRATIONS_PASSWORD", "test-pin"):
            self.assertTrue(system_settings.verify_integrations_password("test-pin"))
            self.assertFalse(system_settings.verify_integrations_password("wrong"))

        with patch.object(system_settings.settings, "ADMIN_INTEGRATIONS_PASSWORD", ""):
            self.assertFalse(system_settings.verify_integrations_password("test-pin"))

    async def test_reset_user_session_cache_deletes_only_auth_session_prefixes(self):
        with patch.object(system_settings, "cache_delete_pattern", new=AsyncMock(side_effect=[2, 3])) as delete_pattern:
            payload = await system_settings.reset_user_session_cache()

        self.assertEqual(payload, {"status": "success", "deleted": 5})
        self.assertEqual(
            [call.args[0] for call in delete_pattern.await_args_list],
            [
                "telegram_auth_session:v1:*",
                "vk_auth_flow:v1:*",
            ],
        )

    async def test_theme_hex_settings_are_validated(self):
        async with self.Session() as session:
            with patch.object(system_settings, "cache_delete", new=AsyncMock()):
                payload = await system_settings.update_admin_system_settings(
                    session,
                    [{"key": "THEME_PRIMARY_COLOR", "value": "#ABCDEF"}],
                )

            settings_by_key = {item["key"]: item for item in payload["settings"]}
            self.assertEqual(settings_by_key["THEME_PRIMARY_COLOR"]["value"], "#abcdef")

            with self.assertRaises(ValueError):
                await system_settings.update_admin_system_settings(
                    session,
                    [{"key": "THEME_PRIMARY_COLOR", "value": "cyan"}],
                )

    async def test_brand_kit_settings_are_validated_and_public(self):
        async with self.Session() as session:
            with patch.object(system_settings, "cache_delete", new=AsyncMock()):
                await system_settings.update_admin_system_settings(
                    session,
                    [
                        {"key": "BRAND_LOGO_URL", "value": " https://cdn.example.com/logo.png "},
                        {"key": "BRAND_BACKGROUND_URL", "value": "https://cdn.example.com/bg.webp"},
                        {"key": "THEME_GLASS_OPACITY", "value": "0.58"},
                        {"key": "THEME_GLASS_BLUR_PX", "value": "22"},
                        {"key": "THEME_RADIUS_SCALE", "value": "1.15"},
                        {"key": "THEME_FONT_SCALE", "value": "0.96"},
                        {"key": "THEME_DENSITY", "value": "compact"},
                        {"key": "THEME_GLOW_STRENGTH", "value": "1.2"},
                    ],
                )

                with self.assertRaises(ValueError):
                    await system_settings.update_admin_system_settings(
                        session,
                        [{"key": "THEME_DENSITY", "value": "huge"}],
                    )
                with self.assertRaises(ValueError):
                    await system_settings.update_admin_system_settings(
                        session,
                        [{"key": "BRAND_LOGO_URL", "value": "javascript:alert(1)"}],
                    )

            payload = await system_settings.get_public_theme_settings(session)

        self.assertEqual(payload["brand_logo_url"], "https://cdn.example.com/logo.png")
        self.assertEqual(payload["brand_background_url"], "https://cdn.example.com/bg.webp")
        self.assertEqual(payload["theme_density"], "compact")
        self.assertEqual(payload["glass_blur_px"], 22)
        self.assertAlmostEqual(payload["glass_opacity"], 0.58)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", payload)

    async def test_public_theme_payload_is_sanitized(self):
        async with self.Session() as session:
            session.add_all([
                SystemSetting(key="THEME_PRIMARY_COLOR", value="#112233"),
                SystemSetting(key="THEME_SECONDARY_COLOR", value="#445566"),
                SystemSetting(key="GLOBAL_PERFORMANCE_MODE", value="true"),
                SystemSetting(key="TELEGRAM_BOT_TOKEN", value="real-token", is_secret=True),
            ])
            await session.flush()

            payload = await system_settings.get_public_theme_settings(session)

        self.assertEqual(payload["primary_color"], "#112233")
        self.assertEqual(payload["secondary_color"], "#445566")
        self.assertTrue(payload["global_performance_mode"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", payload)

    async def test_unlock_token_is_cached_without_exposing_secret_values(self):
        with patch.object(system_settings, "cache_set_json", new=AsyncMock()) as cache_set:
            payload = await system_settings.create_integration_unlock_token(admin_id=900)

        self.assertEqual(payload["token_type"], "integration_unlock")
        self.assertTrue(payload["unlock_token"])
        self.assertTrue(payload["expires_at"])
        cache_set.assert_awaited_once()
        cache_key = cache_set.await_args.args[0]
        cached_payload = cache_set.await_args.args[1]
        self.assertNotIn(payload["unlock_token"], cache_key)
        self.assertNotIn(payload["unlock_token"], str(cached_payload))

    async def test_integration_diagnostics_require_unlock_token_and_redact_values(self):
        async with self.Session() as session:
            session.add(SystemSetting(key="TELEGRAM_BOT_TOKEN", value="123456:real-secret", is_secret=True))
            await session.flush()

            with patch.object(system_settings, "validate_integration_unlock_token", new=AsyncMock(return_value=False)):
                with self.assertRaises(ValueError):
                    await system_settings.run_integration_diagnostics(session, unlock_token="bad", group="telegram")

            with patch.object(system_settings, "validate_integration_unlock_token", new=AsyncMock(return_value=True)):
                payload = await system_settings.run_integration_diagnostics(
                    session,
                    unlock_token="valid-token",
                    group="telegram",
                )

        self.assertEqual(payload["overall_status"], "ok")
        self.assertEqual(payload["groups"][0]["group"], "telegram")
        self.assertNotIn("real-secret", str(payload))

    async def test_registration_disabled_blocks_new_telegram_users(self):
        async with self.Session() as session:
            session.add(SystemSetting(key="DISABLE_REGISTRATIONS", value="true"))
            await session.flush()

            with self.assertRaises(HTTPException) as ctx:
                await auth_api._upsert_telegram_user(
                    session,
                    {"id": 424242, "username": "new_user"},
                    "Telegram",
                )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_presence_online_count_scans_redis_keys(self):
        class FakeRedis:
            async def scan_iter(self, match: str, count: int):
                self.match = match
                self.count = count
                for key in ["presence:user:1", "presence:user:2", "presence:user:3"]:
                    yield key

        fake_redis = FakeRedis()
        with patch.object(presence, "get_redis_client", return_value=fake_redis):
            online_count = await presence.count_online_users()

        self.assertEqual(online_count, 3)
        self.assertEqual(fake_redis.match, "presence:user:*")
        self.assertEqual(fake_redis.count, 200)

    async def test_admin_monitoring_online_endpoint_uses_presence_counter(self):
        with patch.object(admin_api, "count_online_users", new=AsyncMock(return_value=7)):
            payload = await admin_api.admin_monitoring_online(admin=object())

        self.assertEqual(payload, {"online_users": 7})

    async def test_admin_monitoring_parser_status_endpoint_shape(self):
        payload = await admin_api.admin_monitoring_parser_status(admin=object())

        self.assertEqual(payload["status"], "active")
        self.assertIsNotNone(payload["last_sync"].tzinfo)

    async def test_monitoring_summary_combines_safe_operational_metrics(self):
        async with self.Session() as session:
            with (
                patch.object(admin_api, "count_online_users", new=AsyncMock(return_value=5)),
                patch.object(admin_api, "get_delivery_outbox_metrics", new=AsyncMock(return_value={"queue_depth": 2})),
                patch.object(admin_api, "get_security_rate_limit_metrics", return_value={"tracked_keys": 3}),
                patch.object(
                    admin_api,
                    "build_observability_alert_payload",
                    return_value={"overall_status": "ok", "alerts": []},
                ),
            ):
                payload = await admin_api.admin_monitoring_summary(admin=object(), db=session)

        self.assertEqual(payload["online"]["online_users"], 5)
        self.assertEqual(payload["delivery_outbox"]["queue_depth"], 2)
        self.assertEqual(payload["rate_limit"]["tracked_keys"], 3)
        self.assertEqual(payload["alerts"]["overall_status"], "ok")
        self.assertEqual(payload["payment_reconciliation"]["total_issues"], 0)
        self.assertFalse(payload["payment_reconciliation"]["provider_checks_included"])
        self.assertEqual(payload["health"]["database"], "ok")

    async def test_monitoring_logs_and_report_are_sanitized(self):
        with TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "backend.log"
            log_path.write_text(
                "2026-06-24 error token=123456:SECRET database_url=postgresql://user:pass@db/app\n",
                encoding="utf-8",
            )
            with patch.object(admin_api, "_monitoring_log_candidates", return_value=[log_path]):
                logs_payload = await admin_api.admin_monitoring_logs(admin=object())

            with (
                patch.object(admin_api, "admin_monitoring_summary", new=AsyncMock(return_value={"health": {"database": "ok"}})),
                patch.object(admin_api, "admin_monitoring_logs", new=AsyncMock(return_value=logs_payload)),
            ):
                report = await admin_api.admin_monitoring_diagnostic_report(
                    format="txt",
                    admin=object(),
                    db=object(),
                )

        self.assertNotIn("SECRET", str(logs_payload))
        self.assertNotIn("postgresql://user:pass", str(logs_payload))
        self.assertEqual(report.media_type, "text/plain")
        self.assertNotIn("SECRET", report.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
