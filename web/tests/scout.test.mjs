import test from 'node:test';
import assert from 'node:assert/strict';
import { rank, profileWeights, validateBusiness, hasEnoughEvidence, clock } from '../public/scout.js';

const audiences = { dog: 'Dogs', bus: 'Buses', tourist: 'Tourists' };
const frame = (counts, segment_id = 'segment') => ({ counts, segment_id });
const corner = (camera, counts) => ({ camera, frames: counts.map((counts, index) => frame(counts, String(index))) });
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);

test('normalizes audience frame shares, with 75% core and 25% helpful influence', () => {
  const rows = rank([
    corner('Dog corner', [{ dog: 1 }, { dog: 1 }, { dog: 1 }, {}]),
    corner('Bus corner', [{ bus: 4, dog: 1 }, { bus: 4 }, { bus: 4 }, { bus: 4 }]),
  ], { dog: 1, bus: 0.5 }, { audiences });
  assert.equal(rows[0].corner.camera, 'Dog corner');
  close(rows[0].score, 0.75);
  close(rows[1].score, 0.5);
  assert.equal(rows[0].core_frames, 3);
  assert.equal(rows[1].core_frames, 1);
  assert.equal(hasEnoughEvidence(rows), true);
});

test('ranking uses presence per frame, not crowd counts or the total number of frames', () => {
  const rows = rank([
    corner('Short', [{ dog: 1 }, { dog: 1 }]),
    corner('Crowded', [{ dog: 100 }, {}, {}, {}]),
    corner('Long', [{ dog: 1 }, { dog: 1 }, { dog: 1 }, { dog: 1 }]),
  ], { dog: 1 });
  assert.deepEqual(rows.map(row => row.corner.camera), ['Long', 'Short', 'Crowded']);
  close(rows[0].score, 1);
  close(rows[1].score, 1);
  close(rows[2].score, 0.25);
});

test('removes baseline audiences and keys outside the exported taxonomy', () => {
  const corners = [corner('A', [{ dog: 1 }]), corner('B', [{ casual_adult: 9, mid_range_car: 9, invented: 9 }])];
  const rows = rank(corners, { dog: 1, casual_adult: 1, mid_range_car: 1, invented: 1 }, { audiences });
  close(rows[0].score, 1);
  close(rows[1].score, 0);
  assert.deepEqual(Object.keys(rows[0].seen), Object.keys(audiences));
});

test('an unobserved audience contributes zero without redistributing its weight', () => {
  const rows = rank([corner('A', [{ dog: 1 }])], { dog: 1, tourist: 1 });
  close(rows[0].score, 0.5);
});

test('no core evidence cannot recommend, even with helpful traffic', () => {
  const rows = rank([corner('A', [{ bus: 1 }, { bus: 1 }, { bus: 1 }])], { dog: 1, bus: 0.5 });
  close(rows[0].score, 0.25);
  assert.equal(rows[0].core_frames, 0);
  assert.deepEqual(rows[0].best, []);
  assert.equal(hasEnoughEvidence(rows), false);
  assert.equal(hasEnoughEvidence([]), false);
  assert.deepEqual(rank([], { dog: 1 }), []);
});

test('no observations produce finite zero scores, ordered by camera', () => {
  const rows = rank([corner('Zulu', []), corner('Alpha', [{}])], { dog: 1 });
  assert.deepEqual(rows.map(row => row.corner.camera), ['Alpha', 'Zulu']);
  assert.ok(rows.every(row => row.score === 0));
});

test('evidence uses capped weighted counts, contains core audiences, and is limited to three frames', () => {
  const rows = rank([corner('A', [
    { dog: 99 }, { dog: 4, bus: 2 }, { bus: 99 }, { dog: 1 }, { dog: 3 },
  ])], { dog: 1, bus: 0.5 });
  assert.deepEqual(rows[0].best.map(item => item.frame.segment_id), ['1', '0', '4']);
  assert.deepEqual(rows[0].best.map(item => item.value), [5, 4, 3]);
  assert.equal(rows[0].core_frames, 4);
});

test('equal evidence scores retain source-frame order; core counts are a union', () => {
  const rows = rank([corner('A', [{ dog: 2, tourist: 2 }, { dog: 4 }, { tourist: 4 }])], { dog: 1, tourist: 1 });
  assert.deepEqual(rows[0].best.map(item => item.frame.segment_id), ['0', '1', '2']);
  assert.equal(rows[0].core_frames, 3);
});

test('published thresholds and caps govern scoring and recommendation', () => {
  const rows = rank([corner('A', [{ dog: 7 }, { dog: 1 }])], { dog: 0.6 }, { coreThreshold: 0.5, cap: 2 });
  close(rows[0].score, 1);
  close(rows[0].best[0].value, 1.2);
  assert.equal(hasEnoughEvidence(rows), false);
  assert.equal(hasEnoughEvidence(rows, 2), true);
});

test('profile helpers preserve server weights and validate the published taxonomy', () => {
  const profile = { customers: 'People walking dogs.', weights: [{ key: 'dog', weight: 1, why: 'Named in your description' }, { key: 'bus', weight: 0, why: 'Optional' }] };
  assert.deepEqual(profileWeights(profile, audiences), { dog: 1, bus: 0 });
  assert.throws(() => profileWeights({ ...profile, weights: [...profile.weights, profile.weights[0]] }, audiences), /invalid/);
  assert.throws(() => profileWeights({ ...profile, weights: [{ key: 'invented', weight: 1, why: 'Unknown' }] }, audiences), /invalid/);
  assert.throws(() => profileWeights({ ...profile, weights: [{ key: 'dog', weight: 0.5, why: 'Helpful' }] }, audiences), /core audience/);
  assert.throws(() => profileWeights({ ...profile, weights: [{ key: 'dog', weight: Infinity, why: 'Bad' }] }, audiences), /invalid/);
});

test('business input and source clocks handle boundaries', () => {
  assert.equal(validateBusiness('  Dog grooming  '), 'Dog grooming');
  assert.throws(() => validateBusiness('  '), /3–200/);
  assert.throws(() => validateBusiness('x'.repeat(201)), /3–200/);
  assert.equal(validateBusiness('x'.repeat(200)).length, 200);
  assert.throws(() => validateBusiness('🏪🏪'), /3–200/);
  assert.equal([...validateBusiness('🏪'.repeat(200))].length, 200);
  assert.throws(() => validateBusiness('🏪'.repeat(201)), /3–200/);
  assert.equal(clock(65.8), '1:05');
  assert.equal(clock(0), '0:00');
});

const recording = (camera, { city = 'San Francisco', expected = 182, positive = [0, 1, 2], failed = [] } = {}) => ({
  camera, city, eligible: true, expected_seconds: expected,
  frames: Array.from({ length: 37 }, (_, index) => ({
    segment_id: String(index), start: 5 * index, end: Math.min(5 * (index + 1), 182),
    counts: failed.includes(index) ? null : { dog: Number(positive.includes(index)) },
  })),
});

test('duration weights shortened final segments and preserves the core observation count', () => {
  const rows = rank([recording('Short final positive', { positive: [0, 1, 36] }), recording('Full positives')], { dog: 1 });
  assert.equal(rows[0].corner.camera, 'Full positives');
  close(rows[0].score, 1);
  close(rows[1].score, 12 / 15);
  assert.equal(rows[1].core_frames, 3);
  assert.equal(rows[1].analyzed_seconds, 182);
  assert.equal(rows[1].coverage, 1);
  assert.equal(hasEnoughEvidence(rows), true);
});

test('unknown frames reduce coverage and three observations cannot replace three analyzed minutes', () => {
  const rows = rank([recording('Gaps', { failed: [4, 5] })], { dog: 1 });
  assert.equal(rows[0].frames, 35);
  assert.equal(rows[0].core_frames, 3);
  assert.equal(rows[0].analyzed_seconds, 172);
  close(rows[0].coverage, 172 / 182);
  assert.equal(rows[0].sufficient, false);
  assert.equal(hasEnoughEvidence(rows), false);
});

test('coverage must reach 90 percent and city comparisons use independent denominators', () => {
  const sf = recording('SF', { expected: 205 });
  const ny = recording('NYC', { city: 'new_york', positive: Array.from({ length: 37 }, (_, i) => i) });
  const rows = rank([sf, ny], { dog: 1 });
  assert.deepEqual(rows.map(row => row.corner.camera), ['SF']);
  close(rows[0].score, 1);
  assert.equal(hasEnoughEvidence(rows), false);
  const nyRows = rank([sf, ny], { dog: 1 }, { city: 'New York' });
  assert.deepEqual(nyRows.map(row => row.corner.camera), ['NYC']);
  assert.equal(hasEnoughEvidence(nyRows), true);
  assert.deepEqual(rank([sf, { ...ny, eligible: false }], { dog: 1 }, { city: 'New York' }), []);
});

test('production observations require valid intervals and reject invalid counts', () => {
  const invalid = recording('Invalid');
  invalid.frames[3].counts = { dog: 'unknown' };
  delete invalid.frames[4].end;
  invalid.frames[5].end = invalid.frames[5].start;
  const rows = rank([invalid], { dog: 1 });
  assert.equal(rows[0].frames, 34);
  assert.equal(rows[0].analyzed_seconds, 167);
  assert.equal(hasEnoughEvidence(rows), false);
});

const intervalRecording = (camera, intervals, expected = 180) => ({
  camera, eligible: true, expected_seconds: expected,
  frames: intervals.map(([start, end, dog], index) => ({
    segment_id: String(index), start, end, counts: dog === null ? null : { dog },
  })),
});

test('duplicate windows cannot supply duration or independent core observations', () => {
  for (const rest of [null, 0]) {
    const rows = rank([intervalRecording('Repeated window', [
      [0, 60, 1], [0, 60, 1], [0, 60, 1], [60, 120, rest], [120, 180, rest],
    ])], { dog: 1 });
    assert.equal(rows[0].analyzed_seconds, rest === null ? 60 : 180);
    close(rows[0].coverage, rest === null ? 1 / 3 : 1);
    assert.equal(rows[0].core_frames, 1);
    assert.equal(rows[0].seen.dog, 1);
    assert.equal(rows[0].best.length, 1);
    assert.equal(hasEnoughEvidence(rows), false);
  }
});

test('overlapping windows use unique positive time while unequal disjoint windows remain valid', () => {
  const rows = rank([
    intervalRecording('Overlapping', [[0, 90, 1], [60, 120, 1], [120, 181.5, 0]], 181.5),
    intervalRecording('Independent unequal', [[0, 11, 1], [11, 41, 1], [41, 181.5, 1]], 181.5),
  ], { dog: 1 });
  assert.equal(rows[0].corner.camera, 'Independent unequal');
  assert.equal(rows[0].analyzed_seconds, 181.5);
  assert.equal(rows[0].coverage, 1);
  assert.equal(rows[0].score, 1);
  assert.equal(rows[0].core_frames, 3);
  assert.equal(hasEnoughEvidence(rows), true);
  assert.equal(rows[1].analyzed_seconds, 181.5);
  close(rows[1].score, 120 / 181.5);
  assert.equal(rows[1].core_frames, 1);
  assert.equal(rows[1].sufficient, false);
});
