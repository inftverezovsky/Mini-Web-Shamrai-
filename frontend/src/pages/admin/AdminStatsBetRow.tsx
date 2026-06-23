import React, { useMemo, useState } from 'react';
import {
  Check,
  Link as LinkIcon,
  Loader2,
  Pencil,
  Save,
  Trash2,
  X,
} from 'lucide-react';

import BookmakerMultiSelect from '../../components/BookmakerMultiSelect';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import { formatDateTime, formatStatsValue, profitTone, resultLabel, StatsValueMode } from '../../features/performance/performanceUi';
import { BetResponse, BookmakerResponse, PerformanceBetItem } from '../../schemas/schemas';
import { apiFetch } from '../../utils/api';
import { notifyError, notifySuccess } from '../../utils/notify';

interface EditDraft {
  event_name: string;
  coefficient: string;
  outcome: string;
  sport_type: string;
  description: string;
  match_link: string;
  selectedBookmakerIds: number[];
  bookmakerLinks: Record<number, string>;
}

function formatOddsInput(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '';
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toFixed(2) : String(value);
}

function normalizeOddsInput(value: string) {
  return value.trim().replace(',', '.');
}

function uniqueNumbers(values: Array<number | null | undefined>) {
  return values.filter((value): value is number => Boolean(value))
    .filter((value, index, valuesList) => valuesList.indexOf(value) === index);
}

function buildEditDraft(bet: PerformanceBetItem): EditDraft {
  const selectedBookmakerIds = uniqueNumbers([
    bet.bookmaker_id,
    ...bet.bookmakers.map((bookmaker) => bookmaker.id),
  ]);
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
    description: bet.description || '',
    match_link: bet.match_link || '',
    selectedBookmakerIds,
    bookmakerLinks,
  };
}

const resultActions: Array<{
  status: 'win' | 'loss';
  label: string;
  Icon: typeof Check;
  className: string;
  activeClassName: string;
}> = [
  {
    status: 'win',
    label: 'Победа',
    Icon: Check,
    className: 'border-emerald-300/24 bg-emerald-400/10 text-emerald-100 hover:bg-emerald-400/16',
    activeClassName: 'border-emerald-200/55 bg-emerald-300/20 text-emerald-50',
  },
  {
    status: 'loss',
    label: 'Неудача',
    Icon: X,
    className: 'border-rose-300/24 bg-rose-400/10 text-rose-100 hover:bg-rose-400/16',
    activeClassName: 'border-rose-200/55 bg-rose-300/20 text-rose-50',
  },
];

function sourceLabel(source: PerformanceBetItem['source_type']) {
  if (source === 'paid_set') return 'Набор';
  return source === 'private' ? 'Закрытая' : 'Лента';
}

interface AdminStatsBetRowProps {
  bet: PerformanceBetItem;
  valueMode: StatsValueMode;
  bookmakers: BookmakerResponse[];
  onChanged: () => Promise<void> | void;
}

export default function AdminStatsBetRow({
  bet,
  valueMode,
  bookmakers,
  onChanged,
}: AdminStatsBetRowProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() => buildEditDraft(bet));
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [resolvingStatus, setResolvingStatus] = useState<'win' | 'loss' | null>(null);

  const selectedBookmakers = useMemo(() => (
    bookmakers.filter((bookmaker) => draft.selectedBookmakerIds.includes(bookmaker.id))
  ), [bookmakers, draft.selectedBookmakerIds]);
  const isBusy = saving || deleting || resolvingStatus !== null;

  const updateDraft = (patch: Partial<EditDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  const handleBookmakerChange = (selectedBookmakerIds: number[]) => {
    setDraft((current) => {
      const selectedSet = new Set(selectedBookmakerIds);
      const bookmakerLinks = Object.entries(current.bookmakerLinks).reduce<Record<number, string>>(
        (acc, [rawId, url]) => {
          const bookmakerId = Number(rawId);
          if (selectedSet.has(bookmakerId)) acc[bookmakerId] = url;
          return acc;
        },
        {},
      );
      return { ...current, selectedBookmakerIds, bookmakerLinks };
    });
  };

  const handleBookmakerLinkChange = (bookmakerId: number, url: string) => {
    setDraft((current) => ({
      ...current,
      bookmakerLinks: {
        ...current.bookmakerLinks,
        [bookmakerId]: url,
      },
    }));
  };

  const handleSave = async () => {
    const eventName = draft.event_name.trim();
    const coefficient = normalizeOddsInput(draft.coefficient);

    if (!eventName) {
      notifyError('Укажите матч');
      return;
    }
    if (!coefficient) {
      notifyError('Укажите коэффициент');
      return;
    }

    try {
      setSaving(true);
      await apiFetch<BetResponse>(`/bets/${bet.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          event_name: eventName,
          coefficient,
          outcome: draft.outcome.trim() || null,
          sport_type: draft.sport_type.trim() || null,
          description: draft.description.trim() || null,
          match_link: draft.match_link.trim() || null,
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
      notifySuccess('Ставка обновлена');
      setIsEditing(false);
      await onChanged();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось сохранить ставку');
    } finally {
      setSaving(false);
    }
  };

  const handleResolve = async (nextStatus: 'win' | 'loss') => {
    if (nextStatus === bet.status) return;
    try {
      setResolvingStatus(nextStatus);
      await apiFetch<BetResponse>(`/bets/${bet.id}/resolve`, {
        method: 'PUT',
        body: JSON.stringify({ status: nextStatus }),
      });
      notifySuccess(nextStatus === 'win' ? 'Результат изменен на победу' : 'Результат изменен на неудачу');
      await onChanged();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось изменить результат');
    } finally {
      setResolvingStatus(null);
    }
  };

  const handleDelete = async () => {
    const confirmed = window.confirm(
      `Удалить ставку «${bet.event_name}» и отозвать доступы клиентов?`,
    );
    if (!confirmed) return;

    try {
      setDeleting(true);
      await apiFetch(`/admin/bets/${bet.id}`, { method: 'DELETE' });
      notifySuccess('Ставка удалена');
      await onChanged();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось удалить ставку');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="rounded-xl border border-white/10 bg-slate-950/35 p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`rounded-full border px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] ${
              bet.status === 'win'
                ? 'border-emerald-300/25 bg-emerald-400/10 text-emerald-100'
                : 'border-rose-300/25 bg-rose-400/10 text-rose-100'
            }`}>
              {resultLabel(bet.status)}
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[8px] font-black uppercase tracking-[0.08em] text-slate-300">
              {sourceLabel(bet.source_type)}
            </span>
            {bet.sport_type && (
              <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[8px] font-bold text-slate-300">
                <SportIconFrame label={bet.sport_type} size="tiny" />
                {bet.sport_type}
              </span>
            )}
          </div>
          <h4 className="mt-1.5 break-words text-xs font-black leading-snug text-white">{bet.event_name}</h4>
          <div className="mt-0.5 text-[9px] font-bold text-slate-500">
            Расчет: {formatDateTime(bet.resolved_at)} · КФ {bet.coefficient.toFixed(2)}
            {bet.outcome ? ` · ${bet.outcome}` : ''}
          </div>
          {bet.bookmakers.length ? (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {bet.bookmakers.map((bookmaker) => (
                <span
                  key={bookmaker.id}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-white/10 bg-black/20 px-1.5 py-1 text-[8.5px] font-bold text-slate-300"
                >
                  <BookmakerLogoFrame bookmaker={bookmaker} size="tiny" />
                  <span className="min-w-0 truncate">{bookmaker.name}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <div className={`shrink-0 text-right text-sm font-black tabular-nums ${profitTone(bet.profit_units)}`}>
          {formatStatsValue(bet.profit_units, valueMode)}
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => {
            setDraft(buildEditDraft(bet));
            setIsEditing((current) => !current);
          }}
          disabled={isBusy}
          className={`flex min-h-[36px] items-center justify-center gap-1.5 rounded-xl border px-2 text-[9px] font-black uppercase tracking-wider transition-all disabled:cursor-not-allowed disabled:opacity-55 ${
            isEditing
              ? 'border-indigo-300/50 bg-indigo-500/22 text-indigo-100'
              : 'border-indigo-300/25 bg-indigo-500/10 text-indigo-200 hover:border-indigo-300/45 hover:bg-indigo-500/18'
          }`}
        >
          <Pencil className="h-3.5 w-3.5" />
          <span className="truncate">Редактировать</span>
        </button>
        <button
          type="button"
          onClick={() => void handleDelete()}
          disabled={isBusy}
          className="flex min-h-[36px] items-center justify-center gap-1.5 rounded-xl border border-rose-500/25 bg-rose-500/10 px-2 text-[9px] font-black uppercase tracking-wider text-rose-300 transition-all hover:border-rose-400/45 hover:bg-rose-500/18 disabled:cursor-not-allowed disabled:opacity-55"
        >
          {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
          <span className="truncate">Удалить</span>
        </button>
      </div>

      {isEditing && (
        <div className="mt-2 rounded-xl border border-indigo-300/18 bg-black/20 p-2.5">
          <div className="grid grid-cols-2 gap-2">
            {resultActions.map(({ status, label, Icon, className, activeClassName }) => {
              const active = bet.status === status;
              return (
                <button
                  key={status}
                  type="button"
                  onClick={() => void handleResolve(status)}
                  disabled={isBusy || active}
                  className={`flex min-h-[36px] items-center justify-center gap-1.5 rounded-xl border px-2 text-[9px] font-black uppercase tracking-wider transition-all disabled:cursor-not-allowed disabled:opacity-60 ${
                    active ? activeClassName : className
                  }`}
                >
                  {resolvingStatus === status ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
                  <span className="truncate">{label}</span>
                </button>
              );
            })}
          </div>

          <div className="mt-2 grid gap-2">
            <label className="grid gap-1">
              <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Матч</span>
              <input
                type="text"
                value={draft.event_name}
                onChange={(event) => updateDraft({ event_name: event.target.value })}
                disabled={saving}
                className="h-10 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-black text-white outline-none transition-all placeholder:text-slate-600 focus:border-indigo-300/55 disabled:opacity-55"
              />
            </label>

            <div className="grid grid-cols-2 gap-2">
              <label className="grid gap-1">
                <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Исход</span>
                <input
                  type="text"
                  value={draft.outcome}
                  onChange={(event) => updateDraft({ outcome: event.target.value })}
                  disabled={saving}
                  className="h-10 min-w-0 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-bold text-white outline-none transition-all placeholder:text-slate-600 focus:border-indigo-300/55 disabled:opacity-55"
                />
              </label>
              <label className="grid gap-1">
                <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Кэф</span>
                <input
                  type="text"
                  inputMode="decimal"
                  value={draft.coefficient}
                  onChange={(event) => updateDraft({ coefficient: event.target.value })}
                  disabled={saving}
                  className="h-10 min-w-0 rounded-xl border border-emerald-300/20 bg-black/[0.24] px-3 text-xs font-black text-emerald-100 outline-none transition-all placeholder:text-slate-600 focus:border-emerald-300/55 disabled:opacity-55"
                />
              </label>
            </div>

            <label className="grid gap-1">
              <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Вид спорта</span>
              <input
                type="text"
                value={draft.sport_type}
                onChange={(event) => updateDraft({ sport_type: event.target.value })}
                disabled={saving}
                placeholder="Футбол"
                className="h-10 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-bold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
              />
            </label>

            <label className="grid gap-1">
              <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Описание</span>
              <textarea
                value={draft.description}
                onChange={(event) => updateDraft({ description: event.target.value })}
                disabled={saving}
                rows={3}
                className="min-h-[76px] resize-y rounded-xl border border-white/10 bg-black/[0.24] px-3 py-2 text-xs font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
              />
            </label>

            <label className="grid gap-1">
              <span className="text-[9px] font-black uppercase tracking-wider text-slate-500">Ссылка на матч</span>
              <input
                type="url"
                value={draft.match_link}
                onChange={(event) => updateDraft({ match_link: event.target.value })}
                disabled={saving}
                placeholder="https://..."
                className="h-10 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
              />
            </label>

            <div className="rounded-xl border border-white/10 bg-black/[0.16] p-2.5">
              <BookmakerMultiSelect
                label="Букмекеры"
                bookmakers={bookmakers}
                selectedIds={draft.selectedBookmakerIds}
                onChange={handleBookmakerChange}
                disabled={saving}
                allowAll={false}
              />
            </div>

            {selectedBookmakers.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-wider text-cyan-200">
                  <LinkIcon className="h-3.5 w-3.5" />
                  Ссылки БК
                </div>
                {selectedBookmakers.map((bookmaker) => (
                  <div
                    key={bookmaker.id}
                    className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] gap-2"
                  >
                    <div className="flex min-w-0 items-center gap-2 rounded-xl border border-white/10 bg-black/[0.18] px-2 py-1.5">
                      <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="h-7 shrink-0" />
                      <span className="min-w-0 truncate text-[10px] font-black text-slate-100">
                        {bookmaker.name}
                      </span>
                    </div>
                    <input
                      type="url"
                      value={draft.bookmakerLinks[bookmaker.id] || ''}
                      onChange={(event) => handleBookmakerLinkChange(bookmaker.id, event.target.value)}
                      disabled={saving}
                      placeholder="https://..."
                      className="h-10 min-w-0 rounded-xl border border-white/10 bg-black/[0.24] px-3 text-xs font-semibold text-white outline-none transition-all placeholder:text-slate-600 focus:border-cyan-300/55 disabled:opacity-55"
                    />
                  </div>
                ))}
              </div>
            )}

            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="mt-1 flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl border border-indigo-300/30 bg-indigo-500/16 px-3 text-[10px] font-black uppercase tracking-wider text-indigo-100 transition-all hover:border-indigo-300/60 hover:bg-indigo-500/24 disabled:cursor-not-allowed disabled:opacity-55"
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              <span>Сохранить изменения</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
