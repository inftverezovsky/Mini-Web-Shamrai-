export const THEME_PRIMARY_COLOR_KEY = 'THEME_PRIMARY_COLOR';
export const THEME_SECONDARY_COLOR_KEY = 'THEME_SECONDARY_COLOR';
export const GLOBAL_PERFORMANCE_MODE_KEY = 'GLOBAL_PERFORMANCE_MODE';
export const BRAND_LOGO_URL_KEY = 'BRAND_LOGO_URL';
export const BRAND_BACKGROUND_URL_KEY = 'BRAND_BACKGROUND_URL';
export const THEME_GLASS_OPACITY_KEY = 'THEME_GLASS_OPACITY';
export const THEME_GLASS_BLUR_PX_KEY = 'THEME_GLASS_BLUR_PX';
export const THEME_RADIUS_SCALE_KEY = 'THEME_RADIUS_SCALE';
export const THEME_FONT_SCALE_KEY = 'THEME_FONT_SCALE';
export const THEME_DENSITY_KEY = 'THEME_DENSITY';
export const THEME_GLOW_STRENGTH_KEY = 'THEME_GLOW_STRENGTH';

export type ThemeDensity = 'compact' | 'cozy' | 'comfortable';

export const DEFAULT_THEME_SETTINGS = {
  primary_color: '#00d2ff',
  secondary_color: '#d946ef',
  global_performance_mode: false,
  brand_logo_url: '',
  brand_background_url: '',
  glass_opacity: 0.42,
  glass_blur_px: 18,
  radius_scale: 1,
  font_scale: 1,
  theme_density: 'compact' as ThemeDensity,
  glow_strength: 1,
};

export const PUBLIC_THEME_QUERY_KEY = ['public-theme-settings'] as const;

export type ThemeSettingKey =
  | typeof THEME_PRIMARY_COLOR_KEY
  | typeof THEME_SECONDARY_COLOR_KEY
  | typeof GLOBAL_PERFORMANCE_MODE_KEY
  | typeof BRAND_LOGO_URL_KEY
  | typeof BRAND_BACKGROUND_URL_KEY
  | typeof THEME_GLASS_OPACITY_KEY
  | typeof THEME_GLASS_BLUR_PX_KEY
  | typeof THEME_RADIUS_SCALE_KEY
  | typeof THEME_FONT_SCALE_KEY
  | typeof THEME_DENSITY_KEY
  | typeof THEME_GLOW_STRENGTH_KEY;

export interface PublicThemeSettingsResponse {
  primary_color: string;
  secondary_color: string;
  global_performance_mode: boolean;
  brand_logo_url: string;
  brand_background_url: string;
  glass_opacity: number;
  glass_blur_px: number;
  radius_scale: number;
  font_scale: number;
  theme_density: ThemeDensity;
  glow_strength: number;
}

const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && HEX_COLOR_PATTERN.test(value.trim());
}

export function normalizeHexColor(value: unknown, fallback: string) {
  const cleanValue = String(value || '').trim();
  return isHexColor(cleanValue) ? cleanValue.toLowerCase() : fallback;
}

export function normalizePublicUrl(value: unknown) {
  const cleanValue = String(value || '').trim();
  if (!cleanValue) return '';
  try {
    const parsed = new URL(cleanValue);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      return '';
    }
    return parsed.toString();
  } catch {
    return '';
  }
}

export function normalizeNumberRange(value: unknown, fallback: number, min: number, max: number) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return fallback;
  return Math.min(max, Math.max(min, numericValue));
}

export function normalizeThemeDensity(value: unknown): ThemeDensity {
  return value === 'cozy' || value === 'comfortable' || value === 'compact'
    ? value
    : DEFAULT_THEME_SETTINGS.theme_density;
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
    brand_logo_url: normalizePublicUrl(theme?.brand_logo_url),
    brand_background_url: normalizePublicUrl(theme?.brand_background_url),
    glass_opacity: normalizeNumberRange(theme?.glass_opacity, DEFAULT_THEME_SETTINGS.glass_opacity, 0.15, 0.9),
    glass_blur_px: normalizeNumberRange(theme?.glass_blur_px, DEFAULT_THEME_SETTINGS.glass_blur_px, 0, 36),
    radius_scale: normalizeNumberRange(theme?.radius_scale, DEFAULT_THEME_SETTINGS.radius_scale, 0.75, 1.5),
    font_scale: normalizeNumberRange(theme?.font_scale, DEFAULT_THEME_SETTINGS.font_scale, 0.85, 1.2),
    theme_density: normalizeThemeDensity(theme?.theme_density),
    glow_strength: normalizeNumberRange(theme?.glow_strength, DEFAULT_THEME_SETTINGS.glow_strength, 0, 1.6),
  };
}

function cssUrl(value: string) {
  return value ? `url("${value.replace(/"/g, '%22')}")` : 'none';
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
  targetRoot.style.setProperty('--brand-logo-url', cssUrl(normalizedTheme.brand_logo_url));
  targetRoot.style.setProperty('--brand-background-url', cssUrl(normalizedTheme.brand_background_url));
  targetRoot.style.setProperty('--glass-panel-opacity', String(normalizedTheme.glass_opacity));
  targetRoot.style.setProperty('--glass-panel-blur', `${normalizedTheme.glass_blur_px}px`);
  targetRoot.style.setProperty('--theme-radius-scale', String(normalizedTheme.radius_scale));
  targetRoot.style.setProperty('--theme-font-scale', String(normalizedTheme.font_scale));
  targetRoot.style.setProperty('--theme-glow-strength', String(normalizedTheme.glow_strength));
  targetRoot.dataset.themeDensity = normalizedTheme.theme_density;
  targetRoot.dataset.globalPerformanceMode = normalizedTheme.global_performance_mode ? 'true' : 'false';
  targetRoot.classList.toggle('shamrai-global-performance-mode', normalizedTheme.global_performance_mode);

  return normalizedTheme;
}
