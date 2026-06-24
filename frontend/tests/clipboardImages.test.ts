import { describe, expect, it } from 'vitest';
import { getClipboardImageFile } from '../src/utils/clipboardImages';

function makeFile(name: string, type: string) {
  return new File(['payload'], name, { type, lastModified: 42 });
}

describe('getClipboardImageFile', () => {
  it('returns the first image file from clipboard items and skips text items', () => {
    const image = makeFile('ticket.webp', 'image/webp');

    const result = getClipboardImageFile({
      items: [
        { kind: 'string', type: 'text/plain', getAsFile: () => null },
        { kind: 'file', type: 'image/webp', getAsFile: () => image },
      ],
      files: [],
    });

    expect(result).toBe(image);
  });

  it('renames unnamed pasted images with a stable extension', () => {
    const image = makeFile('', 'image/png');

    const result = getClipboardImageFile({
      items: [
        { kind: 'file', type: 'image/png', getAsFile: () => image },
      ],
    }, { now: () => 123456 });

    expect(result?.name).toBe('clipboard-123456.png');
    expect(result?.type).toBe('image/png');
    expect(result?.lastModified).toBe(42);
  });

  it('falls back to clipboard files when items are unavailable', () => {
    const image = makeFile('coupon.jpg', 'image/jpeg');

    const result = getClipboardImageFile({
      files: [
        makeFile('notes.txt', 'text/plain'),
        image,
      ],
    });

    expect(result).toBe(image);
  });
});
