import React, { createContext, useContext, useEffect, useState } from 'react';
import { getTelegramWebApp, TELEGRAM_SDK_READY_EVENT } from '../utils/telegramSdk';

export type EffectiveLayoutMode = 'compact' | 'full';

interface LayoutModeContextType {
  effectiveMode: EffectiveLayoutMode;
  isCompact: boolean;
}

const LEGACY_STORAGE_KEY = 'shamrai_layout_mode';
const LayoutModeContext = createContext<LayoutModeContextType | undefined>(undefined);

export function LayoutModeProvider({ children }: { children: React.ReactNode }) {
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth);
  const [, setTelegramSignal] = useState(0);

  useEffect(() => {
    const handleResize = () => setViewportWidth(window.innerWidth);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    localStorage.removeItem(LEGACY_STORAGE_KEY);

    const handleTelegramReady = () => setTelegramSignal((value) => value + 1);
    window.addEventListener(TELEGRAM_SDK_READY_EVENT, handleTelegramReady);
    return () => window.removeEventListener(TELEGRAM_SDK_READY_EVENT, handleTelegramReady);
  }, []);

  const isTelegram = Boolean(getTelegramWebApp<{ initData?: string }>()?.initData);
  const effectiveMode: EffectiveLayoutMode = isTelegram || viewportWidth < 960 ? 'compact' : 'full';

  useEffect(() => {
    document.documentElement.dataset.shamraiLayout = effectiveMode;
  }, [effectiveMode]);

  return (
    <LayoutModeContext.Provider
      value={{
        effectiveMode,
        isCompact: effectiveMode === 'compact',
      }}
    >
      {children}
    </LayoutModeContext.Provider>
  );
}

export function useLayoutMode() {
  const context = useContext(LayoutModeContext);
  if (!context) {
    throw new Error('useLayoutMode must be used within a LayoutModeProvider');
  }
  return context;
}
