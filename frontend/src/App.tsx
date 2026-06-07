import React, { Suspense, lazy, useEffect, useState } from 'react';
import { useTelegram } from './hooks/useTelegram';
import { useAuth } from './context/AuthContext';

// Navigation Layout
import BottomNavigation from './components/BottomNavigation';
import type { AdminShellTabId, UserTabId } from './components/BottomNavigation';
import NotificationCenter from './components/NotificationCenter';
import WelcomeSplash from './components/WelcomeSplash';

import { isStaffRole, roleLabel } from './utils/roles';

// Icons
import { 
  KeyRound, 
  Eye, 
  Code,
  AlertTriangle,
  Loader2
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
  const { isReady, isTelegram, debugAuthEnabled, mockRole, toggleMockRole } = useTelegram();
  const { user: userProfile, loading, error, login: fetchUserProfile } = useAuth();
  
  // Navigation states
  const [activeUserTab, setActiveUserTab] = useState<UserTabId>('feed');
  const [activeAdminTab, setActiveAdminTab] = useState<AdminShellTabId>('manage_bets');
  
  // Admin preview toggle (allows admins to preview what subscribers see)
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

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-[#0F172A] text-slate-50 px-6 text-center space-y-4">
        <AlertTriangle className="w-16 h-16 text-rose-500" />
        <h1 className="text-lg font-bold text-white">Ошибка подключения</h1>
        <p className="text-xs text-slate-400 max-w-xs">{error}</p>
        <button 
          onClick={fetchUserProfile}
          className="bg-emerald-500 text-slate-950 px-5 py-2.5 rounded-xl text-xs font-bold shadow-neon-green active:scale-95 transition-all"
        >
          Повторить попытку
        </button>
      </div>
    );
  }

  const isAdmin = isStaffRole(userProfile?.role);
  const showAdminInterface = isAdmin && !adminPreviewMode;

  const handleTabChange = (tab: UserTabId | AdminShellTabId) => {
    if (showAdminInterface) {
      setActiveAdminTab(tab as AdminShellTabId);
    } else {
      setActiveUserTab(tab as UserTabId);
    }
  };

  const forceOnboarding = import.meta.env.DEV && new URLSearchParams(window.location.search).has('force_onboarding');
  const needsOnboarding = userProfile && (forceOnboarding || userProfile.is_onboarded === false) && !isStaffRole(userProfile.role);

  return (
    <div className="app-shell min-h-screen bg-[#070B19] flex flex-col justify-between max-w-md mx-auto relative px-4 pt-4 pb-24 overflow-x-hidden selection:bg-emerald-500/30">
      <NotificationCenter />
      <div className="ambient-field" aria-hidden="true">
        <div className="ambient-field__grid" />
        <div className="ambient-field__rings" />
      </div>

      {needsOnboarding ? (
        <div className="z-10 flex-grow flex flex-col justify-center w-full">
          <Suspense fallback={<PageLoader />}>
            <Onboarding onCompleted={fetchUserProfile} />
          </Suspense>
        </div>
      ) : (
        <>
          <div className="z-10 flex-grow flex flex-col justify-between w-full">
            {/* 1. Header Admin Mode Toggle Banner */}
            {isAdmin && (
              <div className="bg-white/5 border border-white/10 backdrop-blur-md p-2.5 rounded-2xl mb-4 text-xs flex justify-between items-center z-10">
                <span className="text-slate-300 flex items-center">
                  <KeyRound className="w-4 h-4 text-rose-400 mr-1.5 shrink-0" />
                  Вы вошли как <strong className="text-rose-400 ml-1">{roleLabel(userProfile?.role)}</strong>
                </span>
                <button onClick={() => setAdminPreviewMode(!adminPreviewMode)} className="bg-white/10 hover:bg-white/15 border border-white/10 active:scale-95 text-[10px] px-2.5 py-1 rounded-xl text-white flex items-center space-x-1 transition-all">
                  <Eye className="w-3.5 h-3.5 text-indigo-400" />
                  <span>{adminPreviewMode ? 'Админка' : 'Кабинет юзера'}</span>
                </button>
              </div>
            )}

            {/* 2. Page Content Routing Switcher */}
            <main className="flex-grow">
              <Suspense fallback={<PageLoader />}>
                {showAdminInterface ? (
                  // Admin View Mappings
                  activeAdminTab === 'manage_bets' ? (
                    <AdminDashboard />
                  ) : activeAdminTab === 'stats' ? (
                    <AdminStats />
                  ) : activeAdminTab === 'clients' ? (
                    <AdminCRM />
                  ) : (
                    <Profile />
                  )
                ) : (
                  // Subscriber View Mappings
                  activeUserTab === 'feed' ? (
                    <BetFeed onNavigateToBilling={() => setActiveUserTab('billing')} />
                  ) : activeUserTab === 'stats' ? (
                    <GlobalStats />
                  ) : activeUserTab === 'my_bets' ? (
                    <MyBets />
                  ) : activeUserTab === 'billing' ? (
                    <Tariffs onSubscriptionActivated={fetchUserProfile} />
                  ) : (
                    <Profile />
                  )
                )}
              </Suspense>
            </main>
          </div>

          {/* 3. Bottom Tabs Layout */}
          <BottomNavigation
            role={showAdminInterface ? 'admin' : 'user'}
            activeTab={showAdminInterface ? activeAdminTab : activeUserTab}
            onChangeTab={handleTabChange}
          />
        </>
      )}

      {/* 4. Desktop Web Browser Developer Role Toggle */}
      {!isTelegram && debugAuthEnabled && (
        <div className="fixed bottom-14 left-1/2 transform -translate-x-1/2 bg-slate-900 border border-slate-700/60 py-1.5 px-3 rounded-full flex items-center space-x-2 text-[10px] shadow-lg z-50">
          <Code className="w-3.5 h-3.5 text-amber-500" />
          <span className="text-slate-300">Отладка:</span>
          <button 
            onClick={toggleMockRole} 
            className={`font-black uppercase px-2 py-0.5 rounded transition-all ${
              mockRole === 'admin' 
                ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' 
                : 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
            }`}
          >
            {mockRole}
          </button>
        </div>
      )}

    </div>
  );
}
