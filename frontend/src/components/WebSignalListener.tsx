import { useEffect, useRef } from 'react';

import { apiFetch, buildApiWebSocketUrl } from '../utils/api';
import { MOCK_DEBUG_AUTH_TOKEN, getStoredAuthToken } from '../utils/authStorage';
import { notifyInfo } from '../utils/notify';
import { playIncomingSignalSound, playIncomingSupportSound, unlockIncomingSignalSound } from '../utils/signalAudio';
import { isTelegramMiniApp } from '../utils/telegramSdk';
import {
  isServiceWorkerNotificationMessage,
  rememberWebNotificationEvent,
  type WebNotificationEvent,
} from '../utils/webNotificationEvents';
import { MAX_REALTIME_ITEMS, limitRecent, rememberRecentId } from '../utils/realtimeLimits';
import { supportsServiceWorkerRuntime, supportsWebSocketRuntime } from '../utils/performancePolicy';

interface PersonalSignal {
  id: number;
  text: string;
  type: string;
  created_at?: string;
  data?: {
    message_text?: string;
    push_url?: string;
    url?: string;
  };
}

interface WebSignalListenerProps {
  enabled: boolean;
}

interface SignalStreamTicketResponse {
  ticket: string;
  expires_in: number;
}

export const WEB_SIGNAL_EVENT = 'shamrai:personal-signal';
export const WEB_SIGNAL_STATUS_EVENT = 'shamrai:signal-stream-status';

function signalNoticeText(signal: PersonalSignal) {
  const sourceText = signal.data?.message_text || signal.text;
  const firstLine = sourceText.split('\n').map((line) => line.trim()).find(Boolean) || 'Новое сообщение в личном чате.';
  const limit = signal.type.startsWith('support_') ? 260 : 96;
  return firstLine.length > limit ? `${firstLine.slice(0, limit - 3)}...` : firstLine;
}

function signalNoticeTitle(signal: PersonalSignal) {
  if (signal.type === 'support_staff_message') return 'Shamrai написал в чат';
  return 'Личный бот Shamrai';
}

function signalToNotificationEvent(signal: PersonalSignal, source: WebNotificationEvent['source']): WebNotificationEvent {
  return {
    id: String(signal.id),
    title: signalNoticeTitle(signal),
    body: signalNoticeText(signal),
    type: signal.type,
    url: signal.data?.push_url || signal.data?.url || '/app?open=web-bot-chat',
    source,
  };
}

function mergeSignalsById(currentSignals: PersonalSignal[], incomingSignals: PersonalSignal[]) {
  const signalsById = new Map<number, PersonalSignal>();
  currentSignals.forEach((signal) => signalsById.set(signal.id, signal));
  incomingSignals.forEach((signal) => signalsById.set(signal.id, signal));

  return Array.from(signalsById.values()).sort((left, right) => {
    const leftTime = left.created_at ? new Date(left.created_at).getTime() : 0;
    const rightTime = right.created_at ? new Date(right.created_at).getTime() : 0;
    return (leftTime - rightTime) || (left.id - right.id);
  });
}

export default function WebSignalListener({ enabled }: WebSignalListenerProps) {
  const seenSignalIdsRef = useRef<Set<number>>(new Set());
  const bufferedSignalsRef = useRef<PersonalSignal[]>([]);

  useEffect(() => {
    if (!enabled || isTelegramMiniApp()) return;

    let disposed = false;
    const unlock = () => {
      void unlockIncomingSignalSound().then((unlocked) => {
        if (!unlocked || disposed) return;
        window.removeEventListener('pointerdown', unlock);
        window.removeEventListener('keydown', unlock);
        window.removeEventListener('touchstart', unlock);
      });
    };

    window.addEventListener('pointerdown', unlock, { passive: true });
    window.addEventListener('keydown', unlock);
    window.addEventListener('touchstart', unlock, { passive: true });

    return () => {
      disposed = true;
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
      window.removeEventListener('touchstart', unlock);
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || isTelegramMiniApp()) return;
    if (!supportsWebSocketRuntime()) {
      window.dispatchEvent(new CustomEvent(WEB_SIGNAL_STATUS_EVENT, { detail: { state: 'offline' } }));
      return;
    }

    const token = getStoredAuthToken();
    if (token === MOCK_DEBUG_AUTH_TOKEN) return;

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let closedByUnmount = false;
    let reconnectAttempt = 0;
    let initialHistoryLoaded = false;

    const emitStatus = (state: 'connecting' | 'online' | 'offline') => {
      window.dispatchEvent(new CustomEvent(WEB_SIGNAL_STATUS_EVENT, { detail: { state } }));
    };

    const emitSignal = (signal: PersonalSignal, options: { notify: boolean }) => {
      if (!signal?.id || seenSignalIdsRef.current.has(signal.id)) return;
      rememberRecentId(seenSignalIdsRef.current, signal.id);
      bufferedSignalsRef.current = limitRecent(
        mergeSignalsById(bufferedSignalsRef.current, [signal]),
        MAX_REALTIME_ITEMS,
      );
      window.dispatchEvent(new CustomEvent(WEB_SIGNAL_EVENT, { detail: signal }));
      if (!options.notify) return;
      if (!rememberWebNotificationEvent(signalToNotificationEvent(signal, 'websocket'))) return;
      notifyInfo(signalNoticeText(signal), signalNoticeTitle(signal));
      void (signal.type.startsWith('support_') ? playIncomingSupportSound() : playIncomingSignalSound());
    };

    const catchUpMissedSignals = async (options: { notify: boolean }) => {
      try {
        const history = await apiFetch<PersonalSignal[]>(`/signals/history?limit=${MAX_REALTIME_ITEMS}`);
        if (closedByUnmount) return;
        const mergedSignals = limitRecent(
          mergeSignalsById(bufferedSignalsRef.current, history),
          MAX_REALTIME_ITEMS,
        );
        bufferedSignalsRef.current = mergedSignals;
        const shouldNotify = options.notify && initialHistoryLoaded;
        mergedSignals.forEach((signal) => emitSignal(signal, { notify: shouldNotify }));
        initialHistoryLoaded = true;
      } catch {
        // The websocket will continue reconnecting; history catch-up is retried on the next open.
      }
    };

    const scheduleReconnect = () => {
      if (closedByUnmount) return;
      const baseDelay = Math.min(30_000, 1000 * (2 ** Math.min(reconnectAttempt, 5)));
      const jitter = Math.floor(Math.random() * 600);
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(connect, baseDelay + jitter);
    };

    const connect = async () => {
      emitStatus('connecting');
      let ticket: string;
      try {
        const ticketResponse = await apiFetch<SignalStreamTicketResponse>('/signals/stream-ticket', {
          method: 'POST',
        });
        ticket = ticketResponse.ticket;
      } catch {
        emitStatus('offline');
        scheduleReconnect();
        return;
      }
      if (closedByUnmount) return;

      socket = new WebSocket(buildApiWebSocketUrl('/api/signals/stream', { ticket }));

      socket.onopen = () => {
        reconnectAttempt = 0;
        emitStatus('online');
        void catchUpMissedSignals({ notify: true });
        if (pingTimer) window.clearInterval(pingTimer);
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
        }, 25000);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.type === 'pong') return;
          emitSignal(payload as PersonalSignal, { notify: true });
        } catch {
          // Ignore malformed stream frames.
        }
      };

      socket.onclose = () => {
        if (pingTimer) window.clearInterval(pingTimer);
        emitStatus('offline');
        scheduleReconnect();
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    void connect();
    void catchUpMissedSignals({ notify: false });

    return () => {
      closedByUnmount = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (pingTimer) window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || isTelegramMiniApp() || !supportsServiceWorkerRuntime()) return;

    const handleServiceWorkerMessage = (event: MessageEvent) => {
      if (!isServiceWorkerNotificationMessage(event.data)) return;
      const payload = event.data.payload;
      if (!rememberWebNotificationEvent(payload)) return;
      notifyInfo(payload.body, payload.title);
      void (payload.type.startsWith('support_') ? playIncomingSupportSound() : playIncomingSignalSound());
    };

    navigator.serviceWorker.addEventListener('message', handleServiceWorkerMessage);
    return () => {
      navigator.serviceWorker.removeEventListener('message', handleServiceWorkerMessage);
    };
  }, [enabled]);

  return null;
}
