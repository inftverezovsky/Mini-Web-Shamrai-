import React, { Suspense, lazy, useEffect, useState, useTransition } from 'react';
import { motion, MotionConfig, useReducedMotion } from 'framer-motion';
import { useTelegram } from './hooks/useTelegram';
import { useAuthActions, useAuthSelector } from './context/AuthContext';
import { useLayoutMode } from './context/LayoutModeContext';
import { usePerformanceProfile } from './hooks/usePerformanceProfile';

import BottomNavigation from './components/BottomNavigation';
import type { AdminShellTabId, UserTabId } from './components/BottomNavigation';
import BrowserAuthScreen from './components/BrowserAuthScreen';
import DesktopNavigation from './components/DesktopNavigation';
import IdentityLinkGate from './components/IdentityLinkGate';
import NotificationCenter from './components/NotificationCenter';
import PwaPushGate from './components/PwaPushGate';
import VkConsentWizard from './components/VkConsentWizard';
import WelcomeSplash from './components/WelcomeSplash';
import WebSignalListener from './components/WebSignalListener';
import AdminWebChatListener from './components/AdminWebChatListener';
import AppErrorBoundary from './components/AppErrorBoundary';
import LogoText from './components/LogoText';

import { isStaffRole, roleLabel } from './utils/roles';
import { buildTabPath, trackEvent, trackPageView } from './utils/analytics';
import { hasTelegramLaunchParams, isTelegramMiniApp } from './utils/telegramSdk';
import { registerPwaServiceWorker } from './utils/webPush';

import {
  AlertTriangle,
  Eye,
  KeyRound,
  Loader2,
  MessageCircle,
  X,
} from 'lucide-react';

const loadBetFeed = () => import('./pages/user/BetFeed');
const loadMyBets = () => import('./pages/user/MyBets');
const loadProfile = () => import('./pages/user/Profile');
const loadTariffs = () => import('./pages/user/Tariffs');
const loadOnboarding = () => import('./pages/user/Onboarding');
const loadWebBotChat = () => import('./components/WebBotChat');
const loadAdminDashboard = () => import('./pages/admin/AdminDashboard');
const loadAdminCRM = () => import('./pages/admin/AdminCRM');
const loadAdminWebChat = () => import('./pages/admin/AdminWebChat');
const loadAdminSettings = () => import('./pages/admin/AdminSettings');
const loadAdminStats = () => import('./pages/admin/AdminStats');
const loadAdminBets = () => import('./pages/admin/AdminBets');
const loadAdminBroadcast = () => import('./pages/admin/AdminBroadcast');
const loadAdminResults = () => import('./pages/admin/AdminResults');

const BetFeed = lazy(loadBetFeed);
const MyBets = lazy(loadMyBets);
const Profile = lazy(loadProfile);
const Tariffs = lazy(loadTariffs);
const Onboarding = lazy(loadOnboarding);
const WebBotChat = lazy(loadWebBotChat);
const AdminDashboard = lazy(loadAdminDashboard);
const AdminCRM = lazy(loadAdminCRM);
const AdminWebChat = lazy(loadAdminWebChat);
const AdminSettings = lazy(loadAdminSettings);
const AdminStats = lazy(loadAdminStats);

const userTabLoaders: Partial<Record<UserTabId, () => Promise<unknown>>> = {
  feed: loadBetFeed,
  chat: loadWebBotChat,
  stats: loadMyBets,
  my_bets: loadMyBets,
  billing: loadTariffs,
  profile: loadProfile,
};

const adminTabLoaders: Partial<Record<AdminShellTabId, () => Promise<unknown>>> = {
  manage_bets: loadAdminDashboard,
  stats: loadAdminStats,
  clients: loadAdminCRM,
  chats: loadAdminWebChat,
  settings: loadAdminSettings,
  profile: loadProfile,
};


function runWhenIdle(
  callback: () => void,
  options: { timeout?: number; fallbackDelay?: number } = {},
) {
  const timeout = options.timeout ?? 1400;
  const fallbackDelay = options.fallbackDelay ?? 120;
  const requestIdleCallback = (window as any).requestIdleCallback as
    | ((cb: () => void, options?: { timeout?: number }) => number)
    | undefined;
  const cancelIdleCallback = (window as any).cancelIdleCallback as ((handle: number) => void) | undefined;
  if (typeof requestIdleCallback === 'function') {
    const handle = requestIdleCallback(callback, { timeout });
    return () => cancelIdleCallback?.(handle);
  }
  const handle = window.setTimeout(callback, fallbackDelay);
  return () => window.clearTimeout(handle);
}

function PageSkeleton() {
  return (
    <div
      className="grid min-h-[50vh] gap-3 p-1 opacity-80 [animation:pulse_1.5s_ease-in-out_infinite]"
      aria-label="Загрузка раздела"
    >
      <div className="shamrai-glass-card h-24 rounded-2xl border-white/10 bg-white/[0.045]" />
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="shamrai-glass-card h-40 rounded-2xl border-cyan-200/10 bg-cyan-200/[0.045]" />
        <div className="shamrai-glass-card h-40 rounded-2xl border-fuchsia-200/10 bg-fuchsia-200/[0.04]" />
      </div>
      <div className="shamrai-glass-card h-28 rounded-2xl border-emerald-200/10 bg-emerald-200/[0.04]" />
    </div>
  );
}

interface TelegramLinkPromptProps {
  userId: number;
  vkUserId: string | null;
  onLinkTelegram: () => Promise<void>;
}

function TelegramLinkPrompt({ userId, vkUserId, onLinkTelegram }: TelegramLinkPromptProps) {
  const storageKey = `shamrai_telegram_link_prompt_dismissed:${userId}:${vkUserId || 'vk'}`;
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(storageKey) === '1');
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDismissed(localStorage.getItem(storageKey) === '1');
    setError(null);
  }, [storageKey]);

  if (dismissed) return null;

  const handleDismiss = () => {
    localStorage.setItem(storageKey, '1');
    setDismissed(true);
    trackEvent('Telegram Link Prompt Dismissed', { source: 'vk_web_only_banner' });
  };

  const handleLink = async () => {
    try {
      setLinking(true);
      setError(null);
      trackEvent('Telegram Link Started', { source: 'vk_web_only_banner' });
      await onLinkTelegram();
      trackEvent('Telegram Link Confirmed', { source: 'vk_web_only_banner' });
    } catch (err: any) {
      setError(err?.message || 'Не удалось привязать Telegram. Попробуйте еще раз.');
      trackEvent('Telegram Link Failed', { source: 'vk_web_only_banner' });
    } finally {
      setLinking(false);
    }
  };

  return (
    <section className="shamrai-glass-card relative z-10 mb-3 rounded-2xl p-3.5">
      <button
        type="button"
        onClick={handleDismiss}
        className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-xl border border-white/10 bg-white/5 text-slate-300 transition hover:bg-white/10"
        aria-label="Скрыть предложение привязать Telegram"
      >
        <X className="h-4 w-4" />
      </button>

      <div className="flex items-start gap-3 pr-8">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl border border-[#24a1de]/35 bg-[#24a1de]/16 text-cyan-100">
          <MessageCircle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-black leading-tight text-white">
            Подключите Telegram к этому кабинету
          </h2>
          <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-300">
            Вы вошли через VK ID. Telegram можно привязать сейчас, а баланс, тарифы и настройки останутся здесь.
          </p>
        </div>
      </div>

      <button
        type="button"
        onClick={handleLink}
        disabled={linking}
        className="shamrai-glass-button mt-3 flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl px-3 py-2 text-xs font-black text-white transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
      >
        {linking ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageCircle className="h-4 w-4" />}
        <span>{linking ? 'Ждем подтверждение...' : 'Привязать Telegram'}</span>
      </button>

      {error && (
        <p className="mt-3 rounded-xl border border-rose-300/15 bg-rose-500/10 px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-rose-100">
          {error}
        </p>
      )}
    </section>
  );
}

export default function App() {
  const { isReady, isTelegram } = useTelegram();
  const userProfile = useAuthSelector((state) => state.user);
  const loading = useAuthSelector((state) => state.loading);
  const error = useAuthSelector((state) => state.error);
  const {
    login: fetchUserProfile,
    loginWithTelegramBot,
  } = useAuthActions();
  const { isCompact } = useLayoutMode();
  const performanceProfile = usePerformanceProfile();
  const reduceMotion = useReducedMotion();
  const reducePageMotion = reduceMotion || performanceProfile.shouldReduceMotion;
  const motionReducedMode = performanceProfile.shouldReduceMotion ? 'always' : 'user';
  const [, startTabTransition] = useTransition();

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
    if (!isReady || isTelegram || isTelegramMiniApp() || hasTelegramLaunchParams()) return;
    void registerPwaServiceWorker();
    const params = new URLSearchParams(window.location.search);
    if (params.get('open') === 'web-bot-chat' || params.get('open') === 'web-chat') {
      setActiveUserTab('chat');
      window.setTimeout(() => document.getElementById('web-bot-chat')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 250);
    } else if (params.get('open') === 'admin-web-chat') {
      setActiveAdminTab('chats');
    }
  }, [isReady, isTelegram]);

  const appReady = isReady && !loading;

  const handleIntroComplete = () => {
    setSplashLeaving(true);
    window.setTimeout(() => setIntroComplete(true), 180);
  };

  useEffect(() => {
    if (!introComplete || !userProfile || !performanceProfile.isAppVisible) return;

    const userIsStaff = isStaffRole(userProfile.role);
    const runsInTelegramMiniApp = isTelegram || isTelegramMiniApp() || hasTelegramLaunchParams();
    const shouldPreloadWebChat = !runsInTelegramMiniApp && !userIsStaff && userProfile.is_onboarded !== false;
    const fullLoaders = userIsStaff
      ? [
          loadAdminDashboard,
          loadAdminBets,
          loadAdminResults,
          loadAdminBroadcast,
          loadAdminStats,
          loadAdminCRM,
          loadAdminWebChat,
          loadAdminSettings,
          loadProfile,
        ]
      : [
          loadBetFeed,
          loadMyBets,
          loadTariffs,
          loadProfile,
          ...(shouldPreloadWebChat ? [loadWebBotChat] : []),
          ...(userProfile.is_onboarded === false ? [loadOnboarding] : []),
        ];
    const lightLoaders = userIsStaff
      ? [loadAdminStats]
      : userProfile.is_onboarded === false
        ? [loadOnboarding]
        : [loadMyBets];
    const loaders = performanceProfile.canBulkPreload ? fullLoaders : lightLoaders;
    const staggerMs = performanceProfile.canBulkPreload ? 70 : performanceProfile.isBalanced ? 220 : 420;
    const idleOptions = performanceProfile.canBulkPreload
      ? { timeout: 1400, fallbackDelay: 120 }
      : { timeout: 2600, fallbackDelay: 900 };

    const queuedTimers: number[] = [];
    const cancelIdle = runWhenIdle(() => {
      loaders.forEach((loader, index) => {
        const timer = window.setTimeout(() => {
          if (document.visibilityState === 'hidden') return;
          void loader().catch(() => undefined);
        }, index * staggerMs);
        queuedTimers.push(timer);
      });
    }, idleOptions);

    return () => {
      cancelIdle();
      queuedTimers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [
    introComplete,
    isTelegram,
    performanceProfile.canBulkPreload,
    performanceProfile.isAppVisible,
    performanceProfile.isBalanced,
    userProfile,
  ]);

  const forceOnboarding = import.meta.env.DEV && new URLSearchParams(window.location.search).has('force_onboarding');
  const isAdmin = userProfile ? isStaffRole(userProfile.role) : false;
  const showAdminInterface = isAdmin && !adminPreviewMode;
  const runsInTelegramMiniApp = isTelegram || isTelegramMiniApp() || hasTelegramLaunchParams();
  const requiresIdentityGate = Boolean(
    userProfile
    && !isStaffRole(userProfile.role)
    && userProfile.identity_complete === false,
  );
  const needsOnboarding = Boolean(
    userProfile
    && (forceOnboarding || userProfile.is_onboarded === false)
    && !isStaffRole(userProfile.role)
    && !requiresIdentityGate,
  );
  const showWebChatTab = Boolean(userProfile)
    && !runsInTelegramMiniApp
    && !showAdminInterface
    && !needsOnboarding
    && !requiresIdentityGate;

  useEffect(() => {
    if (!introComplete || !userProfile) return;

    if (requiresIdentityGate) {
      trackPageView('/app/user/identity', {
        role: 'user',
        tab: 'identity',
        layout: isCompact ? 'compact' : 'full',
        onboarded: userProfile.is_onboarded !== false,
      });
      return;
    }

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
    requiresIdentityGate,
    showAdminInterface,
    showWebChatTab,
    userProfile,
  ]);

  if (!introComplete) {
    return (
      <MotionConfig reducedMotion={motionReducedMode}>
        <WelcomeSplash
          appReady={appReady}
          leaving={splashLeaving}
          allowVideo={performanceProfile.canPlayIntroVideo}
          onIntroComplete={handleIntroComplete}
        />
      </MotionConfig>
    );
  }

  if (loading && !userProfile) {
    return (
      <MotionConfig reducedMotion={motionReducedMode}>
        <div className="app-shell compact-ui min-h-[100dvh] px-4 py-8 text-slate-50">
          <PageSkeleton />
        </div>
      </MotionConfig>
    );
  }

  if (!userProfile) {
    return (
      <MotionConfig reducedMotion={motionReducedMode}>
        <BrowserAuthScreen />
      </MotionConfig>
    );
  }

  if (error && !requiresIdentityGate) {
    return (
      <MotionConfig reducedMotion={motionReducedMode}>
        <div className="app-shell compact-ui flex min-h-[100dvh] flex-col items-center justify-center space-y-4 px-6 text-center text-slate-50">
          <AlertTriangle className="h-16 w-16 text-rose-500" />
          <h1 className="text-lg font-bold text-white">Ошибка подключения</h1>
          <p className="max-w-xs text-xs text-slate-400">{error}</p>
          <button
            onClick={fetchUserProfile}
            className="shamrai-glass-button rounded-xl px-5 py-2.5 text-xs font-bold text-white transition-all active:scale-95"
          >
            Повторить попытку
          </button>
        </div>
      </MotionConfig>
    );
  }

  if (requiresIdentityGate) {
    return (
      <MotionConfig reducedMotion={motionReducedMode}>
        <div
          className={`app-shell compact-ui relative isolate min-h-[100dvh] overflow-x-hidden selection:bg-pink-500/30 ${
            isCompact ? 'mobile-app-shell w-full max-w-none min-w-0 px-3 pt-3' : 'w-full px-3 py-3 sm:px-4 sm:py-4 xl:px-6'
          }`}
        >
          <div className="ambient-field z-0 isolate transform-gpu will-change-transform" aria-hidden="true">
            <div className="ambient-field__grid" />
            <div className="ambient-field__rings" />
          </div>
          <IdentityLinkGate />
        </div>
      </MotionConfig>
    );
  }

  const preloadTab = (tab: UserTabId | AdminShellTabId) => {
    if (!performanceProfile.isAppVisible || !performanceProfile.canPreloadOnIntent) return;
    const loader = showAdminInterface
      ? adminTabLoaders[tab as AdminShellTabId]
      : userTabLoaders[tab as UserTabId];
    void loader?.().catch(() => undefined);
  };

  const handleTabChange = (tab: UserTabId | AdminShellTabId) => {
    const role = showAdminInterface ? 'admin' : 'user';
    const from = showAdminInterface ? activeAdminTab : activeUserTab;
    preloadTab(tab);
    trackEvent('Tab Switch', { role, from, to: tab });

    startTabTransition(() => {
      if (showAdminInterface) {
        setActiveAdminTab(tab as AdminShellTabId);
      } else {
        setActiveUserTab(tab as UserTabId);
      }
    });
  };

  const handleToggleAdminPreviewMode = () => {
    const nextMode = !adminPreviewMode;
    trackEvent('Admin Preview Toggle', {
      mode: nextMode ? 'client_preview' : 'admin',
    });
    setAdminPreviewMode(nextMode);
  };

  const isWebOnlyClient = userProfile.telegram_id < 0;
  const showTelegramLinkPrompt = Boolean(
    isWebOnlyClient
    && userProfile.is_web_only
    && userProfile.vk_user_id
    && !runsInTelegramMiniApp
    && !isStaffRole(userProfile.role)
    && !requiresIdentityGate,
  );
  const telegramLinkPrompt = showTelegramLinkPrompt ? (
    <TelegramLinkPrompt
      userId={userProfile.telegram_id}
      vkUserId={userProfile.vk_user_id}
      onLinkTelegram={loginWithTelegramBot}
    />
  ) : null;
  const displayName = [userProfile.first_name, userProfile.last_name].filter(Boolean).join(' ').trim()
    || (userProfile.username ? `@${userProfile.username}` : isWebOnlyClient ? 'Web/VK клиент' : `ID ${userProfile.telegram_id}`);
  const roleText = isWebOnlyClient ? 'Web/VK клиент' : roleLabel(userProfile.role);

  const adminPreviewControl = isAdmin ? (
    <button
      type="button"
      onClick={handleToggleAdminPreviewMode}
      className="shamrai-glass-button flex w-full items-center justify-center gap-2 rounded-2xl px-3 py-3 text-xs font-black text-white transition-all active:scale-[0.98]"
    >
      <Eye className="h-4 w-4 text-indigo-300" />
      <span>{adminPreviewMode ? 'Открыть админку' : 'Кабинет клиента'}</span>
    </button>
  ) : null;

  const renderCurrentPage = () => {
    const safeUserTab = activeUserTab === 'chat' && !showWebChatTab ? 'feed' : activeUserTab;
    const pageResetKey = showAdminInterface ? `admin:${activeAdminTab}` : `user:${safeUserTab}`;

    const page = showAdminInterface ? (
      activeAdminTab === 'manage_bets' ? (
        <AdminDashboard />
      ) : activeAdminTab === 'stats' ? (
        <AdminStats />
      ) : activeAdminTab === 'clients' ? (
        <AdminCRM />
      ) : activeAdminTab === 'chats' ? (
        <AdminWebChat />
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
      <MyBets />
    ) : safeUserTab === 'my_bets' ? (
      <MyBets />
    ) : safeUserTab === 'billing' ? (
      <Tariffs onSubscriptionActivated={fetchUserProfile} />
    ) : (
      <Profile />
    );

    return (
      <AppErrorBoundary resetKey={pageResetKey}>
        <Suspense fallback={<PageSkeleton />}>
          <motion.div
            key={pageResetKey}
            className="page-transition-layer"
            initial={reducePageMotion ? false : { opacity: 0, y: 4 }}
            animate={reducePageMotion ? undefined : { opacity: 1, y: 0 }}
            transition={{ duration: 0.1, ease: 'easeOut' }}
          >
            {page}
          </motion.div>
        </Suspense>
      </AppErrorBoundary>
    );
  };

  return (
    <MotionConfig reducedMotion={motionReducedMode}>
      <div
        className={`app-shell compact-ui relative isolate min-h-[100dvh] overflow-x-hidden selection:bg-pink-500/30 ${
          isCompact
            ? 'mobile-app-shell flex w-full max-w-none min-w-0 flex-col justify-between px-3 pt-3'
            : 'w-full px-3 py-3 sm:px-4 sm:py-4 xl:px-6'
        }`}
      >
      <AppErrorBoundary
        resetKey={`global:${userProfile.telegram_id}:${showWebChatTab}`}
        title="Фоновый виджет временно недоступен"
        description="Основные разделы продолжают работать, можно спокойно пользоваться приложением дальше."
      >
        <NotificationCenter />
        <WebSignalListener enabled={showWebChatTab} />
        <AdminWebChatListener enabled={showAdminInterface} />
        <VkConsentWizard />
      </AppErrorBoundary>
      <div className="ambient-field z-0 isolate transform-gpu will-change-transform" aria-hidden="true">
        <div className="ambient-field__grid" />
        <div className="ambient-field__rings" />
      </div>

      <PwaPushGate enabled={showWebChatTab}>
        {needsOnboarding ? (
          <div
            className={`z-10 flex flex-grow flex-col justify-center ${
              isCompact ? 'w-full' : 'mx-auto min-h-[calc(100dvh-3rem)] w-full max-w-3xl'
            }`}
          >
            {telegramLinkPrompt}
            <AppErrorBoundary
              resetKey="onboarding"
              title="Анкета временно недоступна"
              description="Остальная часть приложения продолжит работать, а анкету можно попробовать открыть повторно."
            >
              <Suspense fallback={<PageSkeleton />}>
                <Onboarding onCompleted={fetchUserProfile} />
              </Suspense>
            </AppErrorBoundary>
          </div>
        ) : isCompact ? (
          <>
            <div className="z-10 flex w-full flex-grow flex-col justify-between">
              {isAdmin && (
                <div className="shamrai-glass-panel z-10 mb-3 flex transform-gpu items-center justify-between rounded-xl p-2 text-[11px] will-change-transform">
                  <span className="flex items-center text-slate-300">
                    <KeyRound className="mr-1.5 h-4 w-4 shrink-0 text-rose-400" />
                    Вы вошли как <strong className="ml-1 text-rose-400">{roleLabel(userProfile.role)}</strong>
                  </span>
                  <button
                    onClick={handleToggleAdminPreviewMode}
                    className="shamrai-glass-button flex transform-gpu items-center space-x-1 rounded-xl px-2.5 py-1 text-[10px] text-white transition-all will-change-transform active:scale-95"
                  >
                    <Eye className="h-3.5 w-3.5 text-indigo-400" />
                    <span>{adminPreviewMode ? 'Админка' : 'Кабинет юзера'}</span>
                  </button>
                </div>
              )}

              {telegramLinkPrompt}
              <main className="flex-grow">{renderCurrentPage()}</main>
            </div>

            <BottomNavigation
              role={showAdminInterface ? 'admin' : 'user'}
              activeTab={showAdminInterface ? activeAdminTab : activeUserTab}
              onChangeTab={handleTabChange}
              onPreloadTab={preloadTab}
              showWebChat={showWebChatTab}
            />
          </>
        ) : (
          <div className="relative z-10 mx-auto flex w-full max-w-[1480px] flex-col items-stretch gap-3 sm:gap-4 lg:flex-row lg:items-start">
            <DesktopNavigation
              role={showAdminInterface ? 'admin' : 'user'}
              activeTab={showAdminInterface ? activeAdminTab : activeUserTab}
              onChangeTab={handleTabChange}
              onPreloadTab={preloadTab}
              userLabel={displayName}
              roleLabelText={roleText}
              adminPreviewControl={adminPreviewControl}
              showWebChat={showWebChatTab}
            />

            <div className="min-w-0 flex-1">
              <header className="shamrai-glass-panel mb-4 flex transform-gpu items-center justify-between gap-3 rounded-2xl px-3 py-2.5 will-change-transform">
                <div className="min-w-0">
                  <LogoText className="h-7 w-28" ariaLabel="Shamrai" width={140} height={42} />
                  <h1 className="mt-0.5 truncate text-lg font-black text-white">{displayName}</h1>
                </div>
              </header>

              {telegramLinkPrompt}
              <main className="shamrai-glass-panel min-h-[calc(100dvh-7rem)] transform-gpu overflow-hidden rounded-2xl p-2.5 will-change-transform sm:p-3 xl:p-4">
                {renderCurrentPage()}
              </main>
            </div>
          </div>
        )}
      </PwaPushGate>
      </div>
    </MotionConfig>
  );
}
