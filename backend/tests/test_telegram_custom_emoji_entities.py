import unittest

from src.core.telegram_custom_emoji_entities import (
    combine_custom_emoji_entities,
    parse_custom_emoji_entities,
)


class TelegramCustomEmojiEntityTests(unittest.TestCase):
    def test_parses_valid_utf16_entities(self):
        entities = parse_custom_emoji_entities(
            '[{"offset":1,"length":2,"custom_emoji_id":"1000000000000000001"}]',
            "A🙂B",
            allowed_custom_emoji_ids={"1000000000000000001"},
        )

        self.assertEqual(
            entities,
            [
                {
                    "offset": 1,
                    "length": 2,
                    "custom_emoji_id": "1000000000000000001",
                }
            ],
        )

    def test_rejects_unknown_or_out_of_bounds_entities(self):
        with self.assertRaisesRegex(ValueError, "библиотек"):
            parse_custom_emoji_entities(
                '[{"offset":0,"length":2,"custom_emoji_id":"1000000000000000009"}]',
                "🙂",
                allowed_custom_emoji_ids={"1000000000000000001"},
            )

        with self.assertRaisesRegex(ValueError, "границ"):
            parse_custom_emoji_entities(
                '[{"offset":4,"length":2,"custom_emoji_id":"1000000000000000001"}]',
                "🙂",
                allowed_custom_emoji_ids={"1000000000000000001"},
            )

    def test_combines_title_and_body_offsets_for_telegram(self):
        combined = combine_custom_emoji_entities(
            "A🙂",
            [{"offset": 1, "length": 2, "custom_emoji_id": "1000000000000000001"}],
            "⚽ B",
            [{"offset": 0, "length": 1, "custom_emoji_id": "1000000000000000002"}],
        )

        self.assertEqual(combined["text"], "A🙂\n\n⚽ B")
        self.assertEqual(
            combined["entities"],
            [
                {
                    "type": "custom_emoji",
                    "offset": 1,
                    "length": 2,
                    "custom_emoji_id": "1000000000000000001",
                },
                {
                    "type": "custom_emoji",
                    "offset": 5,
                    "length": 1,
                    "custom_emoji_id": "1000000000000000002",
                },
            ],
        )
