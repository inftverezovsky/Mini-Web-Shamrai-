import React, { useState, useEffect } from 'react';
import { apiFetch } from '../../utils/api';
import { BookmakerResponse } from '../../schemas/schemas';
import { Send, X, Loader2, ListFilter, Upload, Link as LinkIcon } from 'lucide-react';
import { SPORT_OPTIONS } from '../../constants/sports';
import { BookmakerLogoFrame } from '../../components/LogoFrame';
import BookmakerMultiSelect from '../../components/BookmakerMultiSelect';
import EmojiTextField from '../../components/EmojiTextField';
import { notifyError, notifySuccess } from '../../utils/notify';
import { getClipboardImageFile } from '../../utils/clipboardImages';

interface AdminBetsProps {
  onBetsUpdated?: () => void;
}

const COUPON_IMAGE_MAX_BYTES = 5 * 1024 * 1024;

export default function AdminBets({ onBetsUpdated }: AdminBetsProps) {
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>([]);
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

  const acceptCouponFile = (file: File) => {
    if (!file.type.startsWith('image/')) {
      notifyError('Допустимы только изображения');
      return;
    }
    if (file.size > COUPON_IMAGE_MAX_BYTES) {
      notifyError('Файл слишком большой. Максимум 5 МБ.');
      return;
    }
    if (couponPreview) {
      URL.revokeObjectURL(couponPreview);
    }
    setCouponImage(file);
    setCouponPreview(URL.createObjectURL(file));
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) acceptCouponFile(file);
    e.target.value = '';
  };

  const handleCouponPaste = (event: React.ClipboardEvent) => {
    const pastedFile = getClipboardImageFile(event.clipboardData);
    if (!pastedFile) return;
    event.preventDefault();
    event.stopPropagation();
    acceptCouponFile(pastedFile);
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

  const loadData = async () => {
    try {
      setLoading(true);
      const bkList = await apiFetch('/bookmakers');
      setBookmakers(bkList);
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
    if (!coefficient) {
      notifyError('Укажите коэффициент');
      return;
    }
    try {
      setSubmitting(true);
      setSuccessMsg('');

      const formData = new FormData();
      formData.append('event_name', eventName.trim());
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
        const bookmakerLinksPayload = selectedBookmakers
          .map((bookmaker) => ({
            bookmaker_id: bookmaker.id,
            url: (bookmakerLinks[bookmaker.id] || '').trim(),
          }))
          .filter((link) => link.url.length > 0);
        if (bookmakerLinksPayload.length > 0) {
          formData.append('bookmaker_links', JSON.stringify(bookmakerLinksPayload));
        }
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
      if (couponPreview) URL.revokeObjectURL(couponPreview);
      setCouponImage(null);
      setCouponPreview(null);

      if (onBetsUpdated) onBetsUpdated();

      setTimeout(() => setSuccessMsg(''), 3000);
      notifySuccess('Прогноз опубликован успешно');
    } catch (err: any) {
      notifyError(err.message || 'Ошибка создания прогноза');
    } finally {
      setSubmitting(false);
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
    <div className="space-y-4">
      
      <div className="bg-white/5 border border-white/10 backdrop-blur-lg p-3.5 rounded-2xl shadow-xl space-y-3 relative overflow-hidden">
        <h3 className="text-xs font-black text-white flex items-center uppercase tracking-wider">
          <Send className="w-4 h-4 text-indigo-400 mr-2 shrink-0" />
          Новая публикация
        </h3>

        <form onSubmit={handlePublish} onPaste={handleCouponPaste} className="space-y-2.5 text-[11px] text-slate-300">
          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">
              Событие <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
            </label>
            <EmojiTextField
              type="text"
              value={eventName}
              onValueChange={setEventName}
              placeholder="Реал Мадрид - Барселона"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Исход</label>
            <EmojiTextField
              type="text"
              value={outcome}
              onValueChange={setOutcome}
              placeholder="П1 / победа Реала / тотал больше 2.5"
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
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
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">Цена в звездах (необязательно)</label>
            <input
              type="number"
              value={priceStars}
              onChange={e => setPriceStars(e.target.value)}
              placeholder=""
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-bold"
            />
          </div>

          <BookmakerMultiSelect
            label="Букмекеры (таргет)"
            bookmakers={bookmakers}
            selectedIds={selectedBkIds}
            onChange={handleBookmakerSelectionChange}
            allowAll={false}
            selectAllLabel="Выбрать все БК"
          />

          {selectedBookmakers.length > 0 && (
            <div className="space-y-2">
              <label className="flex items-center text-slate-400 font-bold mb-1 uppercase tracking-wider text-[9px]">
                <LinkIcon className="w-3.5 h-3.5 mr-1.5 text-emerald-400" />
                Ссылки для кнопок БК <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
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
                      placeholder="https://... (необязательно)"
                      className="min-w-0 w-full bg-slate-800/60 border border-white/10 rounded-lg px-2.5 py-1.5 text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/50 transition-colors"
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
            <label className="block text-slate-400 font-bold mb-1 uppercase tracking-wider text-[9px]">
              Вид спорта <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
            </label>
            <select
              value={sportType}
              onChange={e => setSportType(e.target.value)}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all font-semibold"
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
            <EmojiTextField
              multiline
              value={description}
              onValueChange={setDescription}
              placeholder=""
              rows={2}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-xl py-2 px-2.5 text-white focus:outline-none focus:border-indigo-500/50 transition-all"
            />
          </div>

          <div>
            <label className="block text-slate-450 font-bold mb-1 uppercase tracking-wider text-[9px]">
              Скриншот купона <span className="text-slate-600 normal-case tracking-normal">(необязательно)</span>
            </label>
            {!couponPreview ? (
              <label
                onPaste={handleCouponPaste}
                tabIndex={0}
                className="relative flex flex-col items-center justify-center border border-dashed border-slate-700/80 hover:border-indigo-500/50 bg-slate-900/40 hover:bg-slate-900/60 rounded-xl p-3 cursor-pointer transition-all group"
              >
                <input
                  type="file"
                  accept="image/*"
                  onChange={handleFileChange}
                  className="hidden"
                />
                <Upload className="w-4 h-4 text-slate-500 group-hover:text-indigo-400 mb-1 transition-colors" />
              </label>
            ) : (
              <div
                onPaste={handleCouponPaste}
                tabIndex={0}
                className="relative border border-white/10 bg-slate-950/40 rounded-xl p-2 flex items-center justify-between"
              >
                <div className="flex items-center space-x-2.5 overflow-hidden">
                  <div className="w-8 h-8 rounded-lg overflow-hidden border border-white/5 bg-slate-950 shrink-0">
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
            className="w-full bg-emerald-500 hover:bg-emerald-600 active:scale-[0.98] disabled:opacity-50 text-slate-950 font-black py-2.5 rounded-xl flex items-center justify-center space-x-1.5 transition-all shadow-neon-green"
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

    </div>
  );
}
