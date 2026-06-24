import React, { Suspense, lazy, useState, useTransition } from 'react';
import { Loader2, Settings } from 'lucide-react';
import { prefetchAdminDashboardTab, type AdminDashboardTabId } from '../../utils/tabPrefetch';

const AdminBets = lazy(() => import('./AdminBets'));
const AdminBroadcast = lazy(() => import('./AdminBroadcast'));
const AdminResults = lazy(() => import('./AdminResults'));

export default function AdminDashboard() {
  const [, startAdminTabTransition] = useTransition();
  const [activeTab, setActiveTab] = useState<AdminDashboardTabId>('bets');
  const [mountedTabs, setMountedTabs] = useState<AdminDashboardTabId[]>(['bets']);

  const adminTabs: Array<{ id: AdminDashboardTabId; label: string }> = [
    { id: 'bets', label: 'Прогноз' },
    { id: 'broadcast', label: 'Рассылки' },
    { id: 'requests', label: 'Заявки' },
    { id: 'results', label: 'Результаты' },
  ];

  const handleTabIntent = (tab: AdminDashboardTabId) => {
    void prefetchAdminDashboardTab(tab);
  };

  const handleTabChange = (tab: AdminDashboardTabId) => {
    handleTabIntent(tab);
    startAdminTabTransition(() => {
      setMountedTabs((current) => current.includes(tab) ? current : [...current, tab]);
      setActiveTab(tab);
    });
  };

  const renderTab = (tab: AdminDashboardTabId) => {
    if (tab === 'bets') return <AdminBets />;
    if (tab === 'broadcast') return <AdminBroadcast />;
    if (tab === 'requests') return <AdminBroadcast initialMode="requests" showModeTabs={false} />;
    if (tab === 'results') return <AdminResults />;
    return <AdminBets />;
  };

  return (
    <div className="space-y-4 animate-slide-up pb-8">
      
      {/* 1. Header Title */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-black text-white flex items-center uppercase tracking-wider">
            <Settings className="w-4 h-4 text-indigo-400 mr-2 animate-spin-slow" />
            Панель управления
          </h2>
        </div>
      </div>

      {/* 2. Sub-Tab bar Navigation */}
      <div className="bg-white/[0.03] border border-white/10 p-1 rounded-xl grid grid-cols-4 gap-1 shadow-inner">
        {adminTabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            onClick={() => handleTabChange(tab.id)}
            onMouseEnter={() => handleTabIntent(tab.id)}
            onPointerEnter={() => handleTabIntent(tab.id)}
            onTouchStart={() => handleTabIntent(tab.id)}
            onFocus={() => handleTabIntent(tab.id)}
            className={`min-w-0 text-[9px] font-black uppercase tracking-wider py-2 px-1.5 rounded-lg transition-all ${
              activeTab === tab.id
                ? 'bg-indigo-500 text-white shadow-neon-indigo'
                : 'text-slate-400 hover:text-white hover:bg-white/5'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 3. Sub-Tab Component Switcher */}
      <div>
        <Suspense
          fallback={
            <div className="flex min-h-[240px] items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-indigo-300" />
            </div>
          }
        >
          {adminTabs.map((tab) => (
            <div
              key={tab.id}
              className={activeTab === tab.id ? undefined : 'hidden'}
              aria-hidden={activeTab !== tab.id}
            >
              {(mountedTabs.includes(tab.id) || activeTab === tab.id) ? renderTab(tab.id) : null}
            </div>
          ))}
        </Suspense>
      </div>

    </div>
  );
}
