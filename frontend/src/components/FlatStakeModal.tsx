import { useEffect, useMemo, useState } from 'react';
import { Banknote, Loader2, X } from 'lucide-react';

interface FlatStakeModalProps {
  open: boolean;
  flatAmountRub?: string | number | null;
  submitting?: boolean;
  title?: string;
  onClose: () => void;
  onSubmit: (stakeRub: string) => void;
}

export function parseStakeInput(value: string): string | null {
  let normalized = value.trim().toLowerCase()
    .replace(/₽/g, '')
    .replace(/руб(?:лей|ля|ль)?/g, '')
    .trim();
  let multiplier = 1;
  if (/(?:к|k|тыс\.?)\s*$/.test(normalized)) {
    multiplier = 1000;
    normalized = normalized.replace(/(?:к|k|тыс\.?)\s*$/, '').trim();
  }
  normalized = normalized.replace(/\s+/g, '').replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) return null;
  const [wholePart, decimalPart = ''] = normalized.split('.');
  let cents = BigInt(wholePart) * 100n + BigInt(decimalPart.padEnd(2, '0'));
  cents *= BigInt(multiplier);
  if (cents < 100n || cents > 10_000_000_000n) return null;
  return `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
}

export default function FlatStakeModal({
  open,
  flatAmountRub,
  submitting = false,
  title = 'Сумма вашей ставки',
  onClose,
  onSubmit,
}: FlatStakeModalProps) {
  const [value, setValue] = useState('');
  const [touched, setTouched] = useState(false);
  const parsedValue = useMemo(() => parseStakeInput(value), [value]);

  useEffect(() => {
    if (!open) return;
    setValue('');
    setTouched(false);
  }, [open]);

  if (!open) return null;

  const flatAmount = Number(flatAmountRub || 0);
  const stakeFlats = parsedValue && flatAmount > 0 ? Number(parsedValue) / flatAmount : null;

  return (
    <div className="fixed inset-0 z-[120] flex items-end justify-center bg-slate-950/80 p-3 backdrop-blur-sm sm:items-center">
      <div className="w-full max-w-sm rounded-3xl border border-emerald-300/20 bg-slate-950 p-5 shadow-2xl shadow-emerald-950/50">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-emerald-300">
              <Banknote className="h-5 w-5" />
              <h3 className="text-sm font-black uppercase tracking-wide">{title}</h3>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-slate-400">
              Укажите фактическую сумму. Она нужна для точного расчёта прибыли во флетах.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="rounded-xl border border-white/10 bg-white/5 p-2 text-slate-400 hover:text-white disabled:opacity-50"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <label className="mt-5 block text-[10px] font-black uppercase tracking-wider text-slate-500">
          Сумма в рублях
          <input
            autoFocus
            inputMode="decimal"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onBlur={() => setTouched(true)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && parsedValue && !submitting) onSubmit(parsedValue);
            }}
            placeholder="Например: 5 000 или 5 тыс"
            className="mt-2 w-full rounded-2xl border border-white/10 bg-slate-900 px-4 py-3 text-base font-black text-white outline-none transition focus:border-emerald-400/50"
          />
        </label>

        {touched && !parsedValue && (
          <p className="mt-2 text-xs font-bold text-rose-300">Введите сумму от 1 ₽ до 100 000 000 ₽.</p>
        )}
        {stakeFlats !== null && (
          <div className="mt-3 rounded-2xl border border-emerald-300/15 bg-emerald-400/5 px-3 py-2 text-xs text-slate-300">
            Размер флета: <b className="text-white">{flatAmount.toLocaleString('ru-RU')} ₽</b>
            <span className="mx-2 text-slate-600">•</span>
            Ставка: <b className="text-emerald-300">{stakeFlats.toFixed(3)} флета</b>
          </div>
        )}

        <button
          type="button"
          onClick={() => {
            setTouched(true);
            if (parsedValue) onSubmit(parsedValue);
          }}
          disabled={!parsedValue || submitting}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-400 px-4 py-3 text-sm font-black text-slate-950 shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-300 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
          Подтвердить и взять прогноз
        </button>
      </div>
    </div>
  );
}
