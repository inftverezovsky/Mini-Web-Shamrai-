import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import EmojiTextField from '../src/components/EmojiTextField';
import TelegramCustomEmojiText from '../src/components/TelegramCustomEmojiText';

const customEmojiId = '5436188742756893380';
const entity = {
  offset: 7,
  length: 2,
  custom_emoji_id: customEmojiId,
};

describe('Telegram custom emoji rendering', () => {
  it('renders the real cached image in feed text instead of the fallback symbol', () => {
    const markup = renderToStaticMarkup(
      <TelegramCustomEmojiText
        text="Привет ⚡!"
        entities={[entity]}
      />,
    );

    expect(markup).toContain(
      `/api/telegram/custom-emojis/${customEmojiId}/preview`,
    );
    expect(markup).toContain('<img');
    expect(markup).toContain('Привет ');
    expect(markup).toContain('!');
  });

  it('shows an image-backed visual layer after selecting a custom emoji in the editor', () => {
    const markup = renderToStaticMarkup(
      <EmojiTextField
        multiline
        value="Привет ⚡!"
        onValueChange={() => undefined}
        customEmojiEntities={[entity]}
        onCustomEmojiEntitiesChange={() => undefined}
        rows={3}
      />,
    );

    expect(markup).toContain('data-custom-emoji-preview="true"');
    expect(markup).toContain(
      `/api/telegram/custom-emojis/${customEmojiId}/preview`,
    );
    expect(markup).toContain('color:transparent');
    expect(markup).toContain('caret-color:#fff');
  });
});
