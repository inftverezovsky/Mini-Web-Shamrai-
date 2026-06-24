export function toggleBookmakerCodeSelection(
  currentCodes: readonly string[],
  bookmakerCode: string,
): string[] {
  return currentCodes.includes(bookmakerCode)
    ? currentCodes.filter((code) => code !== bookmakerCode)
    : [...currentCodes, bookmakerCode];
}
