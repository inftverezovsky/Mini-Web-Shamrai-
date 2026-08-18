import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { WheelConfigPayload, WheelPrizeConfig } from '../../schemas/schemas';
import { notifyError, notifySuccess } from '../../utils/notify';
import {
  Plus, Trash2, Save, Loader2,
  Star, Percent, Coins, Gift, Trophy, Zap, Crown, Heart, Flame, Target, Award, Diamond, Sparkles, Tag
} from 'lucide-react';

/* ──────────────── Preset palettes ──────────────── */

const COLOR_PRESETS: { label: string; value: string; preview: string }[] = [
  { label: 'Золото',     value: 'from-amber-300 to-yellow-600',   preview: 'linear-gradient(135deg, #fcd34d, #ca8a04)' },
  { label: 'Пурпур',     value: 'from-purple-400 to-indigo-600',  preview: 'linear-gradient(135deg, #c084fc, #4f46e5)' },
  { label: 'Океан',      value: 'from-cyan-400 to-blue-600',      preview: 'linear-gradient(135deg, #22d3ee, #2563eb)' },
  { label: 'Сталь',      value: 'from-slate-400 to-slate-600',    preview: 'linear-gradient(135deg, #94a3b8, #475569)' },
  { label: 'Изумруд',    value: 'from-emerald-400 to-green-600',  preview: 'linear-gradient(135deg, #34d399, #16a34a)' },
  { label: 'Закат',      value: 'from-orange-400 to-rose-600',    preview: 'linear-gradient(135deg, #fb923c, #e11d48)' },
  { label: 'Розовый',    value: 'from-pink-400 to-fuchsia-600',   preview: 'linear-gradient(135deg, #f472b6, #c026d3)' },
  { label: 'Небо',       value: 'from-sky-300 to-indigo-500',     preview: 'linear-gradient(135deg, #7dd3fc, #6366f1)' },
  { label: 'Лайм',       value: 'from-lime-400 to-emerald-600',   preview: 'linear-gradient(135deg, #a3e635, #059669)' },
  { label: 'Красный',    value: 'from-red-400 to-red-700',        preview: 'linear-gradient(135deg, #f87171, #b91c1c)' },
];

/* ──────────────── Icon registry ──────────────── */

const ICON_OPTIONS: { key: string; label: string; Icon: React.ElementType }[] = [
  { key: 'Star',     label: '⭐ Звезда',    Icon: Star },
  { key: 'Percent',  label: '% Процент',    Icon: Percent },
  { key: 'Coins',    label: '🪙 Монеты',    Icon: Coins },
  { key: 'Gift',     label: '🎁 Подарок',   Icon: Gift },
  { key: 'Trophy',   label: '🏆 Трофей',    Icon: Trophy },
  { key: 'Zap',      label: '⚡ Молния',    Icon: Zap },
  { key: 'Crown',    label: '👑 Корона',    Icon: Crown },
  { key: 'Heart',    label: '❤️ Сердце',    Icon: Heart },
  { key: 'Flame',    label: '🔥 Огонь',     Icon: Flame },
  { key: 'Target',   label: '🎯 Мишень',    Icon: Target },
  { key: 'Award',    label: '🏅 Медаль',    Icon: Award },
  { key: 'Diamond',  label: '💎 Алмаз',     Icon: Diamond },
  { key: 'Sparkles', label: '✨ Искры',     Icon: Sparkles },
  { key: 'Tag',      label: '🏷️ Тег',       Icon: Tag },
];

const ICON_MAP: Record<string, React.ElementType> = Object.fromEntries(
  ICON_OPTIONS.map(i => [i.key, i.Icon])
);

/* ──────────────── Reward types ──────────────── */

const REWARD_TYPES: { value: string; label: string; hint: string }[] = [
  { value: 'post_payment_match', label: 'Топ Ошибка',  hint: 'Промокод на послеоплату' },
  { value: 'discount',           label: 'Скидка',       hint: 'Значение = % скидки (50, 70…)' },
  { value: 'bonus_1000',         label: 'Бонусы',       hint: 'Значение = количество бонусов' },
];

/* ──────────────── Helpers ──────────────── */

let idCounter = 0;
const generateId = (label: string) =>
  label
    .toLowerCase()
    .replace(/[^a-zA-Zа-яА-Я0-9]/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 24) + '_' + (++idCounter);

/* ──────────────── Component ──────────────── */

export default function AdminWheelSettings() {
  const [prizes, setPrizes] = useState<WheelPrizeConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => { loadConfig(); }, []);

  const loadConfig = async () => {
    try {
      setLoading(true);
      const data = await apiFetch<WheelConfigPayload>('/admin/marketing/wheel-config');
      setPrizes(data.prizes || []);
    } catch (err: any) {
      notifyError(err.message || 'Ошибка загрузки конфига колеса');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    const totalProb = prizes.reduce((sum, p) => sum + p.probability, 0);
    if (totalProb !== 100) {
      notifyError(`Сумма вероятностей должна быть 100%, сейчас: ${totalProb}%`);
      return;
    }
    if (prizes.length < 2) {
      notifyError('Нужно минимум 2 приза');
      return;
    }
    try {
      setSaving(true);
      await apiFetch('/admin/marketing/wheel-config', {
        method: 'POST',
        body: JSON.stringify({ prizes }),
      });
      notifySuccess('Настройки колеса сохранены');
    } catch (err: any) {
      notifyError(err.message || 'Ошибка при сохранении настроек');
    } finally {
      setSaving(false);
    }
  };

  const update = (index: number, patch: Partial<WheelPrizeConfig>) => {
    setPrizes(prev => prev.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  };

  const handleAddPrize = () => {
    setPrizes([
      ...prizes,
      {
        id: generateId('prize'),
        label: 'Новый приз',
        sub: 'описание',
        probability: 0,
        reward_type: 'bonus_1000',
        reward_value: 0,
        color: COLOR_PRESETS[0].value,
        icon: 'Star',
      },
    ]);
  };

  const handleRemovePrize = (index: number) => {
    setPrizes(prizes.filter((_, i) => i !== index));
  };

  // Auto-generate ID when label changes
  const handleLabelChange = (index: number, label: string) => {
    const existingId = prizes[index].id;
    // If ID looks auto-generated (contains underscore + digits at end), regenerate
    const looksAutoGenerated = /^.*_\d+$/.test(existingId) || existingId.startsWith('prize_');
    update(index, {
      label,
      ...(looksAutoGenerated ? { id: generateId(label) } : {}),
    });
  };

  // Figure out what reward_value label/hint to show
  const getRewardValueLabel = (rewardType: string) => {
    if (rewardType === 'discount') return 'Размер скидки (%)';
    if (rewardType === 'bonus_1000') return 'Кол-во бонусов';
    return null; // hide for post_payment_match
  };

  const totalProb = prizes.reduce((sum, p) => sum + p.probability, 0);
  const probOk = totalProb === 100;

  if (loading) {
    return (
      <div className="flex justify-center p-6">
        <Loader2 className="w-6 h-6 animate-spin text-cyan-500" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <p className="text-sm text-slate-400">
            Настройте призы и вероятности выпадения
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Probability badge */}
          <div className={`text-xs font-bold px-3 py-1.5 rounded-lg border ${
            probOk
              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
              : 'bg-amber-500/10 text-amber-400 border-amber-500/30'
          }`}>
            Σ {totalProb}%{!probOk && ' (нужно 100%)'}
          </div>
          <button
            onClick={handleSave}
            disabled={saving || !probOk}
            className="flex items-center gap-2 px-4 py-2 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 border border-cyan-500/50 rounded-lg text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Сохранить
          </button>
        </div>
      </div>

      {/* Prize cards */}
      <div className="space-y-3">
        {prizes.map((prize, index) => {
          const IconComp = ICON_MAP[prize.icon] || Star;
          const colorPreset = COLOR_PRESETS.find(c => c.value === prize.color) || COLOR_PRESETS[0];
          const rewardValueLabel = getRewardValueLabel(prize.reward_type);

          return (
            <div
              key={index}
              className="relative p-4 bg-slate-900/60 border border-slate-700/60 rounded-xl"
            >
              {/* Top row: icon preview + name + sub + probability */}
              <div className="flex gap-3 items-start">
                {/* Icon preview */}
                <div
                  className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0 shadow-lg"
                  style={{ background: colorPreset.preview }}
                >
                  <IconComp className="w-6 h-6 text-white drop-shadow-md" />
                </div>

                {/* Fields */}
                <div className="flex-1 grid grid-cols-1 sm:grid-cols-3 gap-3 min-w-0">
                  {/* Name */}
                  <div>
                    <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Название</label>
                    <input
                      type="text"
                      value={prize.label}
                      onChange={e => handleLabelChange(index, e.target.value)}
                      placeholder="Скидка 50%"
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                    />
                  </div>

                  {/* Sub */}
                  <div>
                    <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Подпись</label>
                    <input
                      type="text"
                      value={prize.sub}
                      onChange={e => update(index, { sub: e.target.value })}
                      placeholder="на абонемент"
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                    />
                  </div>

                  {/* Probability */}
                  <div>
                    <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Вероятность (%)</label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={prize.probability}
                      onChange={e => update(index, { probability: Number(e.target.value) })}
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                    />
                  </div>
                </div>

                {/* Delete button */}
                <button
                  onClick={() => handleRemovePrize(index)}
                  className="p-2 text-slate-500 hover:text-red-400 hover:bg-red-400/10 rounded-lg shrink-0 transition-colors"
                  title="Удалить приз"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              {/* Bottom row: reward type + value + icon + color */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3 pl-0 sm:pl-15">
                {/* Reward type */}
                <div>
                  <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Тип награды</label>
                  <select
                    value={prize.reward_type}
                    onChange={e => {
                      const rt = e.target.value;
                      const autoValue = rt === 'discount' ? 50 : rt === 'bonus_1000' ? 1000 : 0;
                      update(index, { reward_type: rt, reward_value: autoValue });
                    }}
                    className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                  >
                    {REWARD_TYPES.map(rt => (
                      <option key={rt.value} value={rt.value}>{rt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Reward value — only shown for discount / bonus */}
                {rewardValueLabel ? (
                  <div>
                    <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">{rewardValueLabel}</label>
                    <input
                      type="number"
                      min={0}
                      value={prize.reward_value}
                      onChange={e => update(index, { reward_value: Number(e.target.value) })}
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                    />
                  </div>
                ) : (
                  <div /> /* empty spacer */
                )}

                {/* Icon picker */}
                <div>
                  <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Иконка</label>
                  <select
                    value={prize.icon}
                    onChange={e => update(index, { icon: e.target.value })}
                    className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors"
                  >
                    {ICON_OPTIONS.map(opt => (
                      <option key={opt.key} value={opt.key}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Color picker */}
                <div>
                  <label className="text-[10px] text-slate-500 font-bold uppercase mb-1 block">Цвет</label>
                  <div className="relative">
                    <select
                      value={prize.color}
                      onChange={e => update(index, { color: e.target.value })}
                      className="w-full bg-slate-800 border border-slate-700 text-white rounded-lg pl-9 pr-3 py-2 text-sm focus:border-cyan-500/50 focus:outline-none transition-colors appearance-none"
                    >
                      {COLOR_PRESETS.map(cp => (
                        <option key={cp.value} value={cp.value}>{cp.label}</option>
                      ))}
                    </select>
                    {/* Color swatch in dropdown */}
                    <div
                      className="absolute left-2 top-1/2 -translate-y-1/2 w-5 h-5 rounded-md border border-white/20 pointer-events-none"
                      style={{ background: (COLOR_PRESETS.find(c => c.value === prize.color) || COLOR_PRESETS[0]).preview }}
                    />
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Add button */}
      <button
        onClick={handleAddPrize}
        className="flex items-center justify-center gap-2 w-full py-3 bg-slate-800/60 hover:bg-slate-700/60 text-slate-300 rounded-xl border border-dashed border-slate-600 transition-colors"
      >
        <Plus className="w-4 h-4" />
        <span className="text-sm font-medium">Добавить приз</span>
      </button>
    </div>
  );
}
