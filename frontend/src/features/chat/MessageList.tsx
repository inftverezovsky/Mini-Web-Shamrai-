import React, { useCallback, useEffect, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

interface MessageListItem {
  key: string;
  element: React.ReactNode;
}

interface MessageListProps {
  items: MessageListItem[];
  loading?: boolean;
  empty?: React.ReactNode;
  className?: string;
  active?: boolean;
  hasMore?: boolean;
  loadingMore?: boolean;
  loadMoreLabel?: string;
  onLoadMore?: () => void;
}

function MessageList({
  items,
  loading = false,
  empty,
  className = '',
  active = true,
  hasMore = false,
  loadingMore = false,
  loadMoreLabel = 'Показать ранние сообщения',
  onLoadMore,
}: MessageListProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const previousScrollRef = useRef<{
    firstKey?: string;
    lastKey?: string;
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 132,
    overscan: 1,
  });

  const requestOlderMessages = useCallback(() => {
    if (!hasMore || loadingMore || !onLoadMore) return;
    onLoadMore();
  }, [hasMore, loadingMore, onLoadMore]);

  const handleScroll = useCallback(() => {
    const scrollElement = parentRef.current;
    if (!scrollElement || scrollElement.scrollTop > 80) return;
    if (items.length > 0) {
      previousScrollRef.current = {
        firstKey: items[0]?.key,
        lastKey: items[items.length - 1]?.key,
        scrollHeight: scrollElement.scrollHeight,
        scrollTop: scrollElement.scrollTop,
      };
    }
    requestOlderMessages();
  }, [items, requestOlderMessages]);

  useEffect(() => {
    const scrollElement = parentRef.current;
    if (!scrollElement) return undefined;
    if (!active) return undefined;
    if (items.length === 0) {
      previousScrollRef.current = null;
      return undefined;
    }

    const firstKey = items[0]?.key;
    const lastKey = items[items.length - 1]?.key;
    const previousScroll = previousScrollRef.current;
    const prependedMessages = Boolean(
      previousScroll
      && previousScroll.firstKey !== firstKey
      && previousScroll.lastKey === lastKey,
    );
    const shouldStickToBottom = !previousScroll || previousScroll.lastKey !== lastKey;

    const frame = window.requestAnimationFrame(() => {
      if (prependedMessages && previousScroll) {
        const scrollDelta = scrollElement.scrollHeight - previousScroll.scrollHeight;
        scrollElement.scrollTop = previousScroll.scrollTop + scrollDelta;
      } else if (shouldStickToBottom) {
        scrollElement.scrollTo({ top: scrollElement.scrollHeight, behavior: 'auto' });
      }
      previousScrollRef.current = {
        firstKey,
        lastKey,
        scrollHeight: scrollElement.scrollHeight,
        scrollTop: scrollElement.scrollTop,
      };
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, items]);

  return (
    <div
      ref={parentRef}
      onScroll={handleScroll}
      className={`web-bot-chat__list chat-cover-backdrop min-h-0 min-w-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4 sm:py-4 ${className}`}
    >
      {(hasMore || loadingMore) && (
        <div className="mb-3 flex justify-center">
          <button
            type="button"
            onClick={requestOlderMessages}
            disabled={loadingMore}
            className="min-h-[36px] rounded-xl border border-white/10 bg-slate-950/65 px-3 py-2 text-xs font-black text-slate-200 transition hover:border-cyan-300/25 hover:bg-white/[0.07] disabled:cursor-wait disabled:opacity-60"
          >
            {loadingMore ? 'Загружаем историю...' : loadMoreLabel}
          </button>
        </div>
      )}

      {loading && (
        <div className="flex min-h-[118px] w-full items-center justify-center gap-2 py-8 text-xs font-bold text-slate-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-cyan-300/25 border-t-cyan-200" />
          <span>Синхронизация...</span>
        </div>
      )}

      {!loading && items.length === 0 && empty ? (
        <div className="grid min-h-[118px] w-full place-items-center">
          {empty}
        </div>
      ) : null}

      {items.length > 0 && (
        <div
          className="relative w-full"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const item = items[virtualItem.index];
            return (
              <div
                key={item.key}
                ref={virtualizer.measureElement}
                data-index={virtualItem.index}
                className="web-bot-chat__virtual-row absolute left-0 top-0 w-full pb-3"
                style={{ transform: `translateY(${virtualItem.start}px)` }}
              >
                {item.element}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default React.memo(MessageList);
