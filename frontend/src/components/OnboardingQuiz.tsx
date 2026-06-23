import React, { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion, type Variants } from 'framer-motion';
import {
  Banknote,
  Brain,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Gauge,
  Loader2,
  Lock,
  RadioTower,
  ShieldCheck,
  Sparkles,
  Target,
  ToggleLeft,
  ToggleRight,
  TrendingUp,
  Trophy,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { apiFetch } from '../utils/api';
import { isVkIdReady, isVkRedirectStartedError, linkVkProfile } from '../utils/vkId';
import { BookmakerResponse } from '../schemas/schemas';
import EmojiTextField from './EmojiTextField';
import { BookmakerLogoFrame } from './LogoFrame';
import { useAuth } from '../context/AuthContext';
import { trackEvent } from '../utils/analytics';
import { usePerformanceProfile } from '../hooks/usePerformanceProfile';

type ExperienceLevel = 'novice' | 'amateur' | 'pro';
type BankrollSize = 'micro' | 'mid' | 'high';
type RiskTolerance = 'cautious' | 'balanced' | 'aggressive';
type CurrencyCode = 'RUB' | 'USD' | 'FLATS';
type VkLinkStatus = 'idle' | 'loading' | 'linked' | 'error';

interface OnboardingAnswers {
  anti_capper_pains: string[];
  experience_level: ExperienceLevel | null;
  bankroll_size: BankrollSize | null;
  risk_tolerance: RiskTolerance | null;
  bookmaker_codes: string[];
  other_bookmaker_name: string;
  vk_user_id: string | null;
}

interface OnboardingRecommendation {
  flat_stake_percent: number;
  monthly_profit_percent: number;
  missed_profit_percent_24h: number;
  missed_profit_amount_24h: number;
  currency: CurrencyCode;
  source: string;
  resolved_bets_24h: number;
}

interface OnboardingResponse {
  status: string;
  message: string;
  recommendation: OnboardingRecommendation;
}

interface OnboardingQuizProps {
  userId?: number;
  onCompleted: () => void | Promise<void>;
}

interface ChoiceOption<TValue extends string> {
  value: TValue;
  title: string;
  description: string;
  icon: LucideIcon;
}

const GLASS_SURFACE = 'bg-white/5 backdrop-blur-xl border border-white/10 transform-gpu will-change-transform';
const PINK = '#ff007f';
const CYAN = '#00d2ff';
const GOLD = '#f6c453';
const PINK_GLOW = '0 0 30px rgba(255,0,127,0.55), 0 0 70px rgba(0,210,255,0.16), inset 0 1px 0 rgba(255,255,255,0.16)';
const CYAN_GLOW = '0 0 30px rgba(0,210,255,0.52), 0 0 70px rgba(255,0,127,0.14), inset 0 1px 0 rgba(255,255,255,0.16)';
const GOLD_GLOW = '0 0 32px rgba(246,196,83,0.62), 0 0 84px rgba(246,196,83,0.22), inset 0 1px 0 rgba(255,255,255,0.18)';

const slideVariants: Variants = {
  enter: (direction: number) => ({
    x: direction > 0 ? 56 : -56,
    opacity: 0,
    filter: 'blur(12px)',
  }),
  center: {
    x: 0,
    opacity: 1,
    filter: 'blur(0px)',
  },
  exit: (direction: number) => ({
    x: direction > 0 ? -56 : 56,
    opacity: 0,
    filter: 'blur(12px)',
  }),
};

const antiCapperPains = [
  'Реклама скам-казино и постоянный спам',
  'Удаление/редактирование минусовых прогнозов',
  'Обещания 100% проходимости и договорных матчей',
];

const experienceOptions: ChoiceOption<ExperienceLevel>[] = [
  {
    value: 'novice',
    title: 'Новичок',
    description: 'Нужны ясные сигналы, строгий флэт и защита от импульсивных входов.',
    icon: Sparkles,
  },
  {
    value: 'amateur',
    title: 'Любитель',
    description: 'Опыт есть, но нужна дисциплина и прозрачная статистика дистанции.',
    icon: Trophy,
  },
  {
    value: 'pro',
    title: 'Профи',
    description: 'Работаете от value, линии, риска и холодной математики.',
    icon: Zap,
  },
];

const bankrollOptions: ChoiceOption<BankrollSize>[] = [
  {
    value: 'micro',
    title: 'До 30 000 ₽',
    description: 'Защитный режим: аккуратный темп и жесткая отсечка риска.',
    icon: Banknote,
  },
  {
    value: 'mid',
    title: '50 000 - 100 000 ₽',
    description: 'Рабочий банк для стабильного ритма и контролируемой просадки.',
    icon: CircleDollarSign,
  },
  {
    value: 'high',
    title: 'Более 100 000 ₽',
    description: 'Крупный банк: скрытый PRO-контур и повышенный Brain Score.',
    icon: TrendingUp,
  },
];

const riskOptions: ChoiceOption<RiskTolerance>[] = [
  {
    value: 'cautious',
    title: 'Осторожная',
    description: 'Низкая амплитуда, приоритет сохранения банка.',
    icon: ShieldCheck,
  },
  {
    value: 'balanced',
    title: 'Сбалансированная',
    description: 'Оптимальный темп между ростом и контролем дистанции.',
    icon: Gauge,
  },
  {
    value: 'aggressive',
    title: 'Агрессивная',
    description: 'Больше динамики, выше нагрузка на дисциплину.',
    icon: Target,
  },
];

const fallbackBookmakers: BookmakerResponse[] = [
  { id: 1, name: 'Фонбет', code: 'fonbet', is_active: true },
  { id: 2, name: 'БетБум', code: 'betboom', is_active: true },
  { id: 3, name: 'Винлайн', code: 'winline', is_active: true },
  { id: 4, name: 'Пари', code: 'pari', is_active: true },
  { id: 5, name: 'Лига Ставок', code: 'ligastavok', is_active: true },
  { id: 6, name: 'Марафонбет', code: 'marathon', is_active: true },
  { id: 7, name: 'Бетсити', code: 'betcity', is_active: true },
  { id: 8, name: 'Мелбет', code: 'melbet', is_active: true },
  { id: 9, name: 'Леон', code: 'leon', is_active: true },
  { id: 10, name: 'Олимпбет', code: 'olimpbet', is_active: true },
  { id: 11, name: 'Зенит', code: 'zenit', is_active: true },
  { id: 12, name: 'Другие', code: 'other', is_active: true },
];


const calibrationLogs = [
  'Синхронизация с сервером...',
  'Оценка совместимости (94% Match)...',
  'Расчет математического ожидания профита...',
];

const pulseLogs = [
  '🔥 Сейчас калибруются: 142 пользователя',
  '⚡ Ставка на Футбол рассчитана в плюс',
  '🧠 Shamrai Brain сверяет риск-профили',
  '💎 Закрыт новый value-сигнал для VIP-ленты',
  '📡 Пакеты ставок обновляют баланс без фрибетов',
];

const currencyMeta: Record<CurrencyCode, { label: string; symbol: string; rateFromRub: number }> = {
  RUB: { label: '₽', symbol: '₽', rateFromRub: 1 },
  USD: { label: '$', symbol: '$', rateFromRub: 92 },
  FLATS: { label: '₮', symbol: '₮', rateFromRub: 92 },
};

export default function OnboardingQuiz({ userId, onCompleted }: OnboardingQuizProps) {
  const { user } = useAuth();
  const reduceMotion = useReducedMotion();
  const performanceProfile = usePerformanceProfile();
  const reduceContinuousMotion = reduceMotion || performanceProfile.shouldReduceMotion;
  const [stepIndex, setStepIndex] = useState(0);
  const [direction, setDirection] = useState(1);
  const [answers, setAnswers] = useState<OnboardingAnswers>({
    anti_capper_pains: [],
    experience_level: null,
    bankroll_size: null,
    risk_tolerance: null,
    bookmaker_codes: [],
    other_bookmaker_name: '',
    vk_user_id: null,
  });
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>(fallbackBookmakers);
  const [bookmakersLoading, setBookmakersLoading] = useState(true);
  const [recommendation, setRecommendation] = useState<OnboardingRecommendation | null>(null);
  const [calibrationIndex, setCalibrationIndex] = useState(0);
  const [pulseIndex, setPulseIndex] = useState(0);
  const [currency, setCurrency] = useState<CurrencyCode>('RUB');
  const [manifestAccepted, setManifestAccepted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [vkLinkStatus, setVkLinkStatus] = useState<VkLinkStatus>('idle');
  const [vkDisplayName, setVkDisplayName] = useState<string | null>(null);
  const [vkLinkError, setVkLinkError] = useState<string | null>(null);
  const [flashActive, setFlashActive] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const proMode = answers.bankroll_size === 'high';
  const glow = proMode ? GOLD_GLOW : PINK_GLOW;
  const accent = proMode ? GOLD : PINK;
  const secondaryGlow = proMode ? GOLD_GLOW : CYAN_GLOW;
  const progressIndex = Math.min(stepIndex, 5);
  const vkReady = isVkIdReady();

  const visibleBookmakers = useMemo(() => {
    return bookmakers.length ? bookmakers : fallbackBookmakers;
  }, [bookmakers]);

  useEffect(() => {
    if (!user?.vk_user_id) return;

    setAnswers((current) => (
      current.vk_user_id === user.vk_user_id
        ? current
        : { ...current, vk_user_id: user.vk_user_id }
    ));
    setVkDisplayName(`VK ID ${user.vk_user_id}`);
    setVkLinkStatus('linked');
    setVkLinkError(null);

    if (stepIndex === 0) {
      const timer = window.setTimeout(() => {
        setDirection(1);
        setStepIndex(1);
      }, 850);
      return () => window.clearTimeout(timer);
    }
  }, [stepIndex, user?.vk_user_id]);

  const canContinue = useMemo(() => {
    if (stepIndex === 0 || stepIndex === 4) return false;
    if (stepIndex === 1) return answers.anti_capper_pains.length > 0;
    if (stepIndex === 2) return Boolean(answers.experience_level && answers.bankroll_size);
    if (stepIndex === 3) return Boolean(answers.risk_tolerance && answers.bookmaker_codes.length);
    return manifestAccepted && !finishing;
  }, [answers, finishing, manifestAccepted, stepIndex]);

  useEffect(() => {
    let mounted = true;

    async function loadBookmakers() {
      try {
        const data = await apiFetch<BookmakerResponse[]>('/bookmakers');
        if (mounted && Array.isArray(data) && data.length) {
          setBookmakers(data);
        }
      } catch (error) {
        console.error('Error loading onboarding bookmakers:', error);
      } finally {
        if (mounted) setBookmakersLoading(false);
      }
    }

    loadBookmakers();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setPulseIndex((current) => (current + 1) % pulseLogs.length);
    }, 2400);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (stepIndex !== 4) return;
    setCalibrationIndex(0);
    const timer = window.setInterval(() => {
      setCalibrationIndex((current) => Math.min(current + 1, calibrationLogs.length - 1));
    }, 820);
    return () => window.clearInterval(timer);
  }, [stepIndex]);

  const togglePain = (pain: string) => {
    setErrorMessage(null);
    setAnswers((current) => ({
      ...current,
      anti_capper_pains: current.anti_capper_pains.includes(pain)
        ? current.anti_capper_pains.filter((item) => item !== pain)
        : [...current.anti_capper_pains, pain],
    }));
  };

  const updateAnswer = <TKey extends keyof OnboardingAnswers>(key: TKey, value: OnboardingAnswers[TKey]) => {
    setErrorMessage(null);
    setAnswers((current) => ({ ...current, [key]: value }));
  };

  const toggleBookmaker = (bookmakerCode: string) => {
    setErrorMessage(null);
    setAnswers((current) => {
      const selected = current.bookmaker_codes.includes(bookmakerCode);
      return {
        ...current,
        bookmaker_codes: selected
          ? current.bookmaker_codes.filter((code) => code !== bookmakerCode)
          : [...current.bookmaker_codes, bookmakerCode],
      };
    });
  };

  const handleOtherBookmakerName = (value: string) => {
    setErrorMessage(null);
    setAnswers((current) => ({ ...current, other_bookmaker_name: value }));
  };

  const revealResults = () => {
    setDirection(1);
    setFlashActive(true);
    setStepIndex(5);
    window.setTimeout(() => setFlashActive(false), 760);
  };

  const submitCalibration = async (nextVkUserId = answers.vk_user_id) => {
    if (!answers.experience_level || !answers.bankroll_size || !answers.risk_tolerance || !answers.bookmaker_codes.length) return;

    try {
      setSubmitting(true);
      setErrorMessage(null);
      setDirection(1);
      setStepIndex(4);
      trackEvent('Onboarding Submitted', {
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        bookmakers_count: answers.bookmaker_codes.length,
        vk_linked: Boolean(nextVkUserId),
        currency,
      });

      const [response] = await Promise.all([
        apiFetch<OnboardingResponse>(userId ? `/users/${userId}/onboard` : '/users/me/onboard', {
          method: 'POST',
          body: JSON.stringify({
            anti_capper_pains: answers.anti_capper_pains,
            experience_level: answers.experience_level,
            bankroll_size: answers.bankroll_size,
            risk_tolerance: answers.risk_tolerance,
            bookmakers: answers.bookmaker_codes,
            primary_bookmaker: answers.bookmaker_codes[0],
            other_bookmaker_name: answers.other_bookmaker_name.trim() || null,
            vk_user_id: nextVkUserId,
            currency_preference: currency,
          }),
        }),
        new Promise((resolve) => window.setTimeout(resolve, 3600)),
      ]);

      setRecommendation(response.recommendation);
      setCurrency(response.recommendation.currency || currency);
      trackEvent('Onboarding Result Shown', {
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        vk_linked: Boolean(nextVkUserId),
        currency: response.recommendation.currency || currency,
      });
      revealResults();
    } catch (error: any) {
      setStepIndex(3);
      setErrorMessage(error?.message || 'Не удалось завершить калибровку Shamrai');
      trackEvent('Onboarding Submit Failed', {
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        bookmakers_count: answers.bookmaker_codes.length,
        vk_linked: Boolean(nextVkUserId),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleVkLink = async () => {
      if (!vkReady) {
        setVkLinkStatus('error');
        setVkLinkError('VK ID не настроен. Обратитесь к администратору Shamrai.');
        trackEvent('Onboarding VK Link Failed', { reason: 'not_configured' });
        return;
      }

    try {
      setVkLinkStatus('loading');
      setVkLinkError(null);
      trackEvent('Onboarding VK Link Started');
      const linkedProfile = await linkVkProfile();
      setAnswers((current) => ({ ...current, vk_user_id: linkedProfile.vk_user_id }));
      setVkDisplayName(linkedProfile.vk_display_name || `VK ID ${linkedProfile.vk_user_id}`);
      setVkLinkStatus('linked');
      trackEvent('Onboarding VK Link Success');
      window.setTimeout(() => {
        setDirection(1);
        setStepIndex(1);
      }, 800);
    } catch (error: any) {
      if (isVkRedirectStartedError(error)) return;
      setVkLinkStatus('error');
      setVkLinkError(error?.message || 'Не удалось привязать VK. Попробуйте еще раз.');
      trackEvent('Onboarding VK Link Failed', { reason: 'request_error' });
    }
  };

  const handleSkipVk = () => {
    trackEvent('Onboarding VK Skipped');
    setVkLinkError(null);
    setVkLinkStatus('idle');
    setDirection(1);
    setStepIndex(1);
  };

  const handleSkip = async () => {
    try {
      setSkipping(true);
      setErrorMessage(null);
      await apiFetch('/users/me/onboard/skip', { method: 'POST' });
      trackEvent('Onboarding Skipped');
      await onCompleted();
    } catch (error: any) {
      setErrorMessage(error?.message || 'Не удалось пропустить опрос');
      trackEvent('Onboarding Skip Failed');
    } finally {
      setSkipping(false);
    }
  };

  const handleNext = async () => {
    if (stepIndex === 1 || stepIndex === 2) {
      setDirection(1);
      setStepIndex((current) => current + 1);
      return;
    }
    if (stepIndex === 3) {
      await submitCalibration();
      return;
    }
    if (stepIndex === 5 && manifestAccepted) {
      try {
        setFinishing(true);
        await onCompleted();
        trackEvent('Onboarding Completed', {
          experience_level: answers.experience_level,
          bankroll_size: answers.bankroll_size,
          risk_tolerance: answers.risk_tolerance,
          vk_linked: Boolean(answers.vk_user_id),
          currency,
        });
      } finally {
        setFinishing(false);
      }
    }
  };

  const handleBack = () => {
    if (stepIndex === 0 || submitting || finishing) return;
    setDirection(-1);
    setStepIndex((current) => current - 1);
  };

  return (
    <section
      className={`start-screen relative isolate min-h-[82vh] w-full overflow-hidden rounded-[1.8rem] ${GLASS_SURFACE} p-3 shadow-glass transition duration-500`}
      style={{ boxShadow: glow }}
    >
      <div
        className="pointer-events-none absolute inset-0 z-0 transform-gpu transition duration-500 will-change-transform"
        style={{
          background: proMode
            ? 'radial-gradient(circle at 18% 10%, rgba(246,196,83,0.24), transparent 32%), radial-gradient(circle at 84% 18%, rgba(0,210,255,0.14), transparent 34%), linear-gradient(180deg, rgba(2,6,23,0.06), rgba(2,6,23,0.58))'
            : 'radial-gradient(circle at 18% 10%, rgba(255,0,127,0.28), transparent 32%), radial-gradient(circle at 84% 18%, rgba(0,210,255,0.24), transparent 34%), linear-gradient(180deg, rgba(2,6,23,0.06), rgba(2,6,23,0.58))',
        }}
      />
      <div className="pointer-events-none absolute inset-0 z-0 transform-gpu opacity-45 will-change-transform [background-image:linear-gradient(rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.06)_1px,transparent_1px)] [background-size:30px_30px] [mask-image:linear-gradient(to_bottom,black,transparent_88%)]" />

      <AnimatePresence>
        {flashActive && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.95, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.68, ease: 'easeOut' }}
            className="pointer-events-none absolute inset-0 z-40"
            style={{
              background: 'linear-gradient(115deg, transparent, rgba(255,255,255,0.98), rgba(0,210,255,0.78), transparent)',
            }}
          />
        )}
      </AnimatePresence>

      <div className="relative z-10 flex min-h-[78vh] flex-col gap-3">
        <Header progressIndex={progressIndex} proMode={proMode} glow={glow} accent={accent} />

        <div className="relative flex-1">
          <AnimatePresence mode="wait" custom={direction}>
            <motion.div
              key={`onboarding-step-${stepIndex}`}
              custom={direction}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}
            className={`min-h-[520px] rounded-[1.45rem] ${GLASS_SURFACE} p-4 shadow-glass`}
              style={{ boxShadow: stepIndex === 5 ? secondaryGlow : undefined }}
            >
              {stepIndex === 0 && (
                <VkLinkStep
                  status={vkLinkStatus}
                  displayName={vkDisplayName}
                  error={vkLinkError}
                  vkReady={vkReady}
                  glow={glow}
                  onLink={handleVkLink}
                  onSkip={handleSkipVk}
                />
              )}
              {stepIndex === 1 && (
                <AntiCapperStep
                  selectedPains={answers.anti_capper_pains}
                  onTogglePain={togglePain}
                  glow={glow}
                />
              )}
              {stepIndex === 2 && (
                <ExperienceBankrollStep
                  experience={answers.experience_level}
                  bankroll={answers.bankroll_size}
                  proMode={proMode}
                  glow={glow}
                  onExperience={(value) => updateAnswer('experience_level', value)}
                  onBankroll={(value) => updateAnswer('bankroll_size', value)}
                />
              )}
              {stepIndex === 3 && (
                <RiskBookmakerStep
                  risk={answers.risk_tolerance}
                  bookmakers={visibleBookmakers}
                  selectedBookmakerCodes={answers.bookmaker_codes}
                  otherBookmakerName={answers.other_bookmaker_name}
                  bookmakersLoading={bookmakersLoading}
                  proMode={proMode}
                  glow={glow}
                  onRisk={(value) => updateAnswer('risk_tolerance', value)}
                  onBookmaker={toggleBookmaker}
                  onOtherBookmakerName={handleOtherBookmakerName}
                />
              )}
              {stepIndex === 4 && (
                <CalibrationScreen activeIndex={calibrationIndex} proMode={proMode} glow={glow} reduceMotion={reduceContinuousMotion} />
              )}
              {stepIndex === 5 && (
                <FinalScreen
                  recommendation={recommendation}
                  currency={currency}
                  proMode={proMode}
                  manifestAccepted={manifestAccepted}
                  finishing={finishing}
                  glow={glow}
                  reduceMotion={reduceContinuousMotion}
                  onCurrencyChange={setCurrency}
                  onToggleManifest={() => setManifestAccepted((current) => !current)}
                  onOpenHub={handleNext}
                />
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="space-y-2">
          <AnimatePresence>
            {errorMessage && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                className={`rounded-2xl ${GLASS_SURFACE} px-3 py-2 text-center text-[11px] font-bold text-rose-100`}
              >
                {errorMessage}
              </motion.div>
            )}
          </AnimatePresence>

          {stepIndex > 0 && stepIndex < 4 ? (
            <div className={`grid gap-2 ${stepIndex === 1 ? 'sm:grid-cols-2' : 'sm:grid-cols-[0.82fr_1.35fr]'}`}>
              <ElectricButton
                label={stepIndex === 1 ? 'Пропустить опрос' : 'Назад'}
                icon={stepIndex === 1 ? ChevronRight : ChevronLeft}
                iconAfter={stepIndex === 1}
                disabled={submitting || skipping}
                loading={stepIndex === 1 && skipping}
                glow={glow}
                onClick={stepIndex === 1 ? handleSkip : handleBack}
              />
              <ElectricButton
                label="Далее"
                icon={submitting ? Loader2 : ChevronRight}
                iconAfter
                highlighted={canContinue}
                loading={submitting}
                disabled={!canContinue || submitting || skipping}
                glow={glow}
                onClick={handleNext}
              />
            </div>
          ) : null}
        </div>

        <PulseWidget message={pulseLogs[pulseIndex]} proMode={proMode} glow={proMode ? GOLD_GLOW : CYAN_GLOW} />
      </div>
    </section>
  );
}

function Header({ progressIndex, proMode, glow, accent }: { progressIndex: number; proMode: boolean; glow: string; accent: string }) {
  return (
    <div className={`rounded-[1.35rem] ${GLASS_SURFACE} p-3`} style={{ boxShadow: glow }}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.22em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>
            Shamrai Neural Gate
          </p>
          <h1 className="mt-1 font-display text-xl font-black leading-none text-white">Добро пожаловать</h1>
        </div>
        <div className={`flex h-12 w-12 items-center justify-center rounded-2xl ${GLASS_SURFACE}`}>
          <img
            src="/brand/shamrai-channel-emblem.png"
            alt="Shamrai"
            className="h-11 w-11 object-contain opacity-95"
            style={{ filter: `drop-shadow(0 0 18px ${accent})` }}
          />
        </div>
      </div>
      <div className="mt-3 grid grid-cols-6 gap-2">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <span
            key={index}
            className={`h-1.5 rounded-full ${GLASS_SURFACE}`}
            style={index <= progressIndex ? { boxShadow: glow } : undefined}
          />
        ))}
      </div>
    </div>
  );
}

function AntiCapperStep({
  selectedPains,
  onTogglePain,
  glow,
}: {
  selectedPains: string[];
  onTogglePain: (pain: string) => void;
  glow: string;
}) {
  return (
    <div className="space-y-4">
      <StepTitle index="02" title="Что вас больше всего раздражает в других капперах или каналах?" caption="Выберите хотя бы один пункт. Это триггер честности Shamrai." />
      <div className="space-y-2">
        {antiCapperPains.map((pain) => {
          const selected = selectedPains.includes(pain);
          return (
            <button
              key={pain}
              type="button"
              onClick={() => onTogglePain(pain)}
              className={`group relative flex w-full items-center gap-3 overflow-hidden rounded-2xl ${GLASS_SURFACE} p-3 text-left transition duration-300 active:scale-[0.98]`}
              style={selected ? { boxShadow: glow } : undefined}
            >
              <span className="pointer-events-none absolute inset-0 opacity-0 transition duration-300 group-hover:opacity-100 bg-[linear-gradient(110deg,transparent,rgba(255,255,255,0.13),transparent)]" />
              <span className={`relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-xl ${GLASS_SURFACE}`}>
                {selected && <Check className="h-4 w-4 text-white" strokeWidth={3} />}
              </span>
              <span className="relative z-10 text-sm font-extrabold leading-snug text-white">{pain}</span>
            </button>
          );
        })}
      </div>
      <AnimatePresence>
        {selectedPains.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 14, filter: 'blur(10px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: 10, filter: 'blur(10px)' }}
            className={`rounded-2xl ${GLASS_SURFACE} p-4 text-sm font-bold leading-relaxed text-white`}
            style={{ boxShadow: glow }}
          >
            Мы тоже это ненавидим. В Shamrai вся статистика верифицирована, рекламы БК нет, а за честность отвечает алгоритм. Вы в правильном месте.
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ExperienceBankrollStep({
  experience,
  bankroll,
  proMode,
  glow,
  onExperience,
  onBankroll,
}: {
  experience: ExperienceLevel | null;
  bankroll: BankrollSize | null;
  proMode: boolean;
  glow: string;
  onExperience: (value: ExperienceLevel) => void;
  onBankroll: (value: BankrollSize) => void;
}) {
  return (
    <div className="space-y-4">
      <StepTitle index="03" title="Опыт и рабочий банк" caption="Shamrai не выдает фрибеты. Он настраивает дисциплину пакета." />
      <OptionGroup title="Оценка опыта" options={experienceOptions} value={experience} proMode={proMode} glow={glow} onSelect={onExperience} />
      <OptionGroup title="Размер банка" options={bankrollOptions} value={bankroll} proMode={proMode} glow={glow} onSelect={onBankroll} />
      <AnimatePresence>
        {bankroll === 'high' && (
          <motion.div
            initial={{ opacity: 0, y: 14, filter: 'blur(10px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
            exit={{ opacity: 0, y: 10, filter: 'blur(10px)' }}
            className={`rounded-2xl ${GLASS_SURFACE} p-4 text-sm font-black leading-relaxed text-amber-100`}
            style={{ boxShadow: GOLD_GLOW }}
          >
            Обнаружен крупный рабочий банк. Автоматически активирован скрытый интерфейс 'Shamrai PRO' с доступом к повышенному Brain Score.
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function RiskBookmakerStep({
  risk,
  bookmakers,
  selectedBookmakerCodes,
  otherBookmakerName,
  bookmakersLoading,
  proMode,
  glow,
  onRisk,
  onBookmaker,
  onOtherBookmakerName,
}: {
  risk: RiskTolerance | null;
  bookmakers: BookmakerResponse[];
  selectedBookmakerCodes: string[];
  otherBookmakerName: string;
  bookmakersLoading: boolean;
  proMode: boolean;
  glow: string;
  onRisk: (value: RiskTolerance) => void;
  onBookmaker: (value: string) => void;
  onOtherBookmakerName: (value: string) => void;
}) {
  const otherSelected = selectedBookmakerCodes.includes('other');

  return (
    <div className="space-y-4">
      <StepTitle index="04" title="Стратегия рисков и выбор БК" caption="Выберите стиль и несколько контор, с которыми реально работаете." />
      <OptionGroup title="Стиль рисков" options={riskOptions} value={risk} proMode={proMode} glow={glow} onSelect={onRisk} />
      <div className="space-y-2">
        <SectionLabel>Букмекерские конторы</SectionLabel>
        <div className="grid grid-cols-2 gap-2">
          {bookmakers.map((bookmaker) => (
            <BookmakerChoiceCard
              key={bookmaker.code}
              bookmaker={bookmaker}
              selected={selectedBookmakerCodes.includes(bookmaker.code)}
              glow={glow}
              proMode={proMode}
              onClick={() => onBookmaker(bookmaker.code)}
            />
          ))}
        </div>
        <AnimatePresence>
          {otherSelected && (
            <motion.div
              initial={{ opacity: 0, y: 10, filter: 'blur(8px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, y: 8, filter: 'blur(8px)' }}
              className={`rounded-2xl ${GLASS_SURFACE} p-3`}
              style={{ boxShadow: glow }}
            >
              <label className="block text-[10px] font-black uppercase tracking-[0.16em] text-cyan-100">
                Какие БК используете?
              </label>
              <EmojiTextField
                multiline
                value={otherBookmakerName}
                onValueChange={onOtherBookmakerName}
                rows={3}
                maxLength={180}
                placeholder="Например: 1xBet, Pinnacle, Bet365..."
                className="mt-2 min-h-[88px] w-full resize-none rounded-2xl border border-white/10 bg-slate-950/45 px-3 py-2 text-sm font-bold leading-relaxed text-white outline-none transition placeholder:text-slate-500 focus:border-cyan-200/60 focus:ring-2 focus:ring-cyan-300/25"
              />
              <p className="mt-2 text-[10px] font-semibold leading-snug text-slate-400">
                Это поле только для информации. Рассылка останется по категории "Другие".
              </p>
            </motion.div>
          )}
        </AnimatePresence>
        {bookmakersLoading && (
          <p className="text-center text-[10px] font-bold text-cyan-100">Обновляем список БК...</p>
        )}
      </div>
    </div>
  );
}

function VkLinkStep({
  status,
  displayName,
  error,
  vkReady,
  glow,
  onLink,
  onSkip,
}: {
  status: VkLinkStatus;
  displayName: string | null;
  error: string | null;
  vkReady: boolean;
  glow: string;
  onLink: () => void;
  onSkip: () => void;
}) {
  const linked = status === 'linked';
  const loading = status === 'loading';

  return (
    <div className="flex min-h-[490px] flex-col justify-between gap-5">
      <div className="space-y-5">
        <StepTitle
          index="01"
          title="Дублирование сигналов в VK"
          caption="Привяжи аккаунт ВКонтакте через VK ID. Для доставки анонсов в личные сообщения и VK-уведомления потребуется отдельно разрешить сообщения от сообщества Shamrai."
        />

        <div className={`relative overflow-hidden rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: linked ? '0 0 34px rgba(34,197,94,0.48), inset 0 1px 0 rgba(255,255,255,0.16)' : glow }}>
          <span className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_22%_0%,rgba(0,119,255,0.34),transparent_38%),linear-gradient(135deg,rgba(255,255,255,0.06),transparent)]" />
          <div className="relative z-10 flex items-center gap-3">
            <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-white/15 bg-[#0077ff]/90 text-lg font-black text-white shadow-[0_0_28px_rgba(0,119,255,0.46)]`}>
              VK
            </div>
            <div className="min-w-0">
              <p className="text-sm font-black leading-tight text-white">VK ID secure link</p>
              <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-300">
                VK ID связывает профиль; разрешения на доставку можно включить в профиле после привязки.
              </p>
            </div>
          </div>
        </div>

        <AnimatePresence mode="wait">
          {linked ? (
            <motion.div
              key="vk-linked"
              initial={{ opacity: 0, y: 12, filter: 'blur(10px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              exit={{ opacity: 0, y: 8, filter: 'blur(10px)' }}
              className="rounded-2xl border border-emerald-200/20 bg-emerald-400/12 p-4 text-center text-sm font-black leading-relaxed text-emerald-50 shadow-[0_0_34px_rgba(34,197,94,0.32)]"
            >
              ✅ VK ID привязан: {displayName || 'профиль VK'}. Для доставки включите разрешения VK в профиле.
            </motion.div>
          ) : (
            <motion.button
              key="vk-link-button"
              type="button"
              disabled={!vkReady || loading}
              onClick={onLink}
              whileTap={vkReady && !loading ? { scale: 0.98 } : undefined}
              className={`group relative min-h-[64px] w-full overflow-hidden rounded-2xl border border-white/12 bg-white/5 px-4 text-left transition duration-300 disabled:cursor-not-allowed disabled:opacity-45`}
              style={vkReady ? { boxShadow: glow } : undefined}
            >
              <span className="pointer-events-none absolute inset-0 opacity-0 transition duration-300 group-hover:opacity-100 bg-[linear-gradient(110deg,transparent,rgba(255,255,255,0.16),transparent)]" />
              <span className="relative z-10 flex items-center justify-center gap-3 text-sm font-black uppercase tracking-[0.08em] text-white">
                <span className="text-lg">{loading ? '⏳' : '🔗'}</span>
                {loading ? 'Открываем VK ID...' : 'Связать профиль VK'}
              </span>
            </motion.button>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {(!vkReady || error) && !linked && (
            <motion.p
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              className="rounded-2xl border border-white/10 bg-white/[0.035] px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-slate-300"
            >
              {error || 'VK ID не настроен для этой сборки. Обратитесь к администратору Shamrai.'}
            </motion.p>
          )}
        </AnimatePresence>
      </div>

      {!linked && (
        <button
          type="button"
          onClick={onSkip}
          disabled={loading}
          className="mx-auto rounded-full border border-white/5 bg-white/[0.025] px-4 py-2 text-[10px] font-black uppercase tracking-[0.16em] text-slate-500 transition hover:border-white/15 hover:bg-white/[0.055] hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Пропустить привязку VK
        </button>
      )}
    </div>
  );
}

function OptionGroup<TValue extends string>({
  title,
  options,
  value,
  proMode,
  glow,
  onSelect,
}: {
  title: string;
  options: ChoiceOption<TValue>[];
  value: TValue | null;
  proMode: boolean;
  glow: string;
  onSelect: (value: TValue) => void;
}) {
  return (
    <div className="space-y-2">
      <SectionLabel>{title}</SectionLabel>
      <div className="space-y-2">
        {options.map((option) => (
          <ChoiceCard
            key={option.value}
            option={option}
            selected={value === option.value}
            proMode={proMode}
            glow={glow}
            onClick={() => onSelect(option.value)}
          />
        ))}
      </div>
    </div>
  );
}

function StepTitle({ index, title, caption }: { index: string; title: string; caption: string }) {
  return (
    <div className="space-y-2 text-center">
      <div className={`mx-auto inline-flex rounded-full ${GLASS_SURFACE} px-3 py-1 text-[10px] font-black uppercase tracking-[0.18em] text-cyan-100`}>
        calibration step {index}
      </div>
      <h2 className="font-display text-2xl font-black leading-tight text-white">{title}</h2>
      <p className="mx-auto max-w-[330px] text-xs font-semibold leading-relaxed text-slate-300">{caption}</p>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.16em] text-slate-300">
      <span className="h-px flex-1 bg-white/10" />
      <span>{children}</span>
      <span className="h-px flex-1 bg-white/10" />
    </div>
  );
}

function ChoiceCard<TValue extends string>({
  option,
  selected,
  proMode,
  glow,
  onClick,
}: {
  option: ChoiceOption<TValue>;
  selected: boolean;
  proMode: boolean;
  glow: string;
  onClick: () => void;
}) {
  const Icon = option.icon;

  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`group relative w-full overflow-hidden rounded-2xl ${GLASS_SURFACE} p-3 text-left text-white transition duration-300 active:scale-[0.98]`}
      style={selected ? { boxShadow: glow } : undefined}
    >
      <span className="pointer-events-none absolute inset-0 opacity-0 transition duration-300 group-hover:opacity-100 bg-[linear-gradient(110deg,transparent,rgba(255,255,255,0.12),transparent)]" />
      <span className="relative z-10 flex items-center gap-3">
        <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl ${GLASS_SURFACE}`} style={selected ? { boxShadow: glow, color: proMode ? GOLD : CYAN } : undefined}>
          <Icon className="h-5 w-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-black leading-tight">{option.title}</span>
          <span className="mt-1 block text-[10px] font-semibold leading-snug text-slate-400">{option.description}</span>
        </span>
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${GLASS_SURFACE}`} style={selected ? { boxShadow: glow } : undefined}>
          {selected && <span className="h-2.5 w-2.5 rounded-full bg-white shadow-[0_0_14px_rgba(255,255,255,0.9)]" />}
        </span>
      </span>
    </button>
  );
}

function BookmakerChoiceCard({
  bookmaker,
  selected,
  glow,
  proMode,
  onClick,
}: {
  bookmaker: BookmakerResponse;
  selected: boolean;
  glow: string;
  proMode: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`group relative min-h-[82px] w-full overflow-hidden rounded-2xl ${GLASS_SURFACE} p-3 text-left text-white transition duration-300 active:scale-[0.98]`}
      style={selected ? { boxShadow: glow } : undefined}
    >
      <span className="pointer-events-none absolute inset-0 opacity-0 transition duration-300 group-hover:opacity-100 bg-[linear-gradient(110deg,transparent,rgba(255,255,255,0.12),transparent)]" />
      <span className="relative z-10 flex h-full items-center gap-3">
        {bookmaker.code !== 'other' && (
          <BookmakerLogoFrame bookmaker={bookmaker} size="badge" active={selected} className="h-10 w-12" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block break-words text-sm font-black leading-tight">{bookmaker.name}</span>
          {bookmaker.code !== 'other' && (
            <span className="mt-1 block text-[10px] font-semibold leading-snug text-slate-400">
              {selected ? 'Добавлена в профиль' : 'Multi-select'}
            </span>
          )}
        </span>
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${GLASS_SURFACE}`} style={selected ? { boxShadow: glow, color: proMode ? GOLD : CYAN } : undefined}>
          {selected && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
        </span>
      </span>
    </button>
  );
}

function CalibrationScreen({
  activeIndex,
  proMode,
  glow,
  reduceMotion,
}: {
  activeIndex: number;
  proMode: boolean;
  glow: string;
  reduceMotion: boolean;
}) {
  return (
    <div className="flex min-h-[490px] flex-col items-center justify-center gap-6 text-center">
      <motion.div
        animate={reduceMotion ? undefined : {
          scale: [1, 1.09, 0.99, 1],
          rotate: [0, 1.5, -1.5, 0],
          boxShadow: [glow, proMode ? GOLD_GLOW : CYAN_GLOW, glow],
        }}
        transition={reduceMotion ? undefined : { duration: 1.75, repeat: Infinity, ease: 'easeInOut' }}
        className={`relative flex h-40 w-40 items-center justify-center rounded-[2rem] ${GLASS_SURFACE}`}
        style={reduceMotion ? { boxShadow: glow } : undefined}
      >
        <span className="absolute inset-2 rounded-[2rem] bg-white/5 blur-2xl" />
        <Brain className="relative z-10 h-24 w-24" style={{ color: proMode ? GOLD : CYAN, filter: `drop-shadow(0 0 24px ${proMode ? GOLD : CYAN})` }} />
      </motion.div>

      <div className="space-y-2">
        <p className="font-display text-2xl font-black text-white">Синхронизация Разума</p>
        <p className="text-xs font-bold uppercase tracking-[0.16em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>
          Shamrai Brain is online
        </p>
      </div>

      <div className={`w-full overflow-hidden rounded-2xl ${GLASS_SURFACE} p-3`}>
        <AnimatePresence mode="wait">
          <motion.p
            key={calibrationLogs[activeIndex]}
            initial={{ opacity: 0, x: 18 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -18 }}
            transition={{ duration: 0.26 }}
            className="text-left font-mono text-[11px] font-bold text-cyan-100"
          >
            {calibrationLogs[activeIndex]}
          </motion.p>
        </AnimatePresence>
        <div className={`mt-3 h-2 overflow-hidden rounded-full ${GLASS_SURFACE}`}>
          <motion.span
            className={`block h-full rounded-full ${GLASS_SURFACE}`}
            animate={reduceMotion ? undefined : { x: ['-100%', '110%'] }}
            transition={reduceMotion ? undefined : { duration: 1.0, repeat: Infinity, ease: 'easeInOut' }}
            style={{ boxShadow: glow }}
          />
        </div>
      </div>
    </div>
  );
}

function FinalScreen({
  recommendation,
  currency,
  proMode,
  manifestAccepted,
  finishing,
  glow,
  onCurrencyChange,
  onToggleManifest,
  onOpenHub,
  reduceMotion,
}: {
  recommendation: OnboardingRecommendation | null;
  currency: CurrencyCode;
  proMode: boolean;
  manifestAccepted: boolean;
  finishing: boolean;
  glow: string;
  reduceMotion: boolean;
  onCurrencyChange: (value: CurrencyCode) => void;
  onToggleManifest: () => void;
  onOpenHub: () => void;
}) {
  const missedAmount = recommendation
    ? recommendation.missed_profit_amount_24h / currencyMeta[currency].rateFromRub
    : 0;
  const amountLabel = `${currencyMeta[currency].symbol}${missedAmount.toLocaleString('ru-RU', {
    maximumFractionDigits: currency === 'RUB' ? 0 : 1,
  })}`;

  return (
    <div className="space-y-4">
      <div className="space-y-2 text-center">
        <div className={`mx-auto flex h-20 w-20 items-center justify-center rounded-[1.75rem] ${GLASS_SURFACE}`} style={{ boxShadow: glow }}>
          <Check className="h-10 w-10 text-white" strokeWidth={3} />
        </div>
        <h2 className="font-display text-2xl font-black leading-tight text-white">Результаты и Манифест</h2>
        <p className="text-xs font-semibold leading-relaxed text-slate-300">Все дисциплины уже подключены к ленте. На старте нет фрибетов, только пакетная модель.</p>
      </div>

      <div className={`rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: glow }}>
        <p className="text-[10px] font-black uppercase tracking-[0.18em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>Персональный флэт</p>
        <p className="mt-2 font-display text-xl font-black text-white">
          Рекомендация системы: строго {recommendation?.flat_stake_percent ?? 3}% от банка
        </p>
      </div>

      <div className={`relative overflow-hidden rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: proMode ? GOLD_GLOW : PINK_GLOW }}>
        <div className="relative z-10 space-y-3">
          <p className="text-[10px] font-black uppercase tracking-[0.18em] text-pink-100">FOMO-модуль</p>
          <div className="grid grid-cols-2 gap-2">
            <Metric label="Потенциальный профит" value={`до +${recommendation?.monthly_profit_percent ?? 35}%`} caption="в месяц" />
            <Metric label="Упущено за 24 часа" value={`+${recommendation?.missed_profit_percent_24h ?? 7.4}%`} caption={`${amountLabel} к банку`} />
          </div>
        </div>
      </div>

      <SuperCompensationDemo glow={glow} proMode={proMode} reduceMotion={reduceMotion} />

      <div className={`flex items-center justify-between gap-3 rounded-2xl ${GLASS_SURFACE} p-3`}>
        <span className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-300">Валюта расчета</span>
        <div className={`grid grid-cols-3 gap-1 rounded-2xl ${GLASS_SURFACE} p-1`}>
          {(Object.keys(currencyMeta) as CurrencyCode[]).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => onCurrencyChange(item)}
              className={`h-9 w-10 rounded-xl ${GLASS_SURFACE} text-sm font-black text-white transition duration-300`}
              style={currency === item ? { boxShadow: glow } : undefined}
            >
              {currencyMeta[item].label}
            </button>
          ))}
        </div>
      </div>

      <button
        type="button"
        onClick={onToggleManifest}
        className={`flex w-full items-center gap-3 rounded-2xl ${GLASS_SURFACE} p-3 text-left transition duration-300 active:scale-[0.99]`}
        style={manifestAccepted ? { boxShadow: glow } : undefined}
      >
        <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl ${GLASS_SURFACE}`}>
          {manifestAccepted ? <ToggleRight className="h-6 w-6 text-white" /> : <ToggleLeft className="h-6 w-6 text-slate-300" />}
        </span>
        <span className="text-[11px] font-bold leading-relaxed text-white">
          Манифест Shamrai: Я обязуюсь действовать разумно, соблюдать флэт и следовать калибровке мозга Shamrai
        </span>
      </button>

      <ElectricButton
        label="⚡ Войти в Analytics Hub"
        icon={manifestAccepted ? Zap : Lock}
        highlighted={manifestAccepted}
        loading={finishing}
        disabled={!manifestAccepted || finishing}
        glow={glow}
        onClick={onOpenHub}
      />
    </div>
  );
}

function SuperCompensationDemo({ glow, proMode, reduceMotion }: { glow: string; proMode: boolean; reduceMotion: boolean }) {
  return (
    <div className={`rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: glow }}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>Правило Сверхкомпенсации</p>
          <p className="mt-2 text-[11px] font-semibold leading-relaxed text-slate-300">
            При LOSS списанная ставка возвращается, а сверху начисляется еще +1 ставка. Баланс пакета растет на 1.
          </p>
        </div>
        <motion.div
          animate={reduceMotion ? undefined : { y: [0, -4, 0], scale: [1, 1.04, 1] }}
          transition={reduceMotion ? undefined : { duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
          className={`shrink-0 rounded-2xl ${GLASS_SURFACE} px-3 py-2 text-sm font-black text-white`}
          style={{ boxShadow: glow }}
        >
          +1
        </motion.div>
      </div>
      <div className="mt-3 grid grid-cols-3 items-center gap-2 text-center">
        <CompStep label="До" value="5" />
        <motion.div
          animate={reduceMotion ? undefined : { opacity: [0.45, 1, 0.45] }}
          transition={reduceMotion ? undefined : { duration: 1.1, repeat: Infinity }}
          className={`rounded-2xl ${GLASS_SURFACE} px-2 py-3 text-[10px] font-black uppercase tracking-[0.12em] text-white`}
        >
          LOSS → +1
        </motion.div>
        <CompStep label="После" value="6" />
      </div>
    </div>
  );
}

function CompStep({ label, value }: { label: string; value: string }) {
  return (
    <div className={`rounded-2xl ${GLASS_SURFACE} p-3`}>
      <p className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-400">{label}</p>
      <p className="mt-1 font-display text-xl font-black text-white">{value}</p>
      <p className="text-[10px] font-semibold text-cyan-100">ставок</p>
    </div>
  );
}

function Metric({ label, value, caption }: { label: string; value: string; caption: string }) {
  return (
    <div className={`rounded-2xl ${GLASS_SURFACE} p-3`}>
      <p className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-400">{label}</p>
      <p className="mt-1 font-display text-lg font-black text-white">{value}</p>
      <p className="text-[10px] font-semibold text-cyan-100">{caption}</p>
    </div>
  );
}

function PulseWidget({ message, proMode, glow }: { message: string; proMode: boolean; glow: string }) {
  return (
    <div className={`rounded-[1.35rem] ${GLASS_SURFACE} p-3 shadow-glass`} style={{ boxShadow: glow }}>
      <div className="flex items-center gap-3">
        <span className={`flex h-9 w-9 items-center justify-center rounded-2xl ${GLASS_SURFACE}`} style={{ boxShadow: glow, color: proMode ? GOLD : CYAN }}>
          <RadioTower className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-black uppercase tracking-[0.16em] text-slate-400">Пульс Shamrai</p>
          <AnimatePresence mode="wait">
            <motion.p
              key={message}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22 }}
              className="truncate text-xs font-bold text-white"
            >
              {message}
            </motion.p>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function ElectricButton({
  label,
  icon: Icon,
  iconAfter = false,
  highlighted = false,
  loading = false,
  disabled = false,
  glow,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  iconAfter?: boolean;
  highlighted?: boolean;
  loading?: boolean;
  disabled?: boolean;
  glow: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`shamrai-electric-button relative flex min-h-[52px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl ${GLASS_SURFACE} px-3 text-[11px] font-black uppercase tracking-[0.08em] text-white transition duration-300 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 ${highlighted && !disabled ? 'shamrai-electric-button--hot' : ''}`}
      style={highlighted && !disabled ? { boxShadow: glow } : undefined}
    >
      {!iconAfter && <Icon className={`relative z-10 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}
      <span className="relative z-10 text-center leading-tight">{label}</span>
      {iconAfter && <Icon className={`relative z-10 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}
    </button>
  );
}
