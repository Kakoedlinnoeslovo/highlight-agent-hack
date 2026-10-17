/** Deterministic scoring over the published, cached frame observations. */
export const CORE = 0.75;
export const CAP = 4;
export const MIN_FRAMES = 3;
const BASELINE = new Set(['casual_adult', 'mid_range_car']);

export function validateBusiness(value) {
  const business = String(value ?? '').trim();
  const length = [...business].length;
  if (length < 3 || length > 200) {
    throw new Error('Describe the business in 3–200 characters.');
  }
  return business;
}

/** The server has already applied explicit audience names from the description. */
export function profileWeights(profile, audiences, coreThreshold = CORE) {
  if (!profile || typeof profile.customers !== 'string' || !profile.customers.trim()
      || !Array.isArray(profile.weights) || !profile.weights.length) {
    throw new Error('The audience profile was incomplete. Please try again.');
  }
  const weights = {};
  for (const item of profile.weights) {
    if (!item || !Object.hasOwn(audiences, item.key) || BASELINE.has(item.key)
        || Object.hasOwn(weights, item.key) || typeof item.weight !== 'number'
        || !Number.isFinite(item.weight) || item.weight < 0 || item.weight > 1
        || typeof item.why !== 'string') {
      throw new Error('The audience profile was invalid. Please try again.');
    }
    weights[item.key] = item.weight;
  }
  if (!Object.values(weights).some(weight => weight >= coreThreshold)) {
    throw new Error('The profile did not identify a core audience. Please try again.');
  }
  return weights;
}

/** Matches scripts/corner_scout.py:rank; a frame, rather than a person, is the unit. */
export function rank(corners, inputWeights, options = {}) {
  if (!corners.length) return [];
  const { coreThreshold = CORE, cap = CAP, audiences } = options;
  const weights = Object.fromEntries(Object.entries(inputWeights).filter(([key, value]) =>
    !BASELINE.has(key) && (!audiences || Object.hasOwn(audiences, key))
      && typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1));
  const keys = Object.keys(weights);
  const audienceKeys = audiences ? Object.keys(audiences) : [...new Set([
    ...keys, ...corners.flatMap(corner => corner.frames.flatMap(frame => Object.keys(frame.counts))),
  ])].filter(key => !BASELINE.has(key));
  const core = keys.filter(key => weights[key] >= coreThreshold);
  const helpful = keys.filter(key => weights[key] < coreThreshold);
  const rows = corners.map(corner => {
    const seen = Object.fromEntries(audienceKeys.map(key => [key,
      corner.frames.filter(frame => Boolean(frame.counts[key])).length]));
    const rate = Object.fromEntries(keys.map(key => [key, (seen[key] || 0) / Math.max(1, corner.frames.length)]));
    const scored = corner.frames.map(frame => ({
      value: keys.reduce((sum, key) => sum + weights[key] * Math.min(frame.counts[key] || 0, cap), 0),
      hasCore: core.some(key => Boolean(frame.counts[key])),
      frame,
    }));
    return {
      corner, seen, rate, frames: corner.frames.length,
      core_frames: scored.filter(item => item.hasCore).length,
      best: scored.filter(item => item.hasCore).sort((a, b) => b.value - a.value).slice(0, 3),
    };
  });
  const best = Object.fromEntries(keys.map(key => [key, Math.max(...rows.map(row => row.rate[key]))]));
  const share = (row, group) => {
    const total = group.reduce((sum, key) => sum + weights[key], 0);
    if (!total) return 0;
    return group.reduce((sum, key) => sum + (best[key] ? weights[key] * row.rate[key] / best[key] : 0), 0) / total;
  };
  for (const row of rows) {
    row.score = helpful.length ? 0.75 * share(row, core) + 0.25 * share(row, helpful) : share(row, core);
    delete row.rate;
  }
  return rows.sort((a, b) => b.score - a.score || (a.corner.camera < b.corner.camera ? -1 : a.corner.camera > b.corner.camera ? 1 : 0));
}

export function hasEnoughEvidence(ranking, minFrames = MIN_FRAMES) {
  return ranking.length > 0 && ranking[0].core_frames >= minFrames;
}

export function clock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}
