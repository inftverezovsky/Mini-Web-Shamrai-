import React from 'react';
import { PanelLeft } from 'lucide-react';
import { adminTabs, type AdminShellTabId, userTabs, type UserTabId } from './BottomNavigation';

type DesktopTabId = UserTabId | AdminShellTabId;

interface DesktopNavigationProps {
  role: 'user' | 'admin';
  activeTab: DesktopTabId;
  onChangeTab: (tab: DesktopTabId) => void;
  userLabel: string;
  roleLabelText: string;
  adminPreviewControl?: React.ReactNode;
  showWebChat?: boolean;
}

export default function DesktopNavigation({
  role,
  activeTab,
  onChangeTab,
  userLabel,
  roleLabelText,
  adminPreviewControl,
  showWebChat = false,
}: DesktopNavigationProps) {
  const tabs = role === 'admin'
    ? adminTabs
    : userTabs.filter((tab) => showWebChat || tab.id !== 'chat');

  return (
    <aside className="flex h-auto w-full shrink-0 flex-col justify-between rounded-3xl border border-white/10 bg-slate-950/58 p-4 shadow-glass backdrop-blur-2xl lg:sticky lg:top-6 lg:h-[calc(100vh-3rem)] lg:w-72">
      <div className="space-y-5">
        <div className="flex items-center gap-3 rounded-2xl border border-cyan-300/15 bg-cyan-300/[0.06] p-3">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-950/70 text-cyan-200 ring-1 ring-cyan-300/20">
            <PanelLeft className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-black text-white">{userLabel}</p>
            <p className="mt-0.5 text-[10px] font-black uppercase tracking-wider text-cyan-200/70">
              {roleLabelText}
            </p>
          </div>
        </div>

        <nav className="space-y-1.5">
          {tabs.map(({ id, label, Icon }) => {
            const active = activeTab === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => onChangeTab(id)}
                className={`flex w-full items-center gap-3 rounded-2xl border px-3.5 py-3 text-left text-sm font-extrabold transition-all ${
                  active
                    ? 'border-cyan-300/35 bg-cyan-300/[0.12] text-white shadow-neon-cyan'
                    : 'border-white/5 bg-white/[0.035] text-slate-400 hover:border-white/12 hover:bg-white/[0.055] hover:text-slate-200'
                }`}
              >
                <Icon className={`h-4 w-4 shrink-0 ${active ? 'text-cyan-200' : 'text-slate-500'}`} />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {adminPreviewControl && <div className="space-y-3">{adminPreviewControl}</div>}
    </aside>
  );
}
