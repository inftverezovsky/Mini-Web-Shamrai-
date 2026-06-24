import { describe, expect, it, vi } from 'vitest';
import {
  DEFAULT_THEME_SETTINGS,
  applyThemeSettingsToRoot,
  normalizedThemeSettings,
} from '../src/features/settings/themeSettings';
import {
  createCollapsedSectionState,
  setAllSectionsOpen,
  toggleSection,
} from '../src/features/settings/settingsAccordion';

function makeRoot() {
  const properties = new Map<string, string>();
  return {
    dataset: {} as Record<string, string>,
    style: {
      setProperty: vi.fn((key: string, value: string) => {
        properties.set(key, value);
      }),
    },
    classList: {
      toggle: vi.fn(),
    },
    properties,
  };
}

describe('settings theme manager helpers', () => {
  it('normalizes brand-kit theme settings with safe defaults', () => {
    const theme = normalizedThemeSettings({
      primary_color: '#ABCDEF',
      secondary_color: 'broken',
      brand_logo_url: 'https://cdn.example.com/logo.png',
      glass_opacity: 0.58,
      glass_blur_px: 22,
      radius_scale: 1.15,
      font_scale: 0.96,
      theme_density: 'compact',
      glow_strength: 1.2,
      global_performance_mode: true,
    });

    expect(theme.primary_color).toBe('#abcdef');
    expect(theme.secondary_color).toBe(DEFAULT_THEME_SETTINGS.secondary_color);
    expect(theme.brand_logo_url).toBe('https://cdn.example.com/logo.png');
    expect(theme.glass_opacity).toBe(0.58);
    expect(theme.glass_blur_px).toBe(22);
    expect(theme.theme_density).toBe('compact');
    expect(theme.global_performance_mode).toBe(true);
  });

  it('applies brand-kit values to root CSS variables and flags', () => {
    const root = makeRoot();

    applyThemeSettingsToRoot({
      primary_color: '#112233',
      secondary_color: '#445566',
      brand_logo_url: 'https://cdn.example.com/logo.png',
      brand_background_url: 'https://cdn.example.com/bg.webp',
      glass_opacity: 0.52,
      glass_blur_px: 24,
      radius_scale: 1.1,
      font_scale: 0.98,
      theme_density: 'cozy',
      glow_strength: 1.3,
      global_performance_mode: true,
    }, root as unknown as HTMLElement);

    expect(root.properties.get('--color-primary')).toBe('#112233');
    expect(root.properties.get('--brand-logo-url')).toBe('url("https://cdn.example.com/logo.png")');
    expect(root.properties.get('--brand-background-url')).toBe('url("https://cdn.example.com/bg.webp")');
    expect(root.properties.get('--glass-panel-opacity')).toBe('0.52');
    expect(root.properties.get('--glass-panel-blur')).toBe('24px');
    expect(root.properties.get('--theme-radius-scale')).toBe('1.1');
    expect(root.dataset.themeDensity).toBe('cozy');
    expect(root.classList.toggle).toHaveBeenCalledWith('shamrai-global-performance-mode', true);
  });
});

describe('settings accordion helpers', () => {
  it('starts with every section collapsed and toggles individual sections immutably', () => {
    const initial = createCollapsedSectionState(['telegram', 'vk']);
    const next = toggleSection(initial, 'telegram');

    expect(initial).toEqual({ telegram: false, vk: false });
    expect(next).toEqual({ telegram: true, vk: false });
    expect(next).not.toBe(initial);
  });

  it('opens or closes every section in the current tab', () => {
    expect(setAllSectionsOpen(['a', 'b'], true)).toEqual({ a: true, b: true });
    expect(setAllSectionsOpen(['a', 'b'], false)).toEqual({ a: false, b: false });
  });
});
