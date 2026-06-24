import {
  calculatePerformanceStats,
  mergeSignalHistory,
  sortBetHistory,
  sortFilterSignals,
  type BetHistoryOptions,
  type SignalFilterOptions,
  type SortableBetItem,
  type SortableSignalItem,
} from './dataTransforms';

export type DataProcessorRequest =
  | {
      id: string;
      type: 'signals.merge';
      payload: {
        current: SortableSignalItem[];
        incoming: SortableSignalItem[];
        limit?: number;
      };
    }
  | {
      id: string;
      type: 'signals.sortFilter';
      payload: {
        items: SortableSignalItem[];
        options?: SignalFilterOptions;
      };
    }
  | {
      id: string;
      type: 'bets.sortHistory';
      payload: {
        items: SortableBetItem[];
        options?: BetHistoryOptions;
      };
    }
  | {
      id: string;
      type: 'bets.performanceStats';
      payload: {
        items: SortableBetItem[];
      };
    };

export type DataProcessorResponse =
  | {
      id: string;
      type: 'worker.result';
      payload: unknown;
    }
  | {
      id: string;
      type: 'worker.error';
      error: string;
    };

const processorHandlers = {
  'signals.merge': (request: Extract<DataProcessorRequest, { type: 'signals.merge' }>) => (
    mergeSignalHistory(request.payload.current, request.payload.incoming, request.payload.limit)
  ),
  'signals.sortFilter': (request: Extract<DataProcessorRequest, { type: 'signals.sortFilter' }>) => (
    sortFilterSignals(request.payload.items, request.payload.options)
  ),
  'bets.sortHistory': (request: Extract<DataProcessorRequest, { type: 'bets.sortHistory' }>) => (
    sortBetHistory(request.payload.items, request.payload.options)
  ),
  'bets.performanceStats': (request: Extract<DataProcessorRequest, { type: 'bets.performanceStats' }>) => (
    calculatePerformanceStats(request.payload.items)
  ),
};

self.onmessage = (event: MessageEvent<DataProcessorRequest>) => {
  const request = event.data;

  try {
    const handler = processorHandlers[request.type] as (nextRequest: DataProcessorRequest) => unknown;
    const payload = handler(request);
    self.postMessage({
      id: request.id,
      type: 'worker.result',
      payload,
    } satisfies DataProcessorResponse);
  } catch (error) {
    self.postMessage({
      id: request.id,
      type: 'worker.error',
      error: error instanceof Error ? error.message : 'Worker processing failed',
    } satisfies DataProcessorResponse);
  }
};

export {};
