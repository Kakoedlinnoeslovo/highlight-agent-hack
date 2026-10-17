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
