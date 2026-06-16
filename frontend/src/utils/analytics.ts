import { init, track } from '@plausible-analytics/tracker';

type AnalyticsValue = string | number | boolean | null | undefined;
type AnalyticsProps = Record<string, AnalyticsValue>;

const plausibleDomain = import.meta.env.VITE_PLAUSIBLE_DOMAIN || '';
const plausibleEndpoint = import.meta.env.VITE_PLAUSIBLE_ENDPOINT || '';
const captureLocalhost = import.meta.env.VITE_PLAUSIBLE_CAPTURE_LOCALHOST === 'true';

let initialized = false;
let lastPageKey = '';

function normalizeProps(props?: AnalyticsProps) {
  if (!props) return undefined;

  return Object.entries(props).reduce<Record<string, string>>((acc, [key, value]) => {
    if (value === null || value === undefined || value === '') return acc;
    acc[key] = String(value);
    return acc;
  }, {});
}

function currentSource() {
  const tg = window.Telegram?.WebApp;
  const search = window.location.search || '';

  if (tg?.initData) return 'telegram';
  if (/(?:^|[?&])vk_(?:app_id|platform|user_id)=/i.test(search)) return 'vk';
  return 'web';
}

export function initAnalytics() {
  if (initialized || !plausibleDomain) return;

  init({
    domain: plausibleDomain,
    endpoint: plausibleEndpoint || undefined,
    autoCapturePageviews: false,
    outboundLinks: true,
    fileDownloads: true,
    formSubmissions: false,
    captureOnLocalhost: captureLocalhost,
    customProperties: () => ({
      source: currentSource(),
    }),
  });

  initialized = true;
}

export function trackEvent(name: string, props?: AnalyticsProps, options?: { interactive?: boolean }) {
  if (!initialized) return;

  track(name, {
    props: normalizeProps(props),
    interactive: options?.interactive,
  });
}

export function trackPageView(path: string, props?: AnalyticsProps) {
  if (!initialized) return;

  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const pageKey = `${normalizedPath}:${JSON.stringify(normalizeProps(props) || {})}`;
  if (pageKey === lastPageKey) return;
  lastPageKey = pageKey;

  track('pageview', {
    url: `${window.location.origin}${normalizedPath}`,
    props: normalizeProps(props),
    interactive: false,
  });
}

export function buildTabPath(role: 'user' | 'admin', tab: string) {
  return `/app/${role}/${tab.replace(/_/g, '-')}`;
}
