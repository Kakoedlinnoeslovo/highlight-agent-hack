import test from 'node:test';
import assert from 'node:assert/strict';
import { coreAudiences, proofSamples, proofFrames, initialProofFrame, validFocus, sampleClock } from '../public/proof.js';
import { rank } from '../public/scout.js';

const crop = { x: 0.125, y: 0.5, width: 0.5, height: 0.5 };
const frame = (id, sampledAt, counts = {}, extra = {}) => ({
  segment_id: id, sampledAt, start: sampledAt - 2.5, counts, ...extra,
});

test('core audiences use descending weights, preserve profile order on ties, and include the threshold', () => {
  assert.deepEqual(coreAudiences({ bus: 0.5, tourist: 0.8, dog: 1, cyclist: 1, family: 0.75 }),
    ['dog', 'cyclist', 'tourist', 'family']);
  assert.deepEqual(coreAudiences({ bus: 0.5, dog: 0.7, cyclist: 0.7 }, 0.5),
    ['dog', 'cyclist', 'bus']);
  assert.deepEqual(coreAudiences({ zero: 0, helpful: 0.5, string: '1', infinity: Infinity, high: 2 }), []);
  assert.deepEqual(coreAudiences({}), []);
});

test('all 28 positive samples remain inspectable while ranking evidence stays capped at three', () => {
  const corner = {
    camera: 'Luxury corner',
    frames: Array.from({ length: 36 }, (_, index) =>
      frame(`sample-${index}`, index * 5 + 2.5, { premium_car: index < 28 ? 4 : 0 })),
  };
  const [row] = rank([corner], { premium_car: 1 });
  const matches = proofFrames(corner, 'premium_car');
  assert.equal(row.best.length, 3);
  assert.equal(matches.length, 28);
  assert.equal(matches.length, row.seen.premium_car);
  assert.equal(matches[27], corner.frames[27]);
  assert.deepEqual(matches.map(item => item.segment_id), corner.frames.slice(0, 28).map(item => item.segment_id));
  assert.equal(corner.frames.length, 36);
});

test('presence counts sampled frames, retains each corner denominator, and ignores absent counts', () => {
  const corners = [36, 45].map((length, cornerIndex) => ({
    camera: `Camera ${cornerIndex}`,
    frames: Array.from({ length }, (_, index) => frame(`${cornerIndex}-${index}`, index * 5 + 2.5,
      index < 3 ? { dog: index + 1 } : {}, index === 4 ? { counts: undefined } : {})),
  }));
  for (const corner of corners) {
    const matches = proofFrames(corner, 'dog');
    assert.equal(matches.length, 3);
    assert.ok(matches.every(item => item.counts.dog > 0));
  }
  assert.deepEqual(corners.map(corner => corner.frames.length), [36, 45]);
  assert.deepEqual(proofFrames({ frames: [frame('zero', 2.5, { dog: 0 }),
    frame('negative', 7.5, { dog: -1 }), frame('string', 12.5, { dog: '1' })] }, 'dog'), []);
});

test('ranking and the inspector share independent valid samples for duplicate and overlapping windows', () => {
  const scenarios = [
    { intervals: [[0, 60, 1], [0, 60, 1], [0, 60, 1], [60, 180, null]],
      samples: 1, positives: 1, seconds: 60 },
    { intervals: [[0, 90, 1], [60, 120, 1], [120, 180, 0]],
      samples: 2, positives: 1, seconds: 180 },
    { intervals: [[0, 120, 1], [0, 60, 0], [60, 120, 0], [120, 180, 1]],
      samples: 3, positives: 1, seconds: 180 },
  ];
  for (const scenario of scenarios) {
    const corner = { camera: 'Overlapping recording', eligible: true, expected_seconds: 180,
      frames: scenario.intervals.map(([start, end, dog], index) => ({
        segment_id: String(index), start, end, sampledAt: (start + end) / 2,
        counts: dog === null ? null : { dog },
      })) };
    const originalFrames = [...corner.frames];
    const [row] = rank([corner], { dog: 1 });
    const samples = proofSamples(row.corner);
    const positives = proofFrames(row.corner, 'dog');
    assert.equal(samples.length, scenario.samples);
    assert.equal(samples.length, row.frames);
    assert.equal(positives.length, scenario.positives);
    assert.equal(positives.length, row.seen.dog);
    assert.equal(positives.length, row.core_frames);
    assert.equal(samples.filter(item => item.counts.dog > 0).length, positives.length);
    assert.ok(positives.every(item => samples.includes(item)));
    assert.ok(row.best.every(item => positives.includes(item.frame)));
    assert.ok(samples.includes(initialProofFrame(row.corner, 'dog')));
    assert.equal(row.analyzed_seconds, scenario.seconds);
    assert.equal(row.coverage, scenario.seconds / 180);
    assert.equal(row.sufficient, false);
    assert.deepEqual(corner.frames, originalFrames);
  }
});

test('proof matches sort by sampledAt with start fallback, retain stable ties, and do not reorder source frames', () => {
  const frames = [
    frame('late', 12.5, { dog: 1 }),
    frame('fallback', undefined, { dog: 1 }, { start: 7.5 }),
    frame('same-time', 7.5, { dog: 1 }),
    frame('early', 2.5, { dog: 1 }),
    frame('zero-time', 0, { dog: 1 }),
    frame('negative', 5, {}),
  ];
  const originalOrder = [...frames];
  const selected = proofFrames({ frames }, 'dog');
  assert.deepEqual(selected.map(item => item.segment_id),
    ['zero-time', 'early', 'fallback', 'same-time', 'late']);
  assert.equal(selected[2], frames[1]);
  assert.deepEqual(frames, originalOrder);
});

test('initial selection uses the first chronological reviewed positive for the selected audience', () => {
  const first = frame('first-positive', 2.5, { dog: 1 });
  const reviewed = frame('reviewed-positive', 7.5, { dog: 1 }, { focus: { dog: crop } });
  const later = frame('later-reviewed', 12.5, { dog: 1 }, { focus: { dog: crop } });
  const negativeWithFocus = frame('negative', 0, {}, { focus: { dog: crop } });
  const wrongAudience = frame('other-focus', 5, { dog: 1 }, { focus: { bus: crop } });
  const corner = { frames: [later, reviewed, wrongAudience, negativeWithFocus, first] };
  assert.equal(initialProofFrame(corner, 'dog'), reviewed);
  assert.equal(initialProofFrame({ frames: [wrongAudience, first] }, 'dog'), first);
});

test('invalid focus cannot displace a positive and a zero state uses an actual chronological sample', () => {
  const invalid = frame('invalid-focus', 7.5, { dog: 1 }, { focus: { dog: { ...crop, width: 2 } } });
  const first = frame('first-positive', 2.5, { dog: 1 });
  assert.equal(initialProofFrame({ frames: [invalid, first] }, 'dog'), first);
  const helpful = frame('helpful', 7.5, { bus: 1 }, { focus: { bus: crop } });
  const negative = frame('negative', 2.5);
  assert.deepEqual(proofFrames({ frames: [helpful, negative] }, 'dog'), []);
  assert.equal(initialProofFrame({ frames: [helpful, negative] }, 'dog'), negative);
  assert.equal(initialProofFrame({ frames: [] }, 'dog'), null);
  assert.equal(initialProofFrame(null, 'dog'), null);
});

test('focus accepts only finite normalized rectangles inside the full image', () => {
  const original = { ...crop, note: 'reviewed manually' };
  assert.deepEqual(validFocus({ focus: { dog: original } }, 'dog'), crop);
  assert.deepEqual(validFocus({ focus: { dog: { x: 0, y: 0, width: 1, height: 1 } } }, 'dog'),
    { x: 0, y: 0, width: 1, height: 1 });
  for (const invalid of [null, [], {},
    { ...crop, x: -0.1 }, { ...crop, y: -0.1 }, { ...crop, width: 0 }, { ...crop, height: 0 },
    { ...crop, width: -0.1 }, { ...crop, height: -0.1 }, { ...crop, x: 0.6 }, { ...crop, y: 0.6 },
    { ...crop, x: NaN }, { ...crop, width: Infinity }, { ...crop, height: '0.5' },
  ]) assert.equal(validFocus({ focus: { dog: invalid } }, 'dog'), null, JSON.stringify(invalid));
  assert.equal(validFocus({ focus: { bus: crop } }, 'dog'), null);
  assert.equal(validFocus({}, 'dog'), null);
  assert.equal(validFocus(null, 'dog'), null);
});

test('sample clocks retain tenths and carry rounded seconds across minute boundaries', () => {
  for (const [seconds, expected] of [[2.5, '0:02.5'], [5, '0:05.0'], [0, '0:00.0'],
    [65.8, '1:05.8'], [59.96, '1:00.0'], [602.5, '10:02.5'], [-5, '0:00.0']]) {
    assert.equal(sampleClock(seconds), expected);
  }
});
