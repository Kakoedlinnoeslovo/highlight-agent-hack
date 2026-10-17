/** Deterministic scoring over the published, cached frame observations. */
export const CORE = 0.75;
export const CAP = 4;
export const MIN_FRAMES = 3;
export const MIN_ANALYZED_SECONDS = 180;
export const MIN_COVERAGE = 0.9;
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

export function cityLabel(value) {
  const input = String(value || 'San Francisco').trim();
  const aliases = { san_francisco: 'San Francisco', 'san francisco': 'San Francisco', sf: 'San Francisco',
    new_york: 'New York', 'new york': 'New York', nyc: 'New York', toronto: 'Toronto', nashville: 'Nashville' };
  return aliases[input.toLowerCase()] || input.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase());
}

function frameDuration(frame, strict = false) {
  const start = frame.start_seconds ?? frame.start;
  const end = frame.end_seconds ?? frame.end;
  if (start == null || end == null) return strict ? 0 : 5;
  return typeof start === 'number' && typeof end === 'number' && Number.isFinite(start)
    && Number.isFinite(end) && start >= 0 && end > start ? end - start : 0;
}

function analyzedDuration(frames) {
  const intervals = [];
  let seconds = 0;
  for (const frame of frames) {
    const start = frame.start_seconds ?? frame.start;
    const end = frame.end_seconds ?? frame.end;
    if (start == null || end == null) seconds += frameDuration(frame);
    else if (frameDuration(frame) > 0) intervals.push([start, end]);
  }
  intervals.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let coveredEnd = 0;
  for (const [start, end] of intervals) {
    seconds += Math.max(0, end - Math.max(start, coveredEnd));
    coveredEnd = Math.max(coveredEnd, end);
  }
  return seconds;
}

function independentObservations(frames) {
  const selected = [];
  const intervals = [];
  frames.forEach((frame, index) => {
    const start = frame.start_seconds ?? frame.start;
    const end = frame.end_seconds ?? frame.end;
    if (start == null || end == null) selected.push({ frame, index });
    else intervals.push({ frame, index, start, end });
  });
  intervals.sort((a, b) => a.end - b.end || a.start - b.start || a.index - b.index);
  let coveredEnd = 0;
  for (const item of intervals) {
    if (item.start >= coveredEnd) {
      selected.push(item);
      coveredEnd = item.end;
    }
  }
  return selected.sort((a, b) => a.index - b.index).map(item => item.frame);
}

function validCounts(frame) {
  return frame?.counts !== null && typeof frame?.counts === 'object' && !Array.isArray(frame.counts)
    && Object.values(frame.counts).every(value => Number.isInteger(value) && value >= 0);
}

function validObservationFrames(corner) {
  const strict = ['expected_seconds', 'coverage', 'eligible'].some(key => Object.hasOwn(corner, key));
  return (corner.frames ?? []).filter(frame => validCounts(frame) && frameDuration(frame, strict) > 0);
}

/** The same independent observations support ranking counters and the proof inspector. */
export function scoringFrames(corner) {
  return independentObservations(validObservationFrames(corner));
}

/** Matches scripts/corner_scout.py:rank; scores positive presence per valid analyzed second. */
export function rank(corners, inputWeights, options = {}) {
  const selectedCity = cityLabel(options.city);
  corners = corners.filter(corner => corner.eligible !== false
    && cityLabel(corner.city ?? corner.location) === selectedCity);
  if (!corners.length) return [];
  const { coreThreshold = CORE, cap = CAP, audiences } = options;
  const weights = Object.fromEntries(Object.entries(inputWeights).filter(([key, value]) =>
    !BASELINE.has(key) && (!audiences || Object.hasOwn(audiences, key))
      && typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1));
  const keys = Object.keys(weights);
  const audienceKeys = audiences ? Object.keys(audiences) : [...new Set([
    ...keys, ...corners.flatMap(corner => corner.frames.flatMap(frame => Object.keys(frame.counts || {}))),
  ])].filter(key => !BASELINE.has(key));
  const core = keys.filter(key => weights[key] >= coreThreshold);
  const helpful = keys.filter(key => weights[key] < coreThreshold);
  const rows = corners.map(corner => {
    const strict = ['expected_seconds', 'coverage', 'eligible'].some(key => Object.hasOwn(corner, key));
    const frames = validObservationFrames(corner);
    const observations = scoringFrames(corner);
    const analyzedSeconds = analyzedDuration(frames);
    let expectedSeconds = corner.expected_seconds ?? corner.duration ?? analyzedSeconds;
    if (typeof expectedSeconds !== 'number' || !Number.isFinite(expectedSeconds)) expectedSeconds = 0;
    const coverage = expectedSeconds > 0 ? Math.min(1, analyzedSeconds / expectedSeconds) : 0;
    const seen = Object.fromEntries(audienceKeys.map(key => [key,
      observations.filter(frame => Boolean(frame.counts[key])).length]));
    const rate = Object.fromEntries(keys.map(key => [key,
      analyzedDuration(frames.filter(frame => Boolean(frame.counts[key])))
        / Math.max(1e-12, analyzedSeconds)]));
    const scored = observations.map(frame => ({
      value: keys.reduce((sum, key) => sum + weights[key] * Math.min(frame.counts[key] || 0, cap), 0),
      hasCore: core.some(key => Boolean(frame.counts[key])),
      frame,
    }));
    const coreFrames = scored.filter(item => item.hasCore).length;
    return {
      corner, seen, rate, frames: observations.length,
      core_frames: coreFrames, analyzed_seconds: analyzedSeconds, expected_seconds: expectedSeconds,
      coverage, eligible: corner.eligible !== false, coverage_required: strict,
      sufficient: coreFrames >= MIN_FRAMES && (!strict || (analyzedSeconds >= MIN_ANALYZED_SECONDS && coverage >= MIN_COVERAGE)),
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
  if (!ranking.length || ranking[0].core_frames < minFrames) return false;
  const row = ranking[0];
  if (row.eligible === false) return false;
  const strict = row.coverage_required ?? ('analyzed_seconds' in row || 'coverage' in row);
  return !strict || (row.analyzed_seconds >= MIN_ANALYZED_SECONDS && row.coverage >= MIN_COVERAGE);
}

export function clock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}
