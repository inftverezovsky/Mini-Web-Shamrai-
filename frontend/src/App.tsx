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
import WebBotChat from './components/WebBotChat';
import WebSignalListener from './components/WebSignalListener';

import { isStaffRole, roleLabel } from './utils/roles';
import { isTelegramMiniApp } from './utils/telegramSdk';
import { registerPwaServiceWorker } from './utils/webPush';

import {
  AlertTriangle,
  Eye,
  KeyRound,
  Loader2,
} from 'lucide-react';

const BetFeed = lazy(() => import('./pages/user/BetFeed'));
const GlobalStats = lazy(() => import('./pages/user/GlobalStats'));
const MyBets = lazy(() => import('./pages/user/MyBets'));
const Profile = lazy(() => import('./pages/user/Profile'));
const Tariffs = lazy(() => import('./pages/user/Tariffs'));
const Onboarding = lazy(() => import('./pages/user/Onboarding'));
const AdminDashboard = lazy(() => import('./pages/admin/AdminDashboard'));
const AdminCRM = lazy(() => import('./pages/admin/AdminCRM'));
const AdminStats = lazy(() => import('./pages/admin/AdminStats'));

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
    window.setTimeout(() => setIntroComplete(true), 760);
  };

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

  const isAdmin = isStaffRole(userProfile.role);
  const showAdminInterface = isAdmin && !adminPreviewMode;

  const handleTabChange = (tab: UserTabId | AdminShellTabId) => {
    if (showAdminInterface) {
      setActiveAdminTab(tab as AdminShellTabId);
    } else {
      setActiveUserTab(tab as UserTabId);
    }
  };

  const forceOnboarding = import.meta.env.DEV && new URLSearchParams(window.location.search).has('force_onboarding');
  const needsOnboarding = (forceOnboarding || userProfile.is_onboarded === false) && !isStaffRole(userProfile.role);
  const isWebOnlyClient = userProfile.telegram_id < 0;
  const displayName = [userProfile.first_name, userProfile.last_name].filter(Boolean).join(' ').trim()
    || (userProfile.username ? `@${userProfile.username}` : isWebOnlyClient ? 'Web/VK клиент' : `ID ${userProfile.telegram_id}`);
  const roleText = isWebOnlyClient ? 'Web/VK клиент' : roleLabel(userProfile.role);
  const runsInTelegramMiniApp = isTelegram || isTelegramMiniApp();
  const showWebChatTab = !runsInTelegramMiniApp && !showAdminInterface && !needsOnboarding;

  const adminPreviewControl = isAdmin ? (
    <button
      type="button"
      onClick={() => setAdminPreviewMode(!adminPreviewMode)}
      className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[0.045] px-3 py-3 text-xs font-black text-white transition-all hover:bg-white/[0.07] active:scale-[0.98]"
    >
      <Eye className="h-4 w-4 text-indigo-300" />
      <span>{adminPreviewMode ? 'Открыть админку' : 'Кабинет клиента'}</span>
    </button>
  ) : null;

  const renderCurrentPage = () => {
    const safeUserTab = activeUserTab === 'chat' && !showWebChatTab ? 'feed' : activeUserTab;

    return (
      <Suspense fallback={<PageLoader />}>
        {showAdminInterface ? (
          activeAdminTab === 'manage_bets' ? (
            <AdminDashboard />
          ) : activeAdminTab === 'stats' ? (
            <AdminStats />
          ) : activeAdminTab === 'clients' ? (
            <AdminCRM />
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
    );
  };

  return (
    <div
      className={`app-shell min-h-screen bg-[#070B19] relative overflow-x-hidden selection:bg-emerald-500/30 ${
        isCompact
          ? 'mx-auto flex max-w-md flex-col justify-between px-4 pb-24 pt-4'
          : 'w-full px-5 py-6 xl:px-8'
      }`}
    >
      <NotificationCenter />
      <WebSignalListener enabled={showWebChatTab} />
      <VkConsentWizard />
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
            <Suspense fallback={<PageLoader />}>
              <Onboarding onCompleted={fetchUserProfile} />
            </Suspense>
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
                    onClick={() => setAdminPreviewMode(!adminPreviewMode)}
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
