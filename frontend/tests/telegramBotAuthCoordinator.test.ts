import { describe, expect, it, vi } from 'vitest';
import { createTelegramBotAuthCoordinator } from '../src/utils/telegramBotAuthCoordinator';

describe('Telegram bot auth coordinator', () => {
  it('reuses one in-flight auth flow for concurrent starts', async () => {
    const coordinator = createTelegramBotAuthCoordinator();
    const firstSessionStarted = vi.fn();
    const secondSessionStarted = vi.fn();
    const executor = vi.fn(async (notifySessionStarted) => {
      notifySessionStarted({
        botUrl: 'https://t.me/Shamra1_bot?start=auth_shared',
        expiresAt: '2026-06-25T00:00:00.000Z',
      });
    });

    const firstPromise = coordinator.run({ onSessionStarted: firstSessionStarted }, executor);
    const secondPromise = coordinator.run({ onSessionStarted: secondSessionStarted }, executor);

    expect(secondPromise).toBe(firstPromise);
    await Promise.all([firstPromise, secondPromise]);

    expect(executor).toHaveBeenCalledTimes(1);
    expect(firstSessionStarted).toHaveBeenCalledWith({
      botUrl: 'https://t.me/Shamra1_bot?start=auth_shared',
      expiresAt: '2026-06-25T00:00:00.000Z',
    });
    expect(secondSessionStarted).toHaveBeenCalledWith({
      botUrl: 'https://t.me/Shamra1_bot?start=auth_shared',
      expiresAt: '2026-06-25T00:00:00.000Z',
    });
  });

  it('replays the active session to callers that join after Telegram was opened', async () => {
    const coordinator = createTelegramBotAuthCoordinator();
    const lateSessionStarted = vi.fn();
    let finishFlow: (() => void) | undefined;
    const executor = vi.fn(async (notifySessionStarted) => {
      notifySessionStarted({
        botUrl: 'https://t.me/Shamra1_bot?start=auth_late',
        expiresAt: '2026-06-25T00:00:00.000Z',
      });
      await new Promise<void>((resolve) => {
        finishFlow = resolve;
      });
    });

    const activePromise = coordinator.run({}, executor);
    const latePromise = coordinator.run({ onSessionStarted: lateSessionStarted }, executor);

    expect(latePromise).toBe(activePromise);
    expect(lateSessionStarted).toHaveBeenCalledWith({
      botUrl: 'https://t.me/Shamra1_bot?start=auth_late',
      expiresAt: '2026-06-25T00:00:00.000Z',
    });
    expect(executor).toHaveBeenCalledTimes(1);

    finishFlow?.();
    await activePromise;
  });

  it('allows a new flow after the previous one fails', async () => {
    const coordinator = createTelegramBotAuthCoordinator();
    const failingExecutor = vi.fn(async () => {
      throw new Error('session failed');
    });
    const succeedingExecutor = vi.fn(async (notifySessionStarted) => {
      notifySessionStarted({
        botUrl: 'https://t.me/Shamra1_bot?start=auth_retry',
        expiresAt: '2026-06-25T00:00:00.000Z',
      });
    });

    await expect(coordinator.run({}, failingExecutor)).rejects.toThrow('session failed');
    await coordinator.run({}, succeedingExecutor);

    expect(failingExecutor).toHaveBeenCalledTimes(1);
    expect(succeedingExecutor).toHaveBeenCalledTimes(1);
  });
});
