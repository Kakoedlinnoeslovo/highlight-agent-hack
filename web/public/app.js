import { rank, profileWeights, validateBusiness, hasEnoughEvidence, clock } from './scout.js';

const form = document.querySelector('#cs-query');
const input = document.querySelector('#business');
const go = document.querySelector('#cs-go');
const results = document.querySelector('#results');
const announcements = document.querySelector('#announcements');
const initial = results.innerHTML;
let data;
let pending;
let sequence = 0;

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const audience = key => data.audiences[key] || key;
const weightedKeys = weights => Object.keys(weights).sort((a, b) => weights[b] - weights[a]);
const counted = (frame, weights) => weightedKeys(weights).filter(key => frame.counts[key])
  .map(key => `${audience(key)} ×${frame.counts[key]}`).join(' · ');
const validClip = clip => typeof clip === 'string' && /^\/media\/[a-zA-Z0-9_.-]+\.mp4$/.test(clip);

function announce(message) { announcements.textContent = message; }

function clearPlayback() {
  results.querySelectorAll('video').forEach(video => {
    video.pause();
    video.removeAttribute('src');
    video.load();
  });
}

function replaceResults(html) {
  clearPlayback();
  results.innerHTML = html;
}

function showError(error, loadingData = false) {
  replaceResults(`<section class="cs-card cs-error" role="alert"><h2>Couldn’t ${loadingData ? 'load the scouting sample' : 'scout these corners'}</h2>
    <p>${escape(error.message || 'Something went wrong. Please try again.')}</p>
    <p>${loadingData ? 'Reload the sample to try again.' : 'Edit your business description or try again.'}</p>
    ${loadingData ? '<button type="button" class="cs-text-button" id="retry-data">Reload sample</button>' : ''}</section>`);
  document.querySelector('#retry-data')?.addEventListener('click', loadData);
}

function reset() {
  sequence += 1;
  pending?.abort();
  pending = null;
  input.value = '';
  input.setCustomValidity('');
  go.textContent = 'Find my corner';
  go.disabled = !data;
  results.setAttribute('aria-busy', 'false');
  replaceResults(initial);
  announce('Search reset. Describe another business.');
  input.focus();
}

function renderRecommendation(business, profile, weights, ranking, seconds) {
  const top = ranking[0];
  const enough = hasEnoughEvidence(ranking, data.minFrames);
  const tied = ranking.filter(row => Math.abs(row.score - top.score) < 1e-9).map(row => row.corner.camera);
  const title = enough ? `Best corner: ${tied.join(' and ')}` : 'Not enough evidence to recommend a corner';
  const chips = weightedKeys(weights).map(key => `<span class="cs-chip ${weights[key] >= data.coreThreshold ? 'cs-core' : ''}" title="${escape(profile.weights.find(item => item.key === key)?.why)}">${escape(audience(key))} · ${weights[key] >= data.coreThreshold ? 'core' : 'helpful'}</span>`).join('');
  const coreCounts = weightedKeys(weights).filter(key => weights[key] >= data.coreThreshold)
    .map(key => `${audience(key)} in ${top.seen[key] || 0}/${top.frames} frames`).join(' · ');
  return `<section class="cs-card cs-recommendation"><div class="cs-recommendation-main">
    <span class="cs-eyebrow ${enough ? 'cs-blue' : 'cs-warn'}">${enough ? 'Your shortlist' : 'More footage needed'}</span>
    <h2 id="result-title" tabindex="-1" ${enough ? '' : 'class="cs-warn"'}>${escape(title)}</h2>
    ${enough ? '' : `<p class="cs-muted">The best corner shows your core customers in only ${top.core_frames} of ${top.frames} sampled frames. At least ${data.minFrames} core-audience frames are needed.</p>`}
    <p class="cs-customers"><b>${escape(business)}</b> · ${escape(profile.customers)}</p><div class="cs-chips">${chips}</div>
    <p class="cs-counts">${escape(coreCounts)}</p></div>
    <div class="cs-result-meta"><span class="cs-time">${seconds.toFixed(1)}s <span>this search</span></span>
      <span class="cs-profile-model">${escape(profile.model ? `Profile: ${profile.model}` : 'Generated business profile')}<br>Ranked from cached observations</span>
      <details class="cs-reasons"><summary>Why these audiences?</summary><ul>${profile.weights.map(item => `<li><b>${escape(audience(item.key))}:</b> ${escape(item.why)}</li>`).join('')}</ul></details>
    </div></section>`;
}

function renderRanking(ranking, weights) {
  return `<section class="cs-card cs-ranking" aria-labelledby="ranking-title"><div class="cs-section-heading"><h3 id="ranking-title">Every corner, ranked</h3><span>${ranking.length} corners</span></div>
    <p class="cs-muted cs-score-key">Relative match · score out of 100</p>
    ${ranking.map((row, index) => {
      const mix = Object.entries(row.seen).filter(([key, count]) => count && Object.hasOwn(data.audiences, key))
        .sort((a, b) => b[1] - a[1]).slice(0, 5).map(([key, count]) => `${audience(key)} ${count}`).join(' · ');
      const wanted = weightedKeys(weights).map(key => `${audience(key)} in ${row.seen[key] || 0}/${row.frames}`).join(', ');
      return `<div class="cs-ranking-row"><div class="cs-ranking-line"><span class="cs-rank">${String(index + 1).padStart(2, '0')}</span><b>${escape(row.corner.camera)}</b><span class="cs-score">${Math.round(row.score * 100)}<small>/100</small></span></div>
        <div class="cs-track" aria-hidden="true"><div class="cs-fill" style="width:${row.score * 100}%"></div></div>
        <p class="cs-frame-count">Core audience in <b>${row.core_frames} of ${row.frames} sampled frames</b></p>
        <details><summary>Audience &amp; location details</summary><p>${escape(row.corner.headline)}<br>Recorded ${escape(row.corner.recorded)}</p>
        <p>Your audience: ${escape(wanted)} frames</p><p>Corner mix: ${escape(mix || 'No observed audiences')}</p></details></div>`;
    }).join('')}
    <p class="cs-score-note">Scores compare audience signals across these corners, not customer probabilities. Each audience’s share of sampled frames is normalized to the strongest corner. Core audiences decide the match; helpful audiences contribute up to 25%.</p>
  </section>`;
}

function renderEvidence(row, index, weights, enough) {
  const clips = row.best.filter(item => validClip(item.frame.clip));
  return `<section class="cs-card cs-evidence" aria-labelledby="evidence-title-${index}">
    <h3 id="evidence-title-${index}">${enough ? (index ? 'Runner-up evidence' : 'Primary evidence') : 'Limited evidence'} · ${escape(row.corner.camera)}</h3>
    ${clips.length ? `<video id="evidence-video-${index}" controls preload="metadata" playsinline aria-label="Recorded evidence at ${escape(row.corner.camera)}">Your browser does not support video playback.</video>
      <div class="cs-playlist"><button type="button" data-previous="${index}" aria-label="Previous evidence clip at ${escape(row.corner.camera)}">← Previous</button>
        <span class="cs-clip-position" id="clip-position-${index}" aria-live="polite"></span>
        <button type="button" data-next="${index}" aria-label="Next evidence clip at ${escape(row.corner.camera)}">Next →</button></div>
      <p class="cs-clip-caption" id="clip-caption-${index}"></p><p class="cs-video-status" id="video-status-${index}" role="status"></p>`
      : '<div class="cs-no-evidence">No clip strong enough. Try another business or collect more footage.</div>'}
    ${clips.length ? `<details class="cs-source-notes"><summary>Inspect ${clips.length} evidence clip${clips.length === 1 ? '' : 's'} &amp; source timestamps</summary><ol>
      ${clips.map(({ frame }) => `<li><div class="cs-clip-heading"><b>${clock(frame.start)}–${clock(frame.end)}</b><span>${escape(counted(frame, weights))}</span></div>
        <p>${escape(frame.notable)}</p><code>${escape(frame.source)}</code></li>`).join('')}</ol></details>` : ''}
    </section>`;
}

function wireEvidence(row, index, weights) {
  const clips = row.best.filter(item => validClip(item.frame.clip));
  if (!clips.length) return;
  const video = document.querySelector(`#evidence-video-${index}`);
  const previous = document.querySelector(`[data-previous="${index}"]`);
  const next = document.querySelector(`[data-next="${index}"]`);
  const status = document.querySelector(`#video-status-${index}`);
  let position = 0;
  function showClip(nextPosition, play = false) {
    position = nextPosition;
    const frame = clips[position].frame;
    video.src = frame.clip;
    video.load();
    previous.disabled = position === 0;
    next.disabled = position === clips.length - 1;
    document.querySelector(`#clip-position-${index}`).textContent = `Clip ${position + 1} of ${clips.length} · ${clock(frame.start)}–${clock(frame.end)}`;
    document.querySelector(`#clip-caption-${index}`).textContent = [counted(frame, weights), frame.notable].filter(Boolean).join(' · ');
    status.textContent = '';
    if (play) video.play().catch(() => { status.textContent = 'Press play to watch this clip.'; });
  }
  previous.addEventListener('click', () => showClip(position - 1, !video.paused));
  next.addEventListener('click', () => showClip(position + 1, !video.paused));
  video.addEventListener('ended', () => {
    if (position < clips.length - 1) showClip(position + 1, true);
  });
  video.addEventListener('error', () => { status.textContent = 'This evidence clip could not be loaded. Try the next clip or retry the search.'; });
  video.addEventListener('play', () => {
    results.querySelectorAll('video').forEach(other => { if (other !== video) other.pause(); });
  });
  showClip(0);
}

async function submit(event) {
  event?.preventDefault();
  if (!data) return;
  let business;
  try { business = validateBusiness(input.value); }
  catch (error) {
    input.setCustomValidity(error.message);
    input.reportValidity();
    return;
  }
  input.setCustomValidity('');
  pending?.abort();
  const controller = new AbortController();
  pending = controller;
  const current = ++sequence;
  go.disabled = true;
  go.textContent = 'Scouting…';
  results.setAttribute('aria-busy', 'true');
  replaceResults('<section class="cs-card cs-loading"><span class="cs-spinner" aria-hidden="true"></span><div><b>Scouting your corners…</b><p>Generating your audience profile and comparing the recorded sample.</p></div></section>');
  announce(`Scouting corners for ${business}.`);
  const started = performance.now();
  const timeout = setTimeout(() => controller.abort('timeout'), 90000);
  try {
    const response = await fetch('/api/profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ business }), signal: controller.signal,
    });
    let profile;
    try { profile = await response.json(); }
    catch { throw new Error('The profile service returned an unreadable response. Please try again.'); }
    if (!response.ok) throw new Error(typeof profile.error === 'string' ? profile.error : 'The profile service is unavailable. Please try again.');
    const weights = profileWeights(profile, data.audiences, data.coreThreshold);
    const ranking = rank(data.corners, weights, data);
    if (!ranking.length) throw new Error('No corners are available in this scouting sample.');
    if (current !== sequence) return;
    const enough = hasEnoughEvidence(ranking, data.minFrames);
    replaceResults(renderRecommendation(business, profile, weights, ranking, (performance.now() - started) / 1000)
      + `<div id="cs-dashboard"><div class="cs-evidence-column">${ranking.slice(0, 2).map((row, index) => renderEvidence(row, index, weights, enough)).join('')}</div>${renderRanking(ranking, weights)}</div>`);
    ranking.slice(0, 2).forEach((row, index) => wireEvidence(row, index, weights));
    document.querySelector('#result-title')?.focus({ preventScroll: true });
    announce(enough ? `Shortlist ready. Best corner: ${ranking[0].corner.camera}. ${ranking.length} corners compared.` : 'Search complete. Not enough evidence to recommend a corner.');
  } catch (error) {
    if (current !== sequence) return;
    showError(controller.signal.aborted ? new Error('The audience profile took too long. Please try again.') : error);
    announce('Could not complete this search. Edit your description or try again.');
  } finally {
    clearTimeout(timeout);
    if (current === sequence) {
      pending = null;
      go.disabled = false;
      go.textContent = 'Find my corner';
      results.setAttribute('aria-busy', 'false');
    }
  }
}

async function loadData() {
  go.disabled = true;
  announce('Loading the recorded scouting sample.');
  try {
    const response = await fetch('/corners.json');
    if (!response.ok) throw new Error('The recorded scouting sample could not be loaded.');
    const sample = await response.json();
    if (!sample.audiences || !Array.isArray(sample.corners) || !sample.corners.length) throw new Error('This scouting sample has no available corners.');
    data = sample;
    const frameCount = data.corners.reduce((total, corner) => total + corner.frames.length, 0);
    document.querySelector('#data-badges').innerHTML = `<span>${data.corners.length} SF corners</span><span>${frameCount} sampled frames</span><span>Recorded sample</span>`;
    const examples = document.querySelector('#cs-examples');
    examples.replaceChildren();
    for (const business of data.examples || []) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = business;
      button.addEventListener('click', () => {
        input.value = business;
        input.setCustomValidity('');
        submit();
      });
      examples.append(button);
    }
    go.disabled = false;
    replaceResults(initial);
    announce(`${data.corners.length} recorded corners ready. Describe your business to compare them.`);
  } catch (error) { showError(error, true); announce('The scouting sample could not be loaded.'); }
}

form.addEventListener('submit', submit);
input.addEventListener('input', () => input.setCustomValidity(''));
document.querySelector('#reset').addEventListener('click', reset);
loadData();
