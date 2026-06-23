export type WebNotificationSource = 'websocket' | 'service-worker';

export interface WebNotificationEvent {
  id: string;
  title: string;
  body: string;
  type: string;
  url: string;
  source: WebNotificationSource;
}

const DEDUPE_TTL_MS = 120_000;
const seenNotificationEvents = new Map<string, number>();

function notificationEventKey(event: WebNotificationEvent) {
  const stableId = event.id || `${event.type}:${event.title}:${event.body}:${event.url}`;
  return `${event.type || 'notification'}:${stableId}`;
}

function pruneSeenEvents(now: number) {
  for (const [key, lastSeenAt] of seenNotificationEvents) {
    if (now - lastSeenAt > DEDUPE_TTL_MS) {
      seenNotificationEvents.delete(key);
    }
  }
}

export function rememberWebNotificationEvent(event: WebNotificationEvent, now = Date.now()) {
  pruneSeenEvents(now);
  const key = notificationEventKey(event);
  const lastSeenAt = seenNotificationEvents.get(key);
  if (lastSeenAt !== undefined && now - lastSeenAt <= DEDUPE_TTL_MS) {
    return false;
  }
  seenNotificationEvents.set(key, now);
  return true;
}

export function resetWebNotificationDedupeForTests() {
  seenNotificationEvents.clear();
}

export function isServiceWorkerNotificationMessage(value: unknown): value is {
  type: 'SHAMRAI_PUSH_RECEIVED';
  payload: WebNotificationEvent;
} {
  if (!value || typeof value !== 'object') return false;
  const message = value as { type?: unknown; payload?: unknown };
  if (message.type !== 'SHAMRAI_PUSH_RECEIVED' || !message.payload || typeof message.payload !== 'object') {
    return false;
  }
  const payload = message.payload as Partial<WebNotificationEvent>;
  return (
    typeof payload.id === 'string'
    && typeof payload.title === 'string'
    && typeof payload.body === 'string'
    && typeof payload.type === 'string'
    && typeof payload.url === 'string'
    && payload.source === 'service-worker'
  );
}
