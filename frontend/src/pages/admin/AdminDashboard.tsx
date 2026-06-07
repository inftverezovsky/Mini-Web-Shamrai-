import React, { useState } from 'react';
import { Settings } from 'lucide-react';
import AdminBets from './AdminBets';
import AdminBroadcast from './AdminBroadcast';

type AdminTabId = 'bets' | 'broadcast';

export default function AdminDashboard() {
  const [activeTab, setActiveTab] = useState<AdminTabId>('bets');

  const adminTabs: Array<{ id: AdminTabId; label: string }> = [
    { id: 'bets', label: 'Прогнозы' },
    { id: 'broadcast', label: 'Рассылки' },
  ];

  return (
    <div className="space-y-6 animate-slide-up pb-10">
      
      {/* 1. Header Title */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-white flex items-center uppercase tracking-wider">
            <Settings className="w-5 h-5 text-indigo-400 mr-2 animate-spin-slow" />
            Панель управления
          </h2>
          <p className="text-slate-400 text-[10px] uppercase font-bold tracking-widest mt-0.5">Административный центр</p>
        </div>
      </div>

      {/* 2. Sub-Tab bar Navigation */}
      <div className="bg-white/[0.03] border border-white/10 p-1.5 rounded-2xl grid grid-cols-2 gap-1.5 shadow-inner">
        {adminTabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`min-w-0 text-[10px] font-black uppercase tracking-wider py-2.5 px-2 rounded-xl transition-all ${
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
        {activeTab === 'bets' && <AdminBets />}
        {activeTab === 'broadcast' && <AdminBroadcast />}
      </div>

    </div>
  );
}
