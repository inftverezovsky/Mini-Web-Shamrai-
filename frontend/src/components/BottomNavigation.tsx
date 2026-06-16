import React from 'react';
import {
  MessageCircle,
  Newspaper,
  Settings2,
  SlidersHorizontal,
  TrendingUp,
  Trophy,
  User as UserIcon,
  Users,
  type LucideIcon,
} from 'lucide-react';

export type UserTabId = 'feed' | 'chat' | 'stats' | 'my_bets' | 'profile' | 'billing';
export type AdminShellTabId = 'manage_bets' | 'stats' | 'clients' | 'settings' | 'profile';
type BottomTabId = UserTabId | AdminShellTabId;

interface BottomNavigationProps {
  role: 'user' | 'admin';
  activeTab: BottomTabId;
  onChangeTab: (tab: BottomTabId) => void;
  showWebChat?: boolean;
}

interface TabConfig<T extends BottomTabId> {
  id: T;
  label: string;
  Icon: LucideIcon;
}

export const userTabs: TabConfig<UserTabId>[] = [
  { id: 'feed', label: 'Лента', Icon: Newspaper },
  { id: 'chat', label: 'ЧАТ', Icon: MessageCircle },
  { id: 'stats', label: 'Статистика', Icon: TrendingUp },
  { id: 'my_bets', label: 'Мои ставки', Icon: Trophy },
  { id: 'profile', label: 'Профиль', Icon: UserIcon },
];

export const adminTabs: TabConfig<AdminShellTabId>[] = [
  { id: 'manage_bets', label: 'Панель', Icon: Settings2 },
  { id: 'stats', label: 'Статистика', Icon: TrendingUp },
  { id: 'clients', label: 'Клиенты', Icon: Users },
  { id: 'settings', label: 'Настройки', Icon: SlidersHorizontal },
  { id: 'profile', label: 'Профиль', Icon: UserIcon },
];

export default function BottomNavigation({ role, activeTab, onChangeTab, showWebChat = false }: BottomNavigationProps) {
  const tabs = role === 'user'
    ? userTabs.filter((tab) => showWebChat || tab.id !== 'chat')
    : adminTabs;

  const getTabStyles = (isActive: boolean) => {
    if (isActive) {
      return {
        color: 'var(--tg-theme-button-color, #10B981)',
        textShadow: '0 0 10px rgba(16, 185, 129, 0.26)',
      };
    }

    return {
      color: 'var(--tg-theme-hint-color, rgba(255, 255, 255, 0.3))',
    };
  };

  return (
    <nav className="bottom-nav-aurora shimmer-border fixed bottom-4 left-4 right-4 isolate mx-auto flex max-w-md items-center justify-around gap-1 rounded-3xl border border-white/10 px-2.5 py-2.5 shadow-glass backdrop-blur-xl transition-all duration-300 z-45">
      {tabs.map(({ id, label, Icon }) => {
        const isActive = activeTab === id;

        return (
          <button
            key={id}
            onClick={() => onChangeTab(id)}
            className={`bottom-nav-aurora__item group relative flex min-w-0 flex-1 flex-col items-center overflow-hidden rounded-2xl py-1.5 transition-all duration-300 hover:scale-105 active:scale-95 ${
              isActive ? 'bottom-nav-aurora__item--active' : ''
            }`}
            style={getTabStyles(isActive)}
          >
            {isActive && <span className="nav-glow-dot" aria-hidden="true" />}
            <Icon
              className={`h-5 w-5 transition-all duration-300 ${
                isActive ? 'iridescent-icon scale-110' : 'opacity-[0.55] group-hover:opacity-75'
              }`}
            />
            <span className="mt-1 max-w-full truncate text-[8.5px] font-extrabold tracking-wide opacity-80">
              {label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
