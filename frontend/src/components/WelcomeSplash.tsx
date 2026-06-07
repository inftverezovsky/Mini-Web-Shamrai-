import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles } from 'lucide-react';

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
  const [watchProgress, setWatchProgress] = useState(0);

  const markReadyWhenBuffered = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      setMediaReady(true);
    }
  }, []);

  useEffect(() => {
    markReadyWhenBuffered();
  }, [markReadyWhenBuffered]);

  useEffect(() => {
    const minimumTimer = setTimeout(() => setMinimumWatchDone(true), 900);
    const logoTimer = setTimeout(() => {
      setLogoPlayed(true);
      setWatchProgress(1);
    }, 1800);
    const rescueTimer = setTimeout(() => {
      setMediaReady(true);
      setLogoPlayed(true);
      setWatchProgress(1);
    }, 3200);

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

    completedRef.current = true;
    const completeTimer = setTimeout(() => onIntroComplete?.(), 420);
    return () => clearTimeout(completeTimer);
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
        </div>

        <div className="welcome-splash__caption">
          <div className="flex items-center justify-center gap-1.5">
            <Sparkles className="iridescent-icon h-4 w-4" />
            <span>SHAMRAI</span>
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
