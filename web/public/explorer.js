import { mediaUrl } from './media-player.js';

export const PAGE_SIZE = 24;
export function locationLabel(value) {
  const raw = typeof value === 'string' ? value : value?.city || value?.label || 'Unknown location';
  const normalized = raw.trim().toLowerCase().replaceAll('_', ' ');
  return ({ sf: 'San Francisco', 'san francisco': 'San Francisco', nyc: 'New York', 'new york': 'New York',
    toronto: 'Toronto', neighborhood: 'Neighborhood', indoor: 'Indoor / SmartSpace', smartspace: 'Indoor / SmartSpace',
    nashville: 'Nashville', warehouse3: 'Warehouse', warehouse: 'Warehouse' })[normalized] || raw.replaceAll('_', ' ');
}
export function sceneLabel(value) {
  const label = String(value || 'Unknown scene').replaceAll('_', ' ');
  return label[0].toUpperCase() + label.slice(1);
}
export const recordingLabel = recording => recording.label || `${recording.camera_id || 'Recording'} · ${recording.id}`;
export function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m ${total % 60}s`;
}
export function catalogEntries(catalog) {
  return (catalog?.recordings || []).flatMap(recording => (recording.segments || []).map(segment => ({
    recording, segment,
    location: locationLabel(recording.location), scene: sceneLabel(recording.capture_type),
  })));
}
export function filterEntries(entries, { query = '', location = '', scene = '', recording = '' } = {}) {
  const tokens = String(query).toLocaleLowerCase().trim().split(/\s+/u).filter(Boolean);
  return entries.filter(entry => (!location || entry.location === location)
    && (!scene || entry.scene === scene)
    && (!recording || entry.recording.id === recording)
    && tokens.every(token => String(entry.segment.caption || '').toLocaleLowerCase().includes(token)));
}
export function pageEntries(entries, page = 1, pageSize = PAGE_SIZE) {
  const size = Number.isInteger(pageSize) && pageSize > 0 ? pageSize : PAGE_SIZE;
  const pages = Math.max(1, Math.ceil(entries.length / size));
  const current = Math.max(1, Math.min(pages, Math.floor(Number(page) || 1)));
  return { entries: entries.slice((current - 1) * size, current * size), page: current, pages, total: entries.length };
}
export function recordingInterval(entry) {
  return {
    url: mediaUrl(entry?.recording?.media?.url),
    start: Number(entry?.segment?.start), end: Number(entry?.segment?.end),
  };
}
export function coverageSummary(coverage) {
  const expected = coverage?.expected || {};
  const imported = coverage?.imported || {};
  const playable = coverage?.playable || {};
  const declaredTolerance = coverage?.duration_tolerance_seconds;
  // New exporters account for validated rounding loss at each source. Legacy
  // manifests retain their original tenth-second allowance for the collection.
  const validTolerance = declaredTolerance === undefined
    || (typeof declaredTolerance === 'number' && Number.isFinite(declaredTolerance)
      && declaredTolerance >= 0 && declaredTolerance <= Number(expected.chunks) * 0.1 + 1e-6);
  const tolerance = Math.max(0.1, declaredTolerance ?? 0.1);
  const durationCovered = seconds => Number.isFinite(Number(seconds))
    && Number(seconds) + tolerance >= Number(expected.seconds) - 1e-6;
  // The manifest is complete only when every expected source and caption is represented.
  const keys = ['recordings', 'chunks', 'segments'];
  const complete = coverage?.complete === true && validTolerance && !(coverage.failures || []).length && keys.every(key => Number(expected[key]) > 0
    && Number(imported[key]) === Number(expected[key]) && Number(playable[key]) === Number(expected[key]))
    && Number(expected.seconds) > 0 && durationCovered(imported.seconds)
    && durationCovered(playable.seconds);
  return { expected, imported, playable, complete };
}
