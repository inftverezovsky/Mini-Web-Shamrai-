import React from 'react';
import {
  MessageCircle,
  Newspaper,
  Settings2,
  SlidersHorizontal,
  TrendingUp,
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
  onPreloadTab?: (tab: BottomTabId) => void;
  showWebChat?: boolean;
}

interface TabConfig<T extends BottomTabId> {
  id: T;
  label: string;
  Icon: LucideIcon;
  tone?: 'cyan' | 'emerald' | 'violet' | 'gold' | 'rose';
}

export const userTabs: TabConfig<UserTabId>[] = [
  { id: 'feed', label: 'Лента', Icon: Newspaper, tone: 'emerald' },
  { id: 'chat', label: 'Чат', Icon: MessageCircle, tone: 'cyan' },
  { id: 'stats', label: 'Статистика', Icon: TrendingUp, tone: 'gold' },
  { id: 'profile', label: 'Профиль', Icon: UserIcon, tone: 'violet' },
];

export const adminTabs: TabConfig<AdminShellTabId>[] = [
  { id: 'manage_bets', label: 'Панель', Icon: Settings2, tone: 'emerald' },
  { id: 'stats', label: 'Статистика', Icon: TrendingUp, tone: 'gold' },
  { id: 'clients', label: 'Клиенты', Icon: Users, tone: 'cyan' },
  { id: 'settings', label: 'Настройки', Icon: SlidersHorizontal, tone: 'violet' },
  { id: 'profile', label: 'Профиль', Icon: UserIcon, tone: 'rose' },
];

export default function BottomNavigation({ role, activeTab, onChangeTab, onPreloadTab, showWebChat = false }: BottomNavigationProps) {
  const tabs = role === 'user'
    ? userTabs.filter((tab) => showWebChat || tab.id !== 'chat')
    : adminTabs;

  const getTabStyles = (isActive: boolean) => {
    if (isActive) {
      return {
        color: '#F8FBFF',
        textShadow: '0 1px 8px rgba(0, 0, 0, 0.72), 0 0 12px rgba(0, 210, 255, 0.22)',
      };
    }

    return {
      color: 'rgba(226, 238, 250, 0.72)',
    };
  };

  return (
    <nav
      className={`bottom-nav-aurora bottom-nav-aurora--${role} shimmer-border isolate flex min-w-0 items-center justify-around gap-1 rounded-2xl border border-white/10 px-2 py-2 shadow-glass backdrop-blur-xl transition-all duration-300`}
      aria-label={role === 'admin' ? 'Навигация администратора' : 'Навигация приложения'}
    >
      {tabs.map(({ id, label, Icon, tone = 'cyan' }) => {
        const isActive = activeTab === id;

        return (
          <button
            key={id}
            type="button"
            onClick={() => onChangeTab(id)}
            onPointerEnter={() => onPreloadTab?.(id)}
            onFocus={() => onPreloadTab?.(id)}
            onTouchStart={() => onPreloadTab?.(id)}
            aria-current={isActive ? 'page' : undefined}
            className={`bottom-nav-aurora__item bottom-nav-aurora__item--${tone} group smooth-pressable relative flex min-w-0 flex-1 flex-col items-center overflow-hidden rounded-xl px-1.5 py-1.5 transition-all duration-200 ${
              isActive ? 'bottom-nav-aurora__item--active' : ''
            }`}
            style={getTabStyles(isActive)}
          >
            {isActive && <span className="nav-glow-dot" aria-hidden="true" />}
            <Icon
              className={`h-[18px] w-[18px] transition-all duration-300 ${
                isActive ? 'iridescent-icon scale-110' : 'opacity-[0.55] group-hover:opacity-75'
              }`}
            />
            <span className="bottom-nav-aurora__label mt-1 text-xs font-extrabold tracking-normal opacity-100">
              {label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
