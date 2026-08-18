import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.models import SystemSetting
from src.api import telegram_webhook
from src.services.telegram_custom_emoji_library import (
    CUSTOM_EMOJI_LIBRARY_SETTING_KEY,
    CustomEmojiLibraryItem,
    extract_custom_emoji_library_items,
    load_custom_emoji_library,
    save_custom_emoji_library_items,
)


def _utf16_offset(text: str, fragment: str) -> int:
    return len(text[: text.index(fragment)].encode("utf-16-le")) // 2


class TelegramCustomEmojiLibraryParsingTests(unittest.TestCase):
    def test_extracts_unlabelled_custom_emojis_and_fallbacks(self):
        text = "🙂 ⚽"
        items = extract_custom_emoji_library_items(
            {
                "text": text,
                "entities": [
                    {
                        "type": "custom_emoji",
                        "offset": _utf16_offset(text, "🙂"),
                        "length": 2,
                        "custom_emoji_id": "1000000000000000001",
                    },
                    {
                        "type": "custom_emoji",
                        "offset": _utf16_offset(text, "⚽"),
                        "length": 1,
                        "custom_emoji_id": "1000000000000000002",
                    },
                ],
            }
        )

        self.assertEqual(
            items,
            (
                CustomEmojiLibraryItem("1000000000000000001", "🙂"),
                CustomEmojiLibraryItem("1000000000000000002", "⚽"),
            ),
        )

    def test_accepts_custom_emoji_sticker_without_caption(self):
        items = extract_custom_emoji_library_items(
            {
                "sticker": {
                    "custom_emoji_id": "1000000000000000003",
                    "emoji": "🔥",
                }
            }
        )

        self.assertEqual(
            items,
            (CustomEmojiLibraryItem("1000000000000000003", "🔥"),),
        )

    def test_rejects_message_without_custom_emoji(self):
        with self.assertRaisesRegex(ValueError, "custom emoji"):
            extract_custom_emoji_library_items({"text": "обычный текст"})


class TelegramCustomEmojiLibraryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(SystemSetting.__table__.create)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_saves_without_labels_and_deduplicates_by_custom_emoji_id(self):
        async with self.session_factory() as session:
            saved = await save_custom_emoji_library_items(
                session,
                (
                    CustomEmojiLibraryItem("1000000000000000001", "🙂"),
                    CustomEmojiLibraryItem("1000000000000000002", "⚽"),
                ),
            )
            saved = await save_custom_emoji_library_items(
                session,
                (CustomEmojiLibraryItem("1000000000000000001", "🔥"),),
            )
            await session.commit()

            row = (
                await session.execute(
                    select(SystemSetting).where(
                        SystemSetting.key == CUSTOM_EMOJI_LIBRARY_SETTING_KEY
                    )
                )
            ).scalars().one()
            loaded = await load_custom_emoji_library(session)

        self.assertEqual(
            saved,
            (
                CustomEmojiLibraryItem("1000000000000000001", "🔥"),
                CustomEmojiLibraryItem("1000000000000000002", "⚽"),
            ),
        )
        self.assertEqual(loaded, saved)
        self.assertEqual(
            json.loads(row.value),
            [
                {"custom_emoji_id": "1000000000000000001", "fallback": "🔥"},
                {"custom_emoji_id": "1000000000000000002", "fallback": "⚽"},
            ],
        )


class TelegramCustomEmojiLibraryWebhookTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    async def test_owner_can_upload_without_command_or_label(self):
        session = self.FakeSession()
        incoming = (CustomEmojiLibraryItem("1000000000000000001", "🙂"),)
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(
                telegram_webhook,
                "extract_custom_emoji_library_items",
                return_value=incoming,
            ),
            patch.object(
                telegram_webhook,
                "save_custom_emoji_library_items",
                new=AsyncMock(return_value=incoming),
            ) as save,
            patch.object(telegram_webhook, "_run_background") as run_background,
        ):
            response = await telegram_webhook._build_custom_emoji_library_upload_response(
                {
                    "text": "🙂",
                    "entities": [
                        {
                            "type": "custom_emoji",
                            "offset": 0,
                            "length": 2,
                            "custom_emoji_id": "1000000000000000001",
                        }
                    ],
                },
                111,
                session,
            )

        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertIn("Панель → Лента → Текст", response["text"])
        save.assert_awaited_once_with(session, incoming)
        run_background.assert_called_once()
        run_background.call_args.args[0].close()
