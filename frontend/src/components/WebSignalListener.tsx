import { useEffect, useRef } from 'react';

import { AUTH_TOKEN_STORAGE_KEY, buildApiWebSocketUrl } from '../utils/api';
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

    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!token || token === 'mock_debug_access_token') return;

    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let pingTimer: number | undefined;
    let closedByUnmount = false;

    const connect = () => {
      socket = new WebSocket(buildApiWebSocketUrl('/api/signals/stream', { token }));

      socket.onopen = () => {
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
          notifyInfo(signalNoticeText(signal), 'Личный бот Shamrai');
          void playIncomingSignalSound();
        } catch {
          // Ignore malformed stream frames.
        }
      };

      socket.onclose = () => {
        if (pingTimer) window.clearInterval(pingTimer);
        if (!closedByUnmount) {
          reconnectTimer = window.setTimeout(connect, 3500);
        }
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      closedByUnmount = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (pingTimer) window.clearInterval(pingTimer);
      socket?.close();
    };
  }, [enabled]);

  return null;
}
