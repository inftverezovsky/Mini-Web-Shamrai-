import React, { useId, useState } from 'react';
import { AnimatePresence, motion, type Variants } from 'framer-motion';
import { Check, CheckCheck, ChevronDown, Layers3, Sparkles } from 'lucide-react';
import { BookmakerResponse } from '../schemas/schemas';
import { BookmakerLogoFrame } from './LogoFrame';
import SmoothCollapse from './SmoothCollapse';

interface BookmakerMultiSelectProps {
  bookmakers: BookmakerResponse[];
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
  allowAll?: boolean;
  selectAllLabel?: string;
}

const premiumEase = [0.16, 1, 0.3, 1] as [number, number, number, number];

const listMotion: Variants = {
  hidden: { opacity: 0 },
  show: {
    opacity: 1,
    transition: {
      delayChildren: 0.04,
      staggerChildren: 0.035,
    },
  },
};

const cardMotion: Variants = {
  hidden: { opacity: 0, y: 10, scale: 0.985, filter: 'blur(6px)' },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    filter: 'blur(0px)',
    transition: { duration: 0.34, ease: premiumEase },
  },
};

export default function BookmakerMultiSelect({
  bookmakers,
  selectedIds,
  onChange,
  label,
  disabled = false,
  allowAll = true,
  selectAllLabel = 'Выбрать все БК',
}: BookmakerMultiSelectProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const contentId = useId();
  const selectedSet = new Set(selectedIds);
  const allBookmakerIds = bookmakers.map((bookmaker) => bookmaker.id);
  const allSelected = bookmakers.length > 0 && bookmakers.every((bookmaker) => selectedSet.has(bookmaker.id));
  const selectedNames = bookmakers
    .filter((bookmaker) => selectedSet.has(bookmaker.id))
    .map((bookmaker) => bookmaker.name);
  const selectedCountLabel = allSelected ? 'Все' : selectedIds.length === 0 ? (allowAll ? 'Все БК' : '0') : selectedIds.length.toString();
  const selectedSummary = allSelected ? 'Все БК' : selectedNames.length > 0 ? selectedNames.join(', ') : allowAll ? 'Все БК' : '0 БК';

  const toggleBookmaker = (bookmakerId: number) => {
    if (disabled) return;
    if (selectedSet.has(bookmakerId)) {
      onChange(selectedIds.filter((id) => id !== bookmakerId));
      return;
    }
    onChange([...selectedIds, bookmakerId]);
  };

  return (
    <div className="space-y-2.5">
      <button
        type="button"
        onClick={() => setIsExpanded((current) => !current)}
        aria-expanded={isExpanded}
        aria-controls={contentId}
        className="group flex w-full items-center justify-between gap-3 rounded-2xl border border-white/10 bg-slate-950/45 px-3 py-2.5 text-left shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] transition-all hover:border-cyan-300/35 hover:bg-slate-900/60"
      >
        <span className="min-w-0">
          <span className="block text-slate-400 text-[10px] font-bold uppercase tracking-wider">
            {label}
          </span>
          <span className="mt-1 block truncate text-[10px] font-semibold leading-tight text-slate-300">
            {selectedSummary}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="rounded-xl border border-white/10 bg-slate-950/55 px-2.5 py-1.5 text-right shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
            <span className="block text-[9px] uppercase font-bold tracking-wider text-slate-500">Выбрано</span>
            <span className="block text-[11px] font-black text-white">
              {selectedCountLabel}
            </span>
          </span>
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-cyan-200 transition-colors group-hover:border-cyan-300/35 group-hover:bg-cyan-300/10">
            <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} />
          </span>
        </span>
      </button>

      <div id={contentId}>
        <SmoothCollapse open={isExpanded}>
          <div className="bookmaker-picker rounded-2xl p-2.5">
            {allowAll && (
              <motion.button
                type="button"
                onClick={() => !disabled && onChange([])}
                disabled={disabled}
                whileHover={disabled ? undefined : { y: -2, scale: 1.01 }}
                whileTap={disabled ? undefined : { scale: 0.985 }}
                className={`bookmaker-card bookmaker-card--all mb-2 flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left ${
                  selectedIds.length === 0
                    ? 'bookmaker-card--active bookmaker-card--all-active text-cyan-100'
                    : 'text-slate-300'
                } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
              >
                <span className="flex items-center gap-2">
                  <Layers3 className="w-4 h-4 text-cyan-300" />
                  <span className="text-[11px] font-bold uppercase tracking-wider">Все букмекеры</span>
                </span>
                <AnimatePresence>
                  {selectedIds.length === 0 && (
                    <motion.span
                      initial={{ opacity: 0, scale: 0.6, rotate: -18 }}
                      animate={{ opacity: 1, scale: 1, rotate: 0 }}
                      exit={{ opacity: 0, scale: 0.55, rotate: 18 }}
                      transition={{ type: 'spring', stiffness: 420, damping: 24 }}
                      className="bookmaker-card__mark"
                    >
                      <Check className="w-3.5 h-3.5" />
                    </motion.span>
                  )}
                </AnimatePresence>
              </motion.button>
            )}

            {!allowAll && bookmakers.length > 0 && (
              <motion.button
                type="button"
                onClick={() => !disabled && onChange(allBookmakerIds)}
                disabled={disabled}
                whileHover={disabled ? undefined : { y: -2, scale: 1.01 }}
                whileTap={disabled ? undefined : { scale: 0.985 }}
                className={`bookmaker-card bookmaker-card--all mb-2 flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left ${
                  allSelected
                    ? 'bookmaker-card--active bookmaker-card--all-active text-cyan-100'
                    : 'text-slate-300'
                } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
              >
                <span className="flex items-center gap-2">
                  <CheckCheck className="w-4 h-4 text-cyan-300" />
                  <span className="text-[11px] font-bold uppercase tracking-wider">
                    {allSelected ? 'Все БК выбраны' : selectAllLabel}
                  </span>
                </span>
                <AnimatePresence>
                  {allSelected && (
                    <motion.span
                      initial={{ opacity: 0, scale: 0.6, rotate: -18 }}
                      animate={{ opacity: 1, scale: 1, rotate: 0 }}
                      exit={{ opacity: 0, scale: 0.55, rotate: 18 }}
                      transition={{ type: 'spring', stiffness: 420, damping: 24 }}
                      className="bookmaker-card__mark"
                    >
                      <Check className="w-3.5 h-3.5" />
                    </motion.span>
                  )}
                </AnimatePresence>
              </motion.button>
            )}

            <motion.div
              variants={listMotion}
              initial="hidden"
              animate="show"
              className="grid grid-cols-2 gap-2"
            >
              {bookmakers.map((bookmaker) => {
                const active = selectedSet.has(bookmaker.id);
                return (
                  <motion.button
                    key={bookmaker.id}
                    type="button"
                    onClick={() => toggleBookmaker(bookmaker.id)}
                    disabled={disabled}
                    variants={cardMotion}
                    layout
                    whileHover={disabled ? undefined : { y: -3, scale: 1.015 }}
                    whileTap={disabled ? undefined : { scale: 0.975 }}
                    className={`bookmaker-card relative flex min-h-[64px] items-center gap-2.5 rounded-xl px-2.5 py-2 ${
                      active
                        ? 'bookmaker-card--active text-emerald-50'
                        : 'text-slate-300'
                    } ${disabled ? 'opacity-60 cursor-not-allowed' : ''}`}
                  >
                    <BookmakerLogoFrame bookmaker={bookmaker} size="badge" active={active} className="h-8" />
                    <span className="relative z-[2] min-w-0 text-[11px] font-black leading-tight text-left">
                      {bookmaker.name}
                    </span>
                    <AnimatePresence>
                      {active && (
                        <motion.span
                          initial={{ opacity: 0, scale: 0.45, y: -4, rotate: -20 }}
                          animate={{ opacity: 1, scale: 1, y: 0, rotate: 0 }}
                          exit={{ opacity: 0, scale: 0.45, y: -4, rotate: 20 }}
                          transition={{ type: 'spring', stiffness: 520, damping: 24 }}
                          className="bookmaker-card__mark"
                        >
                          <Check className="w-3.5 h-3.5" />
                        </motion.span>
                      )}
                    </AnimatePresence>
                    <Sparkles className="bookmaker-card__spark w-3.5 h-3.5" />
                  </motion.button>
                );
              })}
            </motion.div>
          </div>
        </SmoothCollapse>
      </div>
    </div>
  );
}
