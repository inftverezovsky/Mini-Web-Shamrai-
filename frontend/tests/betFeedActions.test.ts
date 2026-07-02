import { describe, expect, it } from 'vitest';
import { ApiRequestError } from '../src/utils/api';
import { betTakeFailureNotice } from '../src/pages/user/betFeedActions';

describe('bet feed action helpers', () => {
  it('treats stale take conflicts as an informational inactive notice', () => {
    const notice = betTakeFailureNotice(
      new ApiRequestError('Прогноз уже не активен. Реагировать не нужно.', 409, null),
    );

    expect(notice).toEqual({
      type: 'info',
      title: 'Прогноз не активен',
      message: 'Прогноз уже не активен. Реагировать не нужно.',
    });
  });

  it('keeps other take failures as errors', () => {
    const notice = betTakeFailureNotice(new Error('Нет доступа'));

    expect(notice).toEqual({
      type: 'error',
      title: 'Ошибка',
      message: 'Нет доступа',
    });
  });
});
