import { describe, expect, it } from 'vitest';
import { encodeWav, inspectAudio, recordingProblem } from './recording';

describe('private voice PCM recordings', () => {
  it('encodes standard mono 16-bit WAV at the actual sample rate', async () => {
    const blob = encodeWav(new Float32Array([-1, 0, 1]), 24000);
    const view = new DataView(await blob.arrayBuffer());
    expect(blob.type).toBe('audio/wav');
    expect(blob.size).toBe(50);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(24000);
    expect(view.getInt16(44, true)).toBe(-32768);
    expect(view.getInt16(48, true)).toBe(32767);
  });
  it('measures silence and clipping from samples, never elapsed wall time', () => {
    expect(inspectAudio(new Float32Array(24000), 24000)).toMatchObject({ duration: 1, rms: 0, clipped: 0 });
    expect(inspectAudio(new Float32Array(24000).fill(1), 24000).clipped).toBe(1);
  });
  it('rejects short, long, silent and clipped audio with actionable messages', () => {
    expect(recordingProblem({ duration: 4, rms: .1, clipped: 0 })).toContain('5');
    expect(recordingProblem({ duration: 31, rms: .1, clipped: 0 })).toContain('30');
    expect(recordingProblem({ duration: 8, rms: 0, clipped: 0 })).toContain('quiet');
    expect(recordingProblem({ duration: 8, rms: .5, clipped: .1 })).toContain('clipping');
    expect(recordingProblem({ duration: 8, rms: .1, clipped: 0 })).toBeNull();
  });
  it('rejects unsupported sample rates rather than lying in the WAV header', () => {
    expect(() => encodeWav(new Float32Array(1), 192000)).toThrow('sample rate');
  });
});
