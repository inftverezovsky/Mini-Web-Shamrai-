import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import telegram_webhook
from src.core.telegram_emoji_catalog import (
    EmojiCatalogSnapshot,
    clear_emoji_catalog,
    current_emoji_catalog,
    emoji_catalog_is_loaded,
    replace_emoji_catalog,
)
from src.core.telegram_text import (
    decor_custom_emoji,
    decorate_forecast_html,
    sport_custom_emoji,
)
from src.models.models import SystemSetting
from src.services.telegram_emoji_catalog import (
    EmojiAssignment,
    extract_emoji_assignments,
    load_telegram_emoji_catalog,
    parse_emoji_slot,
    save_emoji_assignments,
)


def _utf16_offset(text: str, fragment: str) -> int:
    return len(text[: text.index(fragment)].encode("utf-16-le")) // 2


class TelegramEmojiAssignmentTests(unittest.TestCase):
    def test_parse_emoji_slot_supports_admin_friendly_aliases(self):
        self.assertEqual(parse_emoji_slot("write"), ("write", None))
        self.assertEqual(parse_emoji_slot("шамрай"), ("shamrai", None))
        self.assertEqual(parse_emoji_slot("бк:Winline"), ("bookmaker", "winline"))
        self.assertEqual(parse_emoji_slot("спорт:Футбол"), ("sport", "футбол"))
        self.assertEqual(
            parse_emoji_slot("спорт:Настольный   теннис"),
            ("sport", "настольный теннис"),
        )
        self.assertEqual(parse_emoji_slot("decor:Forecast"), ("decor", "forecast"))

    def test_bulk_command_extracts_custom_emoji_ids_by_line_label(self):
        text = (
            "/emoji_add\n"
            "bk:winline 🙂\n"
            "sport:football ⚽\n"
            "decor:forecast 🔒\n"
            "write ✍️"
        )
        entities = [
            {
                "type": "custom_emoji",
                "offset": _utf16_offset(text, emoji),
                "length": len(emoji.encode("utf-16-le")) // 2,
                "custom_emoji_id": emoji_id,
            }
            for emoji, emoji_id in (
                ("🙂", "1000000000000000001"),
                ("⚽", "1000000000000000002"),
                ("🔒", "1000000000000000003"),
                ("✍️", "1000000000000000004"),
            )
        ]

        assignments = extract_emoji_assignments({"text": text, "entities": entities})

        self.assertEqual(
            [(item.scope, item.key, item.custom_emoji_id) for item in assignments],
            [
                ("bookmaker", "winline", "1000000000000000001"),
                ("sport", "football", "1000000000000000002"),
                ("decor", "forecast", "1000000000000000003"),
                ("write", None, "1000000000000000004"),
            ],
        )

    def test_reply_command_accepts_custom_emoji_sticker(self):
        assignments = extract_emoji_assignments(
            {
                "text": "/emoji_add decor:forecast",
                "entities": [],
                "reply_to_message": {
                    "sticker": {
                        "custom_emoji_id": "1000000000000000005",
                    }
                },
            }
        )

        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].scope, "decor")
        self.assertEqual(assignments[0].key, "forecast")
        self.assertEqual(assignments[0].custom_emoji_id, "1000000000000000005")

    def test_rejects_unscoped_or_non_custom_sticker(self):
        with self.assertRaisesRegex(ValueError, "слот"):
            extract_emoji_assignments(
                {
                    "text": "/emoji_add",
                    "entities": [],
                    "reply_to_message": {
                        "sticker": {
                            "file_id": "regular-sticker",
                        }
                    },
                }
            )


class TelegramEmojiPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(
                SystemSetting.__table__.create,
            )
        self.original_catalog = current_emoji_catalog()
        self.catalog_was_loaded = emoji_catalog_is_loaded()

    async def asyncTearDown(self):
        if self.catalog_was_loaded:
            replace_emoji_catalog(self.original_catalog)
        else:
            clear_emoji_catalog()
        await self.engine.dispose()

    async def test_save_persists_catalog_and_refreshes_runtime_snapshot(self):
        assignments = extract_emoji_assignments(
            {
                "text": "/emoji_add bk:winline 🙂",
                "entities": [
                    {
                        "type": "custom_emoji",
                        "offset": _utf16_offset("/emoji_add bk:winline 🙂", "🙂"),
                        "length": 2,
                        "custom_emoji_id": "1000000000000000001",
                    }
                ],
            }
        )

        async with self.session_factory() as session:
            snapshot = await save_emoji_assignments(session, assignments)
            await session.commit()
            replace_emoji_catalog(snapshot)
            result = await session.execute(
                select(SystemSetting).where(
                    SystemSetting.key == "TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS"
                )
            )
            stored = result.scalars().one()

        self.assertEqual(
            json.loads(stored.value),
            {"winline": "1000000000000000001"},
        )
        self.assertEqual(
            snapshot.custom_emoji_id("bookmaker", "winline"),
            "1000000000000000001",
        )
        self.assertEqual(
            current_emoji_catalog().custom_emoji_id("bookmaker", "winline"),
            "1000000000000000001",
        )

    async def test_database_values_override_environment_defaults_on_load(self):
        async with self.session_factory() as session:
            session.add(
                SystemSetting(
                    key="TELEGRAM_DECOR_CUSTOM_EMOJI_IDS",
                    value='{"forecast":"1000000000000000009"}',
                    is_secret=False,
                )
            )
            await session.commit()
            snapshot = await load_telegram_emoji_catalog(session)

        self.assertEqual(
            snapshot.custom_emoji_id("decor", "forecast"),
            "1000000000000000009",
        )

    async def test_save_rejects_unbounded_catalog_growth(self):
        assignments = tuple(
            EmojiAssignment(
                scope="decor",
                key=f"slot_{index}",
                custom_emoji_id=f"{1000000000000000000 + index}",
            )
            for index in range(257)
        )

        async with self.session_factory() as session:
            with self.assertRaisesRegex(ValueError, "не более 256"):
                await save_emoji_assignments(session, assignments)


class TelegramEmojiWebhookTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    async def test_catalog_commands_fail_closed_for_regular_users(self):
        session = self.FakeSession()
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(telegram_webhook.settings, "SALES_MANAGER_TELEGRAM_ID", 222),
        ):
            response = await telegram_webhook._build_emoji_catalog_response(
                {"text": "/emoji_list"},
                333,
                session,
            )

        self.assertIn("только владельцу", response["text"])
        self.assertFalse(session.committed)

    async def test_add_command_saves_and_commits_for_owner(self):
        session = self.FakeSession()
        message = {
            "text": "/emoji_add decor:forecast 🙂",
            "entities": [
                {
                    "type": "custom_emoji",
                    "offset": _utf16_offset("/emoji_add decor:forecast 🙂", "🙂"),
                    "length": 2,
                    "custom_emoji_id": "1000000000000000001",
                }
            ],
        }
        snapshot = EmojiCatalogSnapshot.from_values(
            decor={"forecast": "1000000000000000001"},
        )
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(telegram_webhook, "save_emoji_assignments", new=AsyncMock(return_value=snapshot)) as save,
            patch.object(telegram_webhook, "replace_emoji_catalog") as replace_catalog,
        ):
            response = await telegram_webhook._build_emoji_catalog_response(
                message,
                111,
                session,
            )

        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertIn("Сохранено: 1", response["text"])
        save.assert_awaited_once()
        replace_catalog.assert_called_once_with(snapshot)


class TelegramEmojiRenderingTests(unittest.TestCase):
    def setUp(self):
        self.original_catalog = current_emoji_catalog()
        self.catalog_was_loaded = emoji_catalog_is_loaded()
        replace_emoji_catalog(
            EmojiCatalogSnapshot.from_values(
                decor={"forecast": "1000000000000000003"},
            )
        )

    def tearDown(self):
        if self.catalog_was_loaded:
            replace_emoji_catalog(self.original_catalog)
        else:
            clear_emoji_catalog()

    def test_decor_helper_builds_trusted_telegram_custom_emoji(self):
        value = decor_custom_emoji("forecast", "🔒")

        self.assertIn('emoji-id="1000000000000000003"', value)
        self.assertTrue(value.endswith("</tg-emoji>"))

    def test_forecast_decorator_adds_configured_header_icon_once(self):
        source = "<b>Закрытый анонс прогноза</b>\n\nМатч: <b>A — B</b>"

        decorated = decorate_forecast_html(source)
        decorated_twice = decorate_forecast_html(decorated)

        self.assertIn(
            '<tg-emoji emoji-id="1000000000000000003">🔒</tg-emoji> '
            "<b>Закрытый анонс прогноза</b>",
            decorated,
        )
        self.assertEqual(decorated_twice, decorated)

    def test_forecast_decorator_uses_clean_semantic_fallbacks_without_generic_bookmaker_icon(self):
        replace_emoji_catalog(EmojiCatalogSnapshot.from_values())
        source = (
            "<b>ПРОГНОЗ SHAMRAI</b>\n\n"
            "Матч: <b>A — B</b>\n\n"
            "Исход: <b>П1</b>\n\n"
            "Коэффициент: <b>1.90</b>\n\n"
            "БК: <b>Фонбет</b>"
        )

        decorated = decorate_forecast_html(source)

        self.assertIn("⚔️ <b>ПРОГНОЗ SHAMRAI</b>", decorated)
        self.assertIn("🏆 Матч: <b>A — B</b>", decorated)
        self.assertIn("🎯 Исход: <b>П1</b>", decorated)
        self.assertIn("📈 Коэффициент: <b>1.90</b>", decorated)
        self.assertIn("\n\nБК: <b>Фонбет</b>", decorated)
        self.assertNotIn("🏦 БК:", decorated)
        self.assertNotIn("🏟️", decorated)

    def test_sport_fallbacks_match_the_selected_sport(self):
        replace_emoji_catalog(EmojiCatalogSnapshot.from_values())

        self.assertEqual(sport_custom_emoji("Футбол"), "⚽")
        self.assertEqual(sport_custom_emoji("Football"), "⚽")
        self.assertEqual(sport_custom_emoji("Баскетбол"), "🏀")
        self.assertEqual(sport_custom_emoji("Волейбол"), "🏐")
        self.assertEqual(sport_custom_emoji("Хоккей"), "🏒")
        self.assertEqual(sport_custom_emoji("Неизвестный спорт"), "🏅")


if __name__ == "__main__":
    unittest.main()
