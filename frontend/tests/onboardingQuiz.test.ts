import { describe, expect, it } from 'vitest';
import { toggleBookmakerCodeSelection } from '../src/utils/bookmakerSelection';

describe('toggleBookmakerCodeSelection', () => {
  it('adds an unselected bookmaker to the end of the selection', () => {
    expect(toggleBookmakerCodeSelection(['fonbet', 'pari'], 'betboom')).toEqual([
      'fonbet',
      'pari',
      'betboom',
    ]);
  });

  it('removes a selected bookmaker without mutating the current selection', () => {
    const selected = ['fonbet', 'pari', 'betboom'];

    expect(toggleBookmakerCodeSelection(selected, 'pari')).toEqual(['fonbet', 'betboom']);
    expect(selected).toEqual(['fonbet', 'pari', 'betboom']);
  });
});
