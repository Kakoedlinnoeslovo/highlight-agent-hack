import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogEntries, filterEntries, pageEntries, recordingInterval, coverageSummary, locationLabel } from '../public/explorer.js';
import { mediaUrl, evidenceMedia, intervalPlayer } from '../public/media-player.js';
import { cityName, cityCorners, availableCities } from '../public/scout-city.js';

const collections = ['San Francisco', 'New York', 'Toronto', 'Neighborhood', 'SmartSpace', 'Nashville', 'Warehouse'];
const catalog = { recordings: collections.map((location, index) => ({
  id: `recording-${index}`, stream_id: `stream-${index}`, camera_id: index < 2 ? 'shared-camera' : `camera-${index}`,
  location, capture_type: index < 3 ? 'street' : 'other', media: { url: `/media/recording-${index}.mp4` },
  segments: Array.from({ length: 30 }, (_, segment) => ({
    id: `segment-${index}-${segment}`, start: segment * 5, end: segment * 5 + 5,
    caption: segment % 2 ? 'A cyclist crosses the INTERSECTION.' : 'A person with a dog walks along a street.',
    source: 'original_chunk.mp4 @ 0–5 s',
  })),
})) };

test('all collections and separate recording sessions are searchable without collapsing shared cameras', () => {
  const entries = catalogEntries(catalog);
  assert.equal(entries.length, 210);
  assert.deepEqual([...new Set(entries.map(entry => entry.location))], collections.map(locationLabel));
  for (const location of collections) assert.equal(filterEntries(entries, { location: locationLabel(location) }).length, 30);
  assert.equal(filterEntries(entries, { recording: 'recording-0' }).length, 30);
  assert.equal(filterEntries(entries, { recording: 'recording-1' }).length, 30);
});

test('caption search matches every case-insensitive token and combines exact metadata filters', () => {
  const entries = catalogEntries(catalog);
  assert.equal(filterEntries(entries, { query: ' INTERSECTION   CyCLiSt ' }).length, 105);
  assert.equal(filterEntries(entries, { query: 'dog intersection' }).length, 0);
  assert.equal(filterEntries(entries, { query: 'dog', location: 'Toronto', scene: 'Street' }).length, 15);
  assert.equal(filterEntries(entries, { query: 'Toronto' }).length, 0, 'search uses caption text, not inferred metadata');
  assert.equal(filterEntries(entries, { scene: 'Other', recording: 'recording-0' }).length, 0);
});

test('pagination reports actual matches and clamps stale pages after filters change', () => {
  const entries = catalogEntries(catalog);
  const first = pageEntries(entries);
  assert.equal(first.entries.length, 24);
  assert.equal(first.total, 210);
  assert.equal(first.pages, 9);
  const final = pageEntries(entries, 9);
  assert.equal(final.entries.length, 18);
  assert.equal(pageEntries(entries.slice(0, 2), 9).page, 1);
  assert.deepEqual(pageEntries([], 9), { entries: [], page: 1, pages: 1, total: 0 });
  assert.equal(pageEntries(entries, -1).page, 1);
});

test('shortened final caption intervals retain their exact source timestamps', () => {
  const entry = { recording: catalog.recordings[0], segment: { start: 175, end: 177.4 } };
  assert.deepEqual(recordingInterval(entry), { url: '/media/recording-0.mp4', start: 175, end: 177.4 });
  assert.deepEqual(evidenceMedia({ mediaUrl: '/media/full.mp4', start: 175, end: 177.4 }),
    { url: '/media/full.mp4', start: 175, end: 177.4 });
  assert.deepEqual(evidenceMedia({ clip: '/media/full.mp4#t=175,177.4', start: 175, end: 177.4 }),
    { url: '/media/full.mp4', start: 175, end: 177.4 });
  assert.deepEqual(evidenceMedia({ clip: '/media/legacy.mp4', start: 175, end: 177.4 }),
    { url: '/media/legacy.mp4', start: 0, end: 2.4000000000000057 });
});

test('only source-complete and fully playable manifests can display complete coverage', () => {
  const totals = { recordings: 55, chunks: 612, segments: 3537, seconds: 17700 };
  const full = { expected: totals, imported: { ...totals }, playable: { ...totals }, complete: true };
  assert.equal(coverageSummary(full).complete, true);
  assert.equal(coverageSummary({ ...full, playable: { ...totals, chunks: 611 } }).complete, false);
  assert.equal(coverageSummary({ ...full, imported: { ...totals, segments: 3536 } }).complete, false);
  assert.equal(coverageSummary({ ...full, playable: { ...totals, seconds: 17690 } }).complete, false);
  assert.equal(coverageSummary({ ...full, complete: false }).complete, false);
  assert.equal(coverageSummary({ ...full, failures: ['missing source'] }).complete, false);
  assert.equal(coverageSummary({ ...full, imported: { ...totals, seconds: 10 } }).complete, false);
  assert.equal(coverageSummary().complete, false);
});

test('validated source rounding may accumulate without hiding missing playback duration', () => {
  const chunks = Array.from({ length: 20 }, () => ({ nominal: 30, measured: 29.98 }));
  const expected = { recordings: 4, chunks: chunks.length, segments: 120,
    seconds: chunks.reduce((sum, chunk) => sum + chunk.nominal, 0) };
  const measured = { ...expected, seconds: chunks.reduce((sum, chunk) => sum + chunk.measured, 0) };
  const coverage = { expected, imported: measured, playable: measured, complete: true, failures: [],
    duration_tolerance_seconds: chunks.reduce((sum, chunk) => sum + chunk.nominal - chunk.measured, 0) };
  assert.ok(expected.seconds - measured.seconds > 0.1);
  assert.equal(coverageSummary(coverage).complete, true);
  assert.equal(coverageSummary({ ...coverage, duration_tolerance_seconds: undefined }).complete, false,
    'legacy manifests do not gain an unchecked per-source allowance');
  for (const field of ['imported', 'playable']) {
    assert.equal(coverageSummary({ ...coverage, [field]: { ...measured, seconds: measured.seconds - 5 } }).complete, false,
      'a missing interval cannot be explained by the declared rounding loss');
  }
  assert.equal(coverageSummary({ ...coverage, complete: false }).complete, false);
  assert.equal(coverageSummary({ ...coverage, failures: ['missing source'] }).complete, false);
  assert.equal(coverageSummary({ ...coverage, playable: { ...measured, chunks: 19 } }).complete, false);
  assert.equal(coverageSummary({ ...coverage, imported: { ...measured, segments: 119 } }).complete, false);
});

test('duration budgets must be finite, nonnegative, and bounded by the validated source count', () => {
  const totals = { recordings: 1, chunks: 20, segments: 120, seconds: 600 };
  const full = { expected: totals, imported: totals, playable: totals, complete: true };
  for (const budget of [null, '0.4', -1, NaN, Infinity, 2.01, 1000]) {
    assert.equal(coverageSummary({ ...full, duration_tolerance_seconds: budget }).complete, false,
      `invalid or excessive duration budget: ${budget}`);
  }
  assert.equal(coverageSummary({ ...full, duration_tolerance_seconds: 2 }).complete, true);
  assert.equal(coverageSummary({ ...full, duration_tolerance_seconds: 0 }).complete, true);
  const measured = { ...totals, seconds: 590 };
  assert.equal(coverageSummary({ ...full, imported: measured, playable: measured,
    duration_tolerance_seconds: 1000 }).complete, false);
});

test('media references allow local recordings and Vercel Blob while rejecting executable or unrelated URLs', () => {
  for (const value of ['/media/full.mp4', '/local-media/full.mp4', 'https://example.public.blob.vercel-storage.com/recording.mp4']) {
    assert.equal(mediaUrl(value), value);
  }
  for (const value of ['javascript:alert(1)', '//evil.test/video.mp4', 'https://evil.test/video.mp4', '/media/fake.mp4.exe', 'data:video/mp4;base64,aaaa']) {
    assert.equal(mediaUrl(value), null);
  }
});

class FakeVideo {
  listeners = new Map();
  readyState = 0;
  duration = 600;
  currentTime = 0;
  paused = true;
  seeking = false;
  plays = 0;
  addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(fn); }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  fire(type) { for (const fn of [...(this.listeners.get(type) || [])]) fn(); }
  pause() { this.paused = true; }
  play() { this.plays++; this.paused = false; this.fire('play'); return Promise.resolve(); }
  removeAttribute() { delete this.src; }
  load() { this.readyState = 0; }
}

test('interval player seeks once, pauses at the selected end, and cancels old async metadata handlers', () => {
  const video = new FakeVideo();
  const statuses = [];
  const player = intervalPlayer(video, message => statuses.push(message));
  player.set({ url: '/media/first.mp4', start: 10, end: 15 }, true);
  const staleMetadata = [...video.listeners.get('loadedmetadata')][0];
  player.set({ url: '/media/second.mp4', start: 120, end: 122.4 }, true);
  staleMetadata();
  assert.equal(video.plays, 0);
  video.fire('loadedmetadata');
  assert.equal(video.currentTime, 120);
  assert.equal(video.plays, 1);
  video.currentTime = 123;
  video.fire('timeupdate');
  assert.equal(video.currentTime, 122.4);
  assert.equal(video.paused, true);
  video.fire('seeked');
  assert.equal(video.plays, 1, 'end-of-interval seeks must not restart autoplay');
  player.clear();
  video.fire('loadedmetadata');
  assert.equal(video.plays, 1);
  assert.equal(video.src, undefined);
  assert.equal(player.selection, null);
  assert.deepEqual(statuses, ['', '']);
});

test('invalid or unavailable media stays unloaded and reports a useful status', () => {
  const video = new FakeVideo();
  let status = '';
  const player = intervalPlayer(video, message => { status = message; });
  assert.equal(player.set({ url: '/media/full.mp4', start: 20, end: 10 }), false);
  assert.match(status, /no playable/);
  assert.equal(video.src, undefined);
  player.set({ url: '/media/full.mp4', start: 601, end: 605 }, true);
  video.fire('loadedmetadata');
  assert.equal(video.plays, 0);
  assert.match(status, /outside/);
});

test('city aliases scope independently and legacy observations belong only to San Francisco', () => {
  assert.equal(cityName('san_francisco'), 'San Francisco');
  assert.equal(cityName('nyc'), 'New York');
  const corners = [{ id: 'legacy' }, { id: 'sf', location: 'san_francisco' }, { id: 'ny', city: 'New York' }];
  assert.deepEqual(cityCorners(corners, 'SF').map(corner => corner.id), ['legacy', 'sf']);
  assert.deepEqual(cityCorners(corners, 'nyc').map(corner => corner.id), ['ny']);
  assert.deepEqual(availableCities(corners), ['San Francisco', 'New York']);
});


test('inventory location slugs have clear labels for all seven supplied collections', () => {
  assert.deepEqual(['san_francisco', 'new_york', 'toronto', 'neighborhood', 'indoor', 'nashville', 'warehouse3'].map(locationLabel),
    ['San Francisco', 'New York', 'Toronto', 'Neighborhood', 'Indoor / SmartSpace', 'Nashville', 'Warehouse']);
});

test('Blob stills and local full-recording images remain usable by the proof view', () => {
  const blob = 'https://store.public.blob.vercel-storage.com/frames/' + 'a'.repeat(64) + '.jpg';
  const local = '/recording-images/' + 'a'.repeat(64) + '.jpg';
  assert.equal(mediaUrl(blob, 'jpg'), blob);
  assert.equal(mediaUrl(local, 'jpg'), local);
  assert.equal(mediaUrl('/media/legacy-frame.jpg', 'jpg'), '/media/legacy-frame.jpg');
  assert.equal(mediaUrl(blob), null);
  assert.equal(mediaUrl('https://evil.test/frame.jpg', 'jpg'), null);
});

test('a full recording seeks to a later shortened interval before playing and replays from its selected start', () => {
  const video = new FakeVideo();
  video.seeking = true;
  const player = intervalPlayer(video);
  const url = 'https://store.public.blob.vercel-storage.com/recordings/' + 'b'.repeat(64) + '.mp4';
  const interval = evidenceMedia({ mediaUrl: url, clip: url + '#t=175,177.4', start: 175, end: 177.4 });
  assert.equal(player.set(interval, true), true);
  video.fire('loadedmetadata');
  assert.equal(video.currentTime, 175);
  assert.equal(video.plays, 0, 'metadata alone must not play before the seek completes');
  video.seeking = false;
  video.fire('seeked');
  assert.equal(video.plays, 1);
  video.currentTime = 177.39;
  video.fire('timeupdate');
  assert.equal(video.currentTime, 177.4);
  assert.equal(video.paused, true);
  video.fire('seeked');
  assert.equal(video.plays, 1);
  video.play();
  assert.equal(video.currentTime, 175);
  assert.equal(video.paused, false);
  player.destroy();
});
