import { ApiRequestError } from '../../utils/api';

export const INACTIVE_BET_TAKE_NOTICE = 'Прогноз уже не активен. Реагировать не нужно.';

export type BetTakeFailureNotice = {
  type: 'error' | 'info';
  title: string;
  message: string;
};

export function isInactiveBetTakeError(error: unknown) {
  return error instanceof ApiRequestError && error.status === 409;
}

export function betTakeFailureNotice(error: unknown): BetTakeFailureNotice {
  if (isInactiveBetTakeError(error)) {
    return {
      type: 'info',
      title: 'Прогноз не активен',
      message: error instanceof Error && error.message ? error.message : INACTIVE_BET_TAKE_NOTICE,
    };
  }

  return {
    type: 'error',
    title: 'Ошибка',
    message: error instanceof Error && error.message ? error.message : 'Не удалось принять ставку',
  };
}
