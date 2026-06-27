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

export const PROFILE_SETUP_NAVIGATION_EVENT = 'shamrai:profile-setup-navigation';

export interface ProfileSetupNavigationDetail {
  actionId: string;
  url: string;
  intent: ProfileSetupIntent;
}

export interface ConnectionSetupActionPresentation {
  title: string;
  caption: string;
  busyLabel: string;
  successLabel: string;
  fallbackLabel: string;
}

const connectionActionTargets: Record<string, { setup: ProfileSetup; targetId: string }> = {
  'connect-telegram': { setup: 'telegram', targetId: 'connect-telegram' },
  'confirm-telegram-chat': { setup: 'telegram', targetId: 'connect-telegram' },
  'connect-vk': { setup: 'vk', targetId: 'connect-vk' },
  'allow-vk-messages': { setup: 'vk-messages', targetId: 'connect-vk' },
  'enable-web-push': { setup: 'notifications', targetId: 'web-push' },
};

const connectionActionPresentations: Record<string, Omit<ConnectionSetupActionPresentation, 'title'>> = {
  'connect-telegram': {
    caption: 'Открою Telegram-бота. Останется нажать Start и вернуться сюда.',
    busyLabel: 'Открываю Telegram...',
    successLabel: 'Telegram открыт',
    fallbackLabel: 'Открою профиль с Telegram-шагом',
  },
  'confirm-telegram-chat': {
    caption: 'Открою Telegram-шаг. Нажмите Start в боте/чате и вернитесь в Shamrai.',
    busyLabel: 'Открываю Telegram...',
    successLabel: 'Telegram открыт',
    fallbackLabel: 'Открою профиль с Telegram-шагом',
  },
  'connect-vk': {
    caption: 'Запущу VK ID и верну вас обратно в Shamrai после подтверждения.',
    busyLabel: 'Открываю подключение...',
    successLabel: 'VK ID запущен',
    fallbackLabel: 'Открою профиль с VK-шагом',
  },
  'allow-vk-messages': {
    caption: 'Открою разрешение сообщений от сообщества, чтобы сигналы доходили в VK.',
    busyLabel: 'Открываю разрешение...',
    successLabel: 'Проверяем VK-доступ',
    fallbackLabel: 'Открою профиль с VK-шагом',
  },
  'enable-web-push': {
    caption: 'Попробую включить уведомления одним нажатием. Если браузер спросит, нажмите Разрешить.',
    busyLabel: 'Включаю уведомления...',
    successLabel: 'Уведомления включены',
    fallbackLabel: 'Открою профиль с инструкцией',
  },
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

function browserOrigin() {
  return typeof window === 'undefined' ? 'https://shamra1.pro' : window.location.origin;
}

function appPathFromUrl(url: string) {
  try {
    const parsed = new URL(url || '/app', browserOrigin());
    const path = parsed.pathname.replace(/\/$/, '') || '/app';
    return path.startsWith('/') ? path : '/app';
  } catch {
    return '/app';
  }
}

function sameOriginPathFromUrl(url: string) {
  if (typeof window === 'undefined') return null;
  const trimmedUrl = url.trim();
  if (!trimmedUrl) return null;

  try {
    const parsed = new URL(trimmedUrl, window.location.origin);
    if (parsed.origin !== window.location.origin) return null;
    return `${parsed.pathname}${parsed.search}${parsed.hash}` || '/app';
  } catch {
    return null;
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

export function shouldRunConnectionSetupInline(actionId: string) {
  return actionId.trim() === 'enable-web-push';
}

export function connectionSetupActionPresentation(
  actionId: string,
  fallbackTitle: string,
): ConnectionSetupActionPresentation {
  const title = fallbackTitle.trim() || 'Открыть настройку';
  const presentation = connectionActionPresentations[actionId.trim()];
  if (presentation) {
    return {
      title,
      ...presentation,
    };
  }

  return {
    title,
    caption: 'Открою нужный шаг и подскажу, что нажать дальше.',
    busyLabel: 'Открываю настройку...',
    successLabel: 'Настройка открыта',
    fallbackLabel: 'Открою профиль',
  };
}

export function profileSetupIntentFromLocation(): ProfileSetupIntent {
  if (typeof window === 'undefined') return resolveProfileSetupIntent('');
  return resolveProfileSetupIntent(window.location.search, window.location.hash);
}

export function navigateToConnectionSetupAction(actionId: string, url: string) {
  if (typeof window === 'undefined') return false;

  const nextUrl = normalizeConnectionSetupActionUrl(actionId, url);
  const localPath = sameOriginPathFromUrl(nextUrl);
  if (!localPath) return false;

  const parsed = new URL(localPath, window.location.origin);
  if (parsed.searchParams.get('open') !== 'profile') return false;

  const targetUrl = `${parsed.pathname}${parsed.search}${parsed.hash}`;
  window.history.pushState(null, '', targetUrl);
  const intent = resolveProfileSetupIntent(parsed.search, parsed.hash);
  const detail: ProfileSetupNavigationDetail = {
    actionId: actionId.trim(),
    url: targetUrl,
    intent,
  };
  window.dispatchEvent(new CustomEvent<ProfileSetupNavigationDetail>(
    PROFILE_SETUP_NAVIGATION_EVENT,
    { detail },
  ));
  return true;
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
