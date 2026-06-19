import React, { Suspense, lazy, useState } from 'react';
import { Loader2, Settings } from 'lucide-react';

const AdminBets = lazy(() => import('./AdminBets'));
const AdminBroadcast = lazy(() => import('./AdminBroadcast'));
const AdminResults = lazy(() => import('./AdminResults'));

type AdminTabId = 'bets' | 'broadcast' | 'requests' | 'results';

export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState<AdminTabId>('bets');

  const adminTabs: Array<{ id: AdminTabId; label: string }> = [
    { id: 'bets', label: 'Прогноз' },
    { id: 'broadcast', label: 'Рассылки' },
    { id: 'requests', label: 'Заявки' },
    { id: 'results', label: 'Результаты' },
  ];

  const renderActiveTab = () => {
    if (activeTab === 'bets') return <AdminBets />;
    if (activeTab === 'broadcast') return <AdminBroadcast />;
    if (activeTab === 'requests') return <AdminBroadcast initialMode="requests" showModeTabs={false} />;
    if (activeTab === 'results') return <AdminResults />;
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
            onClick={() => setActiveTab(tab.id)}
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
          {renderActiveTab()}
        </Suspense>
      </div>

    </div>
  );
}
