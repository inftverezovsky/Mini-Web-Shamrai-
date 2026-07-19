import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('authenticated chat attachment media', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('fetches a chat attachment as a credentialed blob', async () => {
    vi.stubEnv('VITE_ENABLE_BEARER_AUTH_COMPAT', 'false');
    const expectedBlob = new Blob(['private-media'], { type: 'image/png' });
    const fetchMock = vi.fn().mockResolvedValue(new Response(expectedBlob, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const { fetchAuthenticatedChatAttachment } = await import('../src/features/chat/authenticatedAttachment');
    const blob = await fetchAuthenticatedChatAttachment('/chat/attachments/42/download');

    expect(blob.type).toBe('image/png');
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toContain('/api/chat/attachments/42/download');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: 'include' });
  });

  it('rejects non-chat and public-static attachment paths', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { fetchAuthenticatedChatAttachment } = await import('../src/features/chat/authenticatedAttachment');

    await expect(fetchAuthenticatedChatAttachment('/static/chat/private.png')).rejects.toThrow('Некорректный URL вложения');
    await expect(fetchAuthenticatedChatAttachment('https://example.test/private.png')).rejects.toThrow('Некорректный URL вложения');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
