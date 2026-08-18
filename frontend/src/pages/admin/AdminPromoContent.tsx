import React, { useState } from 'react';
import { Target, HelpCircle, Users, Trophy, PlayCircle } from 'lucide-react';
import AdminPromoQuiz from './AdminPromoQuiz';
import AdminPromoPvP from './AdminPromoPvP';
import AdminPromoCrowdBet from './AdminPromoCrowdBet';
import AdminPromoMarathon from './AdminPromoMarathon';

type PromoTabId = 'quiz' | 'pvp' | 'crowd_bet' | 'marathon';

export default function AdminPromoContent() {
  const [activeTab, setActiveTab] = useState<PromoTabId>('quiz');

  const tabs: Array<{ id: PromoTabId; label: string; icon: React.ElementType }> = [
    { id: 'quiz', label: 'Квизы', icon: HelpCircle },
    { id: 'pvp', label: 'PvP', icon: Users },
    { id: 'crowd_bet', label: 'Совместные', icon: Target },
    { id: 'marathon', label: 'Марафоны', icon: Trophy },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="bg-white/[0.02] border border-white/10 rounded-2xl p-4">
        <h3 className="text-lg font-bold text-white mb-2 flex items-center">
          <PlayCircle className="w-5 h-5 text-indigo-400 mr-2" />
          Управление контентом виджетов
        </h3>
        <p className="text-sm text-slate-400 mb-6">
          Здесь вы создаете и запускаете конкретные тесты, голосования и марафоны.
          Сами виджеты (кулдауны, размеры наград) настраиваются во вкладке «Настройки → Промо-виджеты».
        </p>

        {/* Sub-tabs */}
        <div className="flex flex-wrap gap-2 mb-6">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center px-4 py-2 rounded-xl text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30'
                    : 'bg-white/5 text-slate-300 border border-white/5 hover:bg-white/10'
                }`}
              >
                <Icon className="w-4 h-4 mr-2" />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* Tab Content */}
        <div className="bg-black/20 rounded-xl p-4 border border-white/5">
          {activeTab === 'quiz' && <AdminPromoQuiz />}
          {activeTab === 'pvp' && <AdminPromoPvP />}
          {activeTab === 'crowd_bet' && <AdminPromoCrowdBet />}
          {activeTab === 'marathon' && <AdminPromoMarathon />}
        </div>
      </div>
    </div>
  );
}
