import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, auditionAudio, errorMessage, measuredSeconds, safeMessage, studioRequest } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('broker requests', () => {
  it('retrieves generated WAV with the job in a protected POST body', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(new Blob(['wav']), { headers: { 'Content-Type': 'audio/wav' } }));
    vi.stubGlobal('fetch', fetch);
    expect((await auditionAudio('owned')).size).toBe(3);
    expect(fetch).toHaveBeenCalledWith('/api/audition/audio', expect.objectContaining({
      method: 'POST', body: '{"auditionId":"owned"}', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Voicebox-Studio': '1' },
    }));
  });
  it.each(['text/html', 'application/json'])('rejects %s instead of pretending it is generated audio', async (type) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private payload', { headers: { 'Content-Type': type } })));
    await expect(auditionAudio('owned')).rejects.toThrow('did not return a WAV');
  });
  it('rejects empty WAV payloads', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { headers: { 'Content-Type': 'audio/wav' } })));
    await expect(auditionAudio('owned')).rejects.toThrow('empty');
  });
  it('uploads WAV as multipart without overwriting its browser boundary', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const form = new FormData();
    form.set('audio', new Blob(['wav'], { type: 'audio/wav' }), 'reference.wav');
    await studioRequest('voices', { method: 'POST', body: form });
    expect(fetch).toHaveBeenCalledWith('/api/voices', expect.objectContaining({
      method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-Voicebox-Studio': '1' },
    }));
  });
  it.each(['PATCH', 'DELETE'] as const)('sends %s with explicit CSRF protection', async (method) => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await studioRequest('voices/one', { method, body: { confirm: true } });
    expect(fetch).toHaveBeenCalledWith('/api/voices/one', expect.objectContaining({
      method, body: '{"confirm":true}', headers: { 'X-Voicebox-Studio': '1', 'Content-Type': 'application/json' },
    }));
  });
  it('GETs status with same-origin credentials and no token', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ready: true })));
    vi.stubGlobal('fetch', fetch);
    await expect(api('status')).resolves.toEqual({ ready: true });
    expect(fetch).toHaveBeenCalledWith('/api/status', expect.objectContaining({ method: 'GET', credentials: 'same-origin', body: undefined }));
  });
  it('POSTs only the supplied JSON with the CSRF header', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await api('session', {});
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith('/api/session', expect.objectContaining({ method: 'POST', credentials: 'same-origin', body: '{}', headers: { 'Content-Type': 'application/json', 'X-Voicebox-Studio': '1' } }));
  });
  it('keeps cleanup identity in the body, never the URL', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    await api('session/end', { sessionId: 'owned-id' }, true);
    expect(fetch).toHaveBeenCalledWith('/api/session/end', expect.objectContaining({ keepalive: true, signal: undefined, body: '{"sessionId":"owned-id"}' }));
  });
  it('surfaces a safe conflict without retrying or stealing the session', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"message":"A session is active."}', { status: 409 }));
    vi.stubGlobal('fetch', fetch);
    await expect(api('session', {})).rejects.toMatchObject({ status: 409, message: 'A session is active.' });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('does not leak raw network errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('SECRET connection URL')));
    await expect(api('status')).rejects.toMatchObject({ status: 0, message: expect.not.stringContaining('SECRET') });
  });
  it('handles a non-JSON response without displaying its body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>private debug data</html>')));
    await expect(api('status')).rejects.toThrow('unreadable response');
  });
});

describe('safe messages and measured data', () => {
  it('redacts JWT-shaped values and service URLs', () => expect(safeMessage('Failed eyJabc.def.ghi at wss://private.test?token=secret', '')).toBe('Failed [redacted token] at [service address]'));
  it('ignores structured errors', () => expect(safeMessage({ secret: 'value' }, 'Safe fallback')).toBe('Safe fallback'));
  it('bounds server message length', () => expect(safeMessage('a'.repeat(1000), '')).toHaveLength(500));
  it('only renders trusted API errors verbatim', () => {
    expect(errorMessage(new Error('secret'), 'Safe fallback')).toBe('Safe fallback');
    expect(errorMessage(new ApiError(409, 'Owned elsewhere'), 'Fallback')).toBe('Owned elsewhere');
  });
  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY, -1])('does not invent a measurement for %s', (value) => expect(measuredSeconds(value)).toBe('Not measured'));
  it('preserves measured zero', () => expect(measuredSeconds(0)).toBe('0.00 s'));
  it('labels a real measurement in seconds', () => expect(measuredSeconds(1.234)).toBe('1.23 s'));
});
