import { useEffect, useSyncExternalStore } from 'react';

const listeners = new Set<() => void>();
let activeOverlayCount = 0;

function emitOverlayChange() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return activeOverlayCount > 0;
}

function pushGlassOverlay() {
  activeOverlayCount += 1;
  emitOverlayChange();

  return () => {
    activeOverlayCount = Math.max(0, activeOverlayCount - 1);
    emitOverlayChange();
  };
}

export function useGlassOverlayGuard(active: boolean) {
  useEffect(() => {
    if (!active) return undefined;
    return pushGlassOverlay();
  }, [active]);
}

export function useGlassOverlayActive() {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
