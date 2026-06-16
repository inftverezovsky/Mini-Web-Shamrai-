import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from src.api.deps import get_current_privileged_admin


class RoleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_moderator_is_not_privileged_admin(self):
        with self.assertRaises(HTTPException) as exc:
            await get_current_privileged_admin(SimpleNamespace(role="moderator"))

        self.assertEqual(exc.exception.status_code, 403)

    async def test_admin_is_privileged_admin(self):
        admin = SimpleNamespace(role="admin")
        self.assertIs(await get_current_privileged_admin(admin), admin)


if __name__ == "__main__":
    unittest.main()
