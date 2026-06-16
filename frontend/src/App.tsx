import React, { Suspense, lazy, useEffect, useState } from 'react';
import { useTelegram } from './hooks/useTelegram';
import { useAuth } from './context/AuthContext';
import { useLayoutMode } from './context/LayoutModeContext';

import BottomNavigation from './components/BottomNavigation';
import type { AdminShellTabId, UserTabId } from './components/BottomNavigation';
import BrowserAuthScreen from './components/BrowserAuthScreen';
import DesktopNavigation from './components/DesktopNavigation';
import NotificationCenter from './components/NotificationCenter';
import PwaPushGate from './components/PwaPushGate';
import VkConsentWizard from './components/VkConsentWizard';
import WelcomeSplash from './components/WelcomeSplash';
import WebSignalListener from './components/WebSignalListener';
import AppErrorBoundary from './components/AppErrorBoundary';

import { isStaffRole, roleLabel } from './utils/roles';
import { buildTabPath, trackEvent, trackPageView } from './utils/analytics';
import { isTelegramMiniApp } from './utils/telegramSdk';
import { registerPwaServiceWorker } from './utils/webPush';

import {
  AlertTriangle,
  Eye,
  KeyRound,
  Loader2,
} from 'lucide-react';

const loadBetFeed = () => import('./pages/user/BetFeed');
const loadGlobalStats = () => import('./pages/user/GlobalStats');
const loadMyBets = () => import('./pages/user/MyBets');
const loadProfile = () => import('./pages/user/Profile');
const loadTariffs = () => import('./pages/user/Tariffs');
const loadOnboarding = () => import('./pages/user/Onboarding');
const loadWebBotChat = () => import('./components/WebBotChat');
const loadAdminDashboard = () => import('./pages/admin/AdminDashboard');
const loadAdminCRM = () => import('./pages/admin/AdminCRM');
const loadAdminSettings = () => import('./pages/admin/AdminSettings');
const loadAdminStats = () => import('./pages/admin/AdminStats');
const loadAdminBets = () => import('./pages/admin/AdminBets');
const loadAdminBroadcast = () => import('./pages/admin/AdminBroadcast');
const loadAdminResults = () => import('./pages/admin/AdminResults');

const BetFeed = lazy(loadBetFeed);
const GlobalStats = lazy(loadGlobalStats);
const MyBets = lazy(loadMyBets);
const Profile = lazy(loadProfile);
const Tariffs = lazy(loadTariffs);
const Onboarding = lazy(loadOnboarding);
const WebBotChat = lazy(loadWebBotChat);
const AdminDashboard = lazy(loadAdminDashboard);
const AdminCRM = lazy(loadAdminCRM);
const AdminSettings = lazy(loadAdminSettings);
const AdminStats = lazy(loadAdminStats);

function runWhenIdle(callback: () => void) {
  const requestIdleCallback = (window as any).requestIdleCallback as
    | ((cb: () => void, options?: { timeout?: number }) => number)
    | undefined;
  const cancelIdleCallback = (window as any).cancelIdleCallback as ((handle: number) => void) | undefined;
  if (typeof requestIdleCallback === 'function') {
    const handle = requestIdleCallback(callback, { timeout: 1400 });
    return () => cancelIdleCallback?.(handle);
  }
  const handle = window.setTimeout(callback, 120);
  return () => window.clearTimeout(handle);
}

function PageLoader() {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-3 text-slate-400">
      <Loader2 className="h-7 w-7 animate-spin text-emerald-400" />
      <span className="text-xs font-semibold">Загрузка раздела...</span>
    </div>
  );
}

export default function App() {
  const { isReady, isTelegram } = useTelegram();
  const { user: userProfile, loading, error, login: fetchUserProfile } = useAuth();
  const { isCompact } = useLayoutMode();

  const [activeUserTab, setActiveUserTab] = useState<UserTabId>('feed');
  const [activeAdminTab, setActiveAdminTab] = useState<AdminShellTabId>('manage_bets');
  const [adminPreviewMode, setAdminPreviewMode] = useState(false);
  const [splashLeaving, setSplashLeaving] = useState(false);
  const [introComplete, setIntroComplete] = useState(false);

  useEffect(() => {
    if (!introComplete) return;

    const root = document.documentElement;
    root.classList.add('shamrai-intro-complete');

    return () => {
      root.classList.remove('shamrai-intro-complete');
    };
  }, [introComplete]);

  useEffect(() => {
    if (!isReady || isTelegram || isTelegramMiniApp()) return;
    void registerPwaServiceWorker();
    const params = new URLSearchParams(window.location.search);
    if (params.get('open') === 'web-bot-chat') {
      setActiveUserTab('chat');
      window.setTimeout(() => document.getElementById('web-bot-chat')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 250);
    }
  }, [isReady, isTelegram]);

  const appReady = isReady && !loading;

  const handleIntroComplete = () => {
    setSplashLeaving(true);
    window.setTimeout(() => setIntroComplete(true), 180);
  };

  useEffect(() => {
    if (!introComplete || !userProfile) return;

    const userIsStaff = isStaffRole(userProfile.role);
    const runsInTelegramMiniApp = isTelegram || isTelegramMiniApp();
    const shouldPreloadWebChat = !runsInTelegramMiniApp && !userIsStaff && userProfile.is_onboarded !== false;
    const loaders = userIsStaff
      ? [
          loadAdminDashboard,
          loadAdminBets,
          loadAdminResults,
          loadAdminBroadcast,
          loadAdminStats,
          loadAdminCRM,
          loadAdminSettings,
          loadProfile,
        ]
      : [
          loadBetFeed,
          loadGlobalStats,
          loadMyBets,
          loadTariffs,
          loadProfile,
          ...(shouldPreloadWebChat ? [loadWebBotChat] : []),
          ...(userProfile.is_onboarded === false ? [loadOnboarding] : []),
        ];

    const queuedTimers: number[] = [];
    const cancelIdle = runWhenIdle(() => {
      loaders.forEach((loader, index) => {
        const timer = window.setTimeout(() => {
          void loader().catch(() => undefined);
        }, index * 70);
        queuedTimers.push(timer);
      });
    });

    return () => {
      cancelIdle();
      queuedTimers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [introComplete, isTelegram, userProfile]);

  const forceOnboarding = import.meta.env.DEV && new URLSearchParams(window.location.search).has('force_onboarding');
  const isAdmin = userProfile ? isStaffRole(userProfile.role) : false;
  const showAdminInterface = isAdmin && !adminPreviewMode;
  const runsInTelegramMiniApp = isTelegram || isTelegramMiniApp();
  const needsOnboarding = Boolean(
    userProfile
    && (forceOnboarding || userProfile.is_onboarded === false)
    && !isStaffRole(userProfile.role),
  );
  const showWebChatTab = Boolean(userProfile) && !runsInTelegramMiniApp && !showAdminInterface && !needsOnboarding;

  useEffect(() => {
    if (!introComplete || !userProfile) return;

    if (needsOnboarding) {
      trackPageView('/app/user/onboarding', {
        role: 'user',
        tab: 'onboarding',
        layout: isCompact ? 'compact' : 'full',
        onboarded: false,
      });
      return;
    }

    const role = showAdminInterface ? 'admin' : 'user';
    const tab = showAdminInterface
      ? activeAdminTab
      : activeUserTab === 'chat' && !showWebChatTab
        ? 'feed'
        : activeUserTab;

    trackPageView(buildTabPath(role, tab), {
      role,
      tab,
      layout: isCompact ? 'compact' : 'full',
      onboarded: userProfile.is_onboarded !== false,
    });
  }, [
    activeAdminTab,
    activeUserTab,
    introComplete,
    isCompact,
    needsOnboarding,
    showAdminInterface,
    showWebChatTab,
    userProfile,
  ]);

  if (!introComplete) {
    return (
      <WelcomeSplash
        appReady={appReady}
        leaving={splashLeaving}
        onIntroComplete={handleIntroComplete}
      />
    );
  }

  if (loading && !userProfile) {
    return (
      <div className="min-h-screen bg-[#070B19] px-4 py-8 text-slate-50">
        <PageLoader />
      </div>
    );
  }

  if (!userProfile) {
    return <BrowserAuthScreen />;
  }

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center space-y-4 bg-[#0F172A] px-6 text-center text-slate-50">
        <AlertTriangle className="h-16 w-16 text-rose-500" />
        <h1 className="text-lg font-bold text-white">Ошибка подключения</h1>
        <p className="max-w-xs text-xs text-slate-400">{error}</p>
        <button
          onClick={fetchUserProfile}
          className="rounded-xl bg-emerald-500 px-5 py-2.5 text-xs font-bold text-slate-950 shadow-neon-green transition-all active:scale-95"
        >
          Повторить попытку
        </button>
      </div>
    );
  }

  const handleTabChange = (tab: UserTabId | AdminShellTabId) => {
    const role = showAdminInterface ? 'admin' : 'user';
    const from = showAdminInterface ? activeAdminTab : activeUserTab;
    trackEvent('Tab Switch', { role, from, to: tab });

    if (showAdminInterface) {
      setActiveAdminTab(tab as AdminShellTabId);
    } else {
      setActiveUserTab(tab as UserTabId);
    }
  };

  const handleToggleAdminPreviewMode = () => {
    const nextMode = !adminPreviewMode;
    trackEvent('Admin Preview Toggle', {
      mode: nextMode ? 'client_preview' : 'admin',
    });
    setAdminPreviewMode(nextMode);
  };

  const isWebOnlyClient = userProfile.telegram_id < 0;
  const displayName = [userProfile.first_name, userProfile.last_name].filter(Boolean).join(' ').trim()
    || (userProfile.username ? `@${userProfile.username}` : isWebOnlyClient ? 'Web/VK клиент' : `ID ${userProfile.telegram_id}`);
  const roleText = isWebOnlyClient ? 'Web/VK клиент' : roleLabel(userProfile.role);

  const adminPreviewControl = isAdmin ? (
    <button
      type="button"
      onClick={handleToggleAdminPreviewMode}
      className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.045] px-3 py-3 text-xs font-black text-white transition-all hover:bg-white/[0.07] active:scale-[0.98]"
    >
      <Eye className="h-4 w-4 text-indigo-300" />
      <span>{adminPreviewMode ? 'Открыть админку' : 'Кабинет клиента'}</span>
    </button>
  ) : null;

  const renderCurrentPage = () => {
    const safeUserTab = activeUserTab === 'chat' && !showWebChatTab ? 'feed' : activeUserTab;
    const pageResetKey = showAdminInterface ? `admin:${activeAdminTab}` : `user:${safeUserTab}`;

    return (
      <AppErrorBoundary resetKey={pageResetKey}>
        <Suspense fallback={<PageLoader />}>
          {showAdminInterface ? (
            activeAdminTab === 'manage_bets' ? (
              <AdminDashboard />
            ) : activeAdminTab === 'stats' ? (
              <AdminStats />
            ) : activeAdminTab === 'clients' ? (
              <AdminCRM />
            ) : activeAdminTab === 'settings' ? (
              <AdminSettings />
            ) : (
              <Profile />
            )
          ) : safeUserTab === 'feed' ? (
            <BetFeed onNavigateToBilling={() => setActiveUserTab('billing')} />
          ) : safeUserTab === 'chat' ? (
            <WebBotChat />
          ) : safeUserTab === 'stats' ? (
            <GlobalStats />
          ) : safeUserTab === 'my_bets' ? (
            <MyBets />
          ) : safeUserTab === 'billing' ? (
            <Tariffs onSubscriptionActivated={fetchUserProfile} />
          ) : (
            <Profile />
          )}
        </Suspense>
      </AppErrorBoundary>
    );
  };

  return (
    <div
      className={`app-shell min-h-screen bg-[#070B19] relative overflow-x-hidden selection:bg-emerald-500/30 ${
        isCompact
          ? 'mobile-app-shell mx-auto flex max-w-md flex-col justify-between px-4 pt-4'
          : 'w-full px-5 py-6 xl:px-8'
      }`}
    >
      <AppErrorBoundary
        resetKey={`global:${userProfile.telegram_id}:${showWebChatTab}`}
        title="Фоновый виджет временно недоступен"
        description="Основные разделы продолжают работать, можно спокойно пользоваться приложением дальше."
      >
        <NotificationCenter />
        <WebSignalListener enabled={showWebChatTab} />
        <VkConsentWizard />
      </AppErrorBoundary>
      <div className="ambient-field" aria-hidden="true">
        <div className="ambient-field__grid" />
        <div className="ambient-field__rings" />
      </div>

      <PwaPushGate enabled={showWebChatTab}>
        {needsOnboarding ? (
          <div
            className={`z-10 flex flex-grow flex-col justify-center ${
              isCompact ? 'w-full' : 'mx-auto min-h-[calc(100vh-3rem)] w-full max-w-3xl'
            }`}
          >
            <AppErrorBoundary
              resetKey="onboarding"
              title="Анкета временно недоступна"
              description="Остальная часть приложения продолжит работать, а анкету можно попробовать открыть повторно."
            >
              <Suspense fallback={<PageLoader />}>
                <Onboarding onCompleted={fetchUserProfile} />
              </Suspense>
            </AppErrorBoundary>
          </div>
        ) : isCompact ? (
          <>
            <div className="z-10 flex w-full flex-grow flex-col justify-between">
              {isAdmin && (
                <div className="z-10 mb-4 flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 p-2.5 text-xs backdrop-blur-md">
                  <span className="flex items-center text-slate-300">
                    <KeyRound className="mr-1.5 h-4 w-4 shrink-0 text-rose-400" />
                    Вы вошли как <strong className="ml-1 text-rose-400">{roleLabel(userProfile.role)}</strong>
                  </span>
                  <button
                    onClick={handleToggleAdminPreviewMode}
                    className="flex items-center space-x-1 rounded-xl border border-white/10 bg-white/10 px-2.5 py-1 text-[10px] text-white transition-all hover:bg-white/15 active:scale-95"
                  >
                    <Eye className="h-3.5 w-3.5 text-indigo-400" />
                    <span>{adminPreviewMode ? 'Админка' : 'Кабинет юзера'}</span>
                  </button>
                </div>
              )}

              <main className="flex-grow">{renderCurrentPage()}</main>
            </div>

            <BottomNavigation
              role={showAdminInterface ? 'admin' : 'user'}
              activeTab={showAdminInterface ? activeAdminTab : activeUserTab}
              onChangeTab={handleTabChange}
              showWebChat={showWebChatTab}
            />
          </>
        ) : (
          <div className="relative z-10 mx-auto flex w-full max-w-[1480px] flex-col items-stretch gap-5 lg:flex-row lg:items-start">
            <DesktopNavigation
              role={showAdminInterface ? 'admin' : 'user'}
              activeTab={showAdminInterface ? activeAdminTab : activeUserTab}
              onChangeTab={handleTabChange}
              userLabel={displayName}
              roleLabelText={roleText}
              adminPreviewControl={adminPreviewControl}
              showWebChat={showWebChatTab}
            />

            <div className="min-w-0 flex-1">
              <header className="mb-5 flex items-center justify-between gap-4 rounded-3xl border border-white/10 bg-slate-950/45 px-4 py-3 shadow-glass backdrop-blur-2xl">
                <div className="min-w-0">
                  <p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-200/70">
                    Shamrai
                  </p>
                  <h1 className="mt-1 truncate text-xl font-black text-white">{displayName}</h1>
                </div>
              </header>

              <main className="min-h-[calc(100vh-8.25rem)] rounded-3xl border border-white/10 bg-slate-950/28 p-4 shadow-glass backdrop-blur-sm xl:p-5">
                {renderCurrentPage()}
              </main>
            </div>
          </div>
        )}
      </PwaPushGate>
    </div>
  );
}
