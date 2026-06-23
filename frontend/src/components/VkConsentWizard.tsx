import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Loader2,
  MessageCircle,
  RefreshCw,
  ShieldCheck,
  X,
} from 'lucide-react';
import { useAuthActions, useAuthSelector } from '../context/AuthContext';
import { isStaffRole } from '../utils/roles';
import {
  detectVkMiniAppRuntime,
  fetchVkDeliveryStatus,
  getVkGroupInfo,
  getVkMessagesUrl,
  isVkMiniAppRuntime,
  requestVkMessagesPermission,
} from '../utils/vkDelivery';
import type { VkConsentStepResult, VkDeliveryStatus, VkGroupInfo } from '../utils/vkDelivery';

const SESSION_PREFIX = 'shamrai_vk_consent_wizard_shown:';

function statusComplete(status: VkDeliveryStatus | null): boolean {
  return Boolean(status?.messages_allowed);
}

function stepState(status: VkDeliveryStatus | null, results: VkConsentStepResult[], step: VkConsentStepResult['step']) {
  const result = results.find((item) => item.step === step);
  if (result) return result.ok ? 'done' : 'failed';
  if (step === 'group' && status?.group_member) return 'done';
  if (step === 'messages' && status?.messages_allowed) return 'done';
  if (step === 'notifications' && status?.notifications_allowed) return 'done';
  return 'pending';
}

export default function VkConsentWizard() {
  const user = useAuthSelector((state) => state.user);
  const { setUser } = useAuthActions();
  const [status, setStatus] = useState<VkDeliveryStatus | null>(null);
  const [groupInfo, setGroupInfo] = useState<VkGroupInfo | null>(null);
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [vkRuntime, setVkRuntime] = useState(() => isVkMiniAppRuntime());
  const [fallbackVisible, setFallbackVisible] = useState(false);
  const [results, setResults] = useState<VkConsentStepResult[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const sessionKey = user?.vk_user_id ? `${SESSION_PREFIX}${user.vk_user_id}` : '';

  useEffect(() => {
    let cancelled = false;
    detectVkMiniAppRuntime().then((ready) => {
      if (!cancelled) setVkRuntime(ready);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      if (!user?.vk_user_id || isStaffRole(user.role)) {
        setVisible(false);
        return;
      }
      try {
        const nextStatus = await fetchVkDeliveryStatus();
        if (cancelled) return;
        setStatus(nextStatus);
        setUser((current) => current ? {
          ...current,
          vk_group_member: nextStatus.group_member,
          vk_messages_allowed: nextStatus.messages_allowed,
          vk_notifications_allowed: nextStatus.notifications_allowed,
        } : current);
        if (vkRuntime && !statusComplete(nextStatus) && !sessionStorage.getItem(sessionKey)) {
          sessionStorage.setItem(sessionKey, '1');
          setVisible(true);
        }
        if (vkRuntime) {
          const info = await getVkGroupInfo(nextStatus.group_id);
          if (!cancelled) setGroupInfo(info);
        }
      } catch (error: any) {
        if (!cancelled) setLoadError(error?.message || 'Не удалось проверить VK-разрешения');
      }
    }

    void loadStatus();
    return () => {
      cancelled = true;
    };
  }, [sessionKey, setUser, user?.role, user?.vk_user_id, vkRuntime]);

  const finalStatus = useMemo(() => {
    if (!results.length) return status;
    return results[results.length - 1].status;
  }, [results, status]);

  const groupName = groupInfo?.name || 'Shamrai';
  const messagesUrl = getVkMessagesUrl(finalStatus?.group_id);

  const applyStatusToUser = useCallback((nextStatus: VkDeliveryStatus) => {
    setStatus(nextStatus);
    setUser((current) => current ? {
      ...current,
      vk_group_member: nextStatus.group_member,
      vk_messages_allowed: nextStatus.messages_allowed,
      vk_notifications_allowed: nextStatus.notifications_allowed,
    } : current);
  }, [setUser]);

  const handleAllowMessages = useCallback(async () => {
    if (!status || busy) return;
    setBusy(true);
    setResults([]);
    setLoadError(null);
    try {
      const nextStatus = await requestVkMessagesPermission(status.group_id);
      setResults([{ step: 'messages', ok: true, status: nextStatus }]);
      applyStatusToUser(nextStatus);
      setFallbackVisible(!nextStatus.messages_allowed);
    } catch (error: any) {
      const message = error?.message || 'Не удалось открыть VK-разрешения';
      setResults([{ step: 'messages', ok: false, status, error: message }]);
      setLoadError(message);
      setFallbackVisible(true);
    } finally {
      setBusy(false);
    }
  }, [applyStatusToUser, busy, status]);

  const handleCheckMessages = useCallback(async () => {
    if (checking) return;
    setChecking(true);
    setLoadError(null);
    try {
      const nextStatus = await fetchVkDeliveryStatus();
      applyStatusToUser(nextStatus);
      setResults([{ step: 'messages', ok: nextStatus.messages_allowed, status: nextStatus }]);
      setFallbackVisible(!nextStatus.messages_allowed);
      if (!nextStatus.messages_allowed) {
        setLoadError('VK пока не подтвердил доступ. Напишите любое сообщение в диалог и проверьте еще раз.');
      }
    } catch (error: any) {
      setLoadError(error?.message || 'Не удалось проверить VK-доступ');
    } finally {
      setChecking(false);
    }
  }, [applyStatusToUser, checking]);

  if (!visible || !user?.vk_user_id || !status) return null;

  const steps = [
    {
      id: 'messages' as const,
      title: 'Личные сообщения',
      caption: finalStatus?.messages_allowed ? 'Доставка включена' : 'Разрешить сообщения',
      icon: MessageCircle,
    },
  ];
  const failedSteps = results.filter((item) => !item.ok);
  const complete = statusComplete(finalStatus);
  const showFallback = !complete && (fallbackVisible || !vkRuntime);

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center overflow-y-auto bg-slate-950/68 px-4 py-4 backdrop-blur-xl sm:items-center">
      <div className="relative max-h-[calc(100dvh-2rem)] w-full max-w-md overflow-y-auto rounded-3xl border border-white/10 bg-[#070B19]/95 p-5 text-slate-50 shadow-[0_28px_90px_rgba(0,0,0,0.55)]">
        <button
          type="button"
          onClick={() => setVisible(false)}
          className="absolute right-3 top-3 flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/5 text-slate-300 transition hover:bg-white/10"
          aria-label="Закрыть"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-[#0077ff]/20 blur-3xl" aria-hidden="true" />
        <div className="pointer-events-none absolute -bottom-20 left-8 h-40 w-40 rounded-full bg-emerald-400/10 blur-3xl" aria-hidden="true" />

        <div className="relative space-y-4">
          <div className="flex items-start gap-3 pr-9">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-[#0077ff]/35 bg-[#0077ff]/18">
              <ShieldCheck className="h-6 w-6 text-cyan-200" />
            </div>
            <div className="min-w-0">
              <p className="text-[10px] font-black uppercase tracking-[0.18em] text-cyan-200/80">
                VK доставка
              </p>
              <h3 className="mt-1 text-base font-black leading-tight text-white">
                Подключаем {groupName}
              </h3>
            </div>
          </div>

          <div className="space-y-2">
            {steps.map((step) => {
              const Icon = step.icon;
              const state = stepState(finalStatus, results, step.id);
              const isDone = state === 'done';
              const isFailed = state === 'failed';
              return (
                <div
                  key={step.id}
                  className={`flex min-h-[58px] items-center gap-3 rounded-2xl border px-3 py-2.5 ${
                    isDone
                      ? 'border-emerald-300/20 bg-emerald-400/10'
                      : isFailed
                        ? 'border-rose-300/20 bg-rose-500/10'
                        : 'border-white/10 bg-white/[0.035]'
                  }`}
                >
                  <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${
                    isDone ? 'bg-emerald-400/15 text-emerald-200' : isFailed ? 'bg-rose-400/15 text-rose-200' : 'bg-white/[0.08] text-cyan-200'
                  }`}>
                    {isDone ? <Check className="h-4 w-4" /> : isFailed ? <AlertTriangle className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-black uppercase tracking-wider text-white">{step.title}</p>
                  </div>
                  {!isDone && vkRuntime && (
                    <button
                      type="button"
                      onClick={handleAllowMessages}
                      disabled={busy}
                      className="min-h-[38px] shrink-0 rounded-xl bg-[#0077ff] px-3 text-[10px] font-black uppercase tracking-wider text-white transition hover:brightness-110 disabled:opacity-60"
                    >
                      <span className="inline-flex items-center justify-center gap-1.5">
                        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <MessageCircle className="h-3.5 w-3.5" />}
                        {busy ? 'VK...' : 'Разрешить'}
                      </span>
                    </button>
                  )}
                </div>
              );
            })}
          </div>

          {loadError && (
            <p className="rounded-2xl border border-rose-300/20 bg-rose-500/10 px-3 py-2.5 text-xs font-semibold leading-relaxed text-rose-100">
              {loadError}
            </p>
          )}

          {showFallback && (
            <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-3">
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {messagesUrl && (
                  <a
                    href={messagesUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="flex min-h-[42px] items-center justify-center gap-1.5 rounded-xl border border-[#0077ff]/35 bg-[#0077ff]/18 px-3 text-center text-[10px] font-black uppercase tracking-wider text-white transition hover:bg-[#0077ff]/28"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Открыть диалог VK
                  </a>
                )}
                <button
                  type="button"
                  onClick={handleCheckMessages}
                  disabled={checking}
                  className="min-h-[42px] rounded-xl border border-white/10 bg-white/5 px-3 text-[10px] font-black uppercase tracking-wider text-slate-100 transition hover:bg-white/10 disabled:opacity-60"
                >
                  <span className="inline-flex items-center justify-center gap-1.5">
                    {checking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    {checking ? 'Проверяем...' : 'Я написал, проверить'}
                  </span>
                </button>
              </div>
            </div>
          )}

          <div className="flex gap-2">
            {vkRuntime && !complete && (
              <button
                type="button"
                onClick={handleAllowMessages}
                disabled={busy}
                className="min-h-[44px] flex-1 rounded-2xl bg-[#0077ff] px-4 text-xs font-black uppercase tracking-wider text-white shadow-[0_0_24px_rgba(0,119,255,0.32)] transition hover:brightness-110 disabled:opacity-60"
              >
                <span className="inline-flex items-center justify-center gap-2">
                  {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                  {busy ? 'Открываем VK' : failedSteps.length ? 'Повторить' : 'Разрешить сообщения'}
                </span>
              </button>
            )}
            <button
              type="button"
              onClick={() => setVisible(false)}
              className="min-h-[44px] flex-1 rounded-2xl border border-white/10 bg-white/5 px-4 text-xs font-black uppercase tracking-wider text-slate-200 transition hover:bg-white/10"
            >
              {complete ? 'Готово' : 'Пропустить'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
