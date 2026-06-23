export interface ProfileAvatarUser {
  photo_url?: string | null;
  vk_photo_url?: string | null;
}

export interface TelegramRuntimeAvatarUser {
  photo_url?: string | null;
}

function normalizeAvatarUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const url = value.trim();
  if (!url) return null;
  if (/^https?:\/\//i.test(url) || /^data:image\//i.test(url) || /^blob:/i.test(url)) {
    return url;
  }
  return null;
}

export function buildProfileAvatarSources(
  user: ProfileAvatarUser | null | undefined,
  telegramUser?: TelegramRuntimeAvatarUser | null,
): string[] {
  const seen = new Set<string>();
  const candidates = [
    user?.photo_url,
    user?.vk_photo_url,
    telegramUser?.photo_url,
  ];

  return candidates.reduce<string[]>((sources, candidate) => {
    const url = normalizeAvatarUrl(candidate);
    if (!url || seen.has(url)) return sources;
    seen.add(url);
    return [...sources, url];
  }, []);
}
