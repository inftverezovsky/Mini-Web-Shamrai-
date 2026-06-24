import React from 'react';
import { PanelLeft } from 'lucide-react';
import { adminTabs, type AdminShellTabId, visibleUserTabs, type UserTabId } from './BottomNavigation';
import OptimizedImage from './OptimizedImage';

type DesktopTabId = UserTabId | AdminShellTabId;

interface DesktopNavigationProps {
  role: 'user' | 'admin';
  activeTab: DesktopTabId;
  onChangeTab: (tab: DesktopTabId) => void;
  onPreloadTab?: (tab: DesktopTabId) => void;
  userLabel: string;
  roleLabelText: string;
  adminPreviewControl?: React.ReactNode;
  showWebChat?: boolean;
}

export default function DesktopNavigation({
  role,
  activeTab,
  onChangeTab,
  onPreloadTab,
  userLabel,
  roleLabelText,
  adminPreviewControl,
  showWebChat = false,
}: DesktopNavigationProps) {
  const tabs = role === 'admin'
    ? adminTabs
    : visibleUserTabs.filter((tab) => showWebChat || tab.id !== 'chat');

  return (
    <aside className="dashboard-blur-root desktop-navigation-panel shamrai-glass-panel flex h-auto w-full shrink-0 flex-col justify-between rounded-2xl p-3 lg:sticky lg:top-4 lg:h-[calc(100vh-2rem)] lg:w-72">
      <div className="space-y-3.5">
        <div className="shamrai-glass-card flex items-center gap-2.5 rounded-xl p-2.5">
          <div className="relative grid h-9 w-9 place-items-center overflow-hidden rounded-xl border border-cyan-200/20 text-cyan-200">
            <OptimizedImage
              src="/brand-logo-poster.jpg"
              webpSrc="/brand-logo-poster.webp"
              alt=""
              className="absolute inset-0 h-full w-full object-cover opacity-70"
            />
            <div className="absolute inset-0 bg-slate-950/34" />
            <PanelLeft className="relative h-4 w-4 drop-shadow-[0_0_8px_rgba(0,210,255,0.65)]" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-black text-white">{userLabel}</p>
            <p className="mt-0.5 text-xs font-semibold tracking-normal text-cyan-200/80">
              {roleLabelText}
            </p>
          </div>
        </div>

        <nav className="space-y-1" aria-label={role === 'admin' ? 'Навигация администратора' : 'Навигация приложения'}>
          {tabs.map(({ id, label, Icon, tone = 'cyan' }) => {
            const active = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => onChangeTab(id)}
                onMouseEnter={() => onPreloadTab?.(id)}
                onPointerEnter={() => onPreloadTab?.(id)}
                onFocus={() => onPreloadTab?.(id)}
                onTouchStart={() => onPreloadTab?.(id)}
                aria-current={active ? 'page' : undefined}
                className={`flex w-full items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-sm font-bold transition-all ${
                  active
                    ? `shamrai-glass-button shamrai-glass-button--${tone} text-white shadow-neon-cyan`
                    : 'shamrai-glass-card text-slate-400 hover:text-slate-200'
                }`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${active ? 'text-cyan-200' : 'text-slate-500'}`} />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {adminPreviewControl && <div className="space-y-2">{adminPreviewControl}</div>}
    </aside>
  );
}
