import { describe, expect, it } from 'vitest';
import {
  getAuthErrorStatus,
  shouldAttemptFullAuthAfterProfileError,
  shouldKeepExistingUserAfterProfileError,
} from '../src/utils/authRecovery';

describe('auth recovery decisions', () => {
  it('does not start a new Telegram login after profile refresh is rate-limited', () => {
    const error = { status: 429 };

    expect(shouldKeepExistingUserAfterProfileError(error)).toBe(true);
    expect(shouldAttemptFullAuthAfterProfileError(error)).toBe(false);
  });

  it('keeps the current user on transient server and network failures', () => {
    expect(shouldKeepExistingUserAfterProfileError({ status: 503 })).toBe(true);
    expect(shouldKeepExistingUserAfterProfileError(new Error('Failed to fetch'))).toBe(true);
    expect(shouldAttemptFullAuthAfterProfileError(new Error('NetworkError when attempting to fetch resource.'))).toBe(false);
  });

  it('only falls back to full auth when the profile session is actually expired', () => {
    expect(getAuthErrorStatus({ status: 401 })).toBe(401);
    expect(shouldKeepExistingUserAfterProfileError({ status: 401 })).toBe(false);
    expect(shouldAttemptFullAuthAfterProfileError({ status: 401 })).toBe(true);
  });
});
