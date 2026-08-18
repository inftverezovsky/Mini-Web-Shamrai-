import React, { memo, useState } from 'react';
import { buildApiUrl } from '../config/api';
import type { TelegramCustomEmojiEntity } from './EmojiTextField';

interface TelegramCustomEmojiTextProps {
  text: string;
  entities?: TelegramCustomEmojiEntity[] | null;
}

const CustomEmojiImage = memo(function CustomEmojiImage({
  entity,
  fallback,
}: {
  entity: TelegramCustomEmojiEntity;
  fallback: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return <>{fallback}</>;
  return (
    <img
      src={buildApiUrl(`/api/telegram/custom-emojis/${entity.custom_emoji_id}/preview`)}
      alt={fallback}
      className="mx-[0.04em] inline-block h-[1.25em] w-[1.25em] object-contain align-[-0.22em]"
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
});

export default function TelegramCustomEmojiText({
  text,
  entities = [],
}: TelegramCustomEmojiTextProps) {
  const ordered = [...(entities || [])]
    .filter((entity) => (
      Number.isInteger(entity.offset)
      && Number.isInteger(entity.length)
      && entity.offset >= 0
      && entity.length > 0
      && entity.offset + entity.length <= text.length
    ))
    .sort((left, right) => left.offset - right.offset);

  if (!ordered.length) return <>{text}</>;

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  ordered.forEach((entity, index) => {
    if (entity.offset < cursor) return;
    if (entity.offset > cursor) {
      parts.push(text.slice(cursor, entity.offset));
    }
    const fallback = text.slice(entity.offset, entity.offset + entity.length);
    parts.push(
      <CustomEmojiImage
        key={`${entity.custom_emoji_id}-${entity.offset}-${index}`}
        entity={entity}
        fallback={fallback}
      />,
    );
    cursor = entity.offset + entity.length;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}
