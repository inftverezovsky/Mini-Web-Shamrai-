import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch } from '../../utils/api';
import { BetResponse, BookmakerLink, BookmakerResponse, ForecastRequestResponse } from '../../schemas/schemas';
import { SPORT_FILTER_OPTIONS } from '../../constants/sports';
import BookmakerMultiSelect from '../../components/BookmakerMultiSelect';
import EmojiTextField from '../../components/EmojiTextField';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import {
  AlertTriangle,
  BellRing,
  Bot,
  CheckCheck,
  CheckCircle,
  ChevronDown,
  ClipboardList,
  Clock3,
  Handshake,
  Image,
  Inbox,
  Link,
  Loader2,
  Megaphone,
  Radio,
  Send,
  Target,
  Upload,
  UserCheck,
  UserMinus,
  Users,
  XCircle,
} from 'lucide-react';
import { notifyError, notifyInfo, notifySuccess } from '../../utils/notify';

type BroadcastMode = 'announcement' | 'forecast' | 'requests';
type BetCategory = 'prematch' | 'live';
type FullForecastMode = 'prepare' | 'send' | 'bulkSend';
type ForecastRequestStatus = ForecastRequestResponse['status'];

interface DeliveryResult {
  sent: number;
  failed: number;
  telegram?: {
    sent: number;
    failed: number;
  };
  vkMessages?: {
    sent: number;
    failed: number;
  };
  webPush?: {
    sent: number;
    failed: number;
    missing_permission?: number;
  };
}

interface ForecastBulkSendResult {
  status: 'success' | 'partial' | 'failed';
  total: number;
  sent: number;
  failed: number;
  errors: string[];
}

interface ForecastBroadcastFullResult {
  bet: BetResponse;
  auto_send_enabled: boolean;
  auto_send: ForecastBulkSendResult | null;
}

interface ForecastBroadcastStopResult {
  status: string;
  bet_id: string;
  already_stopped: boolean;
  stopped_requests: number;
  skipped_processing: number;
}

interface ForecastRequestGroup {
  key: string;
  eventName: string;
  bet: BetResponse;
  requests: ForecastRequestResponse[];
}

const FORECAST_REQUEST_TABS: Array<{ status: ForecastRequestStatus; label: string }> = [
  { status: 'interested', label: 'Ожидают' },
  { status: 'announced', label: 'Анонсировано' },
  { status: 'processing', label: 'Обработка' },
  { status: 'sent', label: 'Отправлено' },
  { status: 'manual_sent', label: 'Взяли вручную' },
  { status: 'declined', label: 'Отказались' },
  { status: 'removed', label: 'Удалены' },
];

function getForecastRequestUserName(request: ForecastRequestResponse) {
  const user = request.user;
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  if (user.username) return fullName ? `${fullName} (@${user.username})` : `@${user.username}`;
  return fullName || (user.is_web_only ? 'Web/VK клиент' : `ID ${user.telegram_id}`);
}

function getForecastRequestUserIdLabel(request: ForecastRequestResponse) {
  return request.user.is_web_only ? 'Web/VK клиент' : `ID ${request.user.telegram_id}`;
}

function canProcessForecastRequest(request: ForecastRequestResponse) {
  return request.status === 'interested';
}

function canRemoveForecastRequest(request: ForecastRequestResponse) {
  return request.status !== 'removed' && request.status !== 'processing';
}

function getDeliveryMethodLabel(deliveryMethod?: string | null) {
  if (deliveryMethod === 'vk_bot') return 'VK + бот';
  if (deliveryMethod === 'vk') return 'VK';
  if (deliveryMethod === 'web') return 'web';
  if (deliveryMethod === 'manual') return 'вручную';
  return 'бот';
}

function getBetBookmakers(bet: BetResponse | null | undefined): BookmakerResponse[] {
  if (!bet) return [];
  if (bet.bookmakers?.length) return bet.bookmakers;
  return bet.bookmaker ? [bet.bookmaker] : [];
}

function bookmakerLinksToState(links: BookmakerLink[] | null | undefined): Record<number, string> {
  return (links || []).reduce<Record<number, string>>((acc, link) => {
    if (link.bookmaker_id && link.url) {
      acc[link.bookmaker_id] = link.url;
    }
    return acc;
  }, {});
}

function normalizeMatchUrlForPreview(rawUrl: string): string {
  const url = rawUrl.trim().replace(/\u200b|\u200c|\u200d|\ufeff/g, '');
  if (!url || url.startsWith('//') || /[<>\\\s]/.test(url)) return '';

  let withScheme = url;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(withScheme)) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(withScheme)) return '';
    withScheme = `https://${withScheme}`;
  }

  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return '';
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
  if (parsed.username || parsed.password) return '';
  const hostname = parsed.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (!hostname || !/[a-z0-9]/i.test(hostname)) return '';
  if (hostname.toLowerCase() === 'localhost') {
    const scheme = parsed.protocol.slice(0, -1).toLowerCase();
    return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
  }
  if (hostname.includes(':')) {
    const scheme = parsed.protocol.slice(0, -1).toLowerCase();
    return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
  }
  if (!hostname.includes('.')) return '';
  const labels = hostname.split('.');
  if (!labels.every((label) => /^[A-Za-z0-9-]{1,63}$/.test(label) && !label.startsWith('-') && !label.endsWith('-'))) {
    return '';
  }

  const scheme = parsed.protocol.slice(0, -1).toLowerCase();
  return `${scheme}://${withScheme.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '')}`;
}

function bookmakerLinkError(rawUrl: string): string | null {
  if (!rawUrl.trim()) return null;
  return normalizeMatchUrlForPreview(rawUrl) ? null : 'Введите корректную ссылку: домен или URL http/https';
}

function betHasSavedFullForecast(bet: BetResponse | null | undefined): boolean {
  if (!bet || bet.status === 'deleted') return false;
  const eventName = (bet.event_name || '').trim();
  const hasEventName = Boolean(eventName && eventName !== 'Закрытый прогноз');
  const hasOutcome = Boolean((bet.outcome || '').trim());
  const hasCoefficient = Number(bet.coefficient || 0) > 0;
  const hasCoupon = Boolean((bet.coupon_image_url || '').trim());
  const hasBookmakerLink = (bet.bookmaker_links || []).some((link) => (
    Boolean(normalizeMatchUrlForPreview(link.url || ''))
  ));
  return hasEventName && hasOutcome && hasCoefficient && hasCoupon && hasBookmakerLink;
}

function formatRequestDate(value: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getForecastRequestGroupKey(request: ForecastRequestResponse) {
  return request.bet_id || request.bet.id || request.bet.event_name || request.id;
}

function getForecastRequestEventName(request: ForecastRequestResponse) {
  return request.bet.event_name?.trim() || 'Закрытый прогноз';
}

function formatForecastRequestCount(count: number) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  const noun = mod10 === 1 && mod100 !== 11
    ? 'заявка'
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
      ? 'заявки'
      : 'заявок';
  return `${count} ${noun}`;
}

interface UploadDropzoneProps {
  file: File | null;
  preview: string | null;
  existingUrl?: string | null;
  dragging: boolean;
  label: string;
  required?: boolean;
  inputRef: React.RefObject<HTMLInputElement>;
  onDragOver: (event: React.DragEvent) => void;
  onDragLeave: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
  onFileInput: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onRemove: () => void;
}

function UploadDropzone({
  file,
  preview,
  existingUrl = null,
  dragging,
  label,
  required = false,
  inputRef,
  onDragOver,
  onDragLeave,
  onDrop,
  onFileInput,
  onRemove,
}: UploadDropzoneProps) {
  return (
    <div className="space-y-2">
      <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider">
        <Image className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
        {label}{required && <span className="text-rose-400 ml-1">*</span>}
      </label>

      {!preview && existingUrl ? (
        <div
          onClick={() => inputRef.current?.click()}
          className="relative border border-emerald-500/25 rounded-xl p-4 flex items-center gap-3 cursor-pointer bg-emerald-500/10 hover:bg-emerald-500/15 transition-all"
        >
          <CheckCircle className="w-6 h-6 text-emerald-400 shrink-0" />
          <div className="min-w-0">
            <p className="text-xs font-black text-emerald-300 uppercase tracking-wider">
              Скрин купона уже загружен
            </p>
            <p className="text-[10px] text-slate-500 mt-0.5">
              Нажмите, чтобы заменить изображение
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            onChange={onFileInput}
            className="hidden"
          />
        </div>
      ) : !preview ? (
        <div
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={`relative border-dashed border-2 rounded-xl p-7 flex flex-col items-center justify-center cursor-pointer transition-all ${
            dragging
              ? 'border-[#ff007f] bg-[#ff007f]/5 scale-[1.01]'
              : 'border-white/15 bg-slate-900/50 hover:border-[#00d2ff]/70 hover:bg-[#00d2ff]/5'
          }`}
        >
          <Upload className={`w-7 h-7 mb-2 ${dragging ? 'text-[#ff007f]' : 'text-[#00d2ff]/70'}`} />
          <p className="text-xs font-bold text-slate-300">
            {dragging ? 'Отпустите файл сюда' : 'Перетащите изображение или нажмите'}
          </p>
          <p className="text-[10px] text-slate-600 mt-1">PNG, JPG, WEBP до 5 МБ</p>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            onChange={onFileInput}
            className="hidden"
          />
        </div>
      ) : (
        <div className="relative group bg-slate-900/50 border border-white/10 rounded-xl p-2">
          <img
            src={preview}
            alt="Preview"
            className="w-full max-h-52 object-contain rounded-lg"
          />
          <button
            type="button"
            onClick={onRemove}
            className="absolute top-3 right-3 bg-slate-950/85 border border-white/10 text-slate-300 hover:text-rose-400 hover:border-rose-500/30 rounded-lg px-2 py-1 text-[10px] font-bold transition-all"
          >
            Удалить
          </button>
          <p className="text-[10px] text-slate-500 mt-2 truncate">
            {file?.name} ({((file?.size ?? 0) / 1024).toFixed(1)} КБ)
          </p>
        </div>
      )}
    </div>
  );
}

export default function AdminBroadcast() {
  const [mode, setMode] = useState<BroadcastMode>('announcement');
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>([]);
  const [bookmakersLoading, setBookmakersLoading] = useState(true);

  const [announcementTitle, setAnnouncementTitle] = useState('Анонс ставки');
  const [announcementBody, setAnnouncementBody] = useState('');
  const [announcementSport, setAnnouncementSport] = useState('Футбол');
  const [announcementBkIds, setAnnouncementBkIds] = useState<number[]>([]);
  const [announcementCoef, setAnnouncementCoef] = useState('');
  const [announcementMatchLink, setAnnouncementMatchLink] = useState('');

  const [announcementFile, setAnnouncementFile] = useState<File | null>(null);
  const [announcementPreview, setAnnouncementPreview] = useState<string | null>(null);
  const [announcementDragging, setAnnouncementDragging] = useState(false);
  const announcementInputRef = useRef<HTMLInputElement>(null);

  const [forecastCoef, setForecastCoef] = useState('');
  const [forecastBkIds, setForecastBkIds] = useState<number[]>([]);
  const [forecastSport, setForecastSport] = useState('Футбол');
  const [forecastTeaserText, setForecastTeaserText] = useState('');

  const [fullForecastEvent, setFullForecastEvent] = useState('');
  const [fullForecastOutcome, setFullForecastOutcome] = useState('');
  const [fullForecastCoef, setFullForecastCoef] = useState('');
  const [fullForecastSport, setFullForecastSport] = useState('Футбол');
  const [fullForecastDescription, setFullForecastDescription] = useState('');
  const [fullForecastBookmakerIds, setFullForecastBookmakerIds] = useState<number[]>([]);
  const [fullForecastBookmakerLinks, setFullForecastBookmakerLinks] = useState<Record<number, string>>({});
  const [fullForecastCategory, setFullForecastCategory] = useState<BetCategory>('prematch');
  const [fullForecastFile, setFullForecastFile] = useState<File | null>(null);
  const [fullForecastPreview, setFullForecastPreview] = useState<string | null>(null);
  const [fullForecastDragging, setFullForecastDragging] = useState(false);
  const fullForecastInputRef = useRef<HTMLInputElement>(null);
  const [fullForecastMode, setFullForecastMode] = useState<FullForecastMode>('send');
  const [fullForecastPreparedBetId, setFullForecastPreparedBetId] = useState<string | null>(null);
  const [fullForecastExistingCouponUrl, setFullForecastExistingCouponUrl] = useState<string | null>(null);
  const [fullForecastAutoSend, setFullForecastAutoSend] = useState(false);

  const [requestStatusFilter, setRequestStatusFilter] = useState<ForecastRequestStatus>('interested');
  const [forecastRequests, setForecastRequests] = useState<ForecastRequestResponse[]>([]);
  const [expandedForecastMatchKeys, setExpandedForecastMatchKeys] = useState<string[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(false);
  const [requestActionLoading, setRequestActionLoading] = useState<string | null>(null);
  const [selectedRequestIds, setSelectedRequestIds] = useState<string[]>([]);
  const [fullForecastRequest, setFullForecastRequest] = useState<ForecastRequestResponse | null>(null);
  const [fullForecastBulkRequests, setFullForecastBulkRequests] = useState<ForecastRequestResponse[]>([]);

  const [audienceCount, setAudienceCount] = useState<number | null>(null);
  const [audienceLoading, setAudienceLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [deliveryResult, setDeliveryResult] = useState<DeliveryResult | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    async function loadBookmakers() {
      try {
        setBookmakersLoading(true);
        const data = await apiFetch('/bookmakers');
        setBookmakers(data);
      } catch (err) {
        console.error('Failed to load bookmakers:', err);
      } finally {
        setBookmakersLoading(false);
      }
    }

    loadBookmakers();
  }, []);

  const selectedAnnouncementBkNames = bookmakers
    .filter((bookmaker) => announcementBkIds.includes(bookmaker.id))
    .map((bookmaker) => bookmaker.name);
  const selectedForecastBkNames = bookmakers
    .filter((bookmaker) => forecastBkIds.includes(bookmaker.id))
    .map((bookmaker) => bookmaker.name);
  const fullForecastBookmakers = useMemo(() => {
    if (fullForecastBookmakerIds.length === 0) return [];
    const selectedIds = new Set(fullForecastBookmakerIds);
    return bookmakers.filter((bookmaker) => selectedIds.has(bookmaker.id));
  }, [bookmakers, fullForecastBookmakerIds]);
  const fullForecastBookmakerLinkStates = useMemo(() => {
    return fullForecastBookmakers.reduce<Record<number, { error: string | null; preview: string }>>((acc, bookmaker) => {
      const rawUrl = fullForecastBookmakerLinks[bookmaker.id] || '';
      acc[bookmaker.id] = {
        error: bookmakerLinkError(rawUrl),
        preview: normalizeMatchUrlForPreview(rawUrl),
      };
      return acc;
    }, {});
  }, [fullForecastBookmakers, fullForecastBookmakerLinks]);
  const fullForecastHasInvalidBookmakerLink = useMemo(
    () => Object.values(fullForecastBookmakerLinkStates).some((state) => Boolean(state.error)),
    [fullForecastBookmakerLinkStates],
  );

  const fetchAudienceCount = useCallback(async () => {
    if (announcementBkIds.length === 0) {
      setAudienceCount(null);
      return;
    }

    try {
      setAudienceLoading(true);
      const params = new URLSearchParams();
      announcementBkIds.forEach((bookmakerId) => {
        params.append('bookmaker_ids', String(bookmakerId));
      });
      if (announcementSport && announcementSport !== 'Все') params.set('sport_filter', announcementSport);
      if (announcementCoef) params.set('min_coef', announcementCoef);

      const data = await apiFetch(`/admin/announcements/audience-count?${params.toString()}`);
      setAudienceCount(data.total_audience ?? data.count ?? 0);
    } catch (err) {
      console.error('Audience count fetch error:', err);
      setAudienceCount(null);
    } finally {
      setAudienceLoading(false);
    }
  }, [announcementBkIds, announcementSport, announcementCoef]);

  useEffect(() => {
    const timer = setTimeout(fetchAudienceCount, 350);
    return () => clearTimeout(timer);
  }, [fetchAudienceCount]);

  const fetchForecastRequests = useCallback(async () => {
    try {
      setRequestsLoading(true);
      const params = new URLSearchParams();
      params.set('status', requestStatusFilter);
      const data = await apiFetch<ForecastRequestResponse[]>(`/admin/forecast-requests?${params.toString()}`);
      setForecastRequests(data);
      setSelectedRequestIds((previousIds) => previousIds.filter((id) => data.some((request) => request.id === id)));
    } catch (err) {
      console.error('Forecast requests fetch error:', err);
      setForecastRequests([]);
    } finally {
      setRequestsLoading(false);
    }
  }, [requestStatusFilter]);

  useEffect(() => {
    if (mode !== 'requests') return;
    fetchForecastRequests();
  }, [mode, fetchForecastRequests]);

  const processImage = useCallback((
    file: File,
    setFile: React.Dispatch<React.SetStateAction<File | null>>,
    setPreview: React.Dispatch<React.SetStateAction<string | null>>,
  ) => {
    if (!file.type.startsWith('image/')) {
      notifyError('Допустимы только изображения');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      notifyError('Файл слишком большой. Максимум 5 МБ.');
      return;
    }

    setFile(file);
    const reader = new FileReader();
    reader.onload = (event) => setPreview(event.target?.result as string);
    reader.readAsDataURL(file);
  }, []);

  const makeFileHandlers = (
    setFile: React.Dispatch<React.SetStateAction<File | null>>,
    setPreview: React.Dispatch<React.SetStateAction<string | null>>,
    setDragging: React.Dispatch<React.SetStateAction<boolean>>,
  ) => ({
    onDragOver: (event: React.DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setDragging(true);
    },
    onDragLeave: (event: React.DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setDragging(false);
    },
    onDrop: (event: React.DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
      setDragging(false);
      const droppedFile = event.dataTransfer.files?.[0];
      if (droppedFile) processImage(droppedFile, setFile, setPreview);
    },
    onFileInput: (event: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFile = event.target.files?.[0];
      if (selectedFile) processImage(selectedFile, setFile, setPreview);
    },
  });

  const announcementFileHandlers = makeFileHandlers(
    setAnnouncementFile,
    setAnnouncementPreview,
    setAnnouncementDragging,
  );
  const fullForecastFileHandlers = makeFileHandlers(
    setFullForecastFile,
    setFullForecastPreview,
    setFullForecastDragging,
  );

  const clearAnnouncementFile = () => {
    setAnnouncementFile(null);
    setAnnouncementPreview(null);
    if (announcementInputRef.current) announcementInputRef.current.value = '';
  };

  const clearFullForecastFile = () => {
    setFullForecastFile(null);
    setFullForecastPreview(null);
    if (fullForecastInputRef.current) fullForecastInputRef.current.value = '';
  };

  const resetFeedback = () => {
    setDeliveryResult(null);
    setSuccessMessage(null);
    setSubmitError(null);
  };

  const runRequestAction = async (
    requestId: string,
    action: 'mark-manual' | 'cancel' | 'remove-client',
    successText: string,
  ) => {
    try {
      setRequestActionLoading(`${action}:${requestId}`);
      await apiFetch(`/admin/forecast-requests/${requestId}/${action}`, { method: 'POST' });
      notifySuccess(successText);
      await fetchForecastRequests();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось обработать заявку');
    } finally {
      setRequestActionLoading(null);
    }
  };

  const sendSavedFullForecast = async (request: ForecastRequestResponse) => {
    try {
      resetFeedback();
      setRequestActionLoading(`send:${request.id}`);
      await apiFetch(`/admin/forecast-requests/${request.id}/send-saved`, { method: 'POST' });
      notifySuccess('Полный прогноз отправлен клиенту');
      setSelectedRequestIds((current) => current.filter((id) => id !== request.id));
      await fetchForecastRequests();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось отправить полный прогноз');
    } finally {
      setRequestActionLoading(null);
    }
  };

  const stopForecastMatch = async (group: ForecastRequestGroup) => {
    try {
      resetFeedback();
      setRequestActionLoading(`stop:${group.bet.id}`);
      const result = await apiFetch<ForecastBroadcastStopResult>(
        `/admin/forecast-broadcast/${group.bet.id}/stop`,
        { method: 'POST' },
      );
      const message = result.already_stopped
        ? 'Матч уже остановлен'
        : `Матч остановлен. Недоступно заявок: ${result.stopped_requests}`;
      notifySuccess(message);
      setSelectedRequestIds((current) => (
        current.filter((id) => !group.requests.some((request) => request.id === id))
      ));
      await fetchForecastRequests();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось остановить матч');
    } finally {
      setRequestActionLoading(null);
    }
  };

  const handleRemoveForecastRequest = async (request: ForecastRequestResponse) => {
    const confirmed = window.confirm(
      `Удалить клиента «${getForecastRequestUserName(request)}» с матча?`,
    );
    if (!confirmed) return;
    await runRequestAction(request.id, 'remove-client', 'Клиент удален с матча');
    setSelectedRequestIds((current) => current.filter((id) => id !== request.id));
  };

  const fillFullForecastFields = (bet: BetResponse) => {
    const betBookmakers = getBetBookmakers(bet);
    setFullForecastEvent(bet.event_name === 'Закрытый прогноз' ? '' : bet.event_name || '');
    setFullForecastOutcome(bet.outcome || '');
    setFullForecastCoef(String(bet.coefficient || ''));
    setFullForecastSport(bet.sport_type || 'Футбол');
    setFullForecastDescription(bet.description || '');
    setFullForecastBookmakerIds(betBookmakers.map((bookmaker) => bookmaker.id));
    setFullForecastBookmakerLinks(bookmakerLinksToState(bet.bookmaker_links));
    setFullForecastCategory(bet.category === 'live' ? 'live' : 'prematch');
    setFullForecastExistingCouponUrl(bet.coupon_image_url || null);
    setFullForecastAutoSend(Boolean(bet.auto_send_on_interest));
    clearFullForecastFile();
  };

  const openPrepareFullForecastModal = (betId: string, coefficient: string, sport: string) => {
    setFullForecastMode('prepare');
    setFullForecastPreparedBetId(betId);
    setFullForecastRequest(null);
    setFullForecastBulkRequests([]);
    setFullForecastEvent('');
    setFullForecastOutcome('');
    setFullForecastCoef(coefficient);
    setFullForecastSport(sport || 'Футбол');
    setFullForecastDescription('');
    setFullForecastBookmakerIds(forecastBkIds);
    setFullForecastBookmakerLinks({});
    setFullForecastCategory('prematch');
    setFullForecastExistingCouponUrl(null);
    setFullForecastAutoSend(false);
    clearFullForecastFile();
  };

  const openFullForecastModal = (request: ForecastRequestResponse) => {
    setFullForecastMode('send');
    setFullForecastPreparedBetId(request.bet_id);
    setFullForecastRequest(request);
    setFullForecastBulkRequests([]);
    fillFullForecastFields(request.bet);
  };

  const openBulkFullForecastModal = () => {
    const selectedProcessable = forecastRequests.filter(
      (request) => selectedRequestIds.includes(request.id) && canProcessForecastRequest(request),
    );
    if (selectedProcessable.length === 0) {
      notifyError('Выберите клиентов, которые нажали «Беру»');
      return;
    }

    const selectedBetIds = Array.from(new Set(selectedProcessable.map((request) => request.bet_id)));
    if (selectedBetIds.length > 1) {
      notifyError('Выберите клиентов из одного закрытого прогноза');
      return;
    }

    const referenceRequest = selectedProcessable[0];
    setFullForecastMode('bulkSend');
    setFullForecastPreparedBetId(referenceRequest.bet_id);
    setFullForecastRequest(null);
    setFullForecastBulkRequests(selectedProcessable);
    fillFullForecastFields(referenceRequest.bet);
  };

  const closeFullForecastModal = (force = false) => {
    if (!force && (
      requestActionLoading?.startsWith('send:') ||
      requestActionLoading?.startsWith('prepare:') ||
      requestActionLoading?.startsWith('bulk-send:')
    )) return;
    setFullForecastRequest(null);
    setFullForecastBulkRequests([]);
    setFullForecastMode('send');
    setFullForecastPreparedBetId(null);
    setFullForecastEvent('');
    setFullForecastOutcome('');
    setFullForecastCoef('');
    setFullForecastSport('Футбол');
    setFullForecastDescription('');
    setFullForecastBookmakerIds([]);
    setFullForecastBookmakerLinks({});
    setFullForecastCategory('prematch');
    setFullForecastExistingCouponUrl(null);
    setFullForecastAutoSend(false);
    clearFullForecastFile();
  };

  const handleFullForecastSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const isPreparing = fullForecastMode === 'prepare';
    const isBulkSending = fullForecastMode === 'bulkSend';
    const targetBetId = fullForecastPreparedBetId || fullForecastRequest?.bet_id || null;
    if (isPreparing && !targetBetId) return;
    if (isBulkSending && fullForecastBulkRequests.length === 0) return;
    if (!isPreparing && !isBulkSending && !fullForecastRequest) return;
    if (!fullForecastEvent.trim()) {
      notifyError('Укажите матч');
      return;
    }
    if (!fullForecastOutcome.trim()) {
      notifyError('Укажите исход');
      return;
    }
    if (!fullForecastCoef) {
      notifyError('Укажите коэффициент');
      return;
    }
    if (!fullForecastFile && !fullForecastExistingCouponUrl) {
      notifyError('Загрузите скрин купона');
      return;
    }
    if (fullForecastHasInvalidBookmakerLink) {
      notifyError('Проверьте ссылки по БК');
      return;
    }

    resetFeedback();
    try {
      const actionKey = isPreparing
        ? `prepare:${targetBetId}`
        : isBulkSending
          ? `bulk-send:${targetBetId}`
          : `send:${fullForecastRequest?.id}`;
      setRequestActionLoading(actionKey);
      const formData = new FormData();
      if (isBulkSending) {
        fullForecastBulkRequests.forEach((request) => {
          formData.append('request_ids', request.id);
        });
      }
      formData.append('event_name', fullForecastEvent.trim());
      formData.append('outcome', fullForecastOutcome.trim());
      formData.append('coefficient', fullForecastCoef);
      formData.append('sport_type', fullForecastSport);
      formData.append('category', fullForecastCategory);
      if (isPreparing) {
        formData.append('auto_send_interested', fullForecastAutoSend ? 'true' : 'false');
      }
      if (fullForecastDescription.trim()) formData.append('description', fullForecastDescription.trim());
      const bookmakerLinksPayload = fullForecastBookmakers
        .map((bookmaker) => ({
          bookmaker_id: bookmaker.id,
          url: normalizeMatchUrlForPreview(fullForecastBookmakerLinks[bookmaker.id] || ''),
        }))
        .filter((link) => link.url.length > 0);
      if (bookmakerLinksPayload.length === 0) {
        notifyError('Добавьте хотя бы одну ссылку по БК');
        return;
      }
      formData.append('bookmaker_links', JSON.stringify(bookmakerLinksPayload));
      if (fullForecastCategory === 'live') {
        formData.append('live_ends_at', new Date(Date.now() + 15 * 60000).toISOString());
      }
      if (fullForecastFile) formData.append('coupon_image', fullForecastFile);

      const endpoint = isPreparing
        ? `/admin/forecast-broadcast/${targetBetId}/full-forecast`
        : isBulkSending
          ? '/admin/forecast-requests/bulk-send'
          : `/admin/forecast-requests/${fullForecastRequest?.id}/send`;
      const result = await apiFetch<ForecastBroadcastFullResult | ForecastRequestResponse | ForecastBulkSendResult>(endpoint, {
        method: 'POST',
        body: formData,
      });
      if (isBulkSending) {
        const bulkResult = result as ForecastBulkSendResult;
        setDeliveryResult({
          sent: bulkResult.sent ?? 0,
          failed: bulkResult.failed ?? 0,
        });
        const message = bulkResult.failed > 0
          ? `Отправлено выбранным: ${bulkResult.sent}, ошибок: ${bulkResult.failed}`
          : `Полная ставка отправлена выбранным: ${bulkResult.sent}`;
        setSuccessMessage(message);
        setSubmitError(bulkResult.failed > 0 ? bulkResult.errors?.[0] || 'Часть отправок не прошла' : null);
        setSelectedRequestIds([]);
        if (bulkResult.failed > 0) {
          notifyInfo(message);
        } else {
          notifySuccess(message);
        }
      } else if (isPreparing) {
        const preparedResult = result as ForecastBroadcastFullResult;
        const autoSendResult = preparedResult.auto_send;
        if (fullForecastAutoSend && autoSendResult) {
          if ((autoSendResult.total ?? 0) > 0) {
            setDeliveryResult({
              sent: autoSendResult.sent ?? 0,
              failed: autoSendResult.failed ?? 0,
            });
          }
          const message = autoSendResult.failed > 0
            ? `Полная ставка сохранена. Автоотправлено: ${autoSendResult.sent}, не отправлено: ${autoSendResult.failed}`
            : autoSendResult.sent > 0
              ? `Полная ставка сохранена и автоотправлена: ${autoSendResult.sent}`
              : 'Полная ставка сохранена, автоотправка включена';
          setSuccessMessage(message);
          setSubmitError(autoSendResult.failed > 0 ? autoSendResult.errors?.[0] || 'Часть автоотправок не прошла' : null);
          if (autoSendResult.failed > 0) {
            notifyInfo(message);
          } else {
            notifySuccess(message);
          }
        } else {
          notifySuccess('Полная ставка сохранена');
        }
      } else {
        notifySuccess('Полный прогноз отправлен клиенту');
      }
      if (isPreparing) {
        const preparedBet = (result as ForecastBroadcastFullResult).bet;
        setFullForecastExistingCouponUrl(preparedBet.coupon_image_url || fullForecastExistingCouponUrl);
        setFullForecastBookmakerLinks(bookmakerLinksToState(preparedBet.bookmaker_links));
      }
      closeFullForecastModal(true);
      await fetchForecastRequests();
    } catch (err: any) {
      notifyError(err.message || (fullForecastMode === 'prepare' ? 'Не удалось сохранить полную ставку' : 'Не удалось отправить полный прогноз'));
    } finally {
      setRequestActionLoading(null);
    }
  };

  const toggleRequestSelection = (requestId: string) => {
    setSelectedRequestIds((current) => (
      current.includes(requestId)
        ? current.filter((id) => id !== requestId)
        : [...current, requestId]
    ));
  };

  const toggleAllProcessableRequests = () => {
    const processableIds = forecastRequests
      .filter(canProcessForecastRequest)
      .map((request) => request.id);
    if (processableIds.length === 0) return;

    setSelectedRequestIds((current) => {
      const processableSet = new Set(processableIds);
      const allSelected = processableIds.every((id) => current.includes(id));
      if (allSelected) {
        return current.filter((id) => !processableSet.has(id));
      }
      return Array.from(new Set([...current, ...processableIds]));
    });
  };

  const handleBulkManualMark = async () => {
    const selectedProcessable = forecastRequests.filter(
      (request) => selectedRequestIds.includes(request.id) && canProcessForecastRequest(request),
    );
    if (selectedProcessable.length === 0) {
      notifyError('Выберите клиентов, которые нажали «Беру»');
      return;
    }

    try {
      setRequestActionLoading('bulk:manual');
      for (const request of selectedProcessable) {
        await apiFetch(`/admin/forecast-requests/${request.id}/mark-manual`, { method: 'POST' });
      }
      setSelectedRequestIds([]);
      notifySuccess(`Клиенты отмечены как взявшие: ${selectedProcessable.length}`);
      await fetchForecastRequests();
    } catch (err: any) {
      notifyError(err.message || 'Не удалось выполнить массовую отметку');
    } finally {
      setRequestActionLoading(null);
    }
  };

  const handleAnnouncementSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    resetFeedback();

    if (announcementBkIds.length === 0) {
      notifyError('Выберите одну или несколько букмекерских контор для анонса');
      return;
    }
    if (!announcementCoef) {
      notifyError('Укажите коэффициент для анонса');
      return;
    }

    const bookmakerNames = selectedAnnouncementBkNames.join(', ');
    const body = announcementBody.trim();

    try {
      setSubmitting(true);
      const formData = new FormData();
      formData.append('title', announcementTitle.trim() || 'Анонс ставки');
      if (body) formData.append('body', body);
      formData.append('announcement_type', 'urgent');
      announcementBkIds.forEach((bookmakerId) => {
        formData.append('bookmaker_ids', String(bookmakerId));
      });
      formData.append('bookmaker_names', bookmakerNames);
      formData.append('sport_filter', announcementSport);
      formData.append('min_coef', announcementCoef);
      if (announcementMatchLink.trim()) formData.append('match_link', announcementMatchLink.trim());
      if (announcementFile) formData.append('coupon_image', announcementFile);

      const result = await apiFetch('/admin/announcements', {
        method: 'POST',
        body: formData,
      });

      setDeliveryResult({
        sent: result.sent ?? result.delivery?.sent ?? 0,
        failed: result.failed ?? result.delivery?.failed ?? 0,
        telegram: result.delivery?.telegram,
        vkMessages: result.delivery?.vk_messages,
        webPush: result.delivery?.web_push,
      });
      setSuccessMessage('Анонс отправлен выбранной аудитории');
      setAnnouncementBody('');
      setAnnouncementMatchLink('');
      clearAnnouncementFile();
      fetchAudienceCount();
      notifySuccess('Анонс отправлен выбранной аудитории');
    } catch (err: any) {
      setSubmitError(err.message || 'Не удалось отправить анонс');
      notifyError(err.message || 'Не удалось отправить анонс');
    } finally {
      setSubmitting(false);
    }
  };

  const handleForecastSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    resetFeedback();

    if (!forecastCoef) {
      notifyError('Укажите коэффициент для анонса прогноза');
      return;
    }
    if (forecastBkIds.length === 0) {
      notifyError('Выберите хотя бы одну БК для закрытого прогноза');
      return;
    }

    try {
      setSubmitting(true);
      const announcedForecastCoef = forecastCoef;
      const announcedForecastSport = forecastSport;
      const formData = new FormData();
      formData.append('coefficient', forecastCoef);
      formData.append('sport_type', forecastSport);
      formData.append('brain_score', '5');
      formData.append('teaser_text', forecastTeaserText.trim() || 'Есть закрытый прогноз под вашу БК. Берете матч?');
      formData.append('bookmaker_id', String(forecastBkIds[0]));
      forecastBkIds.forEach((bookmakerId) => {
        formData.append('bookmaker_ids', String(bookmakerId));
      });
      const result = await apiFetch('/admin/forecast-broadcast', {
        method: 'POST',
        body: formData,
      });

      const sent = result.sent ?? result.delivery?.sent ?? 0;
      const failed = result.failed ?? result.delivery?.failed ?? 0;
      const totalAudience = result.total_audience ?? result.delivery?.total_audience ?? sent + failed;
      setDeliveryResult({
        sent,
        failed,
        telegram: result.delivery?.telegram,
        vkMessages: result.delivery?.vk_messages,
        webPush: result.delivery?.web_push,
      });
      if (totalAudience === 0) {
        const message = 'Под выбранные БК сейчас нет клиентов для анонса прогноза';
        setSubmitError(message);
        notifyError(message);
        return;
      }
      if (sent === 0) {
        const firstError = result.errors?.[0] ?? result.delivery?.errors?.[0];
        const message = firstError
          ? `Каналы доставки не отправили анонс прогноза: ${firstError}`
          : 'Каналы доставки не отправили анонс прогноза ни одному клиенту';
        setSubmitError(message);
        notifyError(message);
        return;
      }
      const successText = failed > 0
        ? `Анонс прогноза доставлен: ${sent}, не доставлено: ${failed}`
        : 'Анонс прогноза отправлен выбранным клиентам';
      const createdBetId = result.bet_id ? String(result.bet_id) : null;
      setSuccessMessage(successText);
      if (createdBetId) {
        openPrepareFullForecastModal(createdBetId, announcedForecastCoef, announcedForecastSport);
      }
      setForecastCoef('');
      setForecastTeaserText('');
      fetchForecastRequests();
      if (failed > 0) {
        notifyInfo(successText);
      } else {
        notifySuccess(successText);
      }
    } catch (err: any) {
      setSubmitError(err.message || 'Не удалось отправить анонс прогноза');
      notifyError(err.message || 'Не удалось отправить анонс прогноза');
    } finally {
      setSubmitting(false);
    }
  };

  const fullForecastIsPreparing = fullForecastMode === 'prepare';
  const fullForecastIsBulkSending = fullForecastMode === 'bulkSend';
  const fullForecastModalOpen = Boolean(fullForecastRequest || fullForecastPreparedBetId);
  const fullForecastActionKey = fullForecastIsPreparing && fullForecastPreparedBetId
    ? `prepare:${fullForecastPreparedBetId}`
    : fullForecastIsBulkSending && fullForecastPreparedBetId
      ? `bulk-send:${fullForecastPreparedBetId}`
    : fullForecastRequest
      ? `send:${fullForecastRequest.id}`
      : null;
  const fullForecastSubmitting = Boolean(fullForecastActionKey && requestActionLoading === fullForecastActionKey);
  const processableRequestIds = forecastRequests.filter(canProcessForecastRequest).map((request) => request.id);
  const allProcessableSelected = processableRequestIds.length > 0 && processableRequestIds.every((id) => selectedRequestIds.includes(id));
  const forecastRequestGroups = useMemo<ForecastRequestGroup[]>(() => {
    const groups = new Map<string, ForecastRequestGroup>();

    forecastRequests.forEach((request) => {
      const key = getForecastRequestGroupKey(request);
      const existingGroup = groups.get(key);
      if (existingGroup) {
        existingGroup.requests.push(request);
        return;
      }
      groups.set(key, {
        key,
        eventName: getForecastRequestEventName(request),
        bet: request.bet,
        requests: [request],
      });
    });

    return Array.from(groups.values());
  }, [forecastRequests]);
  const expandedForecastMatchKeySet = useMemo(
    () => new Set(expandedForecastMatchKeys),
    [expandedForecastMatchKeys],
  );

  const toggleForecastMatchGroup = (groupKey: string) => {
    setExpandedForecastMatchKeys((current) => (
      current.includes(groupKey)
        ? current.filter((key) => key !== groupKey)
        : [...current, groupKey]
    ));
  };

  return (
    <div className="space-y-5 animate-slide-up pb-10">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-black text-white flex items-center uppercase tracking-wider">
            <Megaphone className="w-5 h-5 text-[#ff007f] mr-2" />
            Рассылка
          </h2>
          <p className="text-slate-400 text-[10px] uppercase font-bold tracking-widest mt-0.5">
            Анонс, прогноз и заявки
          </p>
        </div>
      </div>

      <div className="bg-white/[0.03] border border-white/10 p-1.5 rounded-2xl grid grid-cols-3 gap-1.5 shadow-inner">
        <button
          type="button"
          onClick={() => {
            setMode('announcement');
            resetFeedback();
          }}
          className={`flex items-center justify-center space-x-1.5 text-[10px] font-black uppercase tracking-wider py-2.5 rounded-xl transition-all ${
            mode === 'announcement'
              ? 'bg-[#ff007f] text-white shadow-[0_0_18px_rgba(255,0,127,0.28)]'
              : 'text-slate-400 hover:text-white hover:bg-white/5'
          }`}
        >
          <Radio className="w-3.5 h-3.5" />
          <span>Анонс</span>
        </button>
        <button
          type="button"
          onClick={() => {
            setMode('forecast');
            resetFeedback();
          }}
          className={`flex items-center justify-center space-x-1.5 text-[10px] font-black uppercase tracking-wider py-2.5 rounded-xl transition-all ${
            mode === 'forecast'
              ? 'bg-emerald-500 text-slate-950 shadow-neon-green'
              : 'text-slate-400 hover:text-white hover:bg-white/5'
          }`}
        >
          <ClipboardList className="w-3.5 h-3.5" />
          <span>Прогноз</span>
        </button>
        <button
          type="button"
          onClick={() => {
            setMode('requests');
            resetFeedback();
          }}
          className={`flex items-center justify-center space-x-1.5 text-[10px] font-black uppercase tracking-wider py-2.5 rounded-xl transition-all ${
            mode === 'requests'
              ? 'bg-[#00d2ff] text-slate-950 shadow-[0_0_18px_rgba(0,210,255,0.24)]'
              : 'text-slate-400 hover:text-white hover:bg-white/5'
          }`}
        >
          <Inbox className="w-3.5 h-3.5" />
          <span>Заявки</span>
        </button>
      </div>

      {mode === 'announcement' && (
        <div className="backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl p-4 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Users className="w-5 h-5 text-[#00d2ff]" />
            <div>
              <span className="block text-xs font-bold text-slate-300 uppercase tracking-wider">
                Получатели
              </span>
              <span className="block max-w-[220px] truncate text-[10px] text-slate-500">
                {selectedAnnouncementBkNames.length > 0 ? selectedAnnouncementBkNames.join(', ') : 'выберите БК'}
              </span>
            </div>
          </div>
          <div className="flex items-center space-x-2">
            {audienceLoading ? (
              <Loader2 className="w-4 h-4 text-[#00d2ff] animate-spin" />
            ) : (
              <span className="text-xl font-black text-[#00d2ff]">
                {audienceCount !== null ? audienceCount.toLocaleString('ru-RU') : '—'}
              </span>
            )}
            <span className="text-[10px] text-slate-500 font-bold">чел.</span>
          </div>
        </div>
      )}

      {mode === 'announcement' && (
        <form onSubmit={handleAnnouncementSubmit} className="space-y-4">
          <div className="backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl p-5 space-y-4">
            <div>
              <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                Заголовок
              </label>
              <EmojiTextField
                type="text"
                value={announcementTitle}
                onValueChange={setAnnouncementTitle}
                className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-[#ff007f]/50 transition-colors"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Спорт
                </label>
                <div className="flex items-center gap-2">
                  <SportIconFrame label={announcementSport} size="badge" active className="shrink-0" />
                  <select
                    value={announcementSport}
                    onChange={(event) => setAnnouncementSport(event.target.value)}
                    className="min-w-0 flex-1 bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[#ff007f]/50 transition-colors"
                  >
                    {SPORT_FILTER_OPTIONS.filter((sport) => sport !== 'Все').map((sport) => (
                      <option key={sport} value={sport}>{sport}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Коэффициент
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="1"
                  value={announcementCoef}
                  onChange={(event) => setAnnouncementCoef(event.target.value)}
                  placeholder="1.95"
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-[#ff007f]/50 transition-colors"
                />
              </div>
            </div>

            <BookmakerMultiSelect
              label="Букмекерские конторы"
              hint="Выберите одну или несколько БК для анонса ставки."
              bookmakers={bookmakers}
              selectedIds={announcementBkIds}
              onChange={setAnnouncementBkIds}
              disabled={bookmakersLoading}
              allowAll={false}
            />

            <div>
              <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                Текст <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
              </label>
              <EmojiTextField
                multiline
                value={announcementBody}
                onValueChange={setAnnouncementBody}
                placeholder="Коротко: что за ставка и почему срочно."
                rows={3}
                className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-[#ff007f]/50 transition-colors resize-none"
              />
            </div>

            <div>
              <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                <Link className="w-3.5 h-3.5 inline mr-1 -mt-0.5" />
                Ссылка на матч <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
              </label>
              <input
                type="url"
                value={announcementMatchLink}
                onChange={(event) => setAnnouncementMatchLink(event.target.value)}
                placeholder="https://..."
                className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-[#ff007f]/50 transition-colors"
              />
            </div>
          </div>

          <div className="backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl p-5">
            <UploadDropzone
              file={announcementFile}
              preview={announcementPreview}
              dragging={announcementDragging}
              label="Изображение к анонсу (необязательно)"
              inputRef={announcementInputRef}
              onRemove={clearAnnouncementFile}
              {...announcementFileHandlers}
            />
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-[#ff007f] hover:bg-[#ff007f]/90 active:scale-[0.98] disabled:opacity-50 text-white font-black py-3.5 rounded-xl flex items-center justify-center space-x-2 transition-all shadow-[0_0_20px_rgba(255,0,127,0.4)] uppercase tracking-wider text-sm"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <BellRing className="w-4 h-4" />}
            <span>{submitting ? 'Отправка...' : 'Отправить анонс'}</span>
          </button>
        </form>
      )}

      {mode === 'forecast' && (
        <form onSubmit={handleForecastSubmit} className="space-y-4">
          <div className="backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Коэффициент
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="1"
                  value={forecastCoef}
                  onChange={(event) => setForecastCoef(event.target.value)}
                  placeholder="1.95"
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
                />
              </div>
              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Спорт
                </label>
                <div className="flex items-center gap-2">
                  <SportIconFrame label={forecastSport} size="badge" active className="shrink-0" />
                  <select
                    value={forecastSport}
                    onChange={(event) => setForecastSport(event.target.value)}
                    className="min-w-0 flex-1 bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-emerald-500/50 transition-colors"
                  >
                    {SPORT_FILTER_OPTIONS.filter((sport) => sport !== 'Все').map((sport) => (
                      <option key={sport} value={sport}>{sport}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <BookmakerMultiSelect
              label="Букмекеры"
              hint="Обязательно: анонс уйдет только клиентам, у которых выбрана хотя бы одна из этих БК."
              bookmakers={bookmakers}
              selectedIds={forecastBkIds}
              onChange={setForecastBkIds}
              disabled={bookmakersLoading}
              allowAll={false}
            />

            <div>
              <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                Описание анонса <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
              </label>
              <EmojiTextField
                multiline
                value={forecastTeaserText}
                onValueChange={setForecastTeaserText}
                placeholder="Например: закрытый прогноз по вашей БК, линия скоро уйдет."
                rows={3}
                className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors resize-none"
              />
            </div>

            {forecastBkIds.length > 0 && (
              <div className="bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 p-2.5 rounded-xl text-[10px] leading-normal flex items-center space-x-1.5 select-none">
                <Target className="w-3.5 h-3.5 shrink-0" />
                <span>
                  Анонс получат пользователи, у которых выбрана хотя бы одна БК: {selectedForecastBkNames.join(', ')}.
                </span>
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-3.5 rounded-xl flex items-center justify-center space-x-2 transition-all shadow-neon-green uppercase tracking-wider text-sm"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            <span>{submitting ? 'Отправка...' : 'Отправить анонс прогноза'}</span>
          </button>
        </form>
      )}

      {mode === 'requests' && (
        <div className="backdrop-blur-xl bg-slate-950/40 border border-white/10 rounded-2xl p-5 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center space-x-2 min-w-0">
              <Inbox className="w-5 h-5 text-emerald-400 shrink-0" />
              <div className="min-w-0">
                <h3 className="text-sm font-black text-white uppercase tracking-wider">
                  Заявки на прогноз
                </h3>
                <p className="text-[10px] text-slate-500 font-bold truncate">
                  Матч активируется после действия продажника
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={fetchForecastRequests}
              disabled={requestsLoading}
              className="h-9 w-9 rounded-xl bg-slate-900/70 border border-white/10 text-slate-300 hover:text-white hover:border-emerald-500/30 flex items-center justify-center transition-all disabled:opacity-50"
              title="Обновить очередь"
            >
              {requestsLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Radio className="w-4 h-4" />}
            </button>
          </div>

          <div className="flex overflow-x-auto gap-2 pb-1">
            {FORECAST_REQUEST_TABS.map((tab) => (
              <button
                key={tab.status}
                type="button"
                onClick={() => {
                  setRequestStatusFilter(tab.status);
                  setSelectedRequestIds([]);
                  setExpandedForecastMatchKeys([]);
                }}
                className={`shrink-0 px-3 py-2 rounded-xl border text-[10px] font-black uppercase tracking-wider transition-all ${
                  requestStatusFilter === tab.status
                    ? 'bg-emerald-500 text-slate-950 border-emerald-400'
                    : 'bg-slate-900/60 text-slate-400 border-white/10 hover:text-white'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {requestStatusFilter === 'interested' && (
            <div className="space-y-3 bg-slate-900/50 border border-white/10 rounded-xl p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider">
                  Выбрано: <span className="text-white">{selectedRequestIds.length}</span>
                </div>
                <button
                  type="button"
                  onClick={toggleAllProcessableRequests}
                  disabled={processableRequestIds.length === 0 || requestActionLoading !== null}
                  className="px-3 py-2 rounded-xl border border-white/10 bg-slate-950/50 text-slate-300 disabled:opacity-50 font-black text-[10px] uppercase tracking-wider flex items-center space-x-1.5"
                >
                  <CheckCheck className="w-3.5 h-3.5" />
                  <span>{allProcessableSelected ? 'Снять все' : 'Выбрать всех'}</span>
                </button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={openBulkFullForecastModal}
                  disabled={requestActionLoading !== null || selectedRequestIds.length === 0}
                  className="px-3 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-slate-950 disabled:opacity-50 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5"
                >
                  {requestActionLoading?.startsWith('bulk-send:') ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Send className="w-3.5 h-3.5" />
                  )}
                  <span>Отправить выбранным</span>
                </button>
                <button
                  type="button"
                  onClick={handleBulkManualMark}
                  disabled={requestActionLoading === 'bulk:manual' || selectedRequestIds.length === 0}
                  className="px-3 py-2.5 rounded-xl bg-[#00d2ff] text-slate-950 disabled:opacity-50 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5"
                >
                  {requestActionLoading === 'bulk:manual' ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Handshake className="w-3.5 h-3.5" />
                  )}
                  <span>Взял вручную</span>
                </button>
              </div>
            </div>
          )}

          {requestsLoading ? (
            <div className="h-28 flex items-center justify-center text-slate-500">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : forecastRequests.length === 0 ? (
            <div className="border border-dashed border-white/10 rounded-xl p-7 text-center">
              <Inbox className="w-7 h-7 text-slate-600 mx-auto mb-2" />
              <p className="text-xs text-slate-400 font-bold uppercase tracking-wider">Заявок нет</p>
            </div>
          ) : (
            <div className="space-y-3">
              {forecastRequestGroups.map((group) => {
                const expanded = expandedForecastMatchKeySet.has(group.key);
                const groupBookmakers = getBetBookmakers(group.bet);
                const selectedInGroup = group.requests.filter((request) => selectedRequestIds.includes(request.id)).length;
                const stopActionKey = `stop:${group.bet.id}`;
                const groupStopped = group.bet.status === 'deleted';
                return (
                  <div key={group.key} className="overflow-hidden bg-slate-900/45 border border-white/10 rounded-xl">
                    <div className="flex items-stretch gap-2 px-3 py-3">
                      <button
                        type="button"
                        onClick={() => toggleForecastMatchGroup(group.key)}
                        aria-expanded={expanded}
                        className="min-w-0 flex-1 text-left flex items-center gap-3 rounded-lg hover:bg-white/[0.03] transition-all"
                      >
                        <div className="min-w-0 flex-1">
                          <span className="block text-[10px] text-slate-500 font-black uppercase tracking-wider">
                            Матч
                          </span>
                          <span className="block text-sm font-black text-white truncate">
                            {group.eventName}
                          </span>
                          <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[10px] text-slate-500 font-bold">
                            {groupBookmakers.slice(0, 3).map((bookmaker) => (
                              <BookmakerLogoFrame
                                key={bookmaker.id}
                                bookmaker={bookmaker}
                                size="tiny"
                                className="shrink-0"
                              />
                            ))}
                            <span className="truncate">
                              {groupBookmakers.map((bookmaker) => bookmaker.name).join(', ') || 'БК не указана'}
                            </span>
                          </span>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="text-[10px] font-black uppercase tracking-wider text-slate-300">
                            {formatForecastRequestCount(group.requests.length)}
                          </div>
                          {selectedInGroup > 0 && (
                            <div className="mt-1 text-[9px] font-black uppercase tracking-wider text-emerald-300">
                              выбрано {selectedInGroup}
                            </div>
                          )}
                        </div>
                        <ChevronDown className={`h-4 w-4 shrink-0 text-slate-500 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                      </button>
                      <button
                        type="button"
                        onClick={() => stopForecastMatch(group)}
                        disabled={requestActionLoading !== null || groupStopped}
                        className="shrink-0 self-center rounded-lg border border-rose-500/25 bg-rose-500/10 px-2.5 py-2 text-[9px] font-black uppercase tracking-wider text-rose-300 transition-all hover:bg-rose-500/20 disabled:opacity-50"
                        title={groupStopped ? 'Матч остановлен' : 'Остановить матч'}
                      >
                        {requestActionLoading === stopActionKey ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <span>{groupStopped ? 'Стоп' : 'Остановить'}</span>
                        )}
                      </button>
                    </div>

                    {expanded && (
                      <div className="space-y-3 border-t border-white/10 p-3">
                        {group.requests.map((request) => {
                          const processable = canProcessForecastRequest(request);
                          const removable = canRemoveForecastRequest(request);
                          const selected = selectedRequestIds.includes(request.id);
                          const balance = request.balance_after ?? request.user.matches_remaining;
                          const canSendSavedForecast = processable && betHasSavedFullForecast(request.bet);
                          return (
                            <div key={request.id} className="bg-slate-950/35 border border-white/10 rounded-xl p-3 space-y-3">
                              <div className="flex items-start gap-3">
                                {processable && (
                                  <button
                                    type="button"
                                    onClick={() => toggleRequestSelection(request.id)}
                                    className={`mt-0.5 h-5 w-5 rounded-md border flex items-center justify-center transition-all ${
                                      selected
                                        ? 'bg-emerald-500 border-emerald-400 text-slate-950'
                                        : 'border-white/15 text-transparent hover:border-emerald-400'
                                    }`}
                                    title="Выбрать заявку"
                                  >
                                    <CheckCircle className="w-3.5 h-3.5" />
                                  </button>
                                )}
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                      <p className="text-sm font-black text-white truncate">
                                        {getForecastRequestUserName(request)}
                                      </p>
                                      <p className="text-[10px] text-slate-500 font-bold">
                                        {getForecastRequestUserIdLabel(request)} · баланс {balance}
                                      </p>
                                    </div>
                                    <div className={`px-2 py-1 rounded-lg text-[9px] font-black uppercase tracking-wider border ${
                                      request.status === 'sent'
                                        ? 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10'
                                        : request.status === 'manual_sent'
                                          ? 'text-[#00d2ff] border-[#00d2ff]/30 bg-[#00d2ff]/10'
                                          : request.status === 'removed'
                                            ? 'text-slate-300 border-slate-500/30 bg-slate-500/10'
                                            : request.status === 'declined' || request.status === 'cancelled'
                                            ? 'text-rose-300 border-rose-500/30 bg-rose-500/10'
                                            : request.status === 'processing'
                                              ? 'text-indigo-300 border-indigo-500/30 bg-indigo-500/10'
                                              : 'text-amber-300 border-amber-500/30 bg-amber-500/10'
                                    }`}>
                                      {request.status === 'sent' && getDeliveryMethodLabel(request.delivery_method)}
                                      {request.status === 'manual_sent' && 'взял'}
                                      {request.status === 'removed' && 'удален'}
                                      {request.status === 'declined' && 'отказ'}
                                      {request.status === 'cancelled' && 'отмена'}
                                      {request.status === 'interested' && 'ждет'}
                                      {request.status === 'processing' && 'идет'}
                                      {request.status === 'announced' && 'анонс'}
                                    </div>
                                  </div>

                                  <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2 text-[10px]">
                                    <div className="bg-slate-950/50 rounded-lg px-2.5 py-2 border border-white/5">
                                      <span className="block text-slate-500 font-bold uppercase tracking-wider">Матч</span>
                                      <span className="block text-slate-200 font-bold truncate">{request.bet.event_name}</span>
                                    </div>
                                    <div className="bg-slate-950/50 rounded-lg px-2.5 py-2 border border-white/5">
                                      <span className="block text-slate-500 font-bold uppercase tracking-wider">БК</span>
                                      <span className="flex min-w-0 items-center gap-1.5 text-slate-200 font-bold">
                                        {getBetBookmakers(request.bet).slice(0, 3).map((bookmaker) => (
                                          <BookmakerLogoFrame
                                            key={bookmaker.id}
                                            bookmaker={bookmaker}
                                            size="tiny"
                                            className="shrink-0"
                                          />
                                        ))}
                                        <span className="truncate">
                                          {getBetBookmakers(request.bet).map((bookmaker) => bookmaker.name).join(', ') || '—'}
                                        </span>
                                      </span>
                                    </div>
                                  </div>

                                  {(request.no_balance_warning || request.user.matches_remaining <= 0) && processable && (
                                    <div className="mt-2 bg-amber-500/10 border border-amber-500/20 text-amber-300 rounded-lg p-2 text-[10px] font-bold flex items-center space-x-1.5">
                                      <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                                      <span>У клиента 0 матчей. После подтверждения баланс уйдет в минус.</span>
                                    </div>
                                  )}

                                  <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-slate-500 font-bold">
                                    <span className="flex items-center space-x-1">
                                      <Clock3 className="w-3.5 h-3.5" />
                                      <span>{formatRequestDate(request.responded_at || request.created_at)}</span>
                                    </span>
                                    {request.delivery_method === 'bot' && (
                                      <span className="flex items-center space-x-1 text-emerald-400">
                                        <Bot className="w-3.5 h-3.5" />
                                        <span>бот</span>
                                      </span>
                                    )}
                                    {request.delivery_method === 'vk' && (
                                      <span className="flex items-center space-x-1 text-[#4c8dff]">
                                        <Radio className="w-3.5 h-3.5" />
                                        <span>VK</span>
                                      </span>
                                    )}
                                    {request.delivery_method === 'vk_bot' && (
                                      <span className="flex items-center space-x-1 text-[#4c8dff]">
                                        <Radio className="w-3.5 h-3.5" />
                                        <span>VK + бот</span>
                                      </span>
                                    )}
                                    {request.delivery_method === 'web' && (
                                      <span className="flex items-center space-x-1 text-emerald-300">
                                        <BellRing className="w-3.5 h-3.5" />
                                        <span>web</span>
                                      </span>
                                    )}
                                    {request.delivery_method === 'manual' && (
                                      <span className="flex items-center space-x-1 text-[#00d2ff]">
                                        <Handshake className="w-3.5 h-3.5" />
                                        <span>вручную</span>
                                      </span>
                                    )}
                                  </div>
                                </div>
                              </div>

                              {(processable || removable) && (
                                <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
                                  {processable && (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => (
                                          canSendSavedForecast
                                            ? sendSavedFullForecast(request)
                                            : openFullForecastModal(request)
                                        )}
                                        disabled={requestActionLoading !== null}
                                        className="px-3 py-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 disabled:opacity-50 text-slate-950 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5"
                                      >
                                        {requestActionLoading === `send:${request.id}` ? (
                                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                        ) : canSendSavedForecast ? (
                                          <Send className="w-3.5 h-3.5" />
                                        ) : (
                                          <UserCheck className="w-3.5 h-3.5" />
                                        )}
                                        <span>{canSendSavedForecast ? 'Отправить' : 'Полная ставка'}</span>
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => runRequestAction(request.id, 'mark-manual', 'Клиент отмечен как взявший прогноз')}
                                        disabled={requestActionLoading !== null}
                                        className="px-3 py-2 rounded-xl bg-[#00d2ff] hover:bg-[#00d2ff]/90 disabled:opacity-50 text-slate-950 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5"
                                      >
                                        {requestActionLoading === `mark-manual:${request.id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Handshake className="w-3.5 h-3.5" />}
                                        <span>Взял вручную</span>
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => runRequestAction(request.id, 'cancel', 'Заявка отменена')}
                                        disabled={requestActionLoading !== null}
                                        className="px-3 py-2 rounded-xl bg-slate-800 hover:bg-rose-500/20 disabled:opacity-50 text-slate-300 hover:text-rose-300 border border-white/10 hover:border-rose-500/30 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5"
                                      >
                                        {requestActionLoading === `cancel:${request.id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
                                        <span>Отменить</span>
                                      </button>
                                    </>
                                  )}
                                  {removable && (
                                    <button
                                      type="button"
                                      onClick={() => handleRemoveForecastRequest(request)}
                                      disabled={requestActionLoading !== null}
                                      className={`px-3 py-2 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 disabled:opacity-50 text-rose-300 border border-rose-500/25 hover:border-rose-400/45 font-black text-[10px] uppercase tracking-wider flex items-center justify-center space-x-1.5 ${processable ? '' : 'sm:col-span-4'}`}
                                    >
                                      {requestActionLoading === `remove-client:${request.id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UserMinus className="w-3.5 h-3.5" />}
                                      <span>Удалить клиента</span>
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {fullForecastModalOpen && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-slate-950/80 backdrop-blur-sm px-3 py-4">
          <form
            onSubmit={handleFullForecastSubmit}
            className="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-2xl border border-emerald-500/25 bg-slate-950 shadow-[0_0_45px_rgba(16,185,129,0.18)] p-5 space-y-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[0.18em] text-emerald-300">
                  {fullForecastIsPreparing
                    ? 'Полная ставка после анонса'
                    : fullForecastIsBulkSending
                      ? 'Полная ставка выбранным'
                      : 'Полная ставка'}
                </p>
                <h3 className="mt-1 text-lg font-black text-white">
                  {fullForecastIsPreparing
                    ? 'Заполните детали прогноза'
                    : fullForecastIsBulkSending
                      ? `${fullForecastBulkRequests.length} клиентам`
                      : fullForecastRequest
                        ? getForecastRequestUserName(fullForecastRequest)
                        : 'Клиент'}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => closeFullForecastModal()}
                disabled={fullForecastSubmitting}
                className="h-9 w-9 rounded-xl border border-white/10 bg-slate-900/70 text-slate-400 hover:text-white disabled:opacity-50 flex items-center justify-center transition-all"
              >
                <XCircle className="w-4 h-4" />
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="sm:col-span-2">
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Матч <span className="text-rose-400">*</span>
                </label>
                <EmojiTextField
                  type="text"
                  value={fullForecastEvent}
                  onValueChange={setFullForecastEvent}
                  placeholder="Реал Мадрид - Барселона"
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
                />
              </div>

              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Исход <span className="text-rose-400">*</span>
                </label>
                <EmojiTextField
                  type="text"
                  value={fullForecastOutcome}
                  onValueChange={setFullForecastOutcome}
                  placeholder="П1 / ТБ 2.5"
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
                />
              </div>

              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Коэффициент
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="1"
                  value={fullForecastCoef}
                  onChange={(event) => setFullForecastCoef(event.target.value)}
                  placeholder="1.95"
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
                />
              </div>

              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Спорт
                </label>
                <div className="flex items-center gap-2">
                  <SportIconFrame label={fullForecastSport} size="badge" active className="shrink-0" />
                  <select
                    value={fullForecastSport}
                    onChange={(event) => setFullForecastSport(event.target.value)}
                    className="min-w-0 flex-1 bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-emerald-500/50 transition-colors"
                  >
                    {SPORT_FILTER_OPTIONS.filter((sport) => sport !== 'Все').map((sport) => (
                      <option key={sport} value={sport}>{sport}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Формат
                </label>
                <div className="flex bg-slate-900/50 border border-white/10 rounded-xl p-0.5">
                  <button
                    type="button"
                    onClick={() => setFullForecastCategory('prematch')}
                    className={`flex-1 py-2 rounded-lg font-bold text-[10px] uppercase tracking-wider transition-all ${
                      fullForecastCategory === 'prematch' ? 'bg-indigo-500 text-white shadow-neon-indigo' : 'text-slate-500'
                    }`}
                  >
                    Prematch
                  </button>
                  <button
                    type="button"
                    onClick={() => setFullForecastCategory('live')}
                    className={`flex-1 py-2 rounded-lg font-bold text-[10px] uppercase tracking-wider transition-all ${
                      fullForecastCategory === 'live' ? 'bg-rose-500 text-white shadow-neon-rose' : 'text-slate-500'
                    }`}
                  >
                    Live
                  </button>
                </div>
              </div>

              <div className="sm:col-span-2 space-y-3">
                {fullForecastBookmakers.length > 0 && (
                  <div className="space-y-2">
                    <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider">
                      Ссылки по БК
                    </label>
                    <div className="space-y-2">
                      {fullForecastBookmakers.map((bookmaker) => {
                        const linkState = fullForecastBookmakerLinkStates[bookmaker.id];
                        const linkError = linkState?.error;
                        const linkPreview = linkState?.preview;
                        return (
                          <div
                            key={bookmaker.id}
                            className={`grid grid-cols-1 sm:grid-cols-[190px_minmax(0,1fr)] gap-2 rounded-xl border bg-slate-900/45 p-2 ${
                              linkError ? 'border-rose-500/40' : 'border-white/10'
                            }`}
                          >
                            <div className="flex min-w-0 items-center gap-2 rounded-lg bg-slate-950/40 px-2 py-1.5">
                              <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="shrink-0" />
                              <span className="min-w-0 truncate text-[11px] font-black text-white">
                                {bookmaker.name}
                              </span>
                            </div>
                            <div className="min-w-0 space-y-1.5">
                              <input
                                type="text"
                                inputMode="url"
                                autoCapitalize="none"
                                spellCheck={false}
                                aria-invalid={Boolean(linkError)}
                                value={fullForecastBookmakerLinks[bookmaker.id] || ''}
                                onChange={(event) => {
                                  const nextValue = event.target.value;
                                  setFullForecastBookmakerLinks((current) => ({
                                    ...current,
                                    [bookmaker.id]: nextValue,
                                  }));
                                }}
                                placeholder="https://..."
                                className={`min-w-0 w-full bg-slate-800/60 border rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none transition-colors ${
                                  linkError
                                    ? 'border-rose-500/50 focus:border-rose-400'
                                    : 'border-white/10 focus:border-emerald-500/50'
                                }`}
                              />
                              {linkError && (
                                <p className="text-[10px] font-bold text-rose-300">
                                  {linkError}
                                </p>
                              )}
                              {!linkError && linkPreview && (
                                <p className="text-[10px] font-bold text-slate-500">
                                  Клиент увидит:{' '}
                                  <span className="break-all text-emerald-300">{linkPreview}</span>
                                </p>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              <div className="sm:col-span-2">
                <label className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider mb-1.5">
                  Аналитика <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
                </label>
                <EmojiTextField
                  multiline
                  value={fullForecastDescription}
                  onValueChange={setFullForecastDescription}
                  placeholder="Разбор матча, аргументы, риск."
                  rows={3}
                  className="w-full bg-slate-800/60 border border-white/10 rounded-xl px-3 py-2.5 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors resize-none"
                />
              </div>
            </div>

            <UploadDropzone
              file={fullForecastFile}
              preview={fullForecastPreview}
              existingUrl={fullForecastExistingCouponUrl}
              dragging={fullForecastDragging}
              label="Скрин купона"
              required={!fullForecastExistingCouponUrl}
              inputRef={fullForecastInputRef}
              onRemove={clearFullForecastFile}
              {...fullForecastFileHandlers}
            />

            {fullForecastIsPreparing && (
              <button
                type="button"
                onClick={() => setFullForecastAutoSend((current) => !current)}
                className={`w-full rounded-xl border p-3 text-left transition-all ${
                  fullForecastAutoSend
                    ? 'border-emerald-500/40 bg-emerald-500/10'
                    : 'border-white/10 bg-slate-900/50 hover:border-emerald-500/30'
                }`}
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`relative h-6 w-11 shrink-0 rounded-full transition-all ${
                      fullForecastAutoSend ? 'bg-emerald-500' : 'bg-slate-700'
                    }`}
                  >
                    <span
                      className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow transition-all ${
                        fullForecastAutoSend ? 'left-6' : 'left-1'
                      }`}
                    />
                  </span>
                  <Bot className={`h-4 w-4 shrink-0 ${fullForecastAutoSend ? 'text-emerald-300' : 'text-slate-500'}`} />
                  <div className="min-w-0">
                    <p className="text-xs font-black uppercase tracking-wider text-white">
                      Автоотправка после «Взять»
                    </p>
                    <p className="mt-0.5 text-[10px] font-bold text-slate-500">
                      Текущим и новым заявкам
                    </p>
                  </div>
                </div>
              </button>
            )}

            <button
              type="submit"
              disabled={fullForecastSubmitting}
              className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-3.5 rounded-xl flex items-center justify-center space-x-2 transition-all shadow-neon-green uppercase tracking-wider text-sm"
            >
              {fullForecastSubmitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : fullForecastIsPreparing ? (
                <ClipboardList className="w-4 h-4" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              <span>
                {fullForecastSubmitting
                  ? fullForecastIsPreparing ? 'Сохранение...' : 'Отправка...'
                  : fullForecastIsPreparing
                    ? fullForecastAutoSend ? 'Сохранить и отправить' : 'Сохранить полную ставку'
                    : fullForecastIsBulkSending
                      ? 'Отправить выбранным'
                      : 'Отправить полную ставку'}
              </span>
            </button>
          </form>
        </div>
      )}

      {(successMessage || deliveryResult) && (
        <div className="backdrop-blur-xl bg-slate-950/40 border border-emerald-500/20 rounded-2xl p-5 space-y-3 animate-slide-up">
          <div className="flex items-center space-x-2">
            <CheckCircle className="w-5 h-5 text-emerald-400" />
            <h3 className="text-sm font-bold text-emerald-400 uppercase tracking-wider">
              {successMessage || 'Готово'}
            </h3>
          </div>
          {deliveryResult && (
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-3 text-center">
                <div className="text-xl font-black text-emerald-400">{deliveryResult.sent}</div>
                <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mt-0.5">
                  Доставлено
                </div>
              </div>
              <div className="bg-rose-500/10 border border-rose-500/20 rounded-xl p-3 text-center">
                <div className="text-xl font-black text-rose-400">{deliveryResult.failed}</div>
                <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mt-0.5">
                  Не доставлено
                </div>
              </div>
            </div>
          )}
          {deliveryResult && (deliveryResult.telegram || deliveryResult.vkMessages || deliveryResult.webPush) && (
            <div className="grid grid-cols-1 gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-300 sm:grid-cols-3">
              {deliveryResult.telegram && (
                <div className="rounded-xl border border-sky-400/15 bg-sky-400/10 px-3 py-2">
                  Telegram: <span className="text-emerald-300">{deliveryResult.telegram.sent}</span>
                  <span className="text-slate-500"> / </span>
                  <span className="text-rose-300">{deliveryResult.telegram.failed}</span>
                </div>
              )}
              {deliveryResult.vkMessages && (
                <div className="rounded-xl border border-[#0077ff]/20 bg-[#0077ff]/10 px-3 py-2">
                  VK: <span className="text-emerald-300">{deliveryResult.vkMessages.sent}</span>
                  <span className="text-slate-500"> / </span>
                  <span className="text-rose-300">{deliveryResult.vkMessages.failed}</span>
                </div>
              )}
              {deliveryResult.webPush && (
                <div className="rounded-xl border border-emerald-300/15 bg-emerald-300/10 px-3 py-2">
                  Web Push: <span className="text-emerald-300">{deliveryResult.webPush.sent}</span>
                  <span className="text-slate-500"> / </span>
                  <span className="text-rose-300">{deliveryResult.webPush.failed}</span>
                  {typeof deliveryResult.webPush.missing_permission === 'number' && deliveryResult.webPush.missing_permission > 0 && (
                    <span className="ml-1 text-amber-200">без разрешения: {deliveryResult.webPush.missing_permission}</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {submitError && (
        <div className="backdrop-blur-xl bg-slate-950/40 border border-rose-500/20 rounded-2xl p-4 flex items-center space-x-3 animate-slide-up">
          <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0" />
          <div>
            <p className="text-xs font-bold text-rose-400">Ошибка</p>
            <p className="text-[10px] text-slate-400 mt-0.5">{submitError}</p>
          </div>
        </div>
      )}
    </div>
  );
}
