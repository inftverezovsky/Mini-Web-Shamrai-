export interface TelegramBotAuthSessionStarted {
  botUrl: string;
  expiresAt: string;
}

export interface TelegramBotAuthOptions {
  onSessionStarted?: (session: TelegramBotAuthSessionStarted) => void;
}

type TelegramBotAuthSessionNotifier = (session: TelegramBotAuthSessionStarted) => void;
type TelegramBotAuthExecutor = (notifySessionStarted: TelegramBotAuthSessionNotifier) => Promise<void>;

interface TelegramBotAuthInFlight {
  promise: Promise<void>;
  session: TelegramBotAuthSessionStarted | null;
  listeners: Set<TelegramBotAuthSessionNotifier>;
}

function notifyListener(listener: TelegramBotAuthSessionNotifier, session: TelegramBotAuthSessionStarted) {
  try {
    listener(session);
  } catch {
    // UI callbacks should not break the shared auth flow.
  }
}

export function createTelegramBotAuthCoordinator() {
  let inFlight: TelegramBotAuthInFlight | null = null;

  const attachListener = (
    entry: TelegramBotAuthInFlight,
    listener?: TelegramBotAuthSessionNotifier,
  ) => {
    if (!listener) return;

    if (entry.session) {
      notifyListener(listener, entry.session);
      return;
    }

    entry.listeners.add(listener);
  };

  const notifySessionStarted = (
    entry: TelegramBotAuthInFlight,
    session: TelegramBotAuthSessionStarted,
  ) => {
    entry.session = session;
    for (const listener of entry.listeners) {
      notifyListener(listener, session);
    }
    entry.listeners.clear();
  };

  const run = (options: TelegramBotAuthOptions, executor: TelegramBotAuthExecutor) => {
    if (inFlight) {
      attachListener(inFlight, options.onSessionStarted);
      return inFlight.promise;
    }

    const entry: TelegramBotAuthInFlight = {
      promise: Promise.resolve(),
      session: null,
      listeners: new Set(),
    };
    attachListener(entry, options.onSessionStarted);
    inFlight = entry;

    entry.promise = executor((session) => notifySessionStarted(entry, session))
      .finally(() => {
        if (inFlight === entry) {
          inFlight = null;
        }
        entry.listeners.clear();
      });

    return entry.promise;
  };

  return { run };
}
