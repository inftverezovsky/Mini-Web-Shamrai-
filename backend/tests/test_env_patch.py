import tempfile
import unittest
from pathlib import Path

from src.scripts.env_patch import patch_env_file, read_env_value


class EnvPatchTests(unittest.TestCase):
    def test_patch_env_file_removes_duplicate_stale_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "APP_ENV=production",
                        "VK_CALLBACK_CONFIRMATION_CODE=old-code",
                        "VK_CALLBACK_SECRET=old-secret",
                        "# VK_CALLBACK_CONFIRMATION_CODE=commented-old",
                        "VK_CALLBACK_CONFIRMATION_CODE=stale-code",
                        "VK_CALLBACK_SECRET=stale-secret",
                        "HTTPS_PROXY=http://proxy.example",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = patch_env_file(
                env_path,
                {
                    "VK_CALLBACK_CONFIRMATION_CODE": "new-code",
                    "VK_CALLBACK_SECRET": "new-secret",
                },
            )

            patched = env_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(patched.count("VK_CALLBACK_CONFIRMATION_CODE=new-code"), 1)
            self.assertEqual(patched.count("VK_CALLBACK_SECRET=new-secret"), 1)
            self.assertNotIn("VK_CALLBACK_CONFIRMATION_CODE=old-code", patched)
            self.assertNotIn("VK_CALLBACK_CONFIRMATION_CODE=stale-code", patched)
            self.assertNotIn("VK_CALLBACK_SECRET=old-secret", patched)
            self.assertNotIn("VK_CALLBACK_SECRET=stale-secret", patched)
            self.assertIn("# VK_CALLBACK_CONFIRMATION_CODE=commented-old", patched)
            self.assertEqual(read_env_value(env_path, "VK_CALLBACK_CONFIRMATION_CODE"), "new-code")
            self.assertEqual(result.replaced_counts["VK_CALLBACK_CONFIRMATION_CODE"], 2)
            self.assertEqual(result.replaced_counts["VK_CALLBACK_SECRET"], 2)
            self.assertEqual(result.appended_keys, [])

    def test_patch_env_file_appends_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("APP_ENV=production\n", encoding="utf-8")

            patch_env_file(env_path, {"VK_CALLBACK_CONFIRMATION_CODE": "new-code"})

            self.assertEqual(read_env_value(env_path, "VK_CALLBACK_CONFIRMATION_CODE"), "new-code")
            self.assertEqual(
                patch_env_file(env_path, {"VK_CALLBACK_CONFIRMATION_CODE": "new-code"}).replaced_counts[
                    "VK_CALLBACK_CONFIRMATION_CODE"
                ],
                1,
            )


if __name__ == "__main__":
    unittest.main()
