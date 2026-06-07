const isEnabled = (value: string | undefined) => value === 'true';

export const promoFlags = {
  marathon: isEnabled(import.meta.env.VITE_PROMO_MARATHON),
  swipe: isEnabled(import.meta.env.VITE_PROMO_SWIPE),
  pvp: isEnabled(import.meta.env.VITE_PROMO_PVP),
  quiz: isEnabled(import.meta.env.VITE_PROMO_QUIZ),
  crowdBet: isEnabled(import.meta.env.VITE_PROMO_CROWD_BET),
};

export const hasActivePromo = Object.values(promoFlags).some(Boolean);
