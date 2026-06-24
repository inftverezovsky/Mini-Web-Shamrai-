import { describe, expect, it } from 'vitest';

import { applyReadReceiptToMessages } from '../src/features/chat/readReceipts';
import type { ChatReadUpdatedEvent } from '../src/schemas/schemas';

describe('applyReadReceiptToMessages', () => {
  it('marks opposite-side messages up to the read cursor without changing reader messages', () => {
    const receipt: ChatReadUpdatedEvent = {
      event: 'chat.read.updated',
      conversation_id: 'conversation-1',
      reader_user_id: 901,
      reader_role: 'admin',
      reader_direction: 'staff',
      last_read_message_id: 3,
      updated_at: '2026-06-24T10:00:00.000Z',
    };

    const messages = applyReadReceiptToMessages([
      { id: 1, direction: 'client' as const, read_at: null },
      { id: 2, direction: 'staff' as const, read_at: null },
      { id: 3, direction: 'client' as const, read_at: null },
      { id: 4, direction: 'client' as const, read_at: null },
    ], receipt);

    expect(messages.map((message) => message.read_at)).toEqual([
      receipt.updated_at,
      null,
      receipt.updated_at,
      null,
    ]);
  });
});
