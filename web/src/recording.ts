export interface AudioMeasurements { duration: number; rms: number; clipped: number }
export function inspectAudio(samples: Float32Array, sampleRate: number): AudioMeasurements {
  let energy = 0;
  let clipped = 0;
  for (const value of samples) { energy += value * value; if (Math.abs(value) >= .99) clipped++; }
  return { duration: samples.length / sampleRate, rms: Math.sqrt(energy / (samples.length || 1)), clipped: clipped / (samples.length || 1) };
}
export function recordingProblem(audio: AudioMeasurements): string | null {
  if (audio.duration < 5) return 'Record at least 5 seconds. Read the whole passage at a comfortable pace.';
  if (audio.duration > 30) return 'Keep the recording under 30 seconds and try again.';
  if (audio.rms < .005) return 'This sample is too quiet. Move closer to your microphone and record again.';
  if (audio.clipped > .01) return 'This sample has clipping. Move farther from the microphone or lower its gain and record again.';
  return null;
}
export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  if (!Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 96000) throw new Error('Unsupported microphone sample rate. Use an 8-96 kHz audio device.');
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE'); text(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => { const value = Math.max(-1, Math.min(1, sample)); view.setInt16(44 + index * 2, value * (value < 0 ? 32768 : 32767), true); });
  return new Blob([buffer], { type: 'audio/wav' });
}
