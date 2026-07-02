import React, { useEffect, useMemo, useState } from 'react';
import {
  Calendar,
  Check,
  ExternalLink,
  HelpCircle,
  Link as LinkIcon,
  Loader2,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
  Trash2,
  X,
} from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { BetResponse, BookmakerResponse } from '../../schemas/schemas';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import BookmakerMultiSelect from '../../components/BookmakerMultiSelect';
import { notifyError, notifySuccess } from '../../utils/notify';

interface ResultBet extends BetResponse {
  isFading?: boolean;
}

interface OddsDropNotifyResponse {
  bet: BetResponse;
  total: number;
  sent: number;
  queued?: number;
  failed: number;
  errors: string[];
}

interface EditDraft {
  event_name: string;
  coefficient: string;
  outcome: string;
  sport_type: string;
  selectedBookmakerIds: number[];
  bookmakerLinks: Record<number, string>;
}

const resultActions: Array<{
  status: 'win' | 'loss' | 'refund';
  label: string;
  Icon: typeof Check;
  className: string;
}> = [
  {
    status: 'win',
    label: 'Победа',
    Icon: Check,
    className:
      'border-emerald-400/35 bg-emerald-500/12 text-emerald-300 hover:bg-emerald-500 hover:text-slate-950',
  },
  {
    status: 'loss',
    label: 'Неудача',
    Icon: X,
    className:
      'border-[#ff007f]/35 bg-[#ff007f]/12 text-[#ff3d9c] hover:bg-[#ff007f] hover:text-white',
  },
  {
    status: 'refund',
    label: 'Возврат',
    Icon: RefreshCw,
    className:
      'border-cyan-300/20 bg-cyan-500/10 text-slate-300 hover:bg-cyan-500/20 hover:text-white',
  },
];

function getBetBookmakers(bet: BetResponse): BookmakerResponse[] {
  if (bet.bookmakers?.length) return bet.bookmakers;
  return bet.bookmaker ? [bet.bookmaker] : [];
}

function getBookmakerLinkUrl(bet: BetResponse, bookmakerId: number) {
  const link = bet.bookmaker_links?.find((item) => Number(item.bookmaker_id) === bookmakerId);
  return link?.url?.trim() || null;
}

function formatDate(value: string) {
  return new Date(value).toLocaleDateString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

function formatOddsInput(value: BetResponse['odds_dropped_to']) {
  if (value === null || value === undefined || value === '') return '';
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) : String(value);
}

function normalizeOddsInput(value: string) {
  return value.trim().replace(',', '.');
}

function buildEditDraft(bet: BetResponse): EditDraft {
  const selectedBookmakerIds = getBetBookmakers(bet)
    .map((bookmaker) => bookmaker.id)
    .filter((bookmakerId, index, ids) => bookmakerId && ids.indexOf(bookmakerId) === index);

  const bookmakerLinks = (bet.bookmaker_links || []).reduce<Record<number, string>>((acc, link) => {
    if (link.bookmaker_id && link.url) {
      acc[Number(link.bookmaker_id)] = link.url;
    }
    return acc;
  }, {});

  return {
    event_name: bet.event_name || '',
    coefficient: formatOddsInput(bet.coefficient),
    outcome: bet.outcome || '',
    sport_type: bet.sport_type || '',
    selectedBookmakerIds,
    bookmakerLinks,
  };
}

export default function AdminResults() {
  const [bets, setBets] = useState<ResultBet[]>([]);
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [savingEditId, setSavingEditId] = useState<string | null>(null);
  const [editDrafts, setEditDrafts] = useState<Record<string, EditDraft>>({});
  const [oddsDropInputs, setOddsDropInputs] = useState<Record<string, string>>({});
  const [savingOddsDropId, setSavingOddsDropId] = useState<string | null>(null);
  const [sendingOddsDropId, setSendingOddsDropId] = useState<string | null>(null);

  const counters = useMemo(() => {
    const feed = bets.filter((bet) => bet.delivery_mode === 'feed').length;
    const privateCount = bets.length - feed;
    return { feed, privateCount };
  }, [bets]);

  const loadResults = async (quiet = false) => {
    try {
      if (quiet) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }
      const data = await apiFetch<ResultBet[]>('/admin/bets/pending');
      setBets(data);
      setOddsDropInputs((current) => {
        const next = { ...current };
        data.forEach((bet) => {
          if (!(bet.id in next)) {
            next[bet.id] = formatOddsInput(bet.odds_dropped_to);
          }
        });
        Object.keys(next).forEach((betId) => {
          if (!data.some((bet) => bet.id === betId)) {
            delete next[betId];
          }
        });
        return next;
      });
    } catch (err: any) {
      notifyError(err.message || 'Не удалось загрузить матчи для расчета');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const loadBookmakers = async () => {
    try {
      const data = await apiFetch<BookmakerResponse[]>('/bookmakers');
      setBookmakers(data);
    } catch (err: any) {
      notifyError(err.message || 'Не удалось загрузить список БК для редактирования');
    }
  };

  useEffect(() => {
    loadResults();
    loadBookmakers();
  }, []);

  const handleResolve = async (betId: string, status: 'win' | 'loss' | 'refund') => {
    try {
      setResolvingId(betId);
      const result = await apiFetch(`/bets/${betId}/resolve`, {
        method: 'PUT',
        body: JSON.stringify({ status }),
      });

      if (status === 'loss' && result.guarantee_count > 0) {
        notifySuccess(`Гарантия открыта/продлена для ${result.guarantee_count} клиентов`);
      } else if (status === 'refund' && result.refund_count > 0) {
        notifySuccess(`Возвращено ${result.refund_count} матчей клиентам`);
      } else {
        notifySuccess('Результат матча сохранен');
      }

      setBets((current) =>
        current.map((bet) => (bet.id === betId ? { ...bet, isFading: true } : bet)),
      );
      window.setTimeout(() => {
        setBets((current) => current.filter((bet) => bet.id !== betId));
      }, 280);
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить результат');
    } finally {
      setResolvingId(null);
    }
  };

  const updateBetFromResponse = (updatedBet: BetResponse) => {
    setBets((current) =>
      current.map((bet) => (bet.id === updatedBet.id ? { ...bet, ...updatedBet } : bet)),
    );
    setOddsDropInputs((current) => ({
      ...current,
      [updatedBet.id]: formatOddsInput(updatedBet.odds_dropped_to),
    }));
  };

  const handleStartEdit = (bet: BetResponse) => {
    setEditDrafts((current) => ({
      ...current,
      [bet.id]: current[bet.id] || buildEditDraft(bet),
    }));
    setEditingId((current) => (current === bet.id ? null : bet.id));
  };

  const handleCancelEdit = (bet: BetResponse) => {
    setEditDrafts((current) => ({
      ...current,
      [bet.id]: buildEditDraft(bet),
    }));
    setEditingId(null);
  };

  const updateEditDraft = (betId: string, patch: Partial<EditDraft>) => {
    setEditDrafts((current) => {
      const sourceBet = bets.find((bet) => bet.id === betId);
      const currentDraft = current[betId] || (sourceBet ? buildEditDraft(sourceBet) : null);
      if (!currentDraft) return current;
      return {
        ...current,
        [betId]: { ...currentDraft, ...patch },
      };
    });
  };

  const handleEditBookmakers = (betId: string, selectedBookmakerIds: number[]) => {
    setEditDrafts((current) => {
      const sourceBet = bets.find((bet) => bet.id === betId);
      const currentDraft = current[betId] || (sourceBet ? buildEditDraft(sourceBet) : null);
      if (!currentDraft) return current;
      const selectedSet = new Set(selectedBookmakerIds);
      const bookmakerLinks = Object.entries(currentDraft.bookmakerLinks).reduce<Record<number, string>>(
        (acc, [rawId, url]) => {
          const bookmakerId = Number(rawId);
          if (selectedSet.has(bookmakerId)) acc[bookmakerId] = url;
          return acc;
        },
        {},
      );
      return {
        ...current,
        [betId]: {
          ...currentDraft,
          selectedBookmakerIds,
          bookmakerLinks,
        },
      };
    });
  };

  const handleEditBookmakerLink = (betId: string, bookmakerId: number, url: string) => {
    setEditDrafts((current) => {
      const sourceBet = bets.find((bet) => bet.id === betId);
      const currentDraft = current[betId] || (sourceBet ? buildEditDraft(sourceBet) : null);
      if (!currentDraft) return current;
      return {
        ...current,
        [betId]: {
          ...currentDraft,
          bookmakerLinks: {
            ...currentDraft.bookmakerLinks,
            [bookmakerId]: url,
          },
        },
      };
    });
  };

  const handleSaveEdit = async (bet: BetResponse) => {
    const draft = editDrafts[bet.id] || buildEditDraft(bet);
    const eventName = draft.event_name.trim();
    const coefficient = normalizeOddsInput(draft.coefficient);

    if (!coefficient) {
      notifyError('Укажите коэффициент');
      return;
    }

    try {
      setSavingEditId(bet.id);
      const updatedBet = await apiFetch<BetResponse>(`/bets/${bet.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          event_name: eventName,
          coefficient,
          outcome: draft.outcome.trim() || null,
          sport_type: draft.sport_type.trim() || null,
          bookmaker_id: draft.selectedBookmakerIds[0] || null,
          bookmaker_ids: draft.selectedBookmakerIds,
          bookmaker_links: draft.selectedBookmakerIds
            .map((bookmakerId) => ({
              bookmaker_id: bookmakerId,
              url: (draft.bookmakerLinks[bookmakerId] || '').trim(),
            }))
            .filter((link) => link.url),
        }),
      });
      updateBetFromResponse(updatedBet);
      setEditDrafts((current) => ({
        ...current,
        [updatedBet.id]: buildEditDraft(updatedBet),
      }));
      setEditingId(null);
      notifySuccess('Прогноз обновлен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить прогноз');
    } finally {
      setSavingEditId(null);
    }
  };

  const handleOddsDropInput = (betId: string, value: string) => {
    setOddsDropInputs((current) => ({ ...current, [betId]: value }));
  };

  const handleSaveOddsDrop = async (betId: string) => {
    const rawValue = oddsDropInputs[betId] ?? '';
    const normalizedValue = normalizeOddsInput(rawValue);
    const payload = {
      odds_dropped_to: normalizedValue ? normalizedValue : null,
    };

    try {
      setSavingOddsDropId(betId);
      const updatedBet = await apiFetch<BetResponse>(`/bets/${betId}/odds-drop`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      updateBetFromResponse(updatedBet);
      notifySuccess(normalizedValue ? 'Коэффициент падения сохранен' : 'Коэффициент падения очищен');
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить коэффициент');
    } finally {
      setSavingOddsDropId(null);
    }
  };

  const handleSendOddsDrop = async (betId: string) => {
    const normalizedValue = normalizeOddsInput(oddsDropInputs[betId] ?? '');
    if (!normalizedValue) {
      notifyError('Сначала укажите коэффициент в поле «Упал до»');
      return;
    }

    try {
      setSendingOddsDropId(betId);
      const result = await apiFetch<OddsDropNotifyResponse>(`/bets/${betId}/odds-drop/notify`, {
        method: 'POST',
        body: JSON.stringify({ odds_dropped_to: normalizedValue }),
      });
      updateBetFromResponse(result.bet);
      const queued = result.queued ?? 0;
      if (queued > 0) {
        notifySuccess(
          result.failed > 0
            ? `Поставлено в очередь: ${queued} из ${result.total}. Ошибок: ${result.failed}`
            : `Уведомления поставлены в очередь: ${queued}`,
        );
      } else if (result.failed > 0) {
        notifySuccess(`Отправлено ${result.sent} из ${result.total}. Ошибок: ${result.failed}`);
      } else {
        notifySuccess(`Сообщение отправлено клиентам: ${result.sent}`);
      }
    } catch (err: any) {
      notifyError(err.message || 'Не удалось отправить сообщение клиентам');
    } finally {
      setSendingOddsDropId(null);
    }
  };

  const removeBetFromList = (betId: string) => {
    setBets((current) =>
      current.map((bet) => (bet.id === betId ? { ...bet, isFading: true } : bet)),
    );
    window.setTimeout(() => {
      setBets((current) => current.filter((bet) => bet.id !== betId));
    }, 280);
  };

  const handleDelete = async (bet: ResultBet) => {
    const confirmed = window.confirm(
      `Удалить прогноз «${bet.event_name}» и вернуть доступы клиентам?`,
    );
    if (!confirmed) return;

    try {
      setDeletingId(bet.id);
      const result = await apiFetch<{ revoked_count?: number; balance_delta_total?: number }>(
        `/admin/bets/${bet.id}`,
        { method: 'DELETE' },
      );
      const revokedCount = Number(result.revoked_count || 0);
      notifySuccess(
        revokedCount > 0
          ? `Прогноз удален, доступ снят у ${revokedCount} клиентов`
          : 'Прогноз удален',
      );
      removeBetFromList(bet.id);
    } catch (err: any) {
      notifyError(err.message || 'Не удалось удалить прогноз');
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center text-xs font-black uppercase tracking-wider text-slate-300">
            <HelpCircle className="mr-2 h-4 w-4 shrink-0 text-cyan-300" />
            Результаты матчей
          </h3>
          <p className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
            Лента: {counters.feed} · Выдачи: {counters.privateCount}
          </p>
        </div>
        <button
          type="button"
          onClick={() => loadResults(true)}
          disabled={refreshing}
          className="shrink-0 rounded-xl border border-white/10 bg-white/5 p-2 text-cyan-200 transition-all hover:bg-white/10 disabled:opacity-50"
          aria-label="Обновить результаты"
        >
          <RotateCcw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {bets.length === 0 ? (
        <div className="rounded-2xl border border-white/10 bg-white/5 p-6 text-center text-xs font-semibold text-slate-500">
          Нет матчей, ожидающих результата.
        </div>
      ) : (
        <div className="space-y-3">
          {bets.map((bet) => {
            const betBookmakers = getBetBookmakers(bet);
            const isResolving = resolvingId === bet.id;
            const isDeleting = deletingId === bet.id;
            const isEditing = editingId === bet.id;
            const isSavingEdit = savingEditId === bet.id;
            const isBusy = isResolving || isDeleting || isSavingEdit;
            const isSavingOddsDrop = savingOddsDropId === bet.id;
            const isSendingOddsDrop = sendingOddsDropId === bet.id;
            const oddsDropValue = oddsDropInputs[bet.id] ?? formatOddsInput(bet.odds_dropped_to);
            const hasOddsDropValue = normalizeOddsInput(oddsDropValue).length > 0;
            const editDraft = editDrafts[bet.id] || buildEditDraft(bet);
            const editSelectedBookmakers = bookmakers.filter((bookmaker) =>
              editDraft.selectedBookmakerIds.includes(bookmaker.id),
            );

            return (
              <div
                key={bet.id}
                className={`relative overflow-hidden rounded-2xl border border-white/10 bg-[#20102c]/78 p-4 shadow-[0_0_26px_rgba(255,0,127,0.12)] transition-all duration-300 ${
                  bet.isFading ? 'scale-[0.98] opacity-0' : 'scale-100 opacity-100'
                }`}
              >
                <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan-300/45 to-transparent" />

                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h4 className="text-sm font-black leading-snug text-white">
                      {bet.event_name}
                    </h4>
                    {bet.outcome && (
                      <div className="mt-1 text-[10px] font-black uppercase tracking-wider text-indigo-200">
                        {bet.outcome}
                      </div>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="inline-flex items-center rounded-md text-[9px] font-bold text-slate-500">
                        <Calendar className="mr-1 h-3 w-3" />
                        {formatDate(bet.created_at)}
                      </span>
                      {bet.sport_type && (
                        <span className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-1.5 py-0.5 text-[8.5px] font-black uppercase text-slate-300">
                          <SportIconFrame label={bet.sport_type} size="tiny" />
                          {bet.sport_type}
                        </span>
                      )}
                      <span className="rounded-md border border-cyan-300/15 bg-cyan-400/8 px-1.5 py-0.5 text-[8.5px] font-black uppercase text-cyan-200">
                        {bet.delivery_mode === 'feed' ? 'Лента' : 'Выдача'}
                      </span>
                    </div>
                  </div>

                  <span className="shrink-0 rounded-lg border border-emerald-400/25 bg-emerald-500/12 px-2.5 py-1 text-xs font-black text-emerald-300 shadow-[0_0_18px_rgba(16,185,129,0.18)]">
                    кф. {Number(bet.coefficient).toFixed(2)}
                  </span>
                </div>

                {betBookmakers.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {betBookmakers.map((bookmaker) => {
                      const bookmakerUrl = getBookmakerLinkUrl(bet, bookmaker.id);
                      const content = (
                        <>
                          <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="h-7 shrink-0" />
                          <span className="min-w-0 truncate">{bookmaker.name}</span>
                          {bookmakerUrl && <ExternalLink className="h-3 w-3 shrink-0 text-cyan-200/85" />}
                        </>
                      );

                      return bookmakerUrl ? (
                        <a
                          key={bookmaker.id}
                          href={bookmakerUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex max-w-full items-center gap-2 rounded-xl border border-cyan-300/20 bg-slate-950/45 px-2.5 py-1.5 text-[10px] font-black text-slate-100 transition-all hover:border-cyan-300/45 hover:bg-cyan-400/10"
                        >
                          {content}
                        </a>
                      ) : (
                        <span
                          key={bookmaker.id}
                          className="inline-flex max-w-full items-center gap-2 rounded-xl border border-white/10 bg-slate-950/35 px-2.5 py-1.5 text-[10px] font-black text-slate-400"
                        >
                          {content}
                        </span>
                      );
                    })}
                  </div>
                )}

                {isEditing && (
                  <div className="mt-3 rounded-xl border border-indigo-300/18 bg-slate-950/42 p-3">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-[9px] font-black uppercase tracking-wider text-indigo-200">
                        <Pencil className="h-3.5 w-3.5" />
                        Редактирование
                      </span>
                      <button
                        type="button"
                        onClick={() => handleCancelEdit(bet)}
                        disabled={isSavingEdit}
                        className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-[9px] font-black uppercase tracking-wider text-slate-400 transition-all hover:bg-white/10 hover:text-white disabled:opacity-50"
                      >
                        Отмена
                      </button>
                    </div>

                    <div className="grid grid-cols-1 gap-2">
                      <label className="grid gap-1">
                        <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">
                          Матч <span className="normal-case tracking-normal">(необязательно)</span>
                        </span>
                        <input
                          type="text"
                          value={editDraft.event_name}
                          onChange={(event) => updateEditDraft(bet.id, { event_name: event.target.value })}
                          disabled={isSavingEdit || bet.isFading}
                          className="h-10 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-black text-white outline-none transition-all placeholder:text-slate-600 focus:border-indigo-300/55 disabled:opacity-55"
                        />
                      </label>

                      <div className="grid grid-cols-2 gap-2">
                        <label className="grid gap-1">
                          <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">
                            Исход
                          </span>
                          <input
                            type="text"
                            value={editDraft.outcome}
                            onChange={(event) => updateEditDraft(bet.id, { outcome: event.target.value })}
                            disabled={isSavingEdit || bet.isFading}
                            className="h-10 min-w-0 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-bold text-white outline-none transition-all placeholder:text-slate-600 focus:border-indigo-300/55 disabled:opacity-55"
                          />
                        </label>
                        <label className="grid gap-1">
                          <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">
                            Кэф
                          </span>
                          <input
                            type="text"
                            inputMode="decimal"
                            value={editDraft.coefficient}
                            onChange={(event) => updateEditDraft(bet.id, { coefficient: event.target.value })}
                            disabled={isSavingEdit || bet.isFading}
                            className="h-10 min-w-0 rounded-xl border border-emerald-300/20 bg-black/[0.24] px-3 text-xs font-black text-emerald-100 outline-none transition-all placeholder:text-slate-600 focus:border-emerald-300/55 disabled:opacity-55"
                          />
                        </label>
                      </div>

                      <label className="grid gap-1">
                        <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">
                          Вид спорта <span className="normal-case tracking-normal">(необязательно)</span>
                        </span>
                        <input
                          type="text"
                          value={editDraft.sport_type}
                          onChange={(event) => updateEditDraft(bet.id, { sport_type: event.target.value })}
                          disabled={isSavingEdit || bet.isFading}
                          placeholder="Футбол"
                          className="h-10 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-bold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
                        />
                      </label>

                      <div className="rounded-xl border border-white/10 bg-black/[0.16] p-2.5">
                        <BookmakerMultiSelect
                          label="Букмекеры"
                          bookmakers={bookmakers}
                          selectedIds={editDraft.selectedBookmakerIds}
                          onChange={(ids) => handleEditBookmakers(bet.id, ids)}
                          disabled={isSavingEdit || bet.isFading}
                          allowAll={false}
                        />
                      </div>

                      {editSelectedBookmakers.length > 0 && (
                        <div className="space-y-2">
                          <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-wider text-cyan-200">
                            <LinkIcon className="h-3.5 w-3.5" />
                            Ссылки БК <span className="normal-case tracking-normal">(необязательно)</span>
                          </div>
                          {editSelectedBookmakers.map((bookmaker) => (
                            <div
                              key={bookmaker.id}
                              className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,8rem)_minmax(0,1fr)]"
                            >
                              <div className="flex min-w-0 items-center gap-2 rounded-xl border border-white/10 bg-black/[0.18] px-2 py-1.5">
                                <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="h-7 shrink-0" />
                                <span className="min-w-0 truncate text-[10px] font-black text-slate-100">
                                  {bookmaker.name}
                                </span>
                              </div>
                              <input
                                type="url"
                                value={editDraft.bookmakerLinks[bookmaker.id] || ''}
                                onChange={(event) => handleEditBookmakerLink(bet.id, bookmaker.id, event.target.value)}
                                disabled={isSavingEdit || bet.isFading}
                                placeholder="https://..."
                                className="h-10 min-w-0 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
                              />
                            </div>
                          ))}
                        </div>
                      )}

                      <button
                        type="button"
                        onClick={() => handleSaveEdit(bet)}
                        disabled={isSavingEdit || bet.isFading}
                        className="mt-1 flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl border border-indigo-300/30 bg-indigo-500/16 px-3 text-[10px] font-black uppercase tracking-wider text-indigo-100 transition-all hover:border-indigo-300/60 hover:bg-indigo-500/24 disabled:cursor-not-allowed disabled:opacity-55"
                      >
                        {isSavingEdit ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                        <span>Сохранить изменения</span>
                      </button>
                    </div>
                  </div>
                )}

                <div className="mt-3 rounded-xl border border-emerald-400/15 bg-slate-950/38 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[9px] font-black uppercase tracking-wider text-emerald-200">
                      Упал до
                    </span>
                    {bet.odds_drop_notified_at && (
                      <span className="truncate text-[8.5px] font-bold uppercase tracking-wider text-slate-500">
                        отправлено {formatDate(bet.odds_drop_notified_at)}
                      </span>
                    )}
                  </div>
                  <div className="mt-2 grid grid-cols-[minmax(0,4.5rem)_42px_minmax(0,1fr)] gap-2">
                    <input
                      type="text"
                      inputMode="decimal"
                      value={oddsDropValue}
                      onChange={(event) => handleOddsDropInput(bet.id, event.target.value)}
                      disabled={isBusy || isSavingOddsDrop || isSendingOddsDrop || bet.isFading}
                      placeholder="1.50"
                      className="h-10 min-w-0 appearance-none rounded-xl border border-emerald-300/20 bg-black/[0.24] px-2.5 text-sm font-black text-emerald-100 outline-none transition-all [color-scheme:dark] placeholder:text-slate-600 focus:border-emerald-300/55 focus:bg-black/35 disabled:bg-black/[0.24] disabled:text-slate-500 disabled:opacity-55"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveOddsDrop(bet.id)}
                      disabled={isBusy || isSavingOddsDrop || isSendingOddsDrop || bet.isFading}
                      className="flex h-10 items-center justify-center rounded-xl border border-emerald-400/25 bg-emerald-500/10 text-emerald-200 transition-all hover:border-emerald-300/55 hover:bg-emerald-500/18 disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label="Сохранить коэффициент падения"
                    >
                      {isSavingOddsDrop ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSendOddsDrop(bet.id)}
                      disabled={isBusy || isSavingOddsDrop || isSendingOddsDrop || !hasOddsDropValue || bet.isFading}
                      className="flex h-10 items-center justify-center gap-1.5 rounded-xl border border-cyan-300/22 bg-cyan-400/10 px-2.5 text-[9px] font-black uppercase tracking-wider text-cyan-100 transition-all hover:border-cyan-300/55 hover:bg-cyan-400/18 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isSendingOddsDrop ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                      <span>Отправить</span>
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {resultActions.map(({ status, label, Icon, className }) => (
                    <button
                      key={status}
                      type="button"
                      onClick={() => handleResolve(bet.id, status)}
                      disabled={isBusy || bet.isFading}
                      className={`min-h-[42px] rounded-xl border px-2 text-[10px] font-black transition-all active:scale-95 disabled:cursor-not-allowed disabled:opacity-55 ${className}`}
                    >
                      <span className="flex items-center justify-center gap-1.5">
                        <Icon className={`h-3.5 w-3.5 shrink-0 ${isResolving ? 'animate-pulse' : ''}`} />
                        <span className="truncate">{label}</span>
                      </span>
                    </button>
                  ))}
                </div>
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() => handleStartEdit(bet)}
                    disabled={isBusy || bet.isFading}
                    className={`flex min-h-[40px] items-center justify-center gap-1.5 rounded-xl border px-2 text-[9px] font-black uppercase tracking-wider transition-all disabled:cursor-not-allowed disabled:opacity-55 ${
                      isEditing
                        ? 'border-indigo-300/50 bg-indigo-500/22 text-indigo-100'
                        : 'border-indigo-300/25 bg-indigo-500/10 text-indigo-200 hover:border-indigo-300/45 hover:bg-indigo-500/18'
                    }`}
                  >
                    {isSavingEdit ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
                    <span className="truncate">Редактировать</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(bet)}
                    disabled={isBusy || bet.isFading}
                    className="flex min-h-[40px] items-center justify-center gap-1.5 rounded-xl border border-rose-500/25 bg-rose-500/10 px-2 text-[9px] font-black uppercase tracking-wider text-rose-300 transition-all hover:border-rose-400/45 hover:bg-rose-500/18 disabled:cursor-not-allowed disabled:opacity-55"
                  >
                    {isDeleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                    <span className="truncate">Удалить прогноз</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
