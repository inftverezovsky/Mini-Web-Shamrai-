import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import telegram_custom_emoji_previews as previews


class TelegramCustomEmojiPreviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.preview_dir = Path(self.temp_dir.name)
        self.preview_dir_patch = patch.object(
            previews,
            "CUSTOM_EMOJI_PREVIEW_DIR",
            self.preview_dir,
        )
        self.preview_dir_patch.start()

    def tearDown(self):
        # Other app-lifespan tests may still have a queued warm-up waiting on
        # the shared lock. Stop redirecting the global path only while no
        # preview writer can be active, then remove the isolated directory.
        with previews._warm_lock:
            self.preview_dir_patch.stop()
            for attempt in range(10):
                try:
                    self.temp_dir.cleanup()
                    break
                except OSError as exc:
                    if getattr(exc, "winerror", None) != 145 or attempt == 9:
                        raise
                    time.sleep(0.02)

    def test_cache_round_trip_detects_webp_and_avoids_network(self):
        custom_id = "1000000000000000001"
        contents = b"RIFF\x10\x00\x00\x00WEBPpreview"

        previews.save_custom_emoji_preview(custom_id, contents)
        loaded = previews.load_cached_custom_emoji_preview(custom_id)

        self.assertEqual(loaded, (contents, "image/webp"))
        self.assertTrue(previews.custom_emoji_preview_is_ready(custom_id))
        self.assertEqual(
            previews.cached_custom_emoji_preview_url(custom_id),
            f"/api/telegram/custom-emojis/{custom_id}/preview",
        )

    def test_batch_warm_resolves_stickers_once_and_persists_every_preview(self):
        ids = [
            "1000000000000000001",
            "1000000000000000002",
        ]
        telegram_response = {
            "ok": True,
            "result": [
                {
                    "custom_emoji_id": ids[0],
                    "thumbnail": {"file_id": "file-1"},
                },
                {
                    "custom_emoji_id": ids[1],
                    "thumbnail": {"file_id": "file-2"},
                },
            ],
        }

        with (
            patch.object(previews, "call_telegram_api", return_value=telegram_response) as api,
            patch.object(
                previews,
                "download_telegram_file",
                side_effect=[
                    (b"RIFF\x10\x00\x00\x00WEBPone", "image/webp"),
                    (b"\x89PNG\r\n\x1a\ntwo", "image/png"),
                ],
            ) as download,
        ):
            report = previews.warm_custom_emoji_previews(ids, max_workers=1)

        api.assert_called_once_with(
            "getCustomEmojiStickers",
            {"custom_emoji_ids": ids},
        )
        self.assertEqual(download.call_count, 2)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.cached, 2)
        self.assertEqual(report.failed, 0)
        self.assertEqual(
            previews.load_cached_custom_emoji_preview(ids[0]),
            (b"RIFF\x10\x00\x00\x00WEBPone", "image/webp"),
        )
        self.assertEqual(
            previews.load_cached_custom_emoji_preview(ids[1]),
            (b"\x89PNG\r\n\x1a\ntwo", "image/png"),
        )

    def test_warm_skips_files_already_cached(self):
        custom_id = "1000000000000000001"
        previews.save_custom_emoji_preview(
            custom_id,
            b"RIFF\x10\x00\x00\x00WEBPcached",
        )

        with patch.object(previews, "call_telegram_api") as api:
            report = previews.warm_custom_emoji_previews([custom_id])

        api.assert_not_called()
        self.assertEqual(report.ready, 1)
        self.assertEqual(report.cached, 0)
