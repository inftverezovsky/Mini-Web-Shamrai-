import { useEffect, useRef } from 'react';

import { apiFetch, buildApiWebSocketUrl } from '../utils/api';
import { getStoredAuthToken } from '../utils/authStorage';
import { notifyInfo } from '../utils/notify';
import { playIncomingSignalSound, unlockIncomingSignalSound } from '../utils/signalAudio';
import { isTelegramMiniApp } from '../utils/telegramSdk';

interface PersonalSignal {
  id: number;
  text: string;
  type: string;
  data?: {
    message_text?: string;
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
  return firstLine.length > 96 ? `${firstLine.slice(0, 93)}...` : firstLine;
}

export default function WebSignalListener({ enabled }: WebSignalListenerProps) {
  const seenSignalIdsRef = useRef<Set<number>>(new Set());

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

    const token = getStoredAuthToken();
    if (!token || token === 'mock_debug_access_token') return;

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let closedByUnmount = false;
    let reconnectAttempt = 0;

    const emitStatus = (state: 'connecting' | 'online' | 'offline') => {
      window.dispatchEvent(new CustomEvent(WEB_SIGNAL_STATUS_EVENT, { detail: { state } }));
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
        if (pingTimer) window.clearInterval(pingTimer);
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
        }, 25000);
      };

      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.type === 'pong') return;
          const signal = payload as PersonalSignal;
          if (seenSignalIdsRef.current.has(signal.id)) return;
          seenSignalIdsRef.current.add(signal.id);
          window.dispatchEvent(new CustomEvent(WEB_SIGNAL_EVENT, { detail: signal }));
          notifyInfo(signalNoticeText(signal), 'Личный бот Shamrai');
          void playIncomingSignalSound();
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

    return () => {
      closedByUnmount = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (pingTimer) window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [enabled]);

  return null;
}
