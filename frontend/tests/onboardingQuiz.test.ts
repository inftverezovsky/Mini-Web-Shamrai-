import { describe, expect, it } from 'vitest';
import { toggleBookmakerCodeSelection } from '../src/utils/bookmakerSelection';
import {
  buildOnboardingPayload,
  isBookmakerStepComplete,
  type OnboardingDraftAnswers,
} from '../src/utils/onboardingQuiz';

describe('toggleBookmakerCodeSelection', () => {
  it('adds an unselected bookmaker to the end of the selection', () => {
    expect(toggleBookmakerCodeSelection(['fonbet', 'pari'], 'betboom')).toEqual([
      'fonbet',
      'pari',
      'betboom',
    ]);
  });

  it('removes a selected bookmaker without mutating the current selection', () => {
    const selected = ['fonbet', 'pari', 'betboom'];

    expect(toggleBookmakerCodeSelection(selected, 'pari')).toEqual(['fonbet', 'betboom']);
    expect(selected).toEqual(['fonbet', 'pari', 'betboom']);
  });
});

describe('onboarding quiz payload rules', () => {
  it('requires a custom bookmaker name when Other is selected', () => {
    expect(isBookmakerStepComplete({
      selectedBookmakerCodes: ['fonbet', 'other'],
      otherBookmakerName: '',
      serviceFormat: 'auto_fast',
    })).toBe(false);

    expect(isBookmakerStepComplete({
      selectedBookmakerCodes: ['fonbet', 'other'],
      otherBookmakerName: 'Pinnacle',
      serviceFormat: 'auto_fast',
    })).toBe(true);
  });

  it('does not require favorite sports on the bookmaker step', () => {
    expect(isBookmakerStepComplete({
      selectedBookmakerCodes: ['fonbet'],
      otherBookmakerName: '',
      serviceFormat: 'auto_fast',
    })).toBe(true);
  });

  it('requires a service format on the bookmaker step', () => {
    expect(isBookmakerStepComplete({
      selectedBookmakerCodes: ['fonbet'],
      otherBookmakerName: '',
      serviceFormat: null,
    })).toBe(false);
  });

  it('includes goal, sports, and service format in the onboarding payload', () => {
    const draft: OnboardingDraftAnswers = {
      anti_capper_pains: ['Поздние сигналы'],
      onboarding_goal: 'fast_signals',
      experience_level: 'pro',
      bankroll_size: 'high',
      risk_tolerance: 'aggressive',
      bookmaker_codes: ['fonbet', 'other'],
      other_bookmaker_name: 'Pinnacle',
      service_format: 'vip_support',
      favorite_sports: ['Футбол', 'Теннис'],
      vk_user_id: '741852963',
    };

    expect(buildOnboardingPayload(draft, 'RUB', '741852963')).toMatchObject({
      anti_capper_pains: ['Поздние сигналы'],
      onboarding_goal: 'fast_signals',
      favorite_sports: ['Футбол', 'Теннис'],
      bookmakers: ['fonbet', 'other'],
      primary_bookmaker: 'fonbet',
      other_bookmaker_name: 'Pinnacle',
      service_format: 'vip_support',
      currency_preference: 'RUB',
      vk_user_id: '741852963',
    });
  });
});
