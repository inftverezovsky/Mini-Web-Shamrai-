import { useCallback, useEffect, useRef } from 'react';
import {
  calculatePerformanceStats,
  mergeSignalHistory,
  sortBetHistory,
  sortFilterSignals,
  type BetHistoryOptions,
  type PerformanceStatsResult,
  type SignalFilterOptions,
  type SignalProcessResult,
  type SortableBetItem,
  type SortableSignalItem,
} from '../workers/dataTransforms';
import type { DataProcessorRequest, DataProcessorResponse } from '../workers/dataProcessor.worker';

export const DATA_PROCESSOR_WORKER_THRESHOLD = 150;

type WorkerJobType = DataProcessorRequest['type'];
type PendingJob = {
  resolve: (payload: unknown) => void;
  reject: (error: Error) => void;
};

function createJobId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `job-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useDataProcessorWorker() {
  const workerRef = useRef<Worker | null>(null);
  const pendingJobsRef = useRef<Map<string, PendingJob>>(new Map());

  const stopWorker = useCallback(() => {
    pendingJobsRef.current.forEach(({ reject }) => {
      reject(new Error('Data processor worker was stopped'));
    });
    pendingJobsRef.current.clear();
    workerRef.current?.terminate();
    workerRef.current = null;
  }, []);

  const ensureWorker = useCallback(() => {
    if (workerRef.current) return workerRef.current;

    const worker = new Worker(new URL('../workers/dataProcessor.worker.ts', import.meta.url), {
      type: 'module',
    });

    worker.onmessage = (event: MessageEvent<DataProcessorResponse>) => {
      const response = event.data;
      const pendingJob = pendingJobsRef.current.get(response.id);
      if (!pendingJob) return;

      pendingJobsRef.current.delete(response.id);
      if (response.type === 'worker.error') {
        pendingJob.reject(new Error(response.error));
        return;
      }
      pendingJob.resolve(response.payload);
    };

    worker.onerror = () => {
      stopWorker();
    };

    workerRef.current = worker;
    return worker;
  }, [stopWorker]);

  const runJob = useCallback(<TResult,>(
    type: WorkerJobType,
    payload: DataProcessorRequest['payload'],
  ) => new Promise<TResult>((resolve, reject) => {
    const id = createJobId();
    pendingJobsRef.current.set(id, {
      resolve: (result) => resolve(result as TResult),
      reject,
    });

    try {
      ensureWorker().postMessage({ id, type, payload } as DataProcessorRequest);
    } catch (error) {
      pendingJobsRef.current.delete(id);
      reject(error instanceof Error ? error : new Error('Failed to post worker message'));
    }
  }), [ensureWorker]);

  const mergeSignals = useCallback(async <TSignal extends SortableSignalItem>(
    current: readonly TSignal[],
    incoming: readonly TSignal[],
    limit = 160,
    threshold = DATA_PROCESSOR_WORKER_THRESHOLD,
  ) => {
    if (current.length + incoming.length < threshold) {
      return mergeSignalHistory(current, incoming, limit);
    }

    return runJob<TSignal[]>('signals.merge', {
      current: [...current],
      incoming: [...incoming],
      limit,
    });
  }, [runJob]);

  const processSignals = useCallback(async <TSignal extends SortableSignalItem>(
    items: readonly TSignal[],
    options: SignalFilterOptions = {},
    threshold = DATA_PROCESSOR_WORKER_THRESHOLD,
  ): Promise<SignalProcessResult<TSignal>> => {
    if (items.length < threshold) {
      return sortFilterSignals(items, options);
    }

    return runJob<SignalProcessResult<TSignal>>('signals.sortFilter', {
      items: [...items],
      options,
    });
  }, [runJob]);

  const processBetHistory = useCallback(async <TBet extends SortableBetItem>(
    items: readonly TBet[],
    options: BetHistoryOptions = {},
    threshold = DATA_PROCESSOR_WORKER_THRESHOLD,
  ) => {
    if (items.length < threshold) {
      return sortBetHistory(items, options);
    }

    return runJob<TBet[]>('bets.sortHistory', {
      items: [...items],
      options,
    });
  }, [runJob]);

  const calculateStats = useCallback(async <TBet extends SortableBetItem>(
    items: readonly TBet[],
    threshold = DATA_PROCESSOR_WORKER_THRESHOLD,
  ): Promise<PerformanceStatsResult> => {
    if (items.length < threshold) {
      return calculatePerformanceStats(items);
    }

    return runJob<PerformanceStatsResult>('bets.performanceStats', {
      items: [...items],
    });
  }, [runJob]);

  useEffect(() => stopWorker, [stopWorker]);

  return {
    mergeSignals,
    processSignals,
    processBetHistory,
    calculateStats,
    stopWorker,
  };
}
