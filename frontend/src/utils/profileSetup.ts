export type ProfileSetup =
  | ''
  | 'identity'
  | 'telegram'
  | 'vk'
  | 'vk-messages'
  | 'notifications';

export type ProfileSetupSection = 'telegram' | 'vk' | 'notifications';
export type ProfileSetupAutoAction = 'telegram' | 'vk-link' | 'vk-messages';

export interface ProfileSetupIntent {
  setup: ProfileSetup;
  section: ProfileSetupSection | null;
  targetId: string;
  autoAction: ProfileSetupAutoAction | null;
}

const connectionActionTargets: Record<string, { setup: ProfileSetup; targetId: string }> = {
  'connect-telegram': { setup: 'telegram', targetId: 'connect-telegram' },
  'connect-vk': { setup: 'vk', targetId: 'connect-vk' },
  'allow-vk-messages': { setup: 'vk-messages', targetId: 'connect-vk' },
  'enable-web-push': { setup: 'notifications', targetId: 'web-push' },
};

const allowedSetups = new Set<ProfileSetup>([
  'identity',
  'telegram',
  'vk',
  'vk-messages',
  'notifications',
]);

function normalizeSetup(rawSetup: string | null): ProfileSetup {
  const setup = (rawSetup || '').trim();
  return allowedSetups.has(setup as ProfileSetup) ? setup as ProfileSetup : '';
}

function normalizeHash(hash: string) {
  return hash.replace(/^#/, '').trim();
}

function appPathFromUrl(url: string) {
  try {
    const baseUrl = typeof window === 'undefined' ? 'https://shamra1.pro' : window.location.origin;
    const parsed = new URL(url || '/app', baseUrl);
    const path = parsed.pathname.replace(/\/$/, '') || '/app';
    return path.startsWith('/') ? path : '/app';
  } catch {
    return '/app';
  }
}

export function normalizeConnectionSetupActionUrl(actionId: string, url: string) {
  const target = connectionActionTargets[actionId.trim()];
  if (!target) return url;

  const params = new URLSearchParams({
    open: 'profile',
    setup: target.setup,
  });
  return `${appPathFromUrl(url)}?${params.toString()}#${target.targetId}`;
}

export function resolveProfileSetupIntent(search: string, hash = ''): ProfileSetupIntent {
  const params = new URLSearchParams(search.startsWith('?') ? search : `?${search}`);
  const setup = normalizeSetup(params.get('setup'));
  const hashTarget = normalizeHash(hash);

  if (setup === 'telegram') {
    return {
      setup,
      section: 'telegram',
      targetId: hashTarget || 'connect-telegram',
      autoAction: 'telegram',
    };
  }

  if (setup === 'vk') {
    return {
      setup,
      section: 'vk',
      targetId: hashTarget || 'connect-vk',
      autoAction: 'vk-link',
    };
  }

  if (setup === 'vk-messages') {
    return {
      setup,
      section: 'vk',
      targetId: hashTarget || 'connect-vk',
      autoAction: 'vk-messages',
    };
  }

  if (setup === 'notifications') {
    return {
      setup,
      section: 'notifications',
      targetId: hashTarget || 'web-push',
      autoAction: null,
    };
  }

  if (setup === 'identity') {
    return {
      setup,
      section: null,
      targetId: hashTarget || 'connect-identity',
      autoAction: null,
    };
  }

  return {
    setup: '',
    section: null,
    targetId: hashTarget,
    autoAction: null,
  };
}
