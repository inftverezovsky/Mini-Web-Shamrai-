import React, { useEffect, useRef } from 'react';
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
}

function MessageList({
  items,
  loading = false,
  empty,
  className = '',
  active = true,
}: MessageListProps) {
  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 132,
    overscan: 1,
  });

  useEffect(() => {
    const scrollElement = parentRef.current;
    if (!active || !scrollElement || items.length === 0) return undefined;
    const frame = window.requestAnimationFrame(() => {
      scrollElement.scrollTo({ top: scrollElement.scrollHeight, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, items.length]);

  return (
    <div ref={parentRef} className={`web-bot-chat__list min-h-0 min-w-0 flex-1 overflow-y-auto px-3 py-3 sm:px-4 sm:py-4 ${className}`}>
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
