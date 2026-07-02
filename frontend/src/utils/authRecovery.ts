export function getAuthErrorStatus(error: unknown): number | null {
  if (typeof error !== 'object' || error === null || !('status' in error)) return null;
  const status = Number((error as { status?: unknown }).status);
  return Number.isFinite(status) ? status : null;
}

function authErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  if (typeof error === 'object' && error !== null && 'message' in error) {
    return String((error as { message?: unknown }).message || '');
  }
  return '';
}

export function isTransientAuthError(error: unknown): boolean {
  const status = getAuthErrorStatus(error);
  if (status !== null) return status === 429 || status >= 500;

  return /failed to fetch|networkerror|load failed|network request failed|timeout|превышено время|не удалось подключиться/i
    .test(authErrorMessage(error));
}

export function shouldKeepExistingUserAfterProfileError(error: unknown): boolean {
  return isTransientAuthError(error);
}

export function shouldAttemptFullAuthAfterProfileError(error: unknown): boolean {
  const status = getAuthErrorStatus(error);
  if (status !== null) return status === 401 || status === 403;
  return !isTransientAuthError(error);
}
