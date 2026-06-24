export const THEME_PRIMARY_COLOR_KEY = 'THEME_PRIMARY_COLOR';
export const THEME_SECONDARY_COLOR_KEY = 'THEME_SECONDARY_COLOR';
export const GLOBAL_PERFORMANCE_MODE_KEY = 'GLOBAL_PERFORMANCE_MODE';

export const DEFAULT_THEME_SETTINGS = {
  primary_color: '#00d2ff',
  secondary_color: '#d946ef',
  global_performance_mode: false,
};

export const PUBLIC_THEME_QUERY_KEY = ['public-theme-settings'] as const;

export type ThemeSettingKey =
  | typeof THEME_PRIMARY_COLOR_KEY
  | typeof THEME_SECONDARY_COLOR_KEY
  | typeof GLOBAL_PERFORMANCE_MODE_KEY;

export interface PublicThemeSettingsResponse {
  primary_color: string;
  secondary_color: string;
  global_performance_mode: boolean;
}

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && HEX_COLOR_PATTERN.test(value.trim());
}

export function normalizeHexColor(value: unknown, fallback: string) {
  const cleanValue = String(value || '').trim();
  return isHexColor(cleanValue) ? cleanValue.toLowerCase() : fallback;
}

export function hexToRgbTriplet(value: string) {
  const color = normalizeHexColor(value, DEFAULT_THEME_SETTINGS.primary_color).slice(1);
  return [
    Number.parseInt(color.slice(0, 2), 16),
    Number.parseInt(color.slice(2, 4), 16),
    Number.parseInt(color.slice(4, 6), 16),
  ].join(' ');
}

export function normalizedThemeSettings(theme?: Partial<PublicThemeSettingsResponse>): PublicThemeSettingsResponse {
  return {
    primary_color: normalizeHexColor(theme?.primary_color, DEFAULT_THEME_SETTINGS.primary_color),
    secondary_color: normalizeHexColor(theme?.secondary_color, DEFAULT_THEME_SETTINGS.secondary_color),
    global_performance_mode: Boolean(theme?.global_performance_mode),
  };
}

export function applyThemeSettingsToRoot(
  theme?: Partial<PublicThemeSettingsResponse>,
  root?: HTMLElement,
) {
  const normalizedTheme = normalizedThemeSettings(theme);
  const targetRoot = root || (typeof document !== 'undefined' ? document.documentElement : null);
  if (!targetRoot) return normalizedTheme;

  const primaryRgb = hexToRgbTriplet(normalizedTheme.primary_color);
  const secondaryRgb = hexToRgbTriplet(normalizedTheme.secondary_color);

  targetRoot.style.setProperty('--color-primary', normalizedTheme.primary_color);
  targetRoot.style.setProperty('--color-secondary', normalizedTheme.secondary_color);
  targetRoot.style.setProperty('--color-primary-rgb', primaryRgb);
  targetRoot.style.setProperty('--color-secondary-rgb', secondaryRgb);
  targetRoot.style.setProperty('--brand-cyan', normalizedTheme.primary_color);
  targetRoot.style.setProperty('--brand-pink', normalizedTheme.secondary_color);
  targetRoot.style.setProperty('--neon-button-a', `rgb(${primaryRgb} / 0.92)`);
  targetRoot.style.setProperty('--neon-button-b', `rgb(${secondaryRgb} / 0.82)`);
  targetRoot.dataset.globalPerformanceMode = normalizedTheme.global_performance_mode ? 'true' : 'false';
  targetRoot.classList.toggle('shamrai-global-performance-mode', normalizedTheme.global_performance_mode);

  return normalizedTheme;
}
