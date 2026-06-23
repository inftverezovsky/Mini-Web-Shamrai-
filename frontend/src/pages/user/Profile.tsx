import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '../../utils/api';
import { BookmakerResponse, ProfileDashboardResponse } from '../../schemas/schemas';
import { getVkIdConfig, isVkRedirectStartedError, linkVkProfile } from '../../utils/vkId';
import {
  detectVkMiniAppRuntime,
  fetchVkDeliveryStatus,
  getVkMessagesUrl,
  isVkMiniAppRuntime,
  requestVkMessagesPermission,
} from '../../utils/vkDelivery';
import type { VkDeliveryStatus } from '../../utils/vkDelivery';
import { useLayoutMode } from '../../context/LayoutModeContext';
import { isOtherBookmaker } from '../../constants/bookmakers';
import EmojiTextField from '../../components/EmojiTextField';
import { BookmakerLogoFrame } from '../../components/LogoFrame';
import SmoothCollapse from '../../components/SmoothCollapse';
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
  Bell,
  Moon,
  Copy,
  Link,
  ExternalLink,
  MessageCircle,
  CreditCard,
  Sliders,
  Sparkles,
  ChevronDown,
  RefreshCw,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { isPrivilegedRole, isStaffRole, roleLabel } from '../../utils/roles';

/* ─────────────────────── Типы ─────────────────────── */
interface Preferences {
  alert_min_coef: number;
  odds_drop_notifications_enabled: boolean;
  is_night_mode: boolean;
  night_mode_start: string;
  night_mode_end: string;
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
const ALERT_MIN_COEF_MIN = 1.0;
const ALERT_MIN_COEF_MAX = 1.6;
const DEFAULT_NIGHT_MODE_START = '23:00';
const DEFAULT_NIGHT_MODE_END = '08:00';

const clampAlertMinCoef = (value: number) =>
  Math.min(ALERT_MIN_COEF_MAX, Math.max(ALERT_MIN_COEF_MIN, value));

type CollapsibleSectionKey = 'vk' | 'achievements' | 'notifications' | 'bookmakers' | 'payments' | 'referral';

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
    <div className="smooth-surface overflow-hidden rounded-2xl border border-white/10 bg-white/[0.03]">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="smooth-pressable w-full flex items-center justify-between gap-3 px-3.5 py-3 text-left transition-all hover:bg-white/[0.04] active:bg-white/[0.06]"
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
      <SmoothCollapse open={isOpen} className="border-t border-white/5">
        <div className="px-3.5 py-3.5">
          {children}
        </div>
      </SmoothCollapse>
    </div>
  );
}

interface AlertMinCoefSliderProps {
  value: number;
  onCommit: (value: number) => void;
}

const AlertMinCoefSlider = React.memo(function AlertMinCoefSlider({
  value,
  onCommit,
}: AlertMinCoefSliderProps) {
  const [draftValue, setDraftValue] = useState(() => clampAlertMinCoef(value));
  const lastCommittedValueRef = useRef(clampAlertMinCoef(value));

  useEffect(() => {
    const nextValue = clampAlertMinCoef(value);
    setDraftValue(nextValue);
    lastCommittedValueRef.current = nextValue;
  }, [value]);

  const updateDraft = useCallback((rawValue: string) => {
    const nextValue = clampAlertMinCoef(Number.parseFloat(rawValue));
    setDraftValue(nextValue);
  }, []);

  const commitDraft = useCallback((rawValue?: string) => {
    const nextValue = clampAlertMinCoef(
      rawValue == null ? draftValue : Number.parseFloat(rawValue),
    );
    setDraftValue(nextValue);
    if (lastCommittedValueRef.current === nextValue) return;
    lastCommittedValueRef.current = nextValue;
    onCommit(nextValue);
  }, [draftValue, onCommit]);

  return (
    <div className="space-y-2">
      <label className="flex items-center justify-between gap-3 text-xs font-semibold text-slate-300">
        <span className="flex min-w-0 items-center space-x-1.5">
          <Bell className="w-3.5 h-3.5 shrink-0" style={{ color: ACCENT_PINK }} />
          <span className="min-w-0">Мин. коэффициент для алертов</span>
        </span>
        <span
          className="shrink-0 text-sm font-black tabular-nums"
          style={{ color: ACCENT_PINK }}
        >
          {draftValue.toFixed(2)}
        </span>
      </label>
      <input
        type="range"
        min={ALERT_MIN_COEF_MIN.toFixed(2)}
        max={ALERT_MIN_COEF_MAX.toFixed(2)}
        step="0.05"
        value={draftValue}
        onInput={(event) => updateDraft(event.currentTarget.value)}
        onChange={(event) => updateDraft(event.currentTarget.value)}
        onPointerUp={(event) => commitDraft(event.currentTarget.value)}
        onTouchEnd={(event) => commitDraft(event.currentTarget.value)}
        onMouseUp={(event) => commitDraft(event.currentTarget.value)}
        onKeyUp={(event) => commitDraft(event.currentTarget.value)}
        onBlur={(event) => commitDraft(event.currentTarget.value)}
        className="shamrai-range w-full"
        style={{
          '--range-progress': `${((draftValue - ALERT_MIN_COEF_MIN) / (ALERT_MIN_COEF_MAX - ALERT_MIN_COEF_MIN)) * 100}%`,
        } as React.CSSProperties}
      />
      <div className="flex justify-between text-[10px] text-slate-600 font-bold">
        <span>{ALERT_MIN_COEF_MIN.toFixed(2)}</span>
        <span>{ALERT_MIN_COEF_MAX.toFixed(2)}</span>
      </div>
    </div>
  );
});

/* ═══════════════════════ Компонент ═══════════════════════ */
export default function Profile() {
  const { user: userProfile, setUser, loginWithTelegramBot } = useAuth();
  const { isCompact } = useLayoutMode();
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

  /* ── Preferences (Block 2) ── */
  const [prefs, setPrefs] = useState<Preferences>({
    alert_min_coef: 1.5,
    odds_drop_notifications_enabled: true,
    is_night_mode: false,
    night_mode_start: DEFAULT_NIGHT_MODE_START,
    night_mode_end: DEFAULT_NIGHT_MODE_END,
  });
  const [loadingPrefs, setLoadingPrefs] = useState(true);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [prefsSaved, setPrefsSaved] = useState(false);
  const [openSettingsSections, setOpenSettingsSections] = useState<
    Record<CollapsibleSectionKey, boolean>
  >({
    vk: false,
    achievements: false,
    notifications: false,
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
  const [linkingVk, setLinkingVk] = useState(false);
  const [vkLinkError, setVkLinkError] = useState<string | null>(null);
  const [vkLinkedDisplayName, setVkLinkedDisplayName] = useState<string | null>(null);
  const [vkDeliveryStatus, setVkDeliveryStatus] = useState<VkDeliveryStatus | null>(null);
  const [vkDeliveryLoading, setVkDeliveryLoading] = useState(false);
  const [vkPermissionBusy, setVkPermissionBusy] = useState<'messages' | 'check' | null>(null);
  const [vkMiniAppRuntime, setVkMiniAppRuntime] = useState(() => isVkMiniAppRuntime());
  const vkDialogOpenedRef = useRef(false);
  const [linkingTelegram, setLinkingTelegram] = useState(false);
  const [telegramLinkError, setTelegramLinkError] = useState<string | null>(null);

  /* ── Admin Tabs ── */
  const [activeAdminTab, setActiveAdminTab] = useState<'access' | 'plans' | 'marketing'>('access');

  /* ── Telegram avatar ── */
  const tgUser = (window as any).Telegram?.WebApp?.initDataUnsafe?.user;
  const avatarUrl: string | null = userProfile?.photo_url || tgUser?.photo_url || null;
  const otherBookmakerSelected = bookmakers.some(
    (bk) => isOtherBookmaker(bk) && selectedBkIds.includes(bk.id),
  );
  const vkConfig = getVkIdConfig();
  const vkReady = vkConfig.ready;
  const showVkSecureFallback = Boolean(vkConfig.configured && !vkConfig.originCompatible && !vkConfig.debugEnabled);
  const vkSecureAppUrl = vkConfig.canonicalAppUrl || 'https://shamra1.pro/app/';
  const vkMessagesAllowed = vkDeliveryStatus?.messages_allowed ?? Boolean(userProfile?.vk_messages_allowed);
  const vkMessagesUrl = getVkMessagesUrl(vkDeliveryStatus?.group_id);
  const vkDeliveryReady = Boolean(userProfile?.vk_user_id && vkMessagesAllowed);
  const vkMissingPermissionsCount = vkDeliveryReady ? 0 : 1;
  const telegramLinked = Boolean(userProfile && !userProfile.is_web_only && userProfile.telegram_id > 0);
  const profileDashboardQuery = useQuery<ProfileDashboardResponse>({
    queryKey: ['profile-dashboard', userProfile?.telegram_id],
    queryFn: () => apiFetch<ProfileDashboardResponse>('/users/me/profile-dashboard'),
    enabled: Boolean(userProfile && !isAdminProfile),
    staleTime: 60_000,
  });

  /* ────────────────── Data loaders ────────────────── */
  useEffect(() => {
    let cancelled = false;
    detectVkMiniAppRuntime().then((ready) => {
      if (!cancelled) setVkMiniAppRuntime(ready);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (isAdminProfile) return;
    if (profileDashboardQuery.isLoading) {
      setLoadingBks(true);
      setLoadingSub(true);
      setLoadingPrefs(true);
      setLoadingRef(true);
      setLoadingPay(true);
      return;
    }
    if (profileDashboardQuery.isError) {
      console.error('Error loading profile dashboard:', profileDashboardQuery.error);
      setLoadingBks(false);
      setLoadingSub(false);
      setLoadingPrefs(false);
      setLoadingRef(false);
      setLoadingPay(false);
      return;
    }

    const dashboard = profileDashboardQuery.data;
    if (!dashboard) return;
    setBookmakers(dashboard.bookmakers);
    setSelectedBkIds(dashboard.selected_bookmaker_ids);
    setOtherBookmakerName(dashboard.user.other_bookmaker_name ?? '');
    setUser(dashboard.user);
    setPrefs({
      alert_min_coef: clampAlertMinCoef(dashboard.preferences.alert_min_coef ?? 1.5),
      odds_drop_notifications_enabled: dashboard.preferences.odds_drop_notifications_enabled ?? true,
      is_night_mode: dashboard.preferences.is_night_mode ?? false,
      night_mode_start: dashboard.preferences.night_mode_start ?? DEFAULT_NIGHT_MODE_START,
      night_mode_end: dashboard.preferences.night_mode_end ?? DEFAULT_NIGHT_MODE_END,
    });
    setReferral(dashboard.referral);
    setPayments(dashboard.payments.transactions ?? []);
    setVkDeliveryStatus(dashboard.vk_delivery_status);
    setLoadingBks(false);
    setLoadingSub(false);
    setLoadingPrefs(false);
    setLoadingRef(false);
    setLoadingPay(false);
  }, [
    isAdminProfile,
    profileDashboardQuery.data,
    profileDashboardQuery.error,
    profileDashboardQuery.isError,
    profileDashboardQuery.isLoading,
    setUser,
  ]);

  const loadVkDeliveryStatus = useCallback(async () => {
    if (!userProfile?.vk_user_id) {
      setVkDeliveryStatus(null);
      return;
    }

    try {
      setVkDeliveryLoading(true);
      const status = await fetchVkDeliveryStatus();
      setVkDeliveryStatus(status);
      setUser((current) => current ? {
        ...current,
        vk_group_member: status.group_member,
        vk_messages_allowed: status.messages_allowed,
        vk_notifications_allowed: status.notifications_allowed,
      } : current);
    } catch (err) {
      console.error('Error loading VK delivery status:', err);
    } finally {
      setVkDeliveryLoading(false);
    }
  }, [setUser, userProfile?.vk_user_id]);

  useEffect(() => {
    if (isAdminProfile || !openSettingsSections.vk) return;
    loadVkDeliveryStatus();
  }, [isAdminProfile, loadVkDeliveryStatus, openSettingsSections.vk]);

  useEffect(() => {
    if (isAdminProfile || !openSettingsSections.vk || !userProfile?.vk_user_id || vkMessagesAllowed) return;

    const checkOnReturn = () => {
      if (document.visibilityState !== 'visible') return;
      if (!vkDialogOpenedRef.current) return;
      vkDialogOpenedRef.current = false;
      void loadVkDeliveryStatus();
    };

    window.addEventListener('focus', checkOnReturn);
    document.addEventListener('visibilitychange', checkOnReturn);
    return () => {
      window.removeEventListener('focus', checkOnReturn);
      document.removeEventListener('visibilitychange', checkOnReturn);
    };
  }, [isAdminProfile, loadVkDeliveryStatus, openSettingsSections.vk, userProfile?.vk_user_id, vkMessagesAllowed]);

  /* ────────────────── Handlers ────────────────── */
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
        body: JSON.stringify({
          ...prefs,
          alert_min_coef: clampAlertMinCoef(prefs.alert_min_coef),
        }),
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

  const updateNightModeTime = (
    field: 'night_mode_start' | 'night_mode_end',
    value: string,
  ) => {
    setPrefs((prev) => ({
      ...prev,
      [field]: value || (field === 'night_mode_start' ? DEFAULT_NIGHT_MODE_START : DEFAULT_NIGHT_MODE_END),
    }));
  };

  const toggleSettingsSection = (section: CollapsibleSectionKey) => {
    setOpenSettingsSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const handleAlertMinCoefCommit = useCallback((nextValue: number) => {
    setPrefs((p) => ({
      ...p,
      alert_min_coef: clampAlertMinCoef(nextValue),
    }));
  }, []);

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

  const handleLinkVkProfile = async () => {
    if (!vkReady) {
      setVkLinkError('VK ID не настроен. Обратитесь к администратору Shamrai.');
      return;
    }

    try {
      setLinkingVk(true);
      setVkLinkError(null);
      const linkedProfile = await linkVkProfile();
      setVkLinkedDisplayName(linkedProfile.vk_display_name || `VK ID ${linkedProfile.vk_user_id}`);
      const refreshedProfile = await apiFetch('/users/me');
      setUser(refreshedProfile);
      const status = await fetchVkDeliveryStatus();
      setVkDeliveryStatus(status);
      setUser((current) => current ? {
        ...current,
        vk_group_member: status.group_member,
        vk_messages_allowed: status.messages_allowed,
        vk_notifications_allowed: status.notifications_allowed,
      } : current);
    } catch (err: any) {
      if (isVkRedirectStartedError(err)) return;
      setVkLinkError(err?.message || 'Не удалось привязать VK. Попробуйте еще раз.');
    } finally {
      setLinkingVk(false);
    }
  };

  const handleLinkTelegramProfile = async () => {
    try {
      setLinkingTelegram(true);
      setTelegramLinkError(null);
      await loginWithTelegramBot();
      apiFetch('/users/me')
        .then(setUser)
        .catch(() => undefined);
    } catch (err: any) {
      setTelegramLinkError(err?.message || 'Не удалось привязать Telegram. Попробуйте еще раз.');
    } finally {
      setLinkingTelegram(false);
    }
  };

  const handleAllowVkMessages = async () => {
    try {
      setVkPermissionBusy('messages');
      setVkLinkError(null);
      const status = await requestVkMessagesPermission(vkDeliveryStatus?.group_id);
      setVkDeliveryStatus(status);
      setUser((current) => current ? {
        ...current,
        vk_group_member: status.group_member,
        vk_messages_allowed: status.messages_allowed,
      } : current);
    } catch (err: any) {
      setVkLinkError(err?.message || 'Не удалось включить сообщения VK.');
    } finally {
      setVkPermissionBusy(null);
    }
  };

  const handleOpenVkDialog = () => {
    if (!vkMessagesUrl) return;
    vkDialogOpenedRef.current = true;
    window.open(vkMessagesUrl, '_blank', 'noopener,noreferrer');
  };

  const handleCheckVkDeliveryAccess = async () => {
    try {
      setVkPermissionBusy('check');
      setVkLinkError(null);
      const status = await fetchVkDeliveryStatus();
      setVkDeliveryStatus(status);
      setUser((current) => current ? {
        ...current,
        vk_group_member: status.group_member,
        vk_messages_allowed: status.messages_allowed,
        vk_notifications_allowed: status.notifications_allowed,
      } : current);
      if (!status.messages_allowed) {
        setVkLinkError('VK пока не подтвердил доступ. Напишите любое сообщение в диалог и проверьте еще раз.');
      }
    } catch (err: any) {
      setVkLinkError(err?.message || 'Не удалось проверить VK-доступ.');
    } finally {
      setVkPermissionBusy(null);
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

  const dashboardLoadFailed = !isAdminProfile && profileDashboardQuery.isError;
  const retryDashboardLoad = () => {
    void profileDashboardQuery.refetch();
  };

  /* ═══════════════════ RENDER ═══════════════════ */
  return (
    <div className={`${isCompact ? 'space-y-6' : 'grid grid-cols-1 gap-5 xl:grid-cols-2'} animate-slide-up pb-10`}>
      {/* ━━━━━━━━━━ BLOCK 1 — User Analytics Dashboard ━━━━━━━━━━ */}
      <div className={GLASS + ' p-5 space-y-4'} style={{ boxShadow: NEON_GLOW_PINK }}>
        {/* Заголовок секции */}
        <div className="flex items-center space-x-2 pb-1 border-b border-white/5">
          <Sparkles className="w-5 h-5" style={{ color: ACCENT_PINK }} />
          <h3 className="text-sm font-black uppercase tracking-wider text-white">
            Профиль Shamrai
          </h3>
        </div>

        {dashboardLoadFailed && (
          <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 p-3 text-center">
            <p className="text-xs font-bold leading-relaxed text-amber-50">
              Не удалось загрузить данные профиля. Проверьте подключение и попробуйте еще раз.
            </p>
            <button
              type="button"
              onClick={retryDashboardLoad}
              className="mx-auto mt-3 inline-flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              <span>Повторить</span>
            </button>
          </div>
        )}

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
              {dashboardLoadFailed ? (
                <div className="bg-amber-500/10 border border-amber-300/20 rounded-xl px-4 py-2 flex items-center space-x-2 text-xs font-semibold text-amber-100">
                  <Clock className="w-4 h-4" />
                  <span>Статус абонемента не обновлен</span>
                </div>
              ) : loadingSub ? (
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

        {!isAdminProfile && (
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
            <div className="flex items-start gap-3">
              <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border ${
                telegramLinked
                  ? 'border-emerald-300/20 bg-emerald-400/10 text-emerald-200'
                  : 'border-cyan-300/25 bg-cyan-300/10 text-cyan-100'
              }`}>
                {telegramLinked ? <Check className="h-5 w-5" /> : <MessageCircle className="h-5 w-5" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[10px] font-black uppercase tracking-wider text-slate-300">
                    Telegram профиль
                  </span>
                  <span className={`rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${
                    telegramLinked
                      ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-200'
                      : 'border-cyan-300/25 bg-cyan-300/10 text-cyan-100'
                  }`}>
                    {telegramLinked ? 'привязан' : 'можно подключить'}
                  </span>
                </div>
              </div>
            </div>

            {!telegramLinked && (
              <button
                type="button"
                onClick={handleLinkTelegramProfile}
                disabled={linkingTelegram}
                className="mt-3 min-h-[44px] w-full rounded-xl border border-[#24a1de]/35 bg-[#24a1de]/18 px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-[#24a1de]/26 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-55"
              >
                <span className="inline-flex items-center justify-center gap-1.5">
                  {linkingTelegram ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageCircle className="h-3.5 w-3.5" />}
                  {linkingTelegram ? 'Ждем подтверждение...' : 'Привязать Telegram'}
                </span>
              </button>
            )}

            {telegramLinkError && (
              <p className="mt-3 rounded-xl border border-rose-300/15 bg-rose-500/10 px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-rose-100">
                {telegramLinkError}
              </p>
            )}
          </div>
        )}

        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4 space-y-3">
          <button
            type="button"
            onClick={() => toggleSettingsSection('vk')}
            aria-expanded={openSettingsSections.vk}
            className="w-full flex items-center justify-between gap-3 text-left transition-all active:scale-[0.99]"
          >
            <span className="flex min-w-0 items-center gap-3">
              <span
                className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white/15 bg-[#0077ff]/90 text-base font-black text-white"
                style={{ boxShadow: '0 0 20px rgba(0,119,255,0.36)' }}
              >
                VK
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-black uppercase tracking-wider text-white">
                  Синхронизация VK
                </span>
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span
                className={`rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${
                  vkMissingPermissionsCount === 0
                    ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-200'
                    : 'border-amber-300/25 bg-amber-400/10 text-amber-200'
                }`}
              >
                {vkMissingPermissionsCount === 0 ? 'Готово' : `${vkMissingPermissionsCount} нужно`}
              </span>
              <ChevronDown
                className={`h-4 w-4 text-cyan-200 transition-transform ${openSettingsSections.vk ? 'rotate-180' : ''}`}
              />
            </span>
          </button>

          {openSettingsSections.vk && (
            <div className="animate-slide-down space-y-3 border-t border-white/5 pt-3">
              {userProfile?.vk_user_id ? (
                <div className="rounded-xl border border-emerald-300/20 bg-emerald-400/10 px-3 py-2 text-[11px] font-black leading-relaxed text-emerald-100">
                  <span className="inline-flex items-center gap-2">
                    <Check className="h-4 w-4" />
                    VK ID привязан: {vkLinkedDisplayName || `ID ${userProfile.vk_user_id}`}
                  </span>
                </div>
              ) : (
                showVkSecureFallback ? (
                  <a
                    href={vkSecureAppUrl}
                    className="flex min-h-[46px] w-full items-center justify-center gap-2 rounded-xl border border-[#0077ff]/35 bg-[#0077ff]/15 px-3 py-2.5 text-center text-[11px] font-black uppercase tracking-wider text-white transition hover:bg-[#0077ff]/24 active:scale-[0.98]"
                    style={{ boxShadow: NEON_GLOW_BLUE }}
                  >
                    <ExternalLink className="h-4 w-4" />
                    Открыть защищенный вход VK
                  </a>
                ) : (
                  <button
                    type="button"
                    onClick={handleLinkVkProfile}
                    disabled={!vkReady || linkingVk}
                    className="min-h-[46px] w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-[11px] font-black uppercase tracking-wider text-white transition hover:border-[#0077ff]/45 hover:bg-[#0077ff]/10 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                    style={vkReady ? { boxShadow: NEON_GLOW_BLUE } : undefined}
                  >
                    <span className="inline-flex items-center justify-center gap-2">
                      {linkingVk ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Link className="h-4 w-4" />
                      )}
                      {linkingVk ? 'Синхронизируем VK...' : 'Синхронизировать VK'}
                    </span>
                  </button>
                )
              )}

              {vkLinkError && (
                <p className="rounded-xl border border-rose-300/15 bg-rose-500/10 px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-rose-100">
                  {vkLinkError}
                </p>
              )}

              {userProfile?.vk_user_id && (
                <div className="space-y-3">
                  <div className={`rounded-xl border px-3 py-3 ${
                    vkMessagesAllowed
                      ? 'border-emerald-300/20 bg-emerald-400/10'
                      : 'border-amber-300/25 bg-amber-400/10'
                  }`}>
                    <div className="flex items-start gap-3">
                      <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
                        vkMessagesAllowed ? 'bg-emerald-400/15 text-emerald-200' : 'bg-amber-400/15 text-amber-100'
                      }`}>
                        {vkMessagesAllowed ? <Check className="h-4 w-4" /> : <MessageCircle className="h-4 w-4" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-[10px] font-black uppercase tracking-wider text-slate-300">
                            Личные сообщения VK
                          </span>
                          <span className={`rounded-full border px-2 py-0.5 text-[9px] font-black uppercase tracking-wider ${
                            vkMessagesAllowed
                              ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-200'
                              : 'border-amber-300/25 bg-amber-400/10 text-amber-200'
                          }`}>
                            {vkMessagesAllowed ? 'доставка включена' : 'нужно разрешить'}
                          </span>
                        </div>
                      </div>
                    </div>

                    {!vkMessagesAllowed && (
                      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {vkMiniAppRuntime && (
                          <button
                            type="button"
                            onClick={handleAllowVkMessages}
                            disabled={vkPermissionBusy !== null || vkDeliveryLoading}
                            className="min-h-[44px] rounded-xl border border-white/10 bg-[#0077ff]/25 px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-[#0077ff]/35 active:scale-[0.98] disabled:opacity-50"
                          >
                            <span className="inline-flex items-center justify-center gap-1.5">
                              {vkPermissionBusy === 'messages' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageCircle className="h-3.5 w-3.5" />}
                              {vkPermissionBusy === 'messages' ? 'Запрашиваем...' : 'Разрешить'}
                            </span>
                          </button>
                        )}
                        {vkMessagesUrl && (
                          <button
                            type="button"
                            onClick={handleOpenVkDialog}
                            disabled={vkPermissionBusy !== null || vkDeliveryLoading}
                            className="min-h-[44px] rounded-xl border border-white/10 bg-[#0077ff]/18 px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-[#0077ff]/28 active:scale-[0.98] disabled:opacity-50"
                          >
                            <span className="inline-flex items-center justify-center gap-1.5">
                              <MessageCircle className="h-3.5 w-3.5" />
                              Открыть диалог VK
                            </span>
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={handleCheckVkDeliveryAccess}
                          disabled={vkPermissionBusy !== null || vkDeliveryLoading}
                          className="min-h-[44px] rounded-xl border border-white/10 bg-white/5 px-3 text-[10px] font-black uppercase tracking-wider text-slate-100 transition hover:bg-white/10 active:scale-[0.98] disabled:opacity-50"
                        >
                          <span className="inline-flex items-center justify-center gap-1.5">
                            {vkPermissionBusy === 'check' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                            {vkPermissionBusy === 'check' ? 'Проверяем...' : 'Проверить доступ'}
                          </span>
                        </button>
                      </div>
                    )}
                  </div>

                </div>
              )}
            </div>
          )}
        </div>

        {/* Earned Badges */}
        {!isAdminProfile && (
          <CollapsibleSection
            title="Ваши достижения"
            badge={
              (userProfile as any)?.badges?.length
                ? `${(userProfile as any).badges.length} получено`
                : 'Пусто'
            }
            icon={<Award className="w-3.5 h-3.5" style={{ color: ACCENT_PINK }} />}
            accent={ACCENT_PINK}
            isOpen={openSettingsSections.achievements}
            onToggle={() => toggleSettingsSection('achievements')}
          >
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
          </CollapsibleSection>
        )}

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
        <button
          type="button"
          onClick={() => toggleSettingsSection('notifications')}
          aria-expanded={openSettingsSections.notifications}
          className="w-full flex items-center justify-between gap-3 pb-1 border-b border-white/5 text-left transition-all active:scale-[0.99]"
        >
          <span className="flex items-center space-x-2 min-w-0">
            <Sliders className="w-5 h-5 shrink-0" style={{ color: ACCENT_BLUE }} />
            <span className="truncate text-sm font-black uppercase tracking-wider text-white">
              Умные уведомления
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
              {dashboardLoadFailed ? 'Ошибка' : loadingPrefs ? 'Загрузка' : `Кф ${prefs.alert_min_coef.toFixed(2)}`}
            </span>
            <ChevronDown
              className={`w-4 h-4 transition-transform ${openSettingsSections.notifications ? 'rotate-180' : ''}`}
              style={{ color: ACCENT_BLUE }}
            />
          </span>
        </button>

        {openSettingsSections.notifications && <div className="space-y-5 animate-slide-down">
          {dashboardLoadFailed ? (
            <div className="rounded-xl border border-amber-300/20 bg-amber-300/10 p-4 text-center text-xs font-bold leading-relaxed text-amber-50">
              Настройки уведомлений временно недоступны.
              <button
                type="button"
                onClick={retryDashboardLoad}
                className="mx-auto mt-3 flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                <span>Повторить</span>
              </button>
            </div>
          ) : loadingPrefs ? (
            <div className="flex justify-center py-6">
              <Loader2
                className="w-6 h-6 animate-spin"
                style={{ color: ACCENT_BLUE }}
              />
            </div>
          ) : (
            <>
            <AlertMinCoefSlider
              value={prefs.alert_min_coef}
              onCommit={handleAlertMinCoefCommit}
            />

            <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-3.5">
              <label className="flex items-center gap-2 text-xs font-semibold text-slate-300 min-w-0">
                <Bell className="w-3.5 h-3.5 shrink-0" style={{ color: ACCENT_PINK }} />
                <span className="min-w-0">
                  <span className="block text-white">Падение кэфа</span>
                  <span className="block text-[10px] text-slate-500 leading-snug">
                    Уведомлять при поле «Упал до»
                  </span>
                </span>
              </label>
              <button
                type="button"
                aria-pressed={prefs.odds_drop_notifications_enabled}
                onClick={() =>
                  setPrefs((p) => ({
                    ...p,
                    odds_drop_notifications_enabled: !p.odds_drop_notifications_enabled,
                  }))
                }
                aria-label="Падение кэфа"
                className={`relative h-8 w-14 overflow-hidden rounded-full border border-white/10 transition-colors shrink-0 ${
                  prefs.odds_drop_notifications_enabled ? '' : 'bg-slate-700'
                }`}
                style={
                  prefs.odds_drop_notifications_enabled
                    ? {
                        background: `linear-gradient(135deg, ${ACCENT_PINK}, ${ACCENT_BLUE})`,
                        boxShadow: NEON_GLOW_BLUE,
                      }
                    : {}
                }
              >
                <span
                  className="absolute inset-[3px] rounded-full bg-white/10"
                  aria-hidden="true"
                />
                <span
                  className={`absolute left-1 top-1 h-6 w-6 rounded-full bg-white shadow-[0_4px_14px_rgba(0,0,0,0.35)] transition-transform duration-150 ${
                    prefs.odds_drop_notifications_enabled ? 'translate-x-6' : 'translate-x-0'
                  }`}
                  aria-hidden="true"
                />
              </button>
            </div>

            {/* Ночной режим */}
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-3.5 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-xs font-semibold text-slate-300 min-w-0">
                  <Moon className="w-3.5 h-3.5 shrink-0" style={{ color: ACCENT_BLUE }} />
                  <span className="min-w-0">
                    <span className="block text-white">Ночной режим</span>
                    <span className="block text-[10px] text-slate-500 leading-snug">
                      Не беспокоить {prefs.night_mode_start}–{prefs.night_mode_end}
                    </span>
                  </span>
                </label>
                <button
                  type="button"
                  aria-pressed={prefs.is_night_mode}
                  onClick={() =>
                    setPrefs((p) => ({ ...p, is_night_mode: !p.is_night_mode }))
                  }
                  aria-label="Ночной режим"
                  className={`relative h-8 w-14 overflow-hidden rounded-full border border-white/10 transition-colors shrink-0 ${
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
                    className="absolute inset-[3px] rounded-full bg-white/10"
                    aria-hidden="true"
                  />
                  <span
                    className={`absolute left-1 top-1 h-6 w-6 bg-white rounded-full shadow-[0_4px_14px_rgba(0,0,0,0.35)] transition-transform duration-150 ${
                      prefs.is_night_mode ? 'translate-x-6' : 'translate-x-0'
                    }`}
                    aria-hidden="true"
                  />
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1.5">
                  <span className="block text-[9px] font-black uppercase tracking-wider text-slate-500">
                    С
                  </span>
                  <input
                    type="time"
                    value={prefs.night_mode_start}
                    onChange={(e) => updateNightModeTime('night_mode_start', e.currentTarget.value)}
                    onInput={(e) => updateNightModeTime('night_mode_start', e.currentTarget.value)}
                    className="w-full min-w-0 rounded-xl border border-white/10 bg-slate-950/55 px-3 py-2 text-sm font-black tabular-nums text-white outline-none transition focus:border-[#00d2ff]/50"
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="block text-[9px] font-black uppercase tracking-wider text-slate-500">
                    До
                  </span>
                  <input
                    type="time"
                    value={prefs.night_mode_end}
                    onChange={(e) => updateNightModeTime('night_mode_end', e.currentTarget.value)}
                    onInput={(e) => updateNightModeTime('night_mode_end', e.currentTarget.value)}
                    className="w-full min-w-0 rounded-xl border border-white/10 bg-slate-950/55 px-3 py-2 text-sm font-black tabular-nums text-white outline-none transition focus:border-[#00d2ff]/50"
                  />
                </label>
              </div>
            </div>

            </>
          )}

          <CollapsibleSection
            title="Букмекерские конторы"
            badge={
              dashboardLoadFailed
                ? 'Ошибка'
                : loadingBks
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

              {dashboardLoadFailed ? (
                <div className="rounded-xl border border-amber-300/20 bg-amber-300/10 p-4 text-center text-xs font-bold leading-relaxed text-amber-50">
                  Список букмекерских контор не загрузился.
                  <button
                    type="button"
                    onClick={retryDashboardLoad}
                    className="mx-auto mt-3 flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    <span>Повторить</span>
                  </button>
                </div>
              ) : loadingBks ? (
                <div className="flex justify-center py-4">
                  <Loader2
                    className="w-6 h-6 animate-spin"
                    style={{ color: ACCENT_BLUE }}
                  />
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2.5">
                  {bookmakers.map((bk) => {
                    const isChecked = selectedBkIds.includes(bk.id);
                    const isSaving = savingBkId === bk.id;
                    const isOther = isOtherBookmaker(bk);

                    return (
                      <label
                        key={bk.id}
                        className={`relative flex min-h-[76px] items-center gap-2.5 rounded-xl border p-2.5 text-xs font-semibold cursor-pointer transition-all ${
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
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleCheckboxChange(bk.id)}
                          className="sr-only"
                          disabled={isSaving}
                        />
                        {isOther ? (
                          <span
                            className="flex h-10 w-12 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.035] text-[10px] font-black uppercase tracking-[0.08em] text-cyan-100"
                            aria-hidden="true"
                          >
                            ...
                          </span>
                        ) : (
                          <BookmakerLogoFrame bookmaker={bk} active={isChecked} size="badge" className="h-10 w-12 shrink-0" />
                        )}
                        <div className="min-w-0 flex-1">
                          <span className="block break-words leading-tight">{bk.name}</span>
                        </div>
                        {isSaving ? (
                          <Loader2
                            className="absolute right-2.5 top-2.5 w-3.5 h-3.5 animate-spin"
                            style={{ color: ACCENT_BLUE }}
                          />
                        ) : isChecked ? (
                          <Check className="absolute right-2.5 top-2.5 w-4 h-4" style={{ color: ACCENT_BLUE }} />
                        ) : null}
                      </label>
                    );
                  })}
                  {otherBookmakerSelected && (
                    <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-xl p-2.5">
                      <EmojiTextField
                        type="text"
                        value={otherBookmakerName}
                        onValueChange={setOtherBookmakerName}
                        onBlur={saveOtherBookmakerName}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.currentTarget.blur();
                          }
                        }}
                        placeholder="Напишите название БК"
                        containerClassName="flex-1 min-w-0"
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

          {!dashboardLoadFailed && !loadingPrefs && (
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
        </div>}
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
                В разработке
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
            {dashboardLoadFailed ? (
              <div className="bg-slate-900/40 border border-amber-300/20 p-4 rounded-xl text-center text-amber-100 text-xs font-semibold uppercase tracking-wider">
                Реферальные данные не загрузились
                <button
                  type="button"
                  onClick={retryDashboardLoad}
                  className="mx-auto mt-3 flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  <span>Повторить</span>
                </button>
              </div>
            ) : loadingRef ? (
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
                      className="min-h-7 flex items-center justify-center text-[10px] font-black uppercase leading-tight"
                      style={{ color: ACCENT_PINK }}
                    >
                      В разработке
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
              {dashboardLoadFailed ? 'Ошибка' : loadingPay ? 'Загрузка' : 'В разработке'}
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
            {dashboardLoadFailed ? (
              <div className="bg-slate-900/40 border border-amber-300/20 p-4 rounded-xl text-center text-amber-100 text-xs font-semibold uppercase tracking-wider leading-relaxed">
                История платежей не загрузилась.
                <button
                  type="button"
                  onClick={retryDashboardLoad}
                  className="mx-auto mt-3 flex min-h-[38px] items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-black text-white transition-all hover:bg-white/[0.1] active:scale-[0.98]"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  <span>Повторить</span>
                </button>
              </div>
            ) : loadingPay ? (
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
