import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ChatAttachmentSecurityConfigTests(unittest.TestCase):
    def test_backend_and_ui_keep_the_25mb_file_contract(self):
        upload_source = (PROJECT_ROOT / "backend" / "src" / "services" / "chat_uploads.py").read_text(
            encoding="utf-8"
        )
        composer_source = (
            PROJECT_ROOT / "frontend" / "src" / "features" / "chat" / "MessageComposer.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("CHAT_FILE_MAX_BYTES = 25 * 1024 * 1024", upload_source)
        self.assertIn("FILE_MAX_BYTES = 25 * 1024 * 1024", composer_source)
        self.assertIn("Максимум 25 МБ", composer_source)

    def test_fastapi_exposes_only_public_coupon_static_mount(self):
        main_source = (PROJECT_ROOT / "backend" / "src" / "main.py").read_text(encoding="utf-8")

        self.assertNotIn('app.mount("/static",', main_source)
        self.assertIn('app.mount("/static/coupons",', main_source)

    def test_nginx_denies_public_chat_static_and_keeps_coupon_static(self):
        for relative_path in ("deploy/nginx/shamrai.conf", "frontend/nginx.conf"):
            with self.subTest(config=relative_path):
                source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
                chat_static = re.search(r"location \^~ /static/chat/ \{(?P<body>.*?)\n\s*\}", source, re.DOTALL)
                self.assertIsNotNone(chat_static)
                self.assertIn("return 404;", chat_static.group("body"))
                self.assertNotIn("alias ", chat_static.group("body"))
                self.assertIn("location ^~ /static/coupons/", source)

    def test_nginx_gives_only_chat_attachments_the_26m_upload_budget(self):
        for relative_path in ("deploy/nginx/shamrai.conf", "frontend/nginx.conf"):
            with self.subTest(config=relative_path):
                source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
                chat_upload = re.search(
                    r"location ~ \^/api/chat/\(conversations/support/attachments\|admin/conversations/\[0-9a-fA-F-\]\+/attachments\)\$ \{(?P<body>.*?)\n\s*\}",
                    source,
                    re.DOTALL,
                )
                self.assertIsNotNone(chat_upload)
                self.assertIn("client_max_body_size 26m;", chat_upload.group("body"))


if __name__ == "__main__":
    unittest.main()
