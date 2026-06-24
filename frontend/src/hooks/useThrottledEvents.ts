import { useCallback, useEffect, useRef } from 'react';

type Callback<TArgs extends unknown[]> = (...args: TArgs) => void;

function useLatestCallback<TArgs extends unknown[]>(callback: Callback<TArgs>) {
  const callbackRef = useRef(callback);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  return callbackRef;
}

export function useDebouncedCallback<TArgs extends unknown[]>(
  callback: Callback<TArgs>,
  delayMs: number,
) {
  const callbackRef = useLatestCallback(callback);
  const pendingArgsRef = useRef<TArgs | null>(null);
  const timerRef = useRef<number | undefined>();

  const cancel = useCallback(() => {
    if (timerRef.current === undefined) return;
    window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
  }, []);

  const flush = useCallback(() => {
    cancel();
    if (!pendingArgsRef.current) return;
    const args = pendingArgsRef.current;
    pendingArgsRef.current = null;
    callbackRef.current(...args);
  }, [callbackRef, cancel]);

  const run = useCallback((...args: TArgs) => {
    pendingArgsRef.current = args;
    cancel();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = undefined;
      flush();
    }, delayMs);
  }, [cancel, delayMs, flush]);

  useEffect(() => () => {
    cancel();
    pendingArgsRef.current = null;
  }, [cancel]);

  return { run, flush, cancel };
}

export function useThrottledCallback<TArgs extends unknown[]>(
  callback: Callback<TArgs>,
  delayMs: number,
) {
  const callbackRef = useLatestCallback(callback);
  const timerRef = useRef<number | undefined>();
  const lastRunRef = useRef(0);
  const trailingArgsRef = useRef<TArgs | null>(null);

  const flush = useCallback(() => {
    timerRef.current = undefined;
    if (!trailingArgsRef.current) return;
    const nextArgs = trailingArgsRef.current;
    trailingArgsRef.current = null;
    lastRunRef.current = Date.now();
    callbackRef.current(...nextArgs);
  }, [callbackRef]);

  const throttled = useCallback((...args: TArgs) => {
    const elapsed = Date.now() - lastRunRef.current;
    if (elapsed >= delayMs) {
      if (timerRef.current !== undefined) {
        window.clearTimeout(timerRef.current);
        timerRef.current = undefined;
      }
      trailingArgsRef.current = null;
      lastRunRef.current = Date.now();
      callbackRef.current(...args);
      return;
    }

    trailingArgsRef.current = args;
    if (timerRef.current !== undefined) return;
    timerRef.current = window.setTimeout(flush, delayMs - elapsed);
  }, [callbackRef, delayMs, flush]);

  useEffect(() => () => {
    if (timerRef.current !== undefined) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    trailingArgsRef.current = null;
  }, []);

  return throttled;
}

export function useThrottledEventBuffer<TEvent>(
  onFlush: (events: TEvent[]) => void,
  delayMs = 400,
  maxBufferSize = 100,
) {
  const flushRef = useLatestCallback(onFlush);
  const bufferRef = useRef<TEvent[]>([]);
  const timerRef = useRef<number | undefined>();

  const flushNow = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }

    if (!bufferRef.current.length) return;
    const events = bufferRef.current;
    bufferRef.current = [];
    flushRef.current(events);
  }, [flushRef]);

  const enqueue = useCallback((event: TEvent) => {
    const nextBuffer = [...bufferRef.current, event];
    bufferRef.current = nextBuffer.length > maxBufferSize ? nextBuffer.slice(-maxBufferSize) : nextBuffer;
    if (timerRef.current !== undefined) return;
    timerRef.current = window.setTimeout(flushNow, delayMs);
  }, [delayMs, flushNow, maxBufferSize]);

  const clear = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
    bufferRef.current = [];
  }, []);

  useEffect(() => clear, [clear]);

  return { enqueue, flushNow, clear };
}
