import { rank, profileWeights, validateBusiness, hasEnoughEvidence } from './scout.js';
import { renderProof, wireProof } from './proof-view.js';

const form = document.querySelector('#cs-query');
const input = document.querySelector('#business');
const go = document.querySelector('#cs-go');
const results = document.querySelector('#results');
const announcements = document.querySelector('#announcements');
let data;
let pending;
let sequence = 0;
let activeProof;

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const audience = key => data.audiences[key] || key;
const weightedKeys = weights => Object.keys(weights).sort((a, b) => weights[b] - weights[a]);
const validPoster = poster => typeof poster === 'string' && /^\/previews\/[a-zA-Z0-9_.-]+\.jpg$/.test(poster);

function announce(message) { announcements.textContent = message; }

function clearPlayback() {
  results.querySelectorAll('video').forEach(video => {
    video.pause();
    video.removeAttribute('src');
    video.load();
  });
}

function replaceResults(html) {
  activeProof?.destroy();
  activeProof = null;
  clearPlayback();
  results.innerHTML = html;
  document.querySelector('#cs-shell').classList.toggle('cs-has-results', Boolean(results.querySelector('.cs-recommendation')));
  form.classList.remove('cs-query-expanded');
  document.querySelector('#edit-business').setAttribute('aria-expanded', 'false');
}

function renderInitial(sample) {
  const cards = (sample?.corners || []).slice(0, 5).map((corner, index) => {
    const preview = validPoster(corner.poster)
      ? `<img src="${escape(corner.poster)}" alt="${escape(corner.camera)}: recorded street footage">`
      : '<div class="cs-camera-placeholder" aria-hidden="true">⌖</div>';
    return `<div class="cs-camera-card" style="--camera-index:${index}">${preview}
      <div class="cs-camera-label"><b>${escape(corner.camera)}</b><span>Source preview</span></div></div>`;
  }).join('');
  return `<section class="cs-scene"><div class="cs-scene-copy">
    <span class="cs-eyebrow cs-blue">A better place to start</span>
    <h2>See who walks past.<br>Before you sign.</h2>
    <p>Describe your business above. We’ll compare the audiences on these streets and show you the clips behind your shortlist.</p>
    <div class="cs-scene-tags"><span>People</span><span>Vehicles</span><span>Animals</span></div></div>
    <div class="cs-camera-stage"><div class="cs-stage-header"><span>SF STREETS</span><span>THE SOURCE FOOTAGE</span></div>
    <div class="cs-stage-cards">${cards}</div><div class="cs-stage-footer"><span>Recorded footage · San Francisco</span><span aria-hidden="true">⌖</span></div></div>
    <div class="cs-steps"><span><b>01</b> Describe your business</span><span><b>02</b> Compare the audiences</span><span><b>03</b> Watch the evidence</span></div></section>`;
}

function renderLoading(corners) {
  const paths = corners.slice(0, 5).map((_, index) => `<path class="cs-beam-path" style="--order:${index}" d="M${60 + index * 120},24 Q300,24 300,132"/>`).join('');
  const nodes = corners.slice(0, 5).map((corner, index) => `<span class="cs-scan-node" style="--order:${index}" title="${escape(corner.camera)}">${String(index + 1).padStart(2, '0')}</span>`).join('');
  return `<section class="cs-scan-stage" role="status" aria-live="polite">
    <div class="cs-scan-orbit" aria-hidden="true"><svg class="cs-beam-lines" viewBox="0 0 600 160" preserveAspectRatio="none">${paths}</svg>${nodes}<span class="cs-scan-core">⌖</span></div>
    <div class="cs-scan-copy"><h3>Finding your audience.</h3><p>Generating your business profile and comparing recorded frame observations.</p></div></section>`;
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
  replaceResults(renderInitial(data));
  announce('Search reset. Describe another business.');
  input.focus();
}

function renderRecommendation(business, profile, weights, ranking, seconds) {
  const top = ranking[0];
  const enough = hasEnoughEvidence(ranking, data.minFrames);
  const tied = ranking.filter(row => Math.abs(row.score - top.score) < 1e-9).map(row => row.corner.camera);
  const title = enough ? `Best corner: ${tied.join(' and ')}` : 'Not enough evidence to recommend a corner';
  const chips = weightedKeys(weights).map((key, index) => `<span class="cs-chip ${weights[key] >= data.coreThreshold ? 'cs-core' : ''}" style="--order:${index}" title="${escape(profile.weights.find(item => item.key === key)?.why)}">${escape(audience(key))} · ${weights[key] >= data.coreThreshold ? 'core' : 'helpful'}</span>`).join('');
  const coreCounts = weightedKeys(weights).filter(key => weights[key] >= data.coreThreshold)
    .map(key => `<button type="button" class="cs-count-link" data-count-audience="${escape(key)}">${escape(audience(key))} detected in ${top.seen[key] || 0} of ${top.frames} sampled frames <span aria-hidden="true">↗</span></button>`).join('');
  return `<section class="cs-card cs-recommendation"><div class="cs-recommendation-main">
    <span class="cs-eyebrow ${enough ? 'cs-blue' : 'cs-warn'}">${enough ? 'Your shortlist' : 'More footage needed'}</span>
    <h2 id="result-title" tabindex="-1" ${enough ? '' : 'class="cs-warn"'}>${escape(title)}</h2>
    ${enough ? '' : `<p class="cs-muted">The best corner shows your core customers in only ${top.core_frames} of ${top.frames} sampled frames. At least ${data.minFrames} core-audience frames are needed.</p>`}
    <p class="cs-customers"><b>${escape(business)}</b> · ${escape(profile.customers)}</p><div class="cs-chips">${chips}</div>
    <div class="cs-counts">${coreCounts}</div></div>
    <div class="cs-result-meta"><div class="cs-match"><span>Relative match</span><b>${Math.round(top.score * 100)}<small>/100</small></b></div>
      <span class="cs-time">${seconds.toFixed(1)}s <span>this search</span></span>
      <details class="cs-reasons"><summary>Why these audiences?</summary><ul>${profile.weights.map(item => `<li><b>${escape(audience(item.key))}:</b> ${escape(item.why)}</li>`).join('')}</ul>
      <p class="cs-profile-model">${escape(profile.model ? `Profile: ${profile.model}` : 'Generated business profile')}<br>Ranked from cached observations</p>
    </div></section>`;
}

function renderRanking(ranking, weights) {
  return `<section class="cs-card cs-ranking" aria-labelledby="ranking-title"><div class="cs-section-heading"><h3 id="ranking-title">Every corner, ranked</h3><span>${ranking.length} corners</span></div>
    <p class="cs-muted cs-score-key">Relative match · score out of 100</p>
    ${ranking.map((row, index) => {
      const mix = Object.entries(row.seen).filter(([key, count]) => count && Object.hasOwn(data.audiences, key))
        .sort((a, b) => b[1] - a[1]).slice(0, 5).map(([key, count]) => `${audience(key)} ${count}`).join(' · ');
      const wanted = weightedKeys(weights).map(key => `${audience(key)} in ${row.seen[key] || 0}/${row.frames}`).join(', ');
      return `<div class="cs-ranking-row" style="--order:${index}"><div class="cs-ranking-line"><span class="cs-rank">${String(index + 1).padStart(2, '0')}</span><b>${escape(row.corner.camera)}</b><span class="cs-score">${Math.round(row.score * 100)}<small>/100</small></span></div>
        <div class="cs-track" aria-hidden="true"><div class="cs-fill" style="width:${row.score * 100}%"></div></div>
        <p class="cs-frame-count">Core audience in <b>${row.core_frames} of ${row.frames} sampled frames</b></p>
        <details><summary>Audience &amp; location details</summary><p>${escape(row.corner.headline)}<br>Recorded ${escape(row.corner.recorded)}</p>
        <p>Your audience: ${escape(wanted)} frames</p><p>Corner mix: ${escape(mix || 'No observed audiences')}</p></details></div>`;
    }).join('')}
    <p class="cs-score-note">Scores compare audience signals across these corners, not customer probabilities. Each audience’s share of sampled frames is normalized to the strongest corner. Core audiences decide the match; helpful audiences contribute up to 25%.</p>
  </section>`;
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
  replaceResults(renderLoading(data.corners));
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
      + `<div id="cs-dashboard">${renderProof()}${renderRanking(ranking, weights)}</div>`);
    activeProof = wireProof(ranking, weights, data);
    results.querySelectorAll('[data-count-audience]').forEach(button => button.addEventListener('click', () => activeProof.selectAudience(button.dataset.countAudience)));
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
      button.className = 'gallery-item';
      button.textContent = business;
      button.addEventListener('click', () => {
        input.value = business;
        input.setCustomValidity('');
        submit();
      });
      examples.append(button);
    }
    go.disabled = false;
    replaceResults(renderInitial(data));
    announce(`${data.corners.length} recorded corners ready. Describe your business to compare them.`);
  } catch (error) { showError(error, true); announce('The scouting sample could not be loaded.'); }
}

form.addEventListener('submit', submit);
document.querySelector('#edit-business').addEventListener('click', () => {
  form.classList.add('cs-query-expanded');
  document.querySelector('#edit-business').setAttribute('aria-expanded', 'true');
});
input.addEventListener('input', () => input.setCustomValidity(''));
document.querySelector('#reset').addEventListener('click', reset);
loadData();
