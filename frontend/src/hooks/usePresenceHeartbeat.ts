import { useEffect } from 'react';

import { apiFetch } from '../utils/api';

export function usePresenceHeartbeat(enabled: boolean) {
  useEffect(() => {
    if (!enabled || typeof document === 'undefined') return;

    let disposed = false;
    const sendHeartbeat = () => {
      if (disposed || document.visibilityState === 'hidden') return;
      void apiFetch('/users/me/presence', { method: 'POST' }).catch(() => undefined);
    };

    sendHeartbeat();
    const intervalId = window.setInterval(sendHeartbeat, 30_000);
    document.addEventListener('visibilitychange', sendHeartbeat);
    window.addEventListener('focus', sendHeartbeat);

    return () => {
      disposed = true;
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', sendHeartbeat);
      window.removeEventListener('focus', sendHeartbeat);
    };
  }, [enabled]);
}
