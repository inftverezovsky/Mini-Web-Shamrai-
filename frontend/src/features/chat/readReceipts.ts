import type { ChatReadUpdatedEvent, ChatSupportDirection } from '../../schemas/schemas';

interface ReadableMessage {
  id: number;
  direction: ChatSupportDirection;
  read_at?: string | null;
}

export function applyReadReceiptToMessages<TMessage extends ReadableMessage>(
  messages: readonly TMessage[],
  receipt: ChatReadUpdatedEvent,
): TMessage[] {
  const lastReadMessageId = Number(receipt.last_read_message_id || 0);
  if (!lastReadMessageId || !receipt.reader_direction) return messages as TMessage[];

  const readAt = receipt.updated_at || new Date().toISOString();
  let changed = false;
  const nextMessages = messages.map((message) => {
    if (
      message.id <= 0
      || message.id > lastReadMessageId
      || message.direction === receipt.reader_direction
      || message.read_at
    ) {
      return message;
    }
    changed = true;
    return { ...message, read_at: readAt };
  });

  return changed ? nextMessages : messages as TMessage[];
}
