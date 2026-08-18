import { describe, expect, it } from 'vitest';

import { parseStakeInput } from '../src/components/FlatStakeModal';

describe('flat stake input', () => {
  it('accepts supported Russian money formats', () => {
    expect(parseStakeInput('5000')).toBe('5000.00');
    expect(parseStakeInput('5 000')).toBe('5000.00');
    expect(parseStakeInput('5000,50')).toBe('5000.50');
    expect(parseStakeInput('5к')).toBe('5000.00');
    expect(parseStakeInput('5 тыс')).toBe('5000.00');
    expect(parseStakeInput('5,55 тыс')).toBe('5550.00');
    expect(parseStakeInput('100000000')).toBe('100000000.00');
  });

  it('rejects values outside the configured limits', () => {
    expect(parseStakeInput('0')).toBeNull();
    expect(parseStakeInput('-100')).toBeNull();
    expect(parseStakeInput('100000001')).toBeNull();
    expect(parseStakeInput('пять тысяч')).toBeNull();
  });
});
