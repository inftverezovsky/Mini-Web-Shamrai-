import { describe, expect, it } from 'vitest';
import { buildProfileAvatarSources } from '../src/utils/profileAvatar';

describe('buildProfileAvatarSources', () => {
  it('keeps Telegram and VK avatar URLs as separate fallbacks without duplicates', () => {
    const sources = buildProfileAvatarSources(
      {
        photo_url: ' https://telegram.example/avatar.jpg ',
        vk_photo_url: 'https://vk.example/avatar.jpg',
      },
      {
        photo_url: 'https://telegram.example/avatar.jpg',
      },
    );

    expect(sources).toEqual([
      'https://telegram.example/avatar.jpg',
      'https://vk.example/avatar.jpg',
    ]);
  });

  it('drops empty and unsafe avatar URLs before rendering an image', () => {
    const sources = buildProfileAvatarSources(
      {
        photo_url: 'javascript:alert(1)',
        vk_photo_url: 'data:image/png;base64,avatar',
      },
      {
        photo_url: '',
      },
    );

    expect(sources).toEqual(['data:image/png;base64,avatar']);
  });
});
