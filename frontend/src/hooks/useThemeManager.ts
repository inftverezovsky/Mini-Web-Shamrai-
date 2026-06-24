import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';

import { apiFetch } from '../utils/api';
import {
  DEFAULT_THEME_SETTINGS,
  PUBLIC_THEME_QUERY_KEY,
  applyThemeSettingsToRoot,
  type PublicThemeSettingsResponse,
} from '../features/settings/themeSettings';

export function useThemeManager() {
  const themeQuery = useQuery({
    queryKey: PUBLIC_THEME_QUERY_KEY,
    queryFn: () => apiFetch<PublicThemeSettingsResponse>('/settings/theme'),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    applyThemeSettingsToRoot(themeQuery.data || DEFAULT_THEME_SETTINGS);
  }, [themeQuery.data]);

  return themeQuery;
}
