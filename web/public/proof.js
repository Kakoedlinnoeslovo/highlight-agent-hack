import { scoringFrames } from './scout.js';

/** Select evidence from ranking's independent observations, beyond its best three. */
export function coreAudiences(weights, threshold = 0.75) {
  return Object.entries(weights ?? {})
    .filter(([, weight]) => typeof weight === 'number' && Number.isFinite(weight)
      && weight >= threshold && weight <= 1)
    .map(([key, weight], index) => ({ key, weight, index }))
    .sort((a, b) => b.weight - a.weight || a.index - b.index)
    .map(({ key }) => key);
}

const frameTime = frame => Number.isFinite(frame.sampledAt) ? frame.sampledAt
  : Number.isFinite(frame.seconds) ? frame.seconds
    : Number.isFinite(frame.start_seconds ?? frame.start) ? (frame.start_seconds ?? frame.start) : 0;

export function proofSamples(corner) {
  return scoringFrames(corner ?? {})
    .map((frame, index) => ({ frame, index }))
    .sort((a, b) => frameTime(a.frame) - frameTime(b.frame) || a.index - b.index)
    .map(({ frame }) => frame);
}

/** Returns original frame objects so selection can use their stable segment_id. */
export function proofFrames(corner, audience) {
  return proofSamples(corner).filter(frame => {
    const count = frame.counts?.[audience];
    return typeof count === 'number' && Number.isFinite(count) && count > 0;
  });
}

/** A manually reviewed detail crop; this does not infer an object location. */
export function validFocus(frame, audience) {
  const focus = frame?.focus?.[audience];
  if (!focus || typeof focus !== 'object' || Array.isArray(focus)) return null;
  const { x, y, width, height } = focus;
  if (![x, y, width, height].every(value => typeof value === 'number' && Number.isFinite(value))
      || x < 0 || y < 0 || width <= 0 || height <= 0
      || x + width > 1 || y + height > 1) return null;
  return { x, y, width, height };
}

/** Prefer a reviewed positive sample, then a positive, then an honest zero-state sample. */
export function initialProofFrame(corner, audience) {
  const matches = proofFrames(corner, audience);
  return matches.find(frame => validFocus(frame, audience))
    ?? matches[0] ?? proofSamples(corner)[0] ?? null;
}

/** Keep the half-second sampling position visible, including on whole seconds. */
export function sampleClock(seconds) {
  const value = Number(seconds);
  const tenths = Number.isFinite(value) ? Math.round(Math.max(0, value) * 10) : 0;
  const minutes = Math.floor(tenths / 600);
  const wholeSeconds = Math.floor(tenths / 10) % 60;
  return `${minutes}:${String(wholeSeconds).padStart(2, '0')}.${tenths % 10}`;
}
