import React, { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../../utils/api';
import { BookmakerResponse } from '../../schemas/schemas';
import { isOtherBookmaker } from '../../constants/bookmakers';
import { ALL_SPORT_LABELS, SPORT_OPTIONS } from '../../constants/sports';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import AdminPlans from '../admin/AdminPlans';
import AdminAccess from '../admin/AdminAccess';
import AdminMarketing from '../admin/AdminMarketing';
import {
  User as UserIcon,
  Wallet,
  ShieldCheck,
  Clock,
  Check,
  Loader2,
  Award,
  FileText,
  Bell,
  Moon,
  Copy,
  Link,
  CreditCard,
  Sliders,
  Sparkles,
  ChevronDown,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { isPrivilegedRole, isStaffRole, roleLabel } from '../../utils/roles';

/* ─────────────────────── Типы ─────────────────────── */
interface Preferences {
  alert_min_coef: number;
  is_night_mode: boolean;
  preferred_sports: string[];
  stats_display_mode: 'percent' | 'flat';
}

interface ReferralInfo {
  referral_code: string;
  referral_link: string;
  invited_count: number;
  purchased_invited_count: number;
  discount_step_percent: number;
  referral_discount_percent: number;
}

interface PaymentRecord {
  id: string | number;
  plan_name: string;
  amount: number | string;
  status: string;
  created_at: string;
}

/* ─────────────── Константы для стилей ─────────────── */
const GLASS =
  'backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl shadow-xl';
const NEON_GLOW_PINK = '0 0 15px rgba(255,0,127,0.3)';
const NEON_GLOW_BLUE = '0 0 15px rgba(0,210,255,0.3)';
const ACCENT_PINK = '#ff007f';
const ACCENT_BLUE = '#00d2ff';

type CollapsibleSectionKey = 'sports' | 'stats' | 'bookmakers' | 'payments' | 'referral';

interface CollapsibleSectionProps {
  title: string;
  badge?: string;
  icon: React.ReactNode;
  accent: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function CollapsibleSection({
  title,
  badge,
  icon,
  accent,
  isOpen,
  onToggle,
  children,
}: CollapsibleSectionProps) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03]">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="w-full flex items-center justify-between gap-3 px-3.5 py-3 text-left transition-all hover:bg-white/[0.04] active:bg-white/[0.06]"
      >
        <span className="flex items-center gap-2 min-w-0">
          {icon}
          <span className="truncate text-xs font-black uppercase tracking-wider text-white">
            {title}
          </span>
        </span>
        <span className="flex items-center gap-2 shrink-0">
          {badge && (
            <span
              className="rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider"
              style={{
                borderColor: `${accent}55`,
                background: `${accent}14`,
                color: accent,
              }}
            >
              {badge}
            </span>
          )}
          <ChevronDown
            className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
            style={{ color: accent }}
          />
        </span>
      </button>
      {isOpen && (
        <div className="border-t border-white/5 px-3.5 py-3.5 animate-slide-down">
          {children}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════ Компонент ═══════════════════════ */
export default function Profile() {
  const { user: userProfile, setUser } = useAuth();
  const isAdminProfile = isStaffRole(userProfile?.role);
  const canManageBilling = isPrivilegedRole(userProfile?.role);

  /* ── Bookmakers (существующая логика) ── */
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>([]);
  const [selectedBkIds, setSelectedBkIds] = useState<number[]>([]);
  const [otherBookmakerName, setOtherBookmakerName] = useState(
    userProfile?.other_bookmaker_name ?? '',
  );
  const [loadingBks, setLoadingBks] = useState(true);
  const [savingBkId, setSavingBkId] = useState<number | null>(null);
  const [savingOtherBookmaker, setSavingOtherBookmaker] = useState(false);

  /* ── Subscription ── */
  const [loadingSub, setLoadingSub] = useState(true);

  /* ── PDF ── */
  const [downloadingPdf, setDownloadingPdf] = useState(false);

  /* ── Preferences (Block 2) ── */
  const [prefs, setPrefs] = useState<Preferences>({
    alert_min_coef: 1.5,
    is_night_mode: false,
    preferred_sports: ALL_SPORT_LABELS,
    stats_display_mode: 'percent',
  });
  const [loadingPrefs, setLoadingPrefs] = useState(true);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [prefsSaved, setPrefsSaved] = useState(false);
  const [openSettingsSections, setOpenSettingsSections] = useState<
    Record<CollapsibleSectionKey, boolean>
  >({
    sports: false,
    stats: false,
    bookmakers: false,
    payments: false,
    referral: false,
  });

  /* ── Referral (Block 3) ── */
  const [referral, setReferral] = useState<ReferralInfo | null>(null);
  const [loadingRef, setLoadingRef] = useState(true);
  const [copied, setCopied] = useState(false);

  /* ── Payments (Block 4) ── */
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [loadingPay, setLoadingPay] = useState(true);

  /* ── Admin Tabs ── */
  const [activeAdminTab, setActiveAdminTab] = useState<'access' | 'plans' | 'marketing'>('access');

  /* ── Telegram avatar ── */
  const tgUser = (window as any).Telegram?.WebApp?.initDataUnsafe?.user;
  const avatarUrl: string | null = tgUser?.photo_url || null;
  const otherBookmakerSelected = bookmakers.some(
    (bk) => isOtherBookmaker(bk) && selectedBkIds.includes(bk.id),
  );

  /* ────────────────── Data loaders ────────────────── */
  useEffect(() => {
    async function loadBookmakers() {
      try {
        setLoadingBks(true);
        const allBks = await apiFetch('/bookmakers');
        setBookmakers(allBks);
        const [myBkIds, freshProfile] = await Promise.all([
          apiFetch('/users/me/bookmakers'),
          apiFetch('/users/me'),
        ]);
        setSelectedBkIds(myBkIds);
        setOtherBookmakerName(freshProfile.other_bookmaker_name ?? '');
        setUser(freshProfile);
      } catch (err) {
        console.error('Error loading bookmaker options:', err);
      } finally {
        setLoadingBks(false);
      }
    }

    async function loadSubscription() {
      try {
        setLoadingSub(true);
        await apiFetch('/subscriptions/my-status');
      } catch (err) {
        console.error('Error loading subscription:', err);
      } finally {
        setLoadingSub(false);
      }
    }

    async function loadPreferences() {
      try {
        setLoadingPrefs(true);
        const data = await apiFetch('/users/me/preferences');
        setPrefs({
          alert_min_coef: data.alert_min_coef ?? 1.5,
          is_night_mode: data.is_night_mode ?? false,
          preferred_sports: data.preferred_sports?.length ? data.preferred_sports : ALL_SPORT_LABELS,
          stats_display_mode: data.stats_display_mode ?? 'percent',
        });
      } catch (err) {
        console.error('Error loading preferences:', err);
      } finally {
        setLoadingPrefs(false);
      }
    }

    async function loadReferral() {
      try {
        setLoadingRef(true);
        const data = await apiFetch('/users/me/referral');
        setReferral(data);
      } catch (err) {
        console.error('Error loading referral:', err);
      } finally {
        setLoadingRef(false);
      }
    }

    async function loadPayments() {
      try {
        setLoadingPay(true);
        const data = await apiFetch('/users/me/payments');
        setPayments(Array.isArray(data) ? data : data.transactions ?? []);
      } catch (err) {
        console.error('Error loading payments:', err);
      } finally {
        setLoadingPay(false);
      }
    }

    if (isAdminProfile) return;

    loadBookmakers();
    loadSubscription();
    loadPreferences();
    loadReferral();
    loadPayments();
  }, [isAdminProfile, setUser]);

  /* ────────────────── Handlers ────────────────── */
  const handleDownloadPdf = async () => {
    try {
      setDownloadingPdf(true);
      const token = localStorage.getItem('bet_tma_jwt_token');
      const API_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? 'http://localhost:8000' : '');
      const response = await fetch(`${API_URL}/api/users/me/report`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Не удалось сгенерировать PDF');

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute(
        'download',
        `shamrai_report_${userProfile?.telegram_id}.pdf`,
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      alert(err.message || 'Ошибка скачивания отчета');
    } finally {
      setDownloadingPdf(false);
    }
  };

  const handleCheckboxChange = async (bkId: number) => {
    const updatedIds = selectedBkIds.includes(bkId)
      ? selectedBkIds.filter((id) => id !== bkId)
      : [...selectedBkIds, bkId];

    setSelectedBkIds(updatedIds);

    try {
      setSavingBkId(bkId);
      await apiFetch('/users/me/bookmakers', {
        method: 'POST',
        body: JSON.stringify({
          bookmaker_ids: updatedIds,
          other_bookmaker_name: otherBookmakerName.trim() || null,
        }),
      });
      if (userProfile) {
        const refreshedProfile = await apiFetch('/users/me');
        setUser(refreshedProfile);
      }
    } catch (err) {
      console.error('Save failed:', err);
      alert('Ошибка при сохранении БК');
      setSelectedBkIds(selectedBkIds);
    } finally {
      setSavingBkId(null);
    }
  };

  const saveOtherBookmakerName = async () => {
    if (!otherBookmakerSelected) return;
    try {
      setSavingOtherBookmaker(true);
      await apiFetch('/users/me/bookmakers', {
        method: 'POST',
        body: JSON.stringify({
          bookmaker_ids: selectedBkIds,
          other_bookmaker_name: otherBookmakerName.trim() || null,
        }),
      });
      if (userProfile) {
        const refreshedProfile = await apiFetch('/users/me');
        setUser(refreshedProfile);
      }
    } catch (err) {
      console.error('Save other bookmaker failed:', err);
      alert('Ошибка при сохранении названия БК');
    } finally {
      setSavingOtherBookmaker(false);
    }
  };

  const handleSavePrefs = useCallback(async () => {
    try {
      setSavingPrefs(true);
      setPrefsSaved(false);
      await apiFetch('/users/me/preferences', {
        method: 'PUT',
        body: JSON.stringify(prefs),
      });
      setPrefsSaved(true);
      setTimeout(() => setPrefsSaved(false), 2500);
    } catch (err) {
      console.error('Error saving preferences:', err);
      alert('Ошибка сохранения настроек');
    } finally {
      setSavingPrefs(false);
    }
  }, [prefs]);

  const toggleSport = (sport: string) => {
    setPrefs((prev) => ({
      ...prev,
      preferred_sports: prev.preferred_sports.includes(sport)
        ? prev.preferred_sports.filter((s) => s !== sport)
        : [...prev.preferred_sports, sport],
    }));
  };

  const toggleSettingsSection = (section: CollapsibleSectionKey) => {
    setOpenSettingsSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const handleCopyReferral = async () => {
    if (!referral) return;
    try {
      await navigator.clipboard.writeText(referral.referral_link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback для Telegram WebView
      const ta = document.createElement('textarea');
      ta.value = referral.referral_link;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  /* ────────────────── Helpers ────────────────── */
  const isSubActive = () => {
    return (userProfile?.matches_remaining || 0) > 0 || Boolean(userProfile?.guarantee_active);
  };

  const formatDate = (dateStr: string) =>
    new Date(dateStr).toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    });

  const statusBadge = (status: string) => {
    const map: Record<string, { bg: string; text: string; label: string }> = {
      completed: {
        bg: 'bg-emerald-500/15',
        text: 'text-emerald-400',
        label: 'Оплачено',
      },
      paid: {
        bg: 'bg-emerald-500/15',
        text: 'text-emerald-400',
        label: 'Оплачено',
      },
      active: {
        bg: 'bg-emerald-500/15',
        text: 'text-emerald-400',
        label: 'Активна',
      },
      success: {
        bg: 'bg-emerald-500/15',
        text: 'text-emerald-400',
        label: 'Оплачено',
      },
      pending: {
        bg: 'bg-amber-500/15',
        text: 'text-amber-400',
        label: 'Ожидание',
      },
      failed: {
        bg: 'bg-red-500/15',
        text: 'text-red-400',
        label: 'Ошибка',
      },
      refunded: {
        bg: 'bg-slate-500/15',
        text: 'text-slate-400',
        label: 'Возврат',
      },
    };
    const s = map[status] || {
      bg: 'bg-slate-500/15',
      text: 'text-slate-400',
      label: status,
    };
    return (
      <span
        className={`${s.bg} ${s.text} text-[10px] font-bold uppercase px-2 py-0.5 rounded-full`}
      >
        {s.label}
      </span>
    );
  };

  /* ═══════════════════ RENDER ═══════════════════ */
  return (
    <div className="space-y-6 animate-slide-up pb-10">
      {/* ━━━━━━━━━━ BLOCK 1 — User Analytics Dashboard ━━━━━━━━━━ */}
      <div className={GLASS + ' p-5 space-y-4'} style={{ boxShadow: NEON_GLOW_PINK }}>
        {/* Заголовок секции */}
        <div className="flex items-center space-x-2 pb-1 border-b border-white/5">
          <Sparkles className="w-5 h-5" style={{ color: ACCENT_PINK }} />
          <h3 className="text-sm font-black uppercase tracking-wider text-white">
            Профиль Shamrai
          </h3>
        </div>

        {/* Аватар + имя */}
        <div className="flex flex-col items-center text-center space-y-3">
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt="Telegram Avatar"
              className="w-20 h-20 rounded-full border-2"
              style={{
                borderColor: ACCENT_PINK,
                boxShadow: NEON_GLOW_PINK,
              }}
            />
          ) : (
            <div
              className="w-20 h-20 rounded-full flex items-center justify-center border-2"
              style={{
                borderColor: ACCENT_PINK,
                background: 'rgba(255,0,127,0.08)',
                boxShadow: NEON_GLOW_PINK,
              }}
            >
              <UserIcon className="w-10 h-10" style={{ color: ACCENT_PINK }} />
            </div>
          )}

          <div>
            <h2 className="text-lg font-bold text-white">
              {userProfile?.first_name} {userProfile?.last_name || ''}
            </h2>
            {userProfile?.username && (
              <p className="text-slate-400 text-xs mt-0.5">
                @{userProfile.username}
              </p>
            )}
          </div>

          {isAdminProfile ? (
            <div className="w-full pt-3 border-t border-white/5 flex flex-col items-center">
              <div
                className="rounded-xl px-4 py-2 flex items-center space-x-2 text-xs font-semibold border"
                style={{
                  background: 'rgba(255,0,127,0.08)',
                  borderColor: 'rgba(255,0,127,0.25)',
                  color: ACCENT_PINK,
                  boxShadow: NEON_GLOW_PINK,
                }}
              >
                <ShieldCheck className="w-4 h-4" />
                <span>{roleLabel(userProfile?.role)}</span>
              </div>
            </div>
          ) : (
            <div className="w-full pt-3 border-t border-white/5 flex flex-col items-center">
              {loadingSub ? (
                <Loader2
                  className="w-4 h-4 animate-spin"
                  style={{ color: ACCENT_BLUE }}
                />
              ) : isSubActive() ? (
                <div
                  className="rounded-xl px-4 py-2 flex items-center space-x-2 text-xs font-semibold border"
                  style={{
                    background: 'rgba(0,210,255,0.08)',
                    borderColor: 'rgba(0,210,255,0.25)',
                    color: ACCENT_BLUE,
                    boxShadow: NEON_GLOW_BLUE,
                  }}
                >
                  <ShieldCheck className="w-4 h-4" />
                  <span>
                    Абонемент: {userProfile?.matches_remaining ?? 0} матчей
                    {userProfile?.guarantee_active ? ' + гарантия до победы' : ''}
                  </span>
                </div>
              ) : (
                <div className="bg-slate-800/40 border border-slate-700/50 rounded-xl px-4 py-2 flex items-center space-x-2 text-xs text-slate-400">
                  <Clock className="w-4 h-4" />
                  <span>Доступ закрыт (нет матчей в абонементе)</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Earned Badges */}
        {!isAdminProfile && <div className="space-y-3">
          <div className="flex items-center space-x-2 pb-1 border-b border-white/5">
            <Award
              className="w-4 h-4 filter"
              style={{
                color: ACCENT_PINK,
                filter: `drop-shadow(0 0 4px ${ACCENT_PINK})`,
              }}
            />
            <h4 className="text-xs font-black uppercase tracking-wider text-white">
              Ваши Достижения
            </h4>
          </div>

          {(userProfile as any)?.badges &&
          (userProfile as any).badges.length > 0 ? (
            <div className="grid grid-cols-2 gap-2.5">
              {(userProfile as any).badges.map(
                (b: { id: number | string; title: string }) => (
                  <div
                    key={b.id}
                    className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col items-center justify-center text-center space-y-1.5 hover:border-[#ff007f]/30 transition-all relative overflow-hidden group"
                    style={{ boxShadow: '0 0 8px rgba(255,0,127,0.1)' }}
                  >
                    <div className="absolute top-0 right-0 w-8 h-8 bg-[#ff007f]/5 rounded-full blur-md" />
                    <span className="text-2xl animate-pulse">🧠</span>
                    <span className="text-[10px] font-black text-white uppercase tracking-wider">
                      {b.title}
                    </span>
                    <span className="text-[7.5px] text-slate-500 font-bold uppercase">
                      Получено
                    </span>
                  </div>
                ),
              )}
            </div>
          ) : (
            <div className="bg-slate-900/40 border border-slate-800/60 p-4 rounded-xl text-center text-slate-500 text-xs font-semibold uppercase tracking-wider leading-relaxed">
              🏆 У вас пока нет достижений.
              <br />
              <span className="text-[9px] text-slate-600 block mt-1 font-bold">
                Выигрывайте прогнозы Shamrai, чтобы получить первый бейдж!
              </span>
            </div>
          )}
        </div>}

        {/* PDF Download */}
        {!isAdminProfile && <button
          onClick={handleDownloadPdf}
          disabled={downloadingPdf}
          className="w-full py-2.5 px-4 rounded-xl flex items-center justify-center space-x-1.5 transition-all text-xs font-extrabold text-white border border-white/5 disabled:opacity-50 hover:brightness-110 active:scale-[0.98]"
          style={{
            background: `linear-gradient(135deg, ${ACCENT_PINK}, ${ACCENT_BLUE})`,
            boxShadow: NEON_GLOW_PINK,
          }}
        >
          {downloadingPdf ? (
            <Loader2 className="w-4 h-4 animate-spin text-white" />
          ) : (
            <>
              <FileText className="w-4 h-4 text-white" />
              <span>Скачать отчет Shamrai PDF</span>
            </>
          )}
        </button>}
      </div>

      {isAdminProfile && (
        <div className="space-y-6 animate-slide-up">
          {/* Pill Tab Selector */}
          <div className="bg-slate-950/60 border border-white/10 p-1.5 rounded-full flex items-center justify-between w-full shadow-inner">
            {([
              { id: 'access', label: 'ДОСТУП' },
              { id: 'plans', label: 'ПАКЕТЫ' },
              { id: 'marketing', label: 'МАРКЕТИНГ' },
            ] as const).map(tab => {
              const isActive = activeAdminTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveAdminTab(tab.id)}
                  className={`flex-1 text-[10px] font-black uppercase tracking-wider py-2 rounded-full text-center transition-all ${
                    isActive
                      ? 'bg-[#5f5fed] text-white border border-white/20 shadow-[0_0_12px_rgba(95,95,237,0.4)]'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {/* Tab Content */}
          <div className="space-y-4">
            {activeAdminTab === 'access' && <AdminAccess />}
            {activeAdminTab === 'plans' && (
              canManageBilling ? (
                <AdminPlans />
              ) : (
                <div className="bg-slate-900/40 border border-slate-800/60 p-6 rounded-2xl text-center text-slate-500 text-xs font-semibold uppercase tracking-wider leading-relaxed">
                  🔒 Доступ к пакетам ограничен.
                  <br />
                  <span className="text-[9px] text-slate-600 block mt-1 font-bold">
                    Требуется роль Администратора или Владельца.
                  </span>
                </div>
              )
            )}
            {activeAdminTab === 'marketing' && <AdminMarketing />}
          </div>
        </div>
      )}

      {/* ━━━━━━━━━━ BLOCK 2 — Smart Notification Settings ━━━━━━━━━━ */}
      {!isAdminProfile && <div className={GLASS + ' p-5 space-y-5'} style={{ boxShadow: NEON_GLOW_BLUE }}>
        <div className="flex items-center space-x-2 pb-1 border-b border-white/5">
          <Sliders className="w-5 h-5" style={{ color: ACCENT_BLUE }} />
          <h3 className="text-sm font-black uppercase tracking-wider text-white">
            Умные уведомления
          </h3>
        </div>

        <div className="space-y-5">
          {loadingPrefs ? (
            <div className="flex justify-center py-6">
              <Loader2
                className="w-6 h-6 animate-spin"
                style={{ color: ACCENT_BLUE }}
              />
            </div>
          ) : (
            <>
            {/* Минимальный коэффициент (slider) */}
            <div className="space-y-2">
              <label className="flex items-center justify-between text-xs font-semibold text-slate-300">
                <span className="flex items-center space-x-1.5">
                  <Bell className="w-3.5 h-3.5" style={{ color: ACCENT_PINK }} />
                  <span>Мин. коэффициент для алертов</span>
                </span>
                <span
                  className="text-sm font-black tabular-nums"
                  style={{ color: ACCENT_PINK }}
                >
                  {prefs.alert_min_coef.toFixed(2)}
                </span>
              </label>
              <input
                type="range"
                min="1.00"
                max="5.00"
                step="0.05"
                value={prefs.alert_min_coef}
                onChange={(e) =>
                  setPrefs((p) => ({
                    ...p,
                    alert_min_coef: parseFloat(e.target.value),
                  }))
                }
                className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(to right, ${ACCENT_PINK}, ${ACCENT_BLUE})`,
                }}
              />
              <div className="flex justify-between text-[10px] text-slate-600 font-bold">
                <span>1.00</span>
                <span>5.00</span>
              </div>
            </div>

            {/* Ночной режим (toggle) */}
            <div className="flex items-center justify-between">
              <label className="flex items-center space-x-1.5 text-xs font-semibold text-slate-300">
                <Moon className="w-3.5 h-3.5" style={{ color: ACCENT_BLUE }} />
                <span>Ночной режим (без уведомлений 23:00–08:00)</span>
              </label>
              <button
                onClick={() =>
                  setPrefs((p) => ({ ...p, is_night_mode: !p.is_night_mode }))
                }
                className={`relative w-11 h-6 rounded-full transition-all ${
                  prefs.is_night_mode ? '' : 'bg-slate-700'
                }`}
                style={
                  prefs.is_night_mode
                    ? {
                        background: `linear-gradient(135deg, ${ACCENT_PINK}, ${ACCENT_BLUE})`,
                        boxShadow: NEON_GLOW_PINK,
                      }
                    : {}
                }
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                    prefs.is_night_mode ? 'translate-x-5' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>

            {/* Виды спорта (multi-checkbox) */}
            <CollapsibleSection
              title="Предпочтительные виды спорта"
              badge={
                prefs.preferred_sports.length > 0
                  ? `${prefs.preferred_sports.length} выбрано`
                  : 'Не выбрано'
              }
              icon={<Award className="w-3.5 h-3.5" style={{ color: ACCENT_PINK }} />}
              accent={ACCENT_PINK}
              isOpen={openSettingsSections.sports}
              onToggle={() => toggleSettingsSection('sports')}
            >
              <div className="grid grid-cols-2 gap-2">
                {SPORT_OPTIONS.map((sport) => {
                  const active = prefs.preferred_sports.includes(sport.label);
                  return (
                    <label
                      key={sport.label}
                      className={`sport-choice-card ${active ? 'sport-choice-card--active' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleSport(sport.label)}
                        className="sr-only"
                      />
                      <span className="sport-choice-card__check" aria-hidden="true">
                        <Check className="w-3.5 h-3.5" />
                      </span>
                      <SportIconFrame label={sport.label} src={sport.icon} size="tile" active={active} />
                      <span className="sport-choice-card__label">{sport.label}</span>
                    </label>
                  );
                })}
              </div>
            </CollapsibleSection>

            {/* Режим отображения статистики (percent / flat) */}
            <CollapsibleSection
              title="Отображение статистики"
              badge={prefs.stats_display_mode === 'percent' ? 'Проценты' : 'Абсолютные'}
              icon={<Sliders className="w-3.5 h-3.5" style={{ color: ACCENT_BLUE }} />}
              accent={ACCENT_BLUE}
              isOpen={openSettingsSections.stats}
              onToggle={() => toggleSettingsSection('stats')}
            >
              <div className="flex space-x-2">
                {(['percent', 'flat'] as const).map((mode) => {
                  const active = prefs.stats_display_mode === mode;
                  const label = mode === 'percent' ? 'Проценты (%)' : 'Абсолютные';
                  return (
                    <button
                      key={mode}
                      onClick={() =>
                        setPrefs((p) => ({ ...p, stats_display_mode: mode }))
                      }
                      className={`flex-1 py-2 rounded-xl text-xs font-bold border transition-all ${
                        active
                          ? 'text-white border-[#00d2ff]/40'
                          : 'text-slate-400 border-white/5 bg-white/5 hover:border-white/15'
                      }`}
                      style={
                        active
                          ? {
                              background: 'rgba(0,210,255,0.12)',
                              boxShadow: NEON_GLOW_BLUE,
                            }
                          : {}
                      }
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </CollapsibleSection>

            </>
          )}

          <CollapsibleSection
            title="Букмекерские конторы"
            badge={
              loadingBks
                ? 'Загрузка'
                : selectedBkIds.length > 0
                  ? `${selectedBkIds.length} выбрано`
                  : 'Не выбрано'
            }
            icon={<Wallet className="w-3.5 h-3.5" style={{ color: ACCENT_BLUE }} />}
            accent={ACCENT_BLUE}
            isOpen={openSettingsSections.bookmakers}
            onToggle={() => toggleSettingsSection('bookmakers')}
          >
            <div className="space-y-3">
              <p className="text-xs text-slate-400 leading-normal">
                Укажите конторы, в которых у вас есть счета. Мы будем автоматически
                подбирать для вас подходящие прогнозы. Настройки сохраняются мгновенно.
              </p>

              {loadingBks ? (
                <div className="flex justify-center py-4">
                  <Loader2
                    className="w-6 h-6 animate-spin"
                    style={{ color: ACCENT_BLUE }}
                  />
                </div>
              ) : (
                <div className="space-y-2.5">
                  {bookmakers.map((bk) => {
                    const isChecked = selectedBkIds.includes(bk.id);
                    const isSaving = savingBkId === bk.id;

                    return (
                      <label
                        key={bk.id}
                        className={`flex items-center justify-between p-3.5 rounded-xl border text-xs font-semibold cursor-pointer transition-all ${
                          isChecked
                            ? 'text-white'
                            : 'bg-white/5 border-white/5 text-slate-400 hover:border-white/15'
                        }`}
                        style={
                          isChecked
                            ? {
                                background: 'rgba(0,210,255,0.08)',
                                borderColor: 'rgba(0,210,255,0.3)',
                                color: ACCENT_BLUE,
                                boxShadow: NEON_GLOW_BLUE,
                              }
                            : {}
                        }
                      >
                        <div className="flex items-center space-x-3 min-w-0">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => handleCheckboxChange(bk.id)}
                            className="accent-[#00d2ff] w-4 h-4 rounded"
                            disabled={isSaving}
                          />
                          <BookmakerLogoFrame bookmaker={bk} active={isChecked} />
                          <span className="truncate">{bk.name}</span>
                        </div>
                        {isSaving ? (
                          <Loader2
                            className="w-3.5 h-3.5 animate-spin"
                            style={{ color: ACCENT_BLUE }}
                          />
                        ) : isChecked ? (
                          <Check className="w-4 h-4" style={{ color: ACCENT_BLUE }} />
                        ) : null}
                      </label>
                    );
                  })}
                  {otherBookmakerSelected && (
                    <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-xl p-2.5">
                      <input
                        type="text"
                        value={otherBookmakerName}
                        onChange={(e) => setOtherBookmakerName(e.target.value)}
                        onBlur={saveOtherBookmakerName}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.currentTarget.blur();
                          }
                        }}
                        placeholder="Напишите название БК"
                        className="flex-1 min-w-0 bg-slate-950/50 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-[#00d2ff]/50"
                      />
                      {savingOtherBookmaker ? (
                        <Loader2
                          className="w-4 h-4 animate-spin shrink-0"
                          style={{ color: ACCENT_BLUE }}
                        />
                      ) : (
                        <Check className="w-4 h-4 shrink-0" style={{ color: ACCENT_BLUE }} />
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </CollapsibleSection>

          {!loadingPrefs && (
            <button
              onClick={handleSavePrefs}
              disabled={savingPrefs}
              className="w-full py-2.5 px-4 rounded-xl flex items-center justify-center space-x-1.5 transition-all text-xs font-extrabold text-white border border-white/5 disabled:opacity-50 hover:brightness-110 active:scale-[0.98]"
              style={{
                background: `linear-gradient(135deg, ${ACCENT_BLUE}, ${ACCENT_PINK})`,
                boxShadow: NEON_GLOW_BLUE,
              }}
            >
              {savingPrefs ? (
                <Loader2 className="w-4 h-4 animate-spin text-white" />
              ) : prefsSaved ? (
                <>
                  <Check className="w-4 h-4 text-white" />
                  <span>Сохранено!</span>
                </>
              ) : (
                <>
                  <Check className="w-4 h-4 text-white" />
                  <span>Сохранить настройки</span>
                </>
              )}
            </button>
          )}
        </div>
      </div>}

      {/* ━━━━━━━━━━ BLOCK 3 — Referral System ━━━━━━━━━━ */}
      {!isAdminProfile && <div className={GLASS + ' p-5 space-y-4'} style={{ boxShadow: NEON_GLOW_PINK }}>
        <button
          type="button"
          onClick={() => toggleSettingsSection('referral')}
          aria-expanded={openSettingsSections.referral}
          className="w-full flex items-center justify-between gap-3 pb-1 border-b border-white/5 text-left transition-all active:scale-[0.99]"
        >
          <span className="flex items-center space-x-2 min-w-0">
            <Link className="w-5 h-5 shrink-0" style={{ color: ACCENT_PINK }} />
            <span className="truncate text-sm font-black uppercase tracking-wider text-white">
              Реферальная программа
            </span>
          </span>
          <span className="flex items-center gap-2 shrink-0">
            {referral && !loadingRef && (
              <span
                className="rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider"
                style={{
                  borderColor: `${ACCENT_PINK}55`,
                  background: `${ACCENT_PINK}14`,
                  color: ACCENT_PINK,
                }}
              >
                {referral.referral_discount_percent > 0 ? `-${referral.referral_discount_percent}%` : '0%'}
              </span>
            )}
            <ChevronDown
              className={`w-4 h-4 transition-transform ${
                openSettingsSections.referral ? 'rotate-180' : ''
              }`}
              style={{ color: ACCENT_PINK }}
            />
          </span>
        </button>

        {openSettingsSections.referral && (
          <div className="animate-slide-down space-y-4">
            {loadingRef ? (
              <div className="flex justify-center py-6">
                <Loader2
                  className="w-6 h-6 animate-spin"
                  style={{ color: ACCENT_PINK }}
                />
              </div>
            ) : referral ? (
              <>
                {/* Реферальный код */}
                <div className="space-y-1.5">
                  <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wider">
                    Ваш реферальный код
                  </span>
                  <div
                    className="flex items-center justify-between bg-white/5 border border-white/10 rounded-xl px-4 py-3"
                    style={{ boxShadow: '0 0 8px rgba(255,0,127,0.08)' }}
                  >
                    <span
                      className="text-sm font-black tracking-widest"
                      style={{ color: ACCENT_PINK }}
                    >
                      {referral.referral_code}
                    </span>
                    <button
                      onClick={handleCopyReferral}
                      className="flex items-center space-x-1 text-xs font-bold transition-all hover:brightness-125 active:scale-95"
                      style={{ color: copied ? '#22c55e' : ACCENT_BLUE }}
                    >
                      {copied ? (
                        <>
                          <Check className="w-3.5 h-3.5" />
                          <span>Скопировано!</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>Копировать</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>

                {/* Реферальная ссылка */}
                <div className="space-y-1.5">
                  <span className="text-[10px] text-slate-500 font-bold uppercase tracking-wider">
                    Ссылка для друзей
                  </span>
                  <div className="bg-white/5 border border-white/10 rounded-xl px-4 py-2.5 text-[11px] text-slate-400 font-mono break-all select-all">
                    {referral.referral_link}
                  </div>
                </div>

                {/* Статистика */}
                <div className="grid grid-cols-3 gap-3">
                  <div
                    className="bg-white/5 border border-white/10 rounded-xl p-3 text-center"
                    style={{ boxShadow: '0 0 8px rgba(0,210,255,0.08)' }}
                  >
                    <div
                      className="text-xl font-black tabular-nums"
                      style={{ color: ACCENT_BLUE }}
                    >
                      {referral.invited_count}
                    </div>
                    <div className="text-[10px] text-slate-500 font-bold uppercase mt-0.5">
                      Приглашено
                    </div>
                  </div>
                  <div
                    className="bg-white/5 border border-white/10 rounded-xl p-3 text-center"
                    style={{ boxShadow: '0 0 8px rgba(0,210,255,0.08)' }}
                  >
                    <div
                      className="text-xl font-black tabular-nums"
                      style={{ color: ACCENT_BLUE }}
                    >
                      {referral.purchased_invited_count}
                    </div>
                    <div className="text-[10px] text-slate-500 font-bold uppercase mt-0.5">
                      Купили
                    </div>
                  </div>
                  <div
                    className="bg-white/5 border border-white/10 rounded-xl p-3 text-center"
                    style={{ boxShadow: '0 0 8px rgba(255,0,127,0.08)' }}
                  >
                    <div
                      className="text-xl font-black tabular-nums"
                      style={{ color: ACCENT_PINK }}
                    >
                      {referral.referral_discount_percent > 0
                        ? `-${referral.referral_discount_percent}%`
                        : '0%'}
                    </div>
                    <div className="text-[10px] text-slate-500 font-bold uppercase mt-0.5">
                      Скидка
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="bg-slate-900/40 border border-slate-800/60 p-4 rounded-xl text-center text-slate-500 text-xs font-semibold uppercase tracking-wider">
                Реферальная программа недоступна
              </div>
            )}
          </div>
        )}
      </div>}

      {/* ━━━━━━━━━━ BLOCK 4 — Transaction History ━━━━━━━━━━ */}
      {!isAdminProfile && <div className={GLASS + ' p-5 space-y-4'} style={{ boxShadow: NEON_GLOW_BLUE }}>
        <button
          type="button"
          onClick={() => toggleSettingsSection('payments')}
          aria-expanded={openSettingsSections.payments}
          className="w-full flex items-center justify-between gap-3 pb-1 border-b border-white/5 text-left transition-all active:scale-[0.99]"
        >
          <span className="flex items-center space-x-2 min-w-0">
            <CreditCard className="w-5 h-5 shrink-0" style={{ color: ACCENT_BLUE }} />
            <span className="truncate text-sm font-black uppercase tracking-wider text-white">
              История платежей
            </span>
          </span>
          <span className="flex items-center gap-2 shrink-0">
            <span
              className="rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider"
              style={{
                borderColor: `${ACCENT_BLUE}55`,
                background: `${ACCENT_BLUE}14`,
                color: ACCENT_BLUE,
              }}
            >
              {loadingPay ? 'Загрузка' : payments.length > 0 ? `${payments.length} шт.` : 'Пусто'}
            </span>
            <ChevronDown
              className={`w-4 h-4 transition-transform ${
                openSettingsSections.payments ? 'rotate-180' : ''
              }`}
              style={{ color: ACCENT_BLUE }}
            />
          </span>
        </button>

        {openSettingsSections.payments && (
          <div className="animate-slide-down">
            {loadingPay ? (
              <div className="flex justify-center py-6">
                <Loader2
                  className="w-6 h-6 animate-spin"
                  style={{ color: ACCENT_BLUE }}
                />
              </div>
            ) : payments.length > 0 ? (
              <div className="space-y-2.5">
                {payments.map((tx) => (
                  <div
                    key={tx.id}
                    className="flex items-center justify-between bg-white/5 border border-white/10 rounded-xl px-4 py-3 transition-all hover:border-white/15"
                  >
                    <div className="flex-1 min-w-0 space-y-0.5">
                      <div className="text-xs font-bold text-white truncate">
                        {tx.plan_name}
                      </div>
                      <div className="text-[10px] text-slate-500 font-semibold">
                        {formatDate(tx.created_at)}
                      </div>
                    </div>
                    <div className="flex items-center space-x-3 shrink-0">
                      <span
                        className="text-sm font-black tabular-nums"
                        style={{ color: ACCENT_BLUE }}
                      >
                        {typeof tx.amount === 'number'
                          ? tx.amount.toLocaleString('ru-RU')
                          : tx.amount}{' '}
                        ⭐
                      </span>
                      {statusBadge(tx.status)}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="bg-slate-900/40 border border-slate-800/60 p-4 rounded-xl text-center text-slate-500 text-xs font-semibold uppercase tracking-wider leading-relaxed">
                💳 Платежей пока нет.
                <br />
                <span className="text-[9px] text-slate-600 block mt-1 font-bold">
                  Оформите подписку, чтобы увидеть историю.
                </span>
              </div>
            )}
          </div>
        )}
      </div>}
    </div>
  );
}
