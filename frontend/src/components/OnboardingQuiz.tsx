import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
import OptimizedImage from './OptimizedImage';
import { useAuthSelector } from '../context/AuthContext';
import { trackEvent } from '../utils/analytics';
import { usePerformanceProfile } from '../hooks/usePerformanceProfile';
import { getBookmakerLogoSrc } from '../constants/bookmakers';
import {
  buildOnboardingPayload,
  isBookmakerStepComplete,
  type BankrollSize,
  type CurrencyCode,
  type ExperienceLevel,
  type OnboardingDraftAnswers,
  type OnboardingGoal,
  type RiskTolerance,
  type ServiceFormat,
} from '../utils/onboardingQuiz';
import { toggleBookmakerCodeSelection } from '../utils/bookmakerSelection';

type VkLinkStatus = 'idle' | 'loading' | 'linked' | 'error';
type OnboardingAnswers = OnboardingDraftAnswers;

interface OnboardingRecommendation {
  flat_stake_percent: number;
  monthly_profit_percent: number;
  missed_profit_percent_24h: number;
  missed_profit_amount_24h: number;
  currency: CurrencyCode;
  source: string;
  resolved_bets_24h: number;
  service_format_label: string;
  vip_verdict_title: string;
  vip_verdict_caption: string;
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

const GLASS_SURFACE = 'bg-white/5 backdrop-blur-xl border border-white/10';
const PINK = '#ff007f';
const CYAN = '#00d2ff';
const GOLD = '#f6c453';
const PINK_GLOW = '0 0 30px rgba(255,0,127,0.55), 0 0 70px rgba(0,210,255,0.16), inset 0 1px 0 rgba(255,255,255,0.16)';
const CYAN_GLOW = '0 0 30px rgba(0,210,255,0.52), 0 0 70px rgba(255,0,127,0.14), inset 0 1px 0 rgba(255,255,255,0.16)';
const GOLD_GLOW = '0 0 32px rgba(246,196,83,0.62), 0 0 84px rgba(246,196,83,0.22), inset 0 1px 0 rgba(255,255,255,0.18)';

const slideVariants: Variants = {
  enter: (direction: number) => ({
    x: direction > 0 ? 36 : -36,
    opacity: 0,
  }),
  center: {
    x: 0,
    opacity: 1,
  },
  exit: (direction: number) => ({
    x: direction > 0 ? -36 : 36,
    opacity: 0,
  }),
};

const antiCapperPains = [
  'Реклама скам-казино и постоянный спам',
  'Удаление или редактирование минусовых прогнозов',
  'Обещания 100% проходимости и договорных матчей',
  'Поздние сигналы, когда линия уже ушла',
  'Нет флэта, риска и понятной дистанции',
];

const goalOptions: ChoiceOption<OnboardingGoal>[] = [
  {
    value: 'fast_signals',
    title: 'Быстрые входы по линии',
    description: 'Нужны уведомления, пока коэффициент еще живой.',
    icon: Zap,
  },
  {
    value: 'discipline',
    title: 'Дисциплина банка',
    description: 'Хочу работать по флэту, без импульсивных доборов.',
    icon: Gauge,
  },
  {
    value: 'trust_check',
    title: 'Проверить честность',
    description: 'Сначала смотрю прозрачность, статистику и логику.',
    icon: ShieldCheck,
  },
  {
    value: 'raise_level',
    title: 'Поднять уровень',
    description: 'Интересуют value, движение линии и холодная математика.',
    icon: TrendingUp,
  },
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
    icon: Brain,
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
    description: 'Крупный банк: расширенный контроль риска и приоритет точности.',
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

const serviceFormatOptions: ChoiceOption<ServiceFormat>[] = [
  {
    value: 'auto_fast',
    title: 'Сигнал сразу',
    description: 'Нужен самый короткий путь от уведомления до входа.',
    icon: Zap,
  },
  {
    value: 'logic_review',
    title: 'С объяснением',
    description: 'Важно понимать логику, value и почему вход появился.',
    icon: Brain,
  },
  {
    value: 'vip_support',
    title: 'VIP-сопровождение',
    description: 'Хочется личного контакта и контроля важных моментов.',
    icon: Trophy,
  },
  {
    value: 'distance_report',
    title: 'Отчёт по дистанции',
    description: 'Нужны цифры, отчётность и спокойная работа по флэту.',
    icon: Gauge,
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

const pulseLogs = [
  '142 профиля сверяют риск-модель',
  'Линия сдвинулась на 0.18 пункта',
  'Shamrai Brain пересчитал флэт-порог',
  'Закрытая лента обновила value-сигнал',
  'Абонементы работают без фрибет-приманок',
];

const currencyMeta: Record<CurrencyCode, { label: string; symbol: string; rateFromRub: number }> = {
  RUB: { label: '₽', symbol: '₽', rateFromRub: 1 },
  USD: { label: '$', symbol: '$', rateFromRub: 92 },
  FLATS: { label: '₮', symbol: '₮', rateFromRub: 92 },
};

const riskFlatHint: Record<RiskTolerance, string> = {
  cautious: 'Базовый коридор флэта: от 7% банка. Цель - пережить просадку без нервных догонов.',
  balanced: 'Базовый коридор флэта: 7-8% банка. Это рабочий режим для дистанции и скорости.',
  aggressive: 'Базовый коридор флэта: 8-9% банка. Входы быстрее, но цена ошибки выше.',
};

const sourceLabels: Record<string, string> = {
  channel_24h_resolved_bets: 'по закрытым прогнозам Shamrai за последние 24 часа',
  simulated_from_empty_24h_window: 'ретро-симуляция по вашей модели риска',
  mock_channel_24h: 'локальная демо-выборка Shamrai за 24 часа',
};

export default function OnboardingQuiz({ userId, onCompleted }: OnboardingQuizProps) {
  const user = useAuthSelector((state) => state.user);
  const reduceMotion = useReducedMotion();
  const performanceProfile = usePerformanceProfile();
  const reduceContinuousMotion = reduceMotion || performanceProfile.shouldReduceMotion;
  const calmControls = reduceContinuousMotion || performanceProfile.isBalanced;
  const [stepIndex, setStepIndex] = useState(0);
  const [direction, setDirection] = useState(1);
  const [answers, setAnswers] = useState<OnboardingAnswers>({
    anti_capper_pains: [],
    onboarding_goal: null,
    experience_level: null,
    bankroll_size: null,
    risk_tolerance: null,
    bookmaker_codes: [],
    other_bookmaker_name: '',
    service_format: null,
    favorite_sports: [],
    vk_user_id: null,
  });
  const [bookmakers, setBookmakers] = useState<BookmakerResponse[]>(fallbackBookmakers);
  const [bookmakersLoading, setBookmakersLoading] = useState(true);
  const [recommendation, setRecommendation] = useState<OnboardingRecommendation | null>(null);
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

  const proMode = answers.bankroll_size === 'high' || answers.experience_level === 'pro';
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
      }, 650);
      return () => window.clearTimeout(timer);
    }
  }, [stepIndex, user?.vk_user_id]);

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
    if (typeof Image === 'undefined') return;
    visibleBookmakers.forEach((bookmaker) => {
      const image = new Image();
      image.src = getBookmakerLogoSrc(bookmaker);
    });
  }, [visibleBookmakers]);

  const canContinue = useMemo(() => {
    if (stepIndex === 0) return false;
    if (stepIndex === 1) return answers.anti_capper_pains.length > 0;
    if (stepIndex === 2) return Boolean(answers.onboarding_goal && answers.experience_level);
    if (stepIndex === 3) return Boolean(answers.bankroll_size && answers.risk_tolerance);
    if (stepIndex === 4) {
      return isBookmakerStepComplete({
        selectedBookmakerCodes: answers.bookmaker_codes,
        otherBookmakerName: answers.other_bookmaker_name,
        serviceFormat: answers.service_format,
      });
    }
    return manifestAccepted && !finishing;
  }, [answers, finishing, manifestAccepted, stepIndex]);

  const togglePain = useCallback((pain: string) => {
    setErrorMessage(null);
    setAnswers((current) => ({
      ...current,
      anti_capper_pains: current.anti_capper_pains.includes(pain)
        ? current.anti_capper_pains.filter((item) => item !== pain)
        : [...current.anti_capper_pains, pain],
    }));
  }, []);

  const updateAnswer = useCallback(<TKey extends keyof OnboardingAnswers,>(key: TKey, value: OnboardingAnswers[TKey]) => {
    setErrorMessage(null);
    setAnswers((current) => ({ ...current, [key]: value }));
  }, []);

  const toggleBookmaker = useCallback((bookmakerCode: string) => {
    setErrorMessage(null);
    setAnswers((current) => ({
      ...current,
      bookmaker_codes: toggleBookmakerCodeSelection(current.bookmaker_codes, bookmakerCode),
      other_bookmaker_name: bookmakerCode === 'other' && current.bookmaker_codes.includes('other')
        ? ''
        : current.other_bookmaker_name,
    }));
  }, []);

  const revealResults = () => {
    setDirection(1);
    setFlashActive(!reduceContinuousMotion);
    setStepIndex(5);
    if (!reduceContinuousMotion) {
      window.setTimeout(() => setFlashActive(false), 560);
    }
  };

  const submitCalibration = async (nextVkUserId = answers.vk_user_id) => {
    if (!answers.onboarding_goal || !answers.experience_level || !answers.bankroll_size || !answers.risk_tolerance) return;

    if (!isBookmakerStepComplete({
      selectedBookmakerCodes: answers.bookmaker_codes,
      otherBookmakerName: answers.other_bookmaker_name,
      serviceFormat: answers.service_format,
    })) {
      setErrorMessage('Выберите БК, формат сервиса и укажите название, если выбрали "Другие".');
      return;
    }

    try {
      setSubmitting(true);
      setErrorMessage(null);
      trackEvent('Onboarding Submitted', {
        onboarding_goal: answers.onboarding_goal,
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        bookmakers_count: answers.bookmaker_codes.length,
        service_format: answers.service_format,
        favorite_sports_count: answers.favorite_sports.length,
        vk_linked: Boolean(nextVkUserId),
        currency,
      });

      const onboardingEndpoint = userId && userId > 0 ? `/users/${userId}/onboard` : '/users/me/onboard';
      const [response] = await Promise.all([
        apiFetch<OnboardingResponse>(onboardingEndpoint, {
          method: 'POST',
          body: JSON.stringify(buildOnboardingPayload(answers, currency, nextVkUserId)),
        }),
        new Promise((resolve) => window.setTimeout(resolve, reduceContinuousMotion ? 420 : 860)),
      ]);

      setRecommendation(response.recommendation);
      setCurrency(response.recommendation.currency || currency);
      trackEvent('Onboarding Result Shown', {
        onboarding_goal: answers.onboarding_goal,
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        service_format: answers.service_format,
        vk_linked: Boolean(nextVkUserId),
        currency: response.recommendation.currency || currency,
      });
      revealResults();
    } catch (error: any) {
      setStepIndex(4);
      setErrorMessage(error?.message || 'Не удалось завершить калибровку Shamrai');
      trackEvent('Onboarding Submit Failed', {
        onboarding_goal: answers.onboarding_goal,
        experience_level: answers.experience_level,
        bankroll_size: answers.bankroll_size,
        risk_tolerance: answers.risk_tolerance,
        bookmakers_count: answers.bookmaker_codes.length,
        service_format: answers.service_format,
        favorite_sports_count: answers.favorite_sports.length,
        vk_linked: Boolean(nextVkUserId),
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleVkLink = async () => {
    if (!vkReady) {
      setVkLinkStatus('error');
      setVkLinkError('VK ID не настроен. Можно продолжить без привязки и включить VK позже в профиле.');
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
      }, 620);
    } catch (error: any) {
      if (isVkRedirectStartedError(error)) return;
      setVkLinkStatus('error');
      setVkLinkError(error?.message || 'Не удалось привязать VK. Можно продолжить и вернуться к этому позже.');
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
    if (stepIndex > 0 && stepIndex < 4) {
      setDirection(1);
      setStepIndex((current) => current + 1);
      return;
    }
    if (stepIndex === 4) {
      await submitCalibration();
      return;
    }
    if (stepIndex === 5 && manifestAccepted) {
      try {
        setFinishing(true);
        await onCompleted();
        trackEvent('Onboarding Completed', {
          onboarding_goal: answers.onboarding_goal,
          experience_level: answers.experience_level,
          bankroll_size: answers.bankroll_size,
          risk_tolerance: answers.risk_tolerance,
          service_format: answers.service_format,
          favorite_sports_count: answers.favorite_sports.length,
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
    setStepIndex((current) => Math.max(0, current - 1));
  };

  const showPulseWidget = stepIndex !== 4 && !performanceProfile.isLowPower && !performanceProfile.isBalanced;

  return (
    <section
      className={`start-screen relative isolate min-h-[82vh] w-full overflow-hidden rounded-[1.8rem] ${GLASS_SURFACE} p-3 shadow-glass transition duration-500`}
      style={{ boxShadow: glow }}
    >
      <div
        className="pointer-events-none absolute inset-0 z-0 transition duration-500"
        style={{
          background: proMode
            ? 'radial-gradient(circle at 18% 10%, rgba(246,196,83,0.24), transparent 32%), radial-gradient(circle at 84% 18%, rgba(0,210,255,0.14), transparent 34%), linear-gradient(180deg, rgba(2,6,23,0.06), rgba(2,6,23,0.58))'
            : 'radial-gradient(circle at 18% 10%, rgba(255,0,127,0.28), transparent 32%), radial-gradient(circle at 84% 18%, rgba(0,210,255,0.24), transparent 34%), linear-gradient(180deg, rgba(2,6,23,0.06), rgba(2,6,23,0.58))',
        }}
      />
      <div className="pointer-events-none absolute inset-0 z-0 opacity-45 [background-image:linear-gradient(rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.06)_1px,transparent_1px)] [background-size:30px_30px] [mask-image:linear-gradient(to_bottom,black,transparent_88%)]" />

      <AnimatePresence>
        {flashActive && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.9, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.52, ease: 'easeOut' }}
            className="pointer-events-none absolute inset-0 z-40"
            style={{
              background: 'linear-gradient(115deg, transparent, rgba(255,255,255,0.92), rgba(0,210,255,0.7), transparent)',
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
              transition={{ duration: reduceContinuousMotion ? 0.12 : 0.28, ease: [0.16, 1, 0.3, 1] }}
              className={`min-h-[520px] rounded-[1.45rem] ${GLASS_SURFACE} p-4 shadow-glass`}
              style={{ boxShadow: stepIndex === 5 ? secondaryGlow : undefined }}
            >
              {stepIndex === 0 && (
                <IntroVkStep
                  status={vkLinkStatus}
                  displayName={vkDisplayName}
                  error={vkLinkError}
                  vkReady={vkReady}
                  glow={glow}
                  calm={calmControls}
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
                <GoalExperienceStep
                  goal={answers.onboarding_goal}
                  experience={answers.experience_level}
                  proMode={proMode}
                  glow={glow}
                  onGoal={(value) => updateAnswer('onboarding_goal', value)}
                  onExperience={(value) => updateAnswer('experience_level', value)}
                />
              )}
              {stepIndex === 3 && (
                <BankrollRiskStep
                  bankroll={answers.bankroll_size}
                  risk={answers.risk_tolerance}
                  proMode={proMode}
                  glow={glow}
                  onBankroll={(value) => updateAnswer('bankroll_size', value)}
                  onRisk={(value) => updateAnswer('risk_tolerance', value)}
                />
              )}
              {stepIndex === 4 && (
                <BookmakerStep
                  bookmakers={visibleBookmakers}
                  selectedBookmakerCodes={answers.bookmaker_codes}
                  otherBookmakerName={answers.other_bookmaker_name}
                  serviceFormat={answers.service_format}
                  bookmakersLoading={bookmakersLoading}
                  proMode={proMode}
                  glow={glow}
                  onBookmaker={toggleBookmaker}
                  onOtherBookmakerName={(value) => updateAnswer('other_bookmaker_name', value)}
                  onServiceFormat={(value) => updateAnswer('service_format', value)}
                />
              )}
              {stepIndex === 5 && (
                <FinalScreen
                  recommendation={recommendation}
                  currency={currency}
                  proMode={proMode}
                  manifestAccepted={manifestAccepted}
                  finishing={finishing}
                  glow={glow}
                  calm={calmControls}
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

          {stepIndex > 0 && stepIndex < 5 ? (
            <div className={`grid gap-2 ${stepIndex === 1 ? 'sm:grid-cols-2' : 'sm:grid-cols-[0.82fr_1.35fr]'}`}>
              <ElectricButton
                label={stepIndex === 1 ? 'Пропустить опрос' : 'Назад'}
                icon={stepIndex === 1 ? ChevronRight : ChevronLeft}
                iconAfter={stepIndex === 1}
                disabled={submitting || skipping}
                loading={stepIndex === 1 && skipping}
                glow={glow}
                calm={calmControls || stepIndex === 4}
                onClick={stepIndex === 1 ? handleSkip : handleBack}
              />
              <ElectricButton
                label={stepIndex === 4 ? 'Рассчитать модель' : 'Далее'}
                icon={submitting ? Loader2 : ChevronRight}
                iconAfter
                highlighted={canContinue}
                loading={submitting}
                disabled={!canContinue || submitting || skipping}
                glow={glow}
                calm={calmControls || stepIndex === 4}
                onClick={handleNext}
              />
            </div>
          ) : null}
        </div>

        {showPulseWidget && <PulseWidget proMode={proMode} glow={proMode ? GOLD_GLOW : CYAN_GLOW} />}
      </div>
    </section>
  );
}

function Header({ progressIndex, proMode, glow, accent }: { progressIndex: number; proMode: boolean; glow: string; accent: string }) {
  return (
    <div className={`rounded-[1.35rem] ${GLASS_SURFACE} p-3`} style={{ boxShadow: glow }}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.18em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>
            Shamrai intake model
          </p>
          <h1 className="mt-1 font-display text-xl font-black leading-none text-white">Добро пожаловать</h1>
        </div>
        <div className={`flex h-12 w-12 items-center justify-center rounded-2xl ${GLASS_SURFACE}`}>
          <OptimizedImage
            src="/brand/shamrai-channel-emblem.png"
            webpSrc="/brand/shamrai-channel-emblem.webp"
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

function IntroVkStep({
  status,
  displayName,
  error,
  vkReady,
  glow,
  calm,
  onLink,
  onSkip,
}: {
  status: VkLinkStatus;
  displayName: string | null;
  error: string | null;
  vkReady: boolean;
  glow: string;
  calm: boolean;
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
          title="Настроим ленту за 60 секунд"
          caption="Быстро настроим риск, цель, БК и удобный формат сигналов."
        />

        <div className={`relative overflow-hidden rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: glow }}>
          <span className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_22%_0%,rgba(0,210,255,0.24),transparent_38%),linear-gradient(135deg,rgba(255,255,255,0.06),transparent)]" />
          <div className="relative z-10 grid gap-3">
            <div className="flex items-center gap-3">
              <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl ${GLASS_SURFACE}`}>
                <Brain className="h-7 w-7 text-cyan-100" />
              </div>
              <div className="min-w-0">
                <p className="text-sm font-black leading-tight text-white">Короткая калибровка риска</p>
                <p className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-300">
                  Без обещаний исходов: только фильтр, флэт и темп доставки под ваш профиль.
                </p>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-center">
              <Metric label="Время" value="~60с" caption="без длинной формы" compact />
              <Metric label="Формат" value="4" caption="варианта сервиса" compact />
              <Metric label="Риск" value="18+" caption="без гарантий" compact />
            </div>
          </div>
        </div>

        <AnimatePresence mode="wait">
          {linked ? (
            <motion.div
              key="vk-linked"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="rounded-2xl border border-emerald-200/20 bg-emerald-400/12 p-4 text-center text-sm font-black leading-relaxed text-emerald-50 shadow-[0_0_34px_rgba(34,197,94,0.32)]"
            >
              VK ID привязан: {displayName || 'профиль VK'}. Переходим к настройке.
            </motion.div>
          ) : (
            <ElectricButton
              label={loading ? 'Открываем VK ID...' : 'Связать VK для дублирования'}
              icon={loading ? Loader2 : Zap}
              highlighted={vkReady && !loading}
              loading={loading}
              disabled={!vkReady || loading}
              glow={glow}
              calm={calm}
              onClick={onLink}
            />
          )}
        </AnimatePresence>

        {(!vkReady || error) && !linked && (
          <p className="rounded-2xl border border-white/10 bg-white/[0.035] px-3 py-2 text-center text-[11px] font-bold leading-relaxed text-slate-300">
            {error || 'VK ID не настроен для этой сборки. Можно продолжить без привязки.'}
          </p>
        )}
      </div>

      {!linked && (
        <button
          type="button"
          onClick={onSkip}
          disabled={loading}
          className="mx-auto rounded-full border border-white/5 bg-white/[0.025] px-4 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500 transition hover:border-white/15 hover:bg-white/[0.055] hover:text-slate-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Настроить без VK
        </button>
      )}
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
      <StepTitle index="02" title="Что сразу убивает доверие?" caption="Отметьте то, что важно исключить сразу." />
      <div className="space-y-2">
        {antiCapperPains.map((pain) => {
          const selected = selectedPains.includes(pain);
          return (
            <button
              key={pain}
              type="button"
              aria-pressed={selected}
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
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            className={`rounded-2xl ${GLASS_SURFACE} p-4 text-sm font-bold leading-relaxed text-white`}
            style={{ boxShadow: glow }}
          >
            Принято. В Shamrai фокус на прозрачной дистанции, риске и скорости входа, а не на сказках про безошибочные серии.
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function GoalExperienceStep({
  goal,
  experience,
  proMode,
  glow,
  onGoal,
  onExperience,
}: {
  goal: OnboardingGoal | null;
  experience: ExperienceLevel | null;
  proMode: boolean;
  glow: string;
  onGoal: (value: OnboardingGoal) => void;
  onExperience: (value: ExperienceLevel) => void;
}) {
  return (
    <div className="space-y-4">
      <StepTitle index="03" title="Цель и уровень" caption="Так Shamrai подберет темп, объяснения и уровень детализации." />
      <OptionGroup title="Зачем вы здесь" options={goalOptions} value={goal} proMode={proMode} glow={glow} onSelect={onGoal} />
      <OptionGroup title="Опыт" options={experienceOptions} value={experience} proMode={proMode} glow={glow} onSelect={onExperience} />
    </div>
  );
}

function BankrollRiskStep({
  bankroll,
  risk,
  proMode,
  glow,
  onBankroll,
  onRisk,
}: {
  bankroll: BankrollSize | null;
  risk: RiskTolerance | null;
  proMode: boolean;
  glow: string;
  onBankroll: (value: BankrollSize) => void;
  onRisk: (value: RiskTolerance) => void;
}) {
  return (
    <div className="space-y-4">
      <StepTitle index="04" title="Банк и риск" caption="Здесь математика важнее эмоций: флэт считается от размера банка и выбранной амплитуды риска." />
      <OptionGroup title="Рабочий банк" options={bankrollOptions} value={bankroll} proMode={proMode} glow={glow} onSelect={onBankroll} />
      <OptionGroup title="Риск-профиль" options={riskOptions} value={risk} proMode={proMode} glow={glow} onSelect={onRisk} />
      {risk && (
        <div className={`rounded-2xl ${GLASS_SURFACE} p-4 text-sm font-bold leading-relaxed text-white`} style={{ boxShadow: glow }}>
          {riskFlatHint[risk]}
        </div>
      )}
    </div>
  );
}

function BookmakerStep({
  bookmakers,
  selectedBookmakerCodes,
  otherBookmakerName,
  serviceFormat,
  bookmakersLoading,
  proMode,
  glow,
  onBookmaker,
  onOtherBookmakerName,
  onServiceFormat,
}: {
  bookmakers: BookmakerResponse[];
  selectedBookmakerCodes: string[];
  otherBookmakerName: string;
  serviceFormat: ServiceFormat | null;
  bookmakersLoading: boolean;
  proMode: boolean;
  glow: string;
  onBookmaker: (value: string) => void;
  onOtherBookmakerName: (value: string) => void;
  onServiceFormat: (value: ServiceFormat) => void;
}) {
  const selectedBookmakerCodeSet = useMemo(
    () => new Set(selectedBookmakerCodes),
    [selectedBookmakerCodes],
  );
  const otherSelected = selectedBookmakerCodeSet.has('other');

  return (
    <div className="space-y-4">
      <StepTitle index="05" title="Букмекерские конторы" caption="Финальный скан: где ловим сигнал и какой формат сервиса вам ближе." />

      <div className="onboarding-fast-picker rounded-2xl p-3">
        <div className="mb-2 flex items-center justify-between gap-3">
          <SectionLabel compact>Букмекерские конторы</SectionLabel>
          <span className="shrink-0 rounded-xl border border-white/10 bg-slate-950/45 px-2 py-1 text-[10px] font-black text-cyan-100">
            {selectedBookmakerCodes.length || 0}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 min-[430px]:grid-cols-3">
          {bookmakers.map((bookmaker) => {
            const selected = selectedBookmakerCodeSet.has(bookmaker.code);
            return (
              <button
                key={bookmaker.code}
                type="button"
                aria-pressed={selected}
                onClick={() => onBookmaker(bookmaker.code)}
                className={`onboarding-bookmaker-card ${selected ? 'onboarding-bookmaker-card--active' : ''} ${proMode ? 'onboarding-bookmaker-card--pro' : ''}`}
              >
                <BookmakerLogoFrame bookmaker={bookmaker} size="badge" active={selected} className="onboarding-bookmaker-card__logo" />
                <span className="onboarding-bookmaker-card__name">{bookmaker.name}</span>
                <span className="onboarding-bookmaker-card__check">
                  {selected && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
                </span>
              </button>
            );
          })}
        </div>
        {bookmakersLoading && (
          <p className="mt-2 text-center text-[10px] font-bold text-cyan-100">Обновляем список БК...</p>
        )}
      </div>

      {otherSelected && (
        <div className="rounded-2xl border border-white/10 bg-slate-950/42 p-3">
          <label className="block text-[10px] font-black uppercase tracking-[0.12em] text-cyan-100">
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
            Нужно, чтобы сигналы и уведомления не расходились с вашим реальным маршрутом.
          </p>
        </div>
      )}

      <div className="onboarding-fast-picker rounded-2xl p-3">
        <div className="mb-2 flex items-center justify-between gap-3">
          <SectionLabel compact>Формат сервиса</SectionLabel>
          <span className="shrink-0 rounded-xl border border-white/10 bg-slate-950/45 px-2 py-1 text-[10px] font-black text-cyan-100">
            {serviceFormat ? '1' : '0'}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {serviceFormatOptions.map((option) => {
            const selected = serviceFormat === option.value;
            const Icon = option.icon;
            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={selected}
                onClick={() => onServiceFormat(option.value)}
                className={`group relative min-h-[104px] overflow-hidden rounded-2xl ${GLASS_SURFACE} p-3 text-left text-white transition duration-300 active:scale-[0.98]`}
                style={selected ? { boxShadow: glow } : undefined}
              >
                <span className="pointer-events-none absolute inset-0 opacity-0 transition duration-300 group-hover:opacity-100 bg-[linear-gradient(110deg,transparent,rgba(255,255,255,0.12),transparent)]" />
                <span className="relative z-10 flex h-full flex-col justify-between gap-3">
                  <span className="flex items-center justify-between gap-2">
                    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl ${GLASS_SURFACE}`} style={selected ? { boxShadow: glow, color: proMode ? GOLD : CYAN } : undefined}>
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${GLASS_SURFACE}`} style={selected ? { boxShadow: glow } : undefined}>
                      {selected && <span className="h-2 w-2 rounded-full bg-white shadow-[0_0_14px_rgba(255,255,255,0.9)]" />}
                    </span>
                  </span>
                  <span>
                    <span className="block text-[12px] font-black leading-tight">{option.title}</span>
                    <span className="mt-1 block text-[10px] font-semibold leading-snug text-slate-400">{option.description}</span>
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

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
      <div className={`mx-auto inline-flex rounded-full ${GLASS_SURFACE} px-3 py-1 text-[10px] font-black uppercase tracking-[0.16em] text-cyan-100`}>
        step {index}
      </div>
      <h2 className="font-display text-2xl font-black leading-tight text-white">{title}</h2>
      <p className="mx-auto max-w-[360px] text-xs font-semibold leading-relaxed text-slate-300">{caption}</p>
    </div>
  );
}

function SectionLabel({ children, compact = false }: { children: React.ReactNode; compact?: boolean }) {
  return (
    <div className={`flex min-w-0 items-center gap-2 text-[10px] font-black uppercase tracking-[0.14em] text-slate-300 ${compact ? 'flex-1' : ''}`}>
      <span className="h-px flex-1 bg-white/10" />
      <span className="shrink-0">{children}</span>
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

function FinalScreen({
  recommendation,
  currency,
  proMode,
  manifestAccepted,
  finishing,
  glow,
  calm,
  onCurrencyChange,
  onToggleManifest,
  onOpenHub,
}: {
  recommendation: OnboardingRecommendation | null;
  currency: CurrencyCode;
  proMode: boolean;
  manifestAccepted: boolean;
  finishing: boolean;
  glow: string;
  calm: boolean;
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
  const sourceLabel = recommendation?.source
    ? sourceLabels[recommendation.source] || recommendation.source
    : 'модельная оценка Shamrai';

  return (
    <div className="space-y-4">
      <div className="space-y-2 text-center">
        <div className={`mx-auto flex h-20 w-20 items-center justify-center rounded-[1.75rem] ${GLASS_SURFACE}`} style={{ boxShadow: glow }}>
          <Check className="h-10 w-10 text-white" strokeWidth={3} />
        </div>
        <h2 className="font-display text-2xl font-black leading-tight text-white">Ваша модель собрана</h2>
        <p className="text-xs font-semibold leading-relaxed text-slate-300">
          Ниже дисциплинарный флэт и ретро-оценка риска без обещаний дохода.
        </p>
      </div>

      <div className={`rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: glow }}>
        <p className="text-[10px] font-black uppercase tracking-[0.16em]" style={{ color: proMode ? '#fde68a' : '#a5f3fc' }}>Персональный флэт</p>
        <p className="mt-2 font-display text-xl font-black text-white">
          {recommendation?.flat_stake_percent ?? 7}% от банка
        </p>
        <p className="mt-2 text-[11px] font-semibold leading-relaxed text-slate-300">
          Это дисциплинарный размер входа, а не призыв увеличивать ставку после минуса.
        </p>
      </div>

      <div className={`relative overflow-hidden rounded-2xl ${GLASS_SURFACE} p-4`} style={{ boxShadow: proMode ? GOLD_GLOW : PINK_GLOW }}>
        <div className="relative z-10 space-y-3">
          <p className="text-[10px] font-black uppercase tracking-[0.16em] text-pink-100">Ретро-оценка окна</p>
          <div className="grid grid-cols-2 gap-2">
            <Metric label="Потенциал модели" value={`до +${recommendation?.monthly_profit_percent ?? 35}%`} caption="оценка месяца" />
            <Metric label="24 часа" value={`+${recommendation?.missed_profit_percent_24h ?? 7.4}%`} caption={`${amountLabel} к банку`} />
          </div>
          <p className="text-[10px] font-semibold leading-relaxed text-slate-400">
            Источник: {sourceLabel}; закрытых прогнозов за 24ч: {recommendation?.resolved_bets_24h ?? 0}.
          </p>
        </div>
      </div>

      <div className={`flex items-center justify-between gap-3 rounded-2xl ${GLASS_SURFACE} p-3`}>
        <span className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-300">Валюта расчета</span>
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
          Я понимаю: ставки связаны с риском, могут вызывать зависимость, а расчеты Shamrai не гарантируют доход.
        </span>
      </button>

      <ElectricButton
        label="Войти в Analytics Hub"
        icon={manifestAccepted ? Zap : Lock}
        highlighted={manifestAccepted}
        loading={finishing}
        disabled={!manifestAccepted || finishing}
        glow={glow}
        calm={calm}
        onClick={onOpenHub}
      />
    </div>
  );
}

function Metric({ label, value, caption, compact = false }: { label: string; value: string; caption: string; compact?: boolean }) {
  return (
    <div className={`rounded-2xl ${GLASS_SURFACE} ${compact ? 'p-2' : 'p-3'}`}>
      <p className="text-[9px] font-black uppercase tracking-[0.1em] text-slate-400">{label}</p>
      <p className={`mt-1 font-display font-black text-white ${compact ? 'text-base' : 'text-lg'}`}>{value}</p>
      <p className="text-[10px] font-semibold text-cyan-100">{caption}</p>
    </div>
  );
}

const PulseWidget = React.memo(function PulseWidget({ proMode, glow }: { proMode: boolean; glow: string }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const message = pulseLogs[activeIndex];

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveIndex((current) => (current + 1) % pulseLogs.length);
    }, 2400);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className={`rounded-[1.35rem] ${GLASS_SURFACE} p-3 shadow-glass`} style={{ boxShadow: glow }}>
      <div className="flex items-center gap-3">
        <span className={`flex h-9 w-9 items-center justify-center rounded-2xl ${GLASS_SURFACE}`} style={{ boxShadow: glow, color: proMode ? GOLD : CYAN }}>
          <RadioTower className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-400">Пульс Shamrai</p>
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
});

function ElectricButton({
  label,
  icon: Icon,
  iconAfter = false,
  highlighted = false,
  loading = false,
  disabled = false,
  glow,
  calm = false,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  iconAfter?: boolean;
  highlighted?: boolean;
  loading?: boolean;
  disabled?: boolean;
  glow: string;
  calm?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`shamrai-electric-button relative flex min-h-[52px] w-full items-center justify-center gap-2 overflow-hidden rounded-2xl ${GLASS_SURFACE} px-3 text-[11px] font-black uppercase tracking-[0.06em] text-white transition duration-300 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 ${highlighted && !disabled ? 'shamrai-electric-button--hot' : ''} ${calm ? 'shamrai-electric-button--calm' : ''}`}
      style={highlighted && !disabled ? { boxShadow: glow } : undefined}
    >
      {!iconAfter && <Icon className={`relative z-10 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}
      <span className="relative z-10 text-center leading-tight">{label}</span>
      {iconAfter && <Icon className={`relative z-10 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />}
    </button>
  );
}
