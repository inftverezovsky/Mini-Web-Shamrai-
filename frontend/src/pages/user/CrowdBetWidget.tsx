import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Loader2, Users } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { CrowdBetFundResponse, CrowdBetResponse } from '../../schemas/schemas';
import { notifyError, notifyPending } from '../../utils/notify';

interface CrowdBetWidgetProps {
  onFunded?: () => void;
}

export default function CrowdBetWidget({ onFunded }: CrowdBetWidgetProps) {
  const [crowdBet, setCrowdBet] = useState<CrowdBetResponse | null>(null);
  const [funding, setFunding] = useState(false);

  useEffect(() => {
    async function loadCrowdBet() {
      try {
        const data = await apiFetch('/crowd-bets/active');
        setCrowdBet(data);
      } catch (err) {
        console.error('Crowd bet loading failed:', err);
      }
    }

    loadCrowdBet();
  }, []);

  const fund = async (amountXtr: number) => {
    if (!crowdBet) return;

    try {
      setFunding(true);
      const data = await apiFetch<CrowdBetFundResponse>(`/crowd-bets/${crowdBet.id}/fund`, {
        method: 'POST',
        body: JSON.stringify({ amount_xtr: amountXtr }),
      });
      setCrowdBet(data.crowd_bet);

      const tg = window.Telegram?.WebApp;
      if (tg && typeof tg.openInvoice === 'function') {
        tg.openInvoice(data.invoice_url, async (paymentStatus: string) => {
          if (paymentStatus === 'paid') {
            notifyPending('Оплата прошла в Telegram. Обновляем складчину после webhook.');
            const refreshed = await apiFetch<CrowdBetResponse>('/crowd-bets/active');
            setCrowdBet(refreshed);
            if (refreshed.status === 'opened' && onFunded) onFunded();
          }
        });
      } else {
        notifyError('Вклад за Stars доступен внутри Telegram.');
      }
    } catch (err: any) {
      notifyError(err.message || 'Не удалось создать счет на оплату');
    } finally {
      setFunding(false);
    }
  };

  if (!crowdBet) return null;

  return (
    <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-white/[0.045] p-4 shadow-glass backdrop-blur-xl">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_10%,rgba(255,0,127,0.16),transparent_32%),radial-gradient(circle_at_88%_38%,rgba(0,210,255,0.14),transparent_34%)]" />
      <div className="relative z-10">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <p className="text-[9px] font-black uppercase tracking-[0.22em] text-[#ff8fc7]">Crowd-Bet</p>
            <h3 className="mt-1 text-sm font-black text-white">Сбор на VIP-прогноз</h3>
          </div>
          <Users className="h-7 w-7 text-[#00d2ff] drop-shadow-[0_0_12px_rgba(0,210,255,0.65)]" />
        </div>

        <div className="mb-2 flex justify-between text-[10px] font-black text-slate-300">
          <span>Собрано: {crowdBet.current_amount} / {crowdBet.target_amount} XTR</span>
          <span>{crowdBet.progress_percent}%</span>
        </div>
        <div className="h-3 overflow-hidden rounded-full bg-white/10">
          <motion.div
            initial={false}
            animate={{ width: `${crowdBet.progress_percent}%` }}
            transition={{ type: 'spring', stiffness: 80, damping: 16 }}
            className="h-full rounded-full bg-gradient-to-r from-[#ff007f] to-[#00d2ff] shadow-[0_0_18px_rgba(255,0,127,0.4)]"
          />
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <button
            onClick={() => fund(50)}
            disabled={funding || crowdBet.status === 'opened'}
            className="rounded-xl border border-white/10 bg-white/10 px-3 py-2 text-[10px] font-black text-white active:scale-95 disabled:opacity-45"
          >
            +50 XTR
          </button>
          <button
            onClick={() => fund(150)}
            disabled={funding || crowdBet.status === 'opened'}
            className="rounded-xl bg-gradient-to-r from-[#00d2ff] to-[#ff007f] px-3 py-2 text-[10px] font-black text-white active:scale-95 disabled:opacity-45"
          >
            {funding ? <Loader2 className="mx-auto h-3.5 w-3.5 animate-spin" /> : '+150 XTR'}
          </button>
        </div>

        {crowdBet.status === 'opened' && (
          <p className="mt-3 rounded-xl border border-emerald-400/25 bg-emerald-400/10 px-3 py-2 text-center text-[11px] font-black text-emerald-200">
            Цель достигнута. Прогноз открыт участникам.
          </p>
        )}
      </div>
    </div>
  );
}
