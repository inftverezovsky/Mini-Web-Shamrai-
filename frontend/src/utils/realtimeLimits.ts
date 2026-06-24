export const MAX_REALTIME_ITEMS = 100;
export const MAX_SEEN_REALTIME_IDS = 160;

export function limitRecent<T>(items: readonly T[], limit = MAX_REALTIME_ITEMS) {
  return limit > 0 && items.length > limit ? items.slice(-limit) : [...items];
}

export function rememberRecentId<T>(seenIds: Set<T>, id: T, limit = MAX_SEEN_REALTIME_IDS) {
  seenIds.add(id);
  while (seenIds.size > limit) {
    const oldestId = seenIds.values().next().value;
    if (oldestId === undefined) break;
    seenIds.delete(oldestId);
  }
}
