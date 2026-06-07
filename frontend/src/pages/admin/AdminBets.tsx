import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { BookmakerResponse, BetResponse } from '../../schemas/schemas';
import { Send, HelpCircle, Calendar, Check, X, RefreshCw, Loader2, ListFilter, Upload, Link as LinkIcon } from 'lucide-react';
import { SPORT_OPTIONS } from '../../constants/sports';
import { BookmakerLogoFrame, SportIconFrame } from '../../components/LogoFrame';
import BookmakerMultiSelect from '../../components/BookmakerMultiSelect';
import { notifyError, notifySuccess } from '../../utils/notify';

interface AdminBetsProps {
  onBetsUpdated?: () => void;
}

interface ExtendedBetResponse extends BetResponse {
  isFading?: boolean;
}

export default function AdminBets({ onBetsUpdated }: AdminBetsProps) {
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>([]);
  const [pendingBets, setPendingBets] = useState<ExtendedBetResponse[]>([]);
  const [loading, setLoading] = useState(true);

  // Form states
  const [eventName, setEventName] = useState('');
  const [coefficient, setCoefficient] = useState('');
  const [selectedBkIds, setSelectedBkIds] = useState<number[]>([]);
  const [sportType, setSportType] = useState('');
  const [outcome, setOutcome] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<'prematch' | 'live'>('prematch');
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');
  const [priceStars, setPriceStars] = useState('');
  const [bookmakerLinks, setBookmakerLinks] = useState<Record<number, string>>({});

  // Coupon upload states
  const [couponImage, setCouponImage] = useState<File | null>(null);
  const [couponPreview, setCouponPreview] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      if (couponPreview) {
        URL.revokeObjectURL(couponPreview);
      }
      setCouponImage(file);
      setCouponPreview(URL.createObjectURL(file));
    }
  };

  const handleRemoveFile = () => {
    if (couponPreview) {
      URL.revokeObjectURL(couponPreview);
    }
    setCouponImage(null);
    setCouponPreview(null);
  };

  useEffect(() => {
    return () => {
      if (couponPreview) {
        URL.revokeObjectURL(couponPreview);
      }
    };
  }, [couponPreview]);

  // Settle loading states
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      const [bkList, betsList] = await Promise.all([
        apiFetch('/bookmakers'),
        apiFetch('/admin/bets/pending')
      ]);
      setBookmakers(bkList);
      setPendingBets(betsList);
    } catch (err) {
      console.error('Failed to load bets data:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const selectedBookmakers = bookmakers.filter((bookmaker) => selectedBkIds.includes(bookmaker.id));
  const selectedBookmakerNames = selectedBookmakers.map((bookmaker) => bookmaker.name);

  const handleBookmakerSelectionChange = (nextIds: number[]) => {
    setSelectedBkIds(nextIds);
    setBookmakerLinks((current) => nextIds.reduce<Record<number, string>>((acc, bookmakerId) => {
      if (current[bookmakerId]) acc[bookmakerId] = current[bookmakerId];
      return acc;
    }, {}));
  };

  const handlePublish = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!eventName || !coefficient) {
      notifyError('Заполните событие и коэффициент');
      return;
    }
    const missingBookmakerLinks = selectedBookmakers.filter((bookmaker) => !bookmakerLinks[bookmaker.id]?.trim());
    if (missingBookmakerLinks.length > 0) {
      notifyError(`Добавьте ссылку для БК: ${missingBookmakerLinks.map((bookmaker) => bookmaker.name).join(', ')}`);
      return;
    }

    try {
      setSubmitting(true);
      setSuccessMsg('');

      const formData = new FormData();
      formData.append('event_name', eventName);
      formData.append('coefficient', coefficient);
      if (selectedBkIds[0]) {
        formData.append('bookmaker_id', selectedBkIds[0].toString());
      }
      selectedBkIds.forEach(id => {
        formData.append('bookmaker_ids', id.toString());
      });
      if (sportType) {
        formData.append('sport_type', sportType);
      }
      if (outcome) {
        formData.append('outcome', outcome.trim());
      }
      if (description) {
        formData.append('description', description.trim());
      }
      formData.append('category', category);
      if (category === 'live') {
        formData.append('live_ends_at', new Date(Date.now() + 15 * 60000).toISOString());
      }
      if (priceStars) {
        formData.append('price_stars', priceStars);
      }
      if (selectedBookmakers.length > 0) {
        formData.append('bookmaker_links', JSON.stringify(
          selectedBookmakers.map((bookmaker) => ({
            bookmaker_id: bookmaker.id,
            url: bookmakerLinks[bookmaker.id].trim(),
          }))
        ));
      }
      formData.append('brain_score', '5');
      if (couponImage) {
        formData.append('coupon_image', couponImage);
      }

      await apiFetch('/bets/with-coupon', {
        method: 'POST',
        body: formData,
      });

      setSuccessMsg('Прогноз опубликован успешно!');
      setEventName('');
      setCoefficient('');
      setSelectedBkIds([]);
      setSportType('');
      setOutcome('');
      setDescription('');
      setPriceStars('');
      setBookmakerLinks({});
      setCouponImage(null);
      setCouponPreview(null);

      // Refresh list
      const freshBets = await apiFetch('/admin/bets/pending');
      setPendingBets(freshBets);
      if (onBetsUpdated) onBetsUpdated();

      setTimeout(() => setSuccessMsg(''), 3000);
      notifySuccess('Прогноз опубликован успешно');
    } catch (err: any) {
      notifyError(err.message || 'Ошибка создания прогноза');
    } finally {
      setSubmitting(false);
    }
  };

  const handleResolve = async (betId: string, status: 'win' | 'loss' | 'refund') => {
    try {
      setResolvingId(betId);
      
      const result = await apiFetch(`/bets/${betId}/resolve`, {
        method: 'PUT',
        body: JSON.stringify({ status })
      });

      if (status === 'loss' && result.guarantee_count > 0) {
        setSuccessMsg(`Гарантия открыта/продлена для ${result.guarantee_count} клиентов`);
        setTimeout(() => setSuccessMsg(''), 4000);
      }
      if (status === 'refund' && result.refund_count > 0) {
        setSuccessMsg(`Возвращено ${result.refund_count} матчей клиентам`);
        setTimeout(() => setSuccessMsg(''), 4000);
      }

      setPendingBets(prev =>
        prev.map(bet => (bet.id === betId ? { ...bet, isFading: true } : bet))
      );

      setTimeout(() => {
        setPendingBets(prev => prev.filter(bet => bet.id !== betId));
        if (onBetsUpdated) onBetsUpdated();
      }, 350);

    } catch (err: any) {
      notifyError(err.message || 'Не удалось рассчитать ставку');
    } finally {
      setResolvingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      
      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-5 rounded-2xl shadow-xl space-y-4 relative overflow-hidden">
        <h3 className="text-sm font-black text-white flex items-center uppercase tracking-wider">
          <Send className="w-4.5 h-4.5 text-indigo-400 mr-2 shrink-0" />
          Новая публикация
        </h3>

        <form onSubmit={handlePublish} className="space-y-3.5 text-xs text-slate-300">
          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Событие</label>
            <input
              type="text"
              value={eventName}
              onChange={e => setEventName(e.target.value)}
              placeholder="Реал Мадрид - Барселона"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Исход</label>
            <input
              type="text"
              value={outcome}
              onChange={e => setOutcome(e.target.value)}
              placeholder="П1 / победа Реала / тотал больше 2.5"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Коэффициент</label>
            <input
              type="number"
              step="0.01"
              value={coefficient}
              onChange={e => setCoefficient(e.target.value)}
              placeholder="1.95"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Цена в звездах (необязательно)</label>
            <input
              type="number"
              value={priceStars}
              onChange={e => setPriceStars(e.target.value)}
              placeholder="Оставьте пустым для бесплатной публикации"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
            />
          </div>

          <BookmakerMultiSelect
            label="Букмекеры (таргет)"
            hint="Выберите конторы, которые появятся кнопками под купоном в ленте."
            bookmakers={bookmakers}
            selectedIds={selectedBkIds}
            onChange={handleBookmakerSelectionChange}
          />

          {selectedBookmakers.length > 0 && (
            <div className="space-y-2">
              <label className="flex items-center text-slate-400 font-bold mb-1 uppercase tracking-wider text-[9px]">
                <LinkIcon className="w-3.5 h-3.5 mr-1.5 text-emerald-400" />
                Ссылки для кнопок БК
              </label>
              <div className="space-y-2">
                {selectedBookmakers.map((bookmaker) => (
                  <div
                    key={bookmaker.id}
                    className="grid grid-cols-1 gap-2 rounded-xl border border-white/10 bg-slate-900/45 p-2"
                  >
                    <div className="flex min-w-0 items-center gap-2 rounded-lg bg-slate-950/40 px-2 py-1.5">
                      <BookmakerLogoFrame bookmaker={bookmaker} size="badge" className="shrink-0" />
                      <span className="min-w-0 truncate text-[11px] font-black text-white">
                        {bookmaker.name}
                      </span>
                    </div>
                    <input
                      type="url"
                      value={bookmakerLinks[bookmaker.id] || ''}
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        setBookmakerLinks((current) => ({
                          ...current,
                          [bookmaker.id]: nextValue,
                        }));
                      }}
                      placeholder="https://..."
                      className="min-w-0 w-full bg-slate-800/60 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <div>
              <label className="block text-slate-400 font-bold mb-1 uppercase tracking-wider text-[9px]">Категория</label>
              <div className="flex bg-slate-900/50 border border-slate-700/60 rounded-xl p-0.5">
                <button
                  type="button"
                  onClick={() => setCategory('prematch')}
                  className={`flex-1 py-1.5 rounded-lg font-bold text-[9px] uppercase tracking-wider transition-all ${
                    category === 'prematch' ? 'bg-indigo-500 text-white shadow-neon-indigo' : 'text-slate-500'
                  }`}
                >
                  Prematch
                </button>
                <button
                  type="button"
                  onClick={() => setCategory('live')}
                  className={`flex-1 py-1.5 rounded-lg font-bold text-[9px] uppercase tracking-wider transition-all ${
                    category === 'live' ? 'bg-rose-500 text-white shadow-neon-rose' : 'text-slate-500'
                  }`}
                >
                  Live
                </button>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-slate-400 font-bold mb-1 uppercase tracking-wider text-[9px]">Вид спорта</label>
            <select
              value={sportType}
              onChange={e => setSportType(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
            >
              <option value="">Без фильтра по спорту</option>
              {SPORT_OPTIONS.map((sport) => (
                <option key={sport.label} value={sport.label}>
                  {sport.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">
              Описание / Обоснование <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Введите аналитический разбор матча..."
              rows={3}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2.5 px-3 text-white focus:outline-none focus:border-indigo-500/50 transition-all"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">
              Скриншот купона <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
            </label>
            {!couponPreview ? (
              <label className="relative flex flex-col items-center justify-center border border-dashed border-slate-700/80 hover:border-indigo-500/50 bg-slate-900/40 hover:bg-slate-900/60 rounded-xl p-4 cursor-pointer transition-all group">
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <Upload className="w-5 h-5 text-slate-500 group-hover:text-indigo-400 mb-1.5 transition-colors" />
                <span className="text-[10px] text-slate-400 group-hover:text-slate-300 font-semibold">Нажмите для выбора изображения</span>
                <span className="text-[8px] text-slate-600 mt-0.5">PNG, JPG, JPEG, WEBP, GIF (до 5 МБ)</span>
              </label>
            ) : (
              <div className="relative border border-white/10 bg-slate-950/40 rounded-xl p-2.5 flex items-center justify-between">
                <div className="flex items-center space-x-2.5 overflow-hidden">
                  <div className="w-10 h-10 rounded-lg overflow-hidden border border-white/5 bg-slate-950 shrink-0">
                    <img
                      src={couponPreview}
                      alt="Купон"
                      className="w-full h-full object-cover"
                    />
                  </div>
                  <div className="overflow-hidden">
                    <div className="text-[10px] text-slate-200 font-bold truncate">
                      {couponImage?.name}
                    </div>
                    <div className="text-[8px] text-slate-500">
                      {couponImage ? `${(couponImage.size / 1024 / 1024).toFixed(2)} MB` : ''}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleRemoveFile}
                  className="p-1.5 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 hover:bg-rose-500 hover:text-white transition-all active:scale-95"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>

          {selectedBkIds.length > 0 && (
            <div className="bg-amber-500/10 border border-amber-500/20 text-amber-400 p-2.5 rounded-xl text-[9px] leading-normal flex items-center space-x-1.5 select-none">
              <ListFilter className="w-3.5 h-3.5 shrink-0" />
              <span>
                Прогноз будет отправлен пользователям, у которых выбрана хотя бы одна БК: {selectedBookmakerNames.join(', ')}.
              </span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-3 rounded-xl flex items-center justify-center space-x-1.5 transition-all shadow-neon-green"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <span>Опубликовать в Ленту</span>
            )}
          </button>

          {successMsg && (
            <p className="text-center text-emerald-400 font-bold text-[10px] uppercase tracking-wider animate-pulse">
              {successMsg}
            </p>
          )}
        </form>
      </div>

      <div className="space-y-3.5">
        <h3 className="text-xs font-black text-slate-400 flex items-center uppercase tracking-wider">
          <HelpCircle className="w-4.5 h-4.5 text-amber-400 mr-2 shrink-0" />
          Расчет результатов
        </h3>

        <div className="space-y-3">
          {pendingBets.length === 0 ? (
            <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-6 text-center text-slate-500 text-xs rounded-2xl select-none">
              Нет активных прогнозов, требующих расчета.
            </div>
          ) : (
            pendingBets.map(bet => (
                <div 
                  key={bet.id}
                  className={`bg-white/5 border border-white/10 backdrop-blur-lg p-4.5 rounded-2xl space-y-3.5 transition-all duration-300 transform ${
                    bet.isFading ? 'opacity-0 scale-95 -translate-y-4' : 'opacity-100 scale-100'
                  }`}
                >
                  <div className="flex justify-between items-start">
                    <div>
                      <h4 className="text-xs font-bold text-white leading-snug">{bet.event_name}</h4>
                      {bet.outcome && (
                        <div className="mt-1 text-[10px] font-black uppercase tracking-wider text-indigo-300">
                          {bet.outcome}
                        </div>
                      )}
                      <div className="flex items-center space-x-2 mt-1">
                        <span className="text-[9px] text-slate-500 flex items-center">
                          <Calendar className="w-3 h-3 mr-1" />
                          {new Date(bet.created_at).toLocaleDateString('ru-RU')}
                        </span>
                        {bet.category === 'live' && (
                          <span className="bg-rose-500/10 text-rose-400 text-[8.5px] border border-rose-500/20 px-1.5 py-0.2 rounded font-black uppercase">Live</span>
                        )}
                        {bet.sport_type && (
                          <span className="bg-white/5 border border-white/10 text-slate-300 text-[8.5px] px-1.5 py-0.5 rounded font-black uppercase flex items-center gap-1">
                            <SportIconFrame label={bet.sport_type} size="tiny" />
                            {bet.sport_type}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="bg-emerald-500/15 border border-emerald-500/25 text-emerald-400 text-xs font-black px-2.5 py-0.5 rounded-lg shadow-neon-green shrink-0 select-none">
                      кф. {parseFloat(bet.coefficient as any).toFixed(2)}
                    </span>
                  </div>

                  <div className="flex items-center space-x-2.5">
                    <button
                      onClick={() => handleResolve(bet.id, 'win')}
                      disabled={resolvingId === bet.id || bet.isFading}
                      className="flex-1 bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500 hover:text-slate-950 active:scale-95 text-[10px] font-black py-2 rounded-xl flex items-center justify-center space-x-1 transition-all duration-200"
                    >
                      <Check className="w-3.5 h-3.5 shrink-0" />
                      <span>Выигрыш</span>
                    </button>
                    
                    <button
                      onClick={() => handleResolve(bet.id, 'loss')}
                      disabled={resolvingId === bet.id || bet.isFading}
                      className="flex-1 bg-rose-500/15 border border-rose-500/30 text-rose-400 hover:bg-rose-500 hover:text-white active:scale-95 text-[10px] font-black py-2 rounded-xl flex items-center justify-center space-x-1 transition-all duration-200"
                    >
                      <X className="w-3.5 h-3.5 shrink-0" />
                      <span>Проигрыш</span>
                    </button>

                    <button
                      onClick={() => handleResolve(bet.id, 'refund')}
                      disabled={resolvingId === bet.id || bet.isFading}
                      className="flex-1 bg-slate-800 border border-slate-700/60 text-slate-400 hover:bg-slate-700 hover:text-white active:scale-95 text-[10px] font-black py-2 rounded-xl flex items-center justify-center space-x-1 transition-all duration-200"
                    >
                      <RefreshCw className="w-3 h-3 shrink-0" />
                      <span>Возврат</span>
                    </button>
                  </div>

                </div>
              ))
          )}
        </div>
      </div>

    </div>
  );
}
