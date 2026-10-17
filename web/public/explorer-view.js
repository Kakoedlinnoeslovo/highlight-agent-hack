import { catalogEntries, filterEntries, pageEntries, recordingLabel, sceneLabel, locationLabel, recordingInterval, coverageSummary, formatDuration } from './explorer.js';
import { intervalPlayer } from './media-player.js';
import { sampleClock } from './proof.js';

const $ = selector => document.querySelector(selector);
const controls = { query: $('#search'), location: $('#location'), scene: $('#scene'), recording: $('#recording') };
const output = $('#explorer-results');
const video = $('#recording-video');
const status = message => { $('#explorer-status').textContent = message; };
const player = intervalPlayer(video, message => { $('#playback-status').textContent = message; });
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
let catalog, entries = [], visible = [], page = 1, selected = null;
const keyOf = entry => `${entry.recording.id}:${entry.segment.id}`;
const readFilters = () => Object.fromEntries(Object.entries(controls).map(([key, input]) => [key, input.value]));

function options(select, values, first) {
  const old = select.value;
  select.replaceChildren(new Option(first, ''));
  for (const [value, label] of values) select.add(new Option(label, value));
  select.value = [...select.options].some(option => option.value === old) ? old : '';
}
function clearSelection() {
  selected = null;
  player.clear();
  video.hidden = true;
  $('#player-title').textContent = 'Choose a caption interval';
  $('#selected-caption').textContent = '';
  $('#provenance').replaceChildren();
  $('#playback-status').textContent = 'Select a result to load its footage.';
}
function updateRecordingOptions() {
  const filters = readFilters();
  const recordings = catalog.recordings.filter(recording => (!filters.location || locationLabel(recording.location) === filters.location)
    && (!filters.scene || sceneLabel(recording.capture_type) === filters.scene));
  options(controls.recording, recordings.map(recording => [recording.id, recordingLabel(recording)]), 'All recordings');
}
function setProvenance(entry) {
  const recording = entry.recording, segment = entry.segment;
  const source = typeof segment.source === 'string' ? segment.source : JSON.stringify(segment.source || {});
  const fields = [
    ['Interval', `${sampleClock(segment.start)}–${sampleClock(segment.end)}`],
    ['Location / scene', `${entry.location} · ${entry.scene}`],
    ['Recording', recordingLabel(recording)],
    ['Camera', recording.camera_id || 'Not supplied'],
    ['Stream ID', recording.stream_id || recording.id],
    ['Source', source || 'Not supplied'],
    ['Segment ID', segment.id],
  ];
  const provenance = $('#provenance');
  provenance.replaceChildren();
  for (const [label, value] of fields) {
    const term = document.createElement('dt'), description = document.createElement('dd');
    term.textContent = label;
    description.textContent = value;
    provenance.append(term, description);
  }
}
function selectEntry(entry) {
  selected = keyOf(entry);
  $('#player-title').textContent = recordingLabel(entry.recording);
  $('#selected-caption').textContent = entry.segment.caption || 'No caption supplied.';
  setProvenance(entry);
  video.hidden = false;
  if (!player.set(recordingInterval(entry), true)) video.hidden = true;
  renderResults(false);
  status(`Selected ${entry.location}, ${sampleClock(entry.segment.start)} to ${sampleClock(entry.segment.end)}.`);
}
function renderResults(announce = true) {
  const matching = filterEntries(entries, readFilters());
  const result = pageEntries(matching, page);
  page = result.page;
  visible = result.entries;
  $('#match-count').textContent = `${result.total.toLocaleString()} matching interval${result.total === 1 ? '' : 's'}`;
  output.innerHTML = visible.length ? visible.map((entry, index) => `<button type="button" class="ex-result" data-result="${index}" aria-pressed="${selected === keyOf(entry)}">
    <span class="ex-meta">${escape(entry.location)} · ${escape(entry.scene)} · ${sampleClock(entry.segment.start)}–${sampleClock(entry.segment.end)}</span>
    <span class="ex-result-caption">${escape(entry.segment.caption || 'No caption supplied.')}</span>
    <span class="ex-result-foot"><span>${escape(recordingLabel(entry.recording))}</span><span class="${recordingInterval(entry).url ? 'ex-watch' : 'ex-pending'}">${recordingInterval(entry).url ? 'Watch interval ↗' : 'Media pending'}</span></span></button>`).join('')
    : '<p class="ex-empty">No caption intervals match these filters. Try fewer words or choose another recording.</p>';
  $('#pagination').innerHTML = result.total ? `<button type="button" id="previous-page" ${page === 1 ? 'disabled' : ''}>Previous</button><span>Page ${page} of ${result.pages}</span><button type="button" id="next-page" ${page === result.pages ? 'disabled' : ''}>Next</button>` : '';
  $('#previous-page')?.addEventListener('click', () => changePage(-1));
  $('#next-page')?.addEventListener('click', () => changePage(1));
  if (announce) status(`${result.total} matching intervals. Page ${page} of ${result.pages}.`);
}
function changePage(delta) {
  page += delta;
  renderResults();
  $('#matches-heading').scrollIntoView({ block: 'start', behavior: 'instant' });
}
function filterChanged() {
  if (!catalog) return;
  page = 1;
  updateRecordingOptions();
  clearSelection();
  renderResults();
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(readFilters())) if (value) parameters.set(key === 'query' ? 'q' : key, value);
  history.replaceState(null, '', `${location.pathname}${parameters.size ? `?${parameters}` : ''}`);
}
function renderCoverage(coverage) {
  const { complete, expected, imported, playable } = coverageSummary(coverage);
  $('#coverage').innerHTML = `<div><h2 class="${complete ? '' : 'ex-incomplete'}">${complete ? 'Complete collection' : 'Collection coverage is incomplete'}</h2>
    <p>${Number(imported.segments || 0).toLocaleString()} of ${Number(expected.segments || 0).toLocaleString()} caption intervals have imported footage · ${Number(playable.recordings || 0)} of ${Number(expected.recordings || 0)} recordings playable</p></div>
    <div class="cs-data-badges"><span>${Number(expected.chunks || 0)} source chunks</span><span>${formatDuration(expected.seconds)} supplied</span><span>${formatDuration(playable.seconds)} playable</span></div>
    ${coverage?.failures?.length ? '<details><summary>Unavailable sources</summary><p id="coverage-failures"></p></details>' : ''}`;
  const failures = $('#coverage-failures');
  if (failures) failures.textContent = coverage.failures.map(failure => typeof failure === 'string' ? failure : failure.error || failure.message || JSON.stringify(failure)).join(' · ');
  $('#coverage').setAttribute('aria-busy', 'false');
}
async function load() {
  output.setAttribute('aria-busy', 'true');
  try {
    const response = await fetch('/catalog.json');
    if (!response.ok) throw new Error('The footage catalog could not be loaded.');
    catalog = await response.json();
    if (!Array.isArray(catalog.recordings)) throw new Error('The footage catalog has no recording list.');
    entries = catalogEntries(catalog);
    renderCoverage(catalog.coverage);
    const unique = values => [...new Set(values)].sort((a, b) => a.localeCompare(b)).map(value => [value, value]);
    options(controls.location, unique(catalog.recordings.map(recording => locationLabel(recording.location))), 'All locations');
    options(controls.scene, unique(catalog.recordings.map(recording => sceneLabel(recording.capture_type))), 'All scenes');
    const params = new URLSearchParams(location.search);
    controls.query.value = params.get('q') || '';
    for (const key of ['location', 'scene']) {
      const value = params.get(key);
      if ([...controls[key].options].some(option => option.value === value)) controls[key].value = value;
    }
    updateRecordingOptions();
    const recording = params.get('recording');
    if ([...controls.recording.options].some(option => option.value === recording)) controls.recording.value = recording;
    renderResults();
  } catch (error) {
    $('#coverage').setAttribute('aria-busy', 'false');
    $('#coverage').textContent = 'Collection coverage unavailable.';
    $('#match-count').textContent = '';
    output.innerHTML = '<p class="ex-empty ex-error" role="alert"></p><button type="button" id="retry-catalog" class="ex-reset">Reload catalog</button>';
    output.querySelector('p').textContent = error.message;
    $('#retry-catalog').addEventListener('click', load);
    status(error.message);
  } finally { output.setAttribute('aria-busy', 'false'); }
}
output.addEventListener('click', event => {
  const button = event.target.closest('[data-result]');
  if (button && visible[button.dataset.result]) selectEntry(visible[button.dataset.result]);
});
$('#explorer-filters').addEventListener('submit', event => event.preventDefault());
controls.query.addEventListener('input', filterChanged);
for (const key of ['location', 'scene', 'recording']) controls[key].addEventListener('change', filterChanged);
$('#explorer-filters').addEventListener('reset', event => {
  event.preventDefault();
  for (const input of Object.values(controls)) input.value = '';
  filterChanged();
});
window.addEventListener('pagehide', () => player.destroy());
load();
