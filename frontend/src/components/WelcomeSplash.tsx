import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';
import LogoText from './LogoText';
import { getTelegramWebApp, hasTelegramLaunchParams } from '../utils/telegramSdk';

interface WelcomeSplashProps {
  appReady?: boolean;
  leaving?: boolean;
  onIntroComplete?: () => void;
}

export default function WelcomeSplash({
  appReady = false,
  leaving = false,
  onIntroComplete,
}: WelcomeSplashProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const completedRef = useRef(false);
  const [mediaReady, setMediaReady] = useState(false);
  const [logoPlayed, setLogoPlayed] = useState(false);
  const [minimumWatchDone, setMinimumWatchDone] = useState(false);
  const [playLogoVideo, setPlayLogoVideo] = useState(false);
  const [watchProgress, setWatchProgress] = useState(0);

  const markReadyWhenBuffered = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      setMediaReady(true);
    }
  }, []);

  useEffect(() => {
    const connection = (navigator as any).connection as
      | { saveData?: boolean; effectiveType?: string }
      | undefined;
    const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const slowConnection = ['slow-2g', '2g'].includes(connection?.effectiveType || '');
    const shouldPlayVideo =
      (Boolean(getTelegramWebApp()) || hasTelegramLaunchParams()) &&
      !prefersReducedMotion &&
      !connection?.saveData &&
      !slowConnection;

    if (shouldPlayVideo) {
      setPlayLogoVideo(true);
      return;
    }

    setMediaReady(true);
    setLogoPlayed(true);
    setWatchProgress(1);
  }, []);

  useEffect(() => {
    if (!playLogoVideo) return;
    markReadyWhenBuffered();
  }, [markReadyWhenBuffered, playLogoVideo]);

  useEffect(() => {
    const minimumTimer = setTimeout(() => setMinimumWatchDone(true), 180);
    const logoTimer = setTimeout(() => {
      setLogoPlayed(true);
      setWatchProgress(1);
    }, 420);
    const rescueTimer = setTimeout(() => {
      setMediaReady(true);
      setLogoPlayed(true);
      setWatchProgress(1);
    }, 900);

    return () => {
      clearTimeout(minimumTimer);
      clearTimeout(logoTimer);
      clearTimeout(rescueTimer);
    };
  }, []);

  useEffect(() => {
    if (!appReady || !mediaReady || !logoPlayed || !minimumWatchDone || completedRef.current) {
      return;
    }

    let active = true;
    const completeTimer = setTimeout(() => {
      if (!active || completedRef.current) return;
      completedRef.current = true;
      onIntroComplete?.();
    }, 80);

    return () => {
      active = false;
      clearTimeout(completeTimer);
    };
  }, [appReady, logoPlayed, mediaReady, minimumWatchDone, onIntroComplete]);

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    setWatchProgress(Math.min(video.currentTime / video.duration, 1));
  };

  const handleVideoError = () => {
    setMediaReady(true);
    setLogoPlayed(true);
    setWatchProgress(1);
  };

  const statusText = useMemo(() => {
    if (!mediaReady) return 'Заряжаем логотип';
    if (!logoPlayed) return '';
    if (!appReady) return 'Синхронизация mini app';
    return 'Открываем доступ';
  }, [appReady, logoPlayed, mediaReady]);

  const displayProgress = useMemo(() => {
    if (!mediaReady) return 14;
    if (!logoPlayed) return Math.min(92, 24 + watchProgress * 68);
    if (!appReady) return 96;
    return 100;
  }, [appReady, logoPlayed, mediaReady, watchProgress]);

  return (
    <div className={`welcome-splash ${leaving ? 'welcome-splash--leaving' : ''}`}>
      <div className="welcome-splash__aura" aria-hidden="true" />
      <div className="welcome-splash__stars" aria-hidden="true" />
      <div className="welcome-splash__gate" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className="welcome-splash__scan" aria-hidden="true" />
      <div className="welcome-splash__floor" aria-hidden="true" />

      <div className="welcome-splash__content">
        <div className="welcome-splash__logo-shell" aria-hidden="true" />
        <div className="welcome-splash__logo shimmer-border">
          {playLogoVideo ? (
            <video
              ref={videoRef}
              className="welcome-splash__video"
              src="/brand-logo.mp4"
              poster="/brand-logo-poster.jpg"
              autoPlay
              muted
              playsInline
              preload="metadata"
              onLoadedMetadata={markReadyWhenBuffered}
              onLoadedData={markReadyWhenBuffered}
              onProgress={markReadyWhenBuffered}
              onCanPlay={markReadyWhenBuffered}
              onCanPlayThrough={markReadyWhenBuffered}
              onTimeUpdate={handleTimeUpdate}
              onEnded={() => {
                setMediaReady(true);
                setLogoPlayed(true);
                setWatchProgress(1);
              }}
              onError={handleVideoError}
            />
          ) : (
            <img
              className="welcome-splash__video"
              src="/brand-logo-poster.jpg"
              alt=""
              loading="eager"
              decoding="async"
            />
          )}
        </div>

        <div className="welcome-splash__caption">
          <div className="flex items-center justify-center gap-1.5" aria-hidden="true">
            <Sparkles className="iridescent-icon h-4 w-4" />
            <LogoText className="welcome-splash__wordmark" ariaLabel="SHAMRAI" width={190} height={58} />
          </div>
          <p>{statusText}</p>
        </div>

        <div
          className="welcome-splash__loader"
          aria-hidden="true"
          style={{ '--splash-progress': `${displayProgress}%` } as React.CSSProperties}
        >
          <span />
        </div>
      </div>
    </div>
  );
}
