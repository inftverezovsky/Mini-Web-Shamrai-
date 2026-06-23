import React, { useEffect } from 'react';

import {
  getWebPushReadiness,
  registerPwaServiceWorker,
} from '../utils/webPush';
import { hasTelegramLaunchParams, isTelegramMiniApp } from '../utils/telegramSdk';

interface PwaPushGateProps {
  enabled: boolean;
  children: React.ReactNode;
}

export default function PwaPushGate({ enabled, children }: PwaPushGateProps) {
  const runsInTelegramMiniApp = isTelegramMiniApp() || hasTelegramLaunchParams();

  useEffect(() => {
    if (!enabled || runsInTelegramMiniApp) return;
    void registerPwaServiceWorker()
      .then(() => getWebPushReadiness())
      .catch(() => undefined);
  }, [enabled, runsInTelegramMiniApp]);

  return <>{children}</>;
}
