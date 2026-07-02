import unittest
from unittest.mock import AsyncMock, patch

from src.api import chat


class ChatStreamTicketCacheTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        chat._chat_stream_tickets.clear()
        chat._chat_stream_cache_backed_tickets.clear()

    async def test_chat_stream_ticket_can_be_consumed_from_shared_cache(self):
        stored_payloads = {}

        async def fake_cache_set(key, value, ttl_seconds=None):
            stored_payloads[key] = dict(value)

        async def fake_cache_pop(key):
            return True, stored_payloads.pop(key, None)

        cache_set = AsyncMock(side_effect=fake_cache_set)
        cache_pop = AsyncMock(side_effect=fake_cache_pop)

        with (
            patch.object(chat, "cache_set_json_with_status", new=cache_set),
            patch.object(chat, "cache_pop_json_with_status", new=cache_pop),
        ):
            ticket = await chat._issue_chat_stream_ticket_cached(123)
            cache_key = chat._chat_stream_ticket_cache_key(ticket)

            self.assertEqual(stored_payloads[cache_key]["user_id"], 123)
            self.assertEqual(cache_set.await_args.kwargs["ttl_seconds"], chat.CHAT_STREAM_TICKET_TTL_SECONDS)

            chat._chat_stream_tickets.clear()
            self.assertEqual(await chat._consume_chat_stream_ticket_cached(ticket), 123)
            self.assertIsNone(await chat._consume_chat_stream_ticket_cached(ticket))

    async def test_chat_stream_ticket_cache_miss_does_not_fallback_to_local_ticket(self):
        async def fake_cache_set(_key, _value, ttl_seconds=None):
            return True

        async def fake_cache_pop(_key):
            return True, None

        with (
            patch.object(chat, "cache_set_json_with_status", new=AsyncMock(side_effect=fake_cache_set)),
            patch.object(chat, "cache_pop_json_with_status", new=AsyncMock(side_effect=fake_cache_pop)),
        ):
            ticket = await chat._issue_chat_stream_ticket_cached(123)

            self.assertIsNone(await chat._consume_chat_stream_ticket_cached(ticket))
            self.assertIsNone(chat._consume_chat_stream_ticket(ticket))

    async def test_chat_stream_ticket_falls_back_to_local_when_cache_set_failed(self):
        async def fake_cache_set(_key, _value, ttl_seconds=None):
            return False

        async def fake_cache_pop(_key):
            return True, None

        with (
            patch.object(chat, "cache_set_json_with_status", new=AsyncMock(side_effect=fake_cache_set)),
            patch.object(chat, "cache_pop_json_with_status", new=AsyncMock(side_effect=fake_cache_pop)),
        ):
            ticket = await chat._issue_chat_stream_ticket_cached(123)

            self.assertEqual(await chat._consume_chat_stream_ticket_cached(ticket), 123)
            self.assertIsNone(chat._consume_chat_stream_ticket(ticket))


if __name__ == "__main__":
    unittest.main()
