export type ExperienceLevel = 'novice' | 'amateur' | 'pro';
export type BankrollSize = 'micro' | 'mid' | 'high';
export type RiskTolerance = 'cautious' | 'balanced' | 'aggressive';
export type CurrencyCode = 'RUB' | 'USD' | 'FLATS';
export type OnboardingGoal = 'trust_check' | 'discipline' | 'fast_signals' | 'raise_level';
export type ServiceFormat = 'auto_fast' | 'logic_review' | 'vip_support' | 'distance_report';

export interface OnboardingDraftAnswers {
  anti_capper_pains: string[];
  onboarding_goal: OnboardingGoal | null;
  experience_level: ExperienceLevel | null;
  bankroll_size: BankrollSize | null;
  risk_tolerance: RiskTolerance | null;
  bookmaker_codes: string[];
  other_bookmaker_name: string;
  service_format: ServiceFormat | null;
  favorite_sports: string[];
  vk_user_id: string | null;
}

export interface BookmakerStepState {
  selectedBookmakerCodes: string[];
  otherBookmakerName: string;
  serviceFormat: ServiceFormat | null;
}

export function isOtherBookmakerNameRequired(selectedBookmakerCodes: readonly string[]) {
  return selectedBookmakerCodes.includes('other');
}

export function isBookmakerStepComplete({
  selectedBookmakerCodes,
  otherBookmakerName,
  serviceFormat,
}: BookmakerStepState) {
  if (!selectedBookmakerCodes.length) return false;
  if (!serviceFormat) return false;
  if (isOtherBookmakerNameRequired(selectedBookmakerCodes) && !otherBookmakerName.trim()) return false;
  return true;
}

export function buildOnboardingPayload(
  answers: OnboardingDraftAnswers,
  currency: CurrencyCode,
  nextVkUserId = answers.vk_user_id,
) {
  return {
    anti_capper_pains: answers.anti_capper_pains,
    onboarding_goal: answers.onboarding_goal,
    experience_level: answers.experience_level,
    bankroll_size: answers.bankroll_size,
    risk_tolerance: answers.risk_tolerance,
    bookmakers: answers.bookmaker_codes,
    primary_bookmaker: answers.bookmaker_codes[0],
    other_bookmaker_name: answers.other_bookmaker_name.trim() || null,
    service_format: answers.service_format ?? 'auto_fast',
    favorite_sports: answers.favorite_sports,
    vk_user_id: nextVkUserId,
    currency_preference: currency,
  };
}
