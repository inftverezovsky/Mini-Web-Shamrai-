export const PAYMENT_CHECKOUT_STORAGE_KEY = 'shamrai_pending_checkout_v1';
export const LEGACY_STARS_CHECKOUT_STORAGE_KEY = 'shamrai_pending_stars_checkout_v1';
export const STARS_CHECKOUT_STORAGE_KEY = 'shamrai_pending_stars_checkout_v2';
export const PAYMENT_CHECKOUT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export type CheckoutProvider = 'tegro' | 'yookassa';

export interface StoredCheckout {
  version: 1;
  intent_id: string;
  attempt_id?: string;
  provider: CheckoutProvider;
  plan_id: number;
  promo_code: string | null;
  created_at: string;
}

export interface StoredStarsCheckout {
  version: 1;
  intent_id: string;
  attempt_id?: string;
  bet_id: string;
  created_at: string;
}

interface StoredStarsCheckoutMap {
  version: 2;
  checkouts: Record<string, StoredStarsCheckout>;
}

type StarsCheckoutStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export interface PaymentAttemptPayload {
  attempt_id?: string;
  id?: string;
  checkout_state?: string | null;
  creation_status?: string | null;
  status?: string | null;
  payment_status?: string | null;
  requires_flat_setup?: boolean;
  flat_setup_required?: boolean;
  confirmation_url?: string | null;
  flat_subscription?: {
    status?: string | null;
    flat_amount_rub?: string | number | null;
  } | null;
}

export type PaymentAttemptPhase = 'checking' | 'succeeded' | 'failed' | 'configure_flat';

export interface NormalizedPaymentAttempt {
  attemptId: string | null;
  checkoutState: string;
  paymentStatus: string;
  phase: PaymentAttemptPhase;
  confirmationUrl: string | null;
}

interface CreateCheckoutIntentOptions {
  provider: CheckoutProvider;
  planId: number;
  promoCode?: string | null;
  now?: number;
  randomUuid?: () => string;
}

interface CreateStarsCheckoutIntentOptions {
  betId: string;
  now?: number;
  randomUuid?: () => string;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TERMINAL_SUCCESS_STATUSES = new Set(['completed', 'fulfilled', 'paid', 'success', 'succeeded']);
const TERMINAL_FAILURE_STATUSES = new Set(['cancelled', 'canceled', 'expired', 'failed', 'refunded']);

export function isValidCheckoutUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value);
}

function normalizePromoCode(value: string | null | undefined) {
  const normalized = value?.trim().toUpperCase();
  return normalized || null;
}

export function parseStoredCheckout(rawValue: string | null, now = Date.now()): StoredCheckout | null {
  if (!rawValue) return null;

  try {
    const parsed = JSON.parse(rawValue) as Partial<StoredCheckout>;
    const createdAt = Date.parse(String(parsed.created_at || ''));
    const isSupportedProvider = parsed.provider === 'tegro' || parsed.provider === 'yookassa';
    const hasValidAttemptId = parsed.attempt_id === undefined || isValidCheckoutUuid(parsed.attempt_id);
    const isFresh = Number.isFinite(createdAt)
      && createdAt <= now + 60_000
      && now - createdAt <= PAYMENT_CHECKOUT_MAX_AGE_MS;

    if (
      parsed.version !== 1
      || !isValidCheckoutUuid(parsed.intent_id)
      || !hasValidAttemptId
      || !isSupportedProvider
      || !Number.isInteger(parsed.plan_id)
      || Number(parsed.plan_id) <= 0
      || !isFresh
    ) {
      return null;
    }

    return {
      version: 1,
      intent_id: parsed.intent_id,
      attempt_id: parsed.attempt_id,
      provider: parsed.provider as CheckoutProvider,
      plan_id: Number(parsed.plan_id),
      promo_code: normalizePromoCode(parsed.promo_code),
      created_at: new Date(createdAt).toISOString(),
    };
  } catch {
    return null;
  }
}

export function parseStoredStarsCheckout(
  rawValue: string | null,
  now = Date.now(),
): StoredStarsCheckout | null {
  if (!rawValue) return null;

  try {
    const parsed = JSON.parse(rawValue) as Partial<StoredStarsCheckout>;
    const createdAt = Date.parse(String(parsed.created_at || ''));
    const hasValidAttemptId = parsed.attempt_id === undefined || isValidCheckoutUuid(parsed.attempt_id);
    // A pending Telegram invoice has no reliable client-side expiry. Keep it
    // until the server reports a terminal state; otherwise a new intent can
    // be rejected while the recoverable original attempt remains active.
    const hasValidTimestamp = Number.isFinite(createdAt) && createdAt <= now + 60_000;
    if (
      parsed.version !== 1
      || !isValidCheckoutUuid(parsed.intent_id)
      || !hasValidAttemptId
      || !isValidCheckoutUuid(parsed.bet_id)
      || !hasValidTimestamp
    ) {
      return null;
    }
    return {
      version: 1,
      intent_id: parsed.intent_id,
      attempt_id: parsed.attempt_id,
      bet_id: parsed.bet_id,
      created_at: new Date(createdAt).toISOString(),
    };
  } catch {
    return null;
  }
}

export function createCheckoutIntent(
  current: StoredCheckout | null,
  options: CreateCheckoutIntentOptions,
): StoredCheckout {
  const now = options.now ?? Date.now();
  const promoCode = normalizePromoCode(options.promoCode);
  const currentCreatedAt = current ? Date.parse(current.created_at) : Number.NaN;
  const canReuse = Boolean(
    current
    && current.provider === options.provider
    && current.plan_id === options.planId
    && normalizePromoCode(current.promo_code) === promoCode
    && Number.isFinite(currentCreatedAt)
    && now - currentCreatedAt <= PAYMENT_CHECKOUT_MAX_AGE_MS,
  );

  if (canReuse && current) return current;

  const randomUuid = options.randomUuid ?? (() => crypto.randomUUID());
  const intentId = randomUuid();
  if (!isValidCheckoutUuid(intentId)) {
    throw new Error('Не удалось создать безопасный идентификатор оплаты');
  }

  return {
    version: 1,
    intent_id: intentId,
    provider: options.provider,
    plan_id: options.planId,
    promo_code: promoCode,
    created_at: new Date(now).toISOString(),
  };
}

export function createStarsCheckoutIntent(
  current: StoredStarsCheckout | null,
  options: CreateStarsCheckoutIntentOptions,
): StoredStarsCheckout {
  const now = options.now ?? Date.now();
  if (!isValidCheckoutUuid(options.betId)) {
    throw new Error('Некорректный идентификатор прогноза');
  }
  if (current && current.bet_id === options.betId) return current;
  const randomUuid = options.randomUuid ?? (() => crypto.randomUUID());
  const intentId = randomUuid();
  if (!isValidCheckoutUuid(intentId)) {
    throw new Error('Не удалось создать безопасный идентификатор оплаты');
  }
  return {
    version: 1,
    intent_id: intentId,
    bet_id: options.betId,
    created_at: new Date(now).toISOString(),
  };
}

export function resolveCheckoutAttemptId(search: string, stored: StoredCheckout | null) {
  const queryAttemptId = new URLSearchParams(search).get('attempt_id');
  if (isValidCheckoutUuid(queryAttemptId)) return queryAttemptId;
  return isValidCheckoutUuid(stored?.attempt_id) ? stored.attempt_id : null;
}

export function normalizePaymentAttempt(payload: PaymentAttemptPayload): NormalizedPaymentAttempt {
  const checkoutState = String(payload.checkout_state || payload.creation_status || 'ready').toLowerCase();
  const paymentStatus = String(payload.payment_status || payload.status || 'pending').toLowerCase();
  const attemptId = isValidCheckoutUuid(payload.attempt_id)
    ? payload.attempt_id
    : isValidCheckoutUuid(payload.id)
      ? payload.id
      : null;
  const creationFailed = checkoutState === 'failed';
  const paymentFailed = TERMINAL_FAILURE_STATUSES.has(paymentStatus);
  const paymentSucceeded = TERMINAL_SUCCESS_STATUSES.has(paymentStatus);
  const flatRequiresSetup = payload.flat_setup_required === true
    || payload.requires_flat_setup === true
    || (
      payload.flat_subscription?.status === 'pending_setup'
      && payload.flat_subscription.flat_amount_rub == null
    );

  let phase: PaymentAttemptPhase = 'checking';
  if (creationFailed || paymentFailed) phase = 'failed';
  else if (paymentSucceeded && flatRequiresSetup) phase = 'configure_flat';
  else if (paymentSucceeded) phase = 'succeeded';

  return {
    attemptId,
    checkoutState,
    paymentStatus,
    phase,
    confirmationUrl: typeof payload.confirmation_url === 'string' && payload.confirmation_url
      ? payload.confirmation_url
      : null,
  };
}

export function readStoredCheckout(storage: Pick<Storage, 'getItem' | 'removeItem'> = window.localStorage) {
  const rawValue = storage.getItem(PAYMENT_CHECKOUT_STORAGE_KEY);
  const parsed = parseStoredCheckout(rawValue);
  if (rawValue && !parsed) storage.removeItem(PAYMENT_CHECKOUT_STORAGE_KEY);
  return parsed;
}

export function writeStoredCheckout(
  checkout: StoredCheckout,
  storage: Pick<Storage, 'setItem'> = window.localStorage,
) {
  storage.setItem(PAYMENT_CHECKOUT_STORAGE_KEY, JSON.stringify(checkout));
}

export function clearStoredCheckout(storage: Pick<Storage, 'removeItem'> = window.localStorage) {
  storage.removeItem(PAYMENT_CHECKOUT_STORAGE_KEY);
}

function parseStoredStarsCheckoutMap(
  rawValue: string | null,
  now = Date.now(),
): StoredStarsCheckoutMap | null {
  if (!rawValue) return null;
  try {
    const parsed = JSON.parse(rawValue) as Partial<StoredStarsCheckoutMap>;
    if (
      parsed.version !== 2
      || !parsed.checkouts
      || typeof parsed.checkouts !== 'object'
      || Array.isArray(parsed.checkouts)
    ) {
      return null;
    }

    const checkouts = Object.entries(parsed.checkouts).reduce<Record<string, StoredStarsCheckout>>(
      (result, [betId, value]) => {
        const checkout = parseStoredStarsCheckout(JSON.stringify(value), now);
        if (!checkout || checkout.bet_id !== betId) return result;
        return { ...result, [betId]: checkout };
      },
      {},
    );
    return { version: 2, checkouts };
  } catch {
    return null;
  }
}

function persistStoredStarsCheckoutMap(
  checkoutMap: StoredStarsCheckoutMap,
  storage: StarsCheckoutStorage,
) {
  if (Object.keys(checkoutMap.checkouts).length === 0) {
    storage.removeItem(STARS_CHECKOUT_STORAGE_KEY);
    return;
  }
  storage.setItem(STARS_CHECKOUT_STORAGE_KEY, JSON.stringify(checkoutMap));
}

function loadStoredStarsCheckoutMap(
  storage: StarsCheckoutStorage,
  now = Date.now(),
): StoredStarsCheckoutMap {
  const currentRawValue = storage.getItem(STARS_CHECKOUT_STORAGE_KEY);
  const current = parseStoredStarsCheckoutMap(currentRawValue, now);
  if (currentRawValue && !current) storage.removeItem(STARS_CHECKOUT_STORAGE_KEY);

  const checkoutMap = current || { version: 2 as const, checkouts: {} };
  const legacyRawValue = storage.getItem(LEGACY_STARS_CHECKOUT_STORAGE_KEY);
  if (!legacyRawValue) return checkoutMap;

  const legacy = parseStoredStarsCheckout(legacyRawValue, now);
  if (!legacy) {
    storage.removeItem(LEGACY_STARS_CHECKOUT_STORAGE_KEY);
    return checkoutMap;
  }

  const migrated = {
    version: 2 as const,
    checkouts: checkoutMap.checkouts[legacy.bet_id]
      ? checkoutMap.checkouts
      : { ...checkoutMap.checkouts, [legacy.bet_id]: legacy },
  };
  // Remove the legacy record only after the versioned map is durable.
  persistStoredStarsCheckoutMap(migrated, storage);
  storage.removeItem(LEGACY_STARS_CHECKOUT_STORAGE_KEY);
  return migrated;
}

export function readStoredStarsCheckout(
  betId: string,
  storage: StarsCheckoutStorage = window.localStorage,
) {
  if (!isValidCheckoutUuid(betId)) return null;
  return loadStoredStarsCheckoutMap(storage).checkouts[betId] || null;
}

export function writeStoredStarsCheckout(
  checkout: StoredStarsCheckout,
  storage: StarsCheckoutStorage = window.localStorage,
) {
  const normalized = parseStoredStarsCheckout(JSON.stringify(checkout));
  if (!normalized) throw new Error('Некорректное состояние оплаты Stars');
  const current = loadStoredStarsCheckoutMap(storage);
  persistStoredStarsCheckoutMap({
    version: 2,
    checkouts: { ...current.checkouts, [normalized.bet_id]: normalized },
  }, storage);
}

export function clearStoredStarsCheckout(
  betId: string,
  expectedIntentId?: string,
  storage: StarsCheckoutStorage = window.localStorage,
) {
  if (!isValidCheckoutUuid(betId)) return false;
  const checkoutMap = loadStoredStarsCheckoutMap(storage);
  const current = checkoutMap.checkouts[betId];
  if (expectedIntentId && current && current.intent_id !== expectedIntentId) return false;
  if (!current) return true;
  const remaining = Object.fromEntries(
    Object.entries(checkoutMap.checkouts).filter(([storedBetId]) => storedBetId !== betId),
  );
  persistStoredStarsCheckoutMap({ version: 2, checkouts: remaining }, storage);
  return true;
}
