import { coreAudiences, proofFrames, initialProofFrame, validFocus, sampleClock } from './proof.js';
import { mediaUrl, evidenceMedia, intervalPlayer } from './media-player.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const media = (url, extension) => Boolean(mediaUrl(url, extension));
const seconds = frame => frame.sampledAt ?? (frame.start + frame.end) / 2;

export function renderProof() {
  return `<section id="cs-proof" class="cs-card cs-proof" aria-labelledby="proof-heading">
    <div class="cs-proof-heading"><div><span class="cs-eyebrow cs-blue">See what supports the result</span><h3 id="proof-heading" tabindex="-1">Frame evidence</h3></div><div id="proof-corners" class="cs-proof-switch" aria-label="Inspect a recording"></div></div>
    <div id="proof-audiences" class="cs-proof-switch cs-proof-audiences" aria-label="Select a core audience"></div>
    <div class="cs-proof-claim-row"><p id="proof-claim"></p><span id="proof-view-label">Full frame</span></div>
    <div id="proof-viewport" class="cs-proof-viewport">
      <img id="proof-image" alt="" decoding="async">
      <video id="proof-context-video" controls playsinline preload="none" hidden></video>
      <p id="proof-image-loading" class="cs-proof-unavailable" role="status" hidden>Loading sampled frame…</p>
      <p id="proof-image-error" class="cs-proof-unavailable" role="status" hidden>Sample image unavailable. You can still play this moment.</p>
      <span id="proof-time" class="cs-proof-time"></span>
    </div>
    <div class="cs-proof-actions"><button id="proof-context" type="button" class="cs-proof-play">▶ Play this moment</button><div><button id="proof-detail" type="button" hidden>Full frame</button><button id="proof-enlarge" type="button">Enlarge</button></div></div>
    <p id="proof-selection" class="cs-proof-selection" aria-live="polite"></p>
    <p id="proof-source" class="cs-proof-source-line"></p>
    <p id="proof-media-status" class="cs-video-status" role="status"></p>
    <div class="cs-proof-rail-heading"><b id="proof-positive-count"></b><span>Choose a frame</span></div>
    <div id="proof-thumbnails" class="cs-proof-thumbnails" aria-label="Matching sampled frames"></div>
    <div class="cs-proof-marker-heading"><b id="proof-sample-count"></b><span><i aria-hidden="true"></i> Audience detected</span></div>
    <div id="proof-markers" class="cs-proof-markers" aria-label="Every sampled frame"></div>
    <p class="cs-proof-note" id="proof-sampling-note"></p>
    <p id="proof-recorded" class="cs-proof-note"></p>
    <dialog id="proof-enlargement" aria-labelledby="proof-enlargement-title"><div class="cs-proof-dialog-heading"><h3 id="proof-enlargement-title">Full sampled frame</h3><button type="button" id="proof-close">Close</button></div><img id="proof-enlarged-image" alt=""><p id="proof-enlarged-caption"></p></dialog>
  </section>`;
}

export function wireProof(ranking, weights, data) {
  const panel = document.querySelector('#cs-proof');
  const $ = selector => panel.querySelector(selector);
  const label = key => data.audiences[key] || key;
  const audiences = coreAudiences(weights, data.coreThreshold);
  const rows = ranking.slice(0, 2);
  let row = rows[0];
  let key = audiences[0];
  let frame;
  let detail = false;
  let context = false;
  let imageReady = false;
  let mediaSequence = 0;
  const image = $('#proof-image');
  const video = $('#proof-context-video');
  const dialog = $('#proof-enlargement');
  const playback = intervalPlayer(video, message => { $('#proof-media-status').textContent = message; });

  function stopVideo() {
    mediaSequence += 1;
    playback.clear();
    video.hidden = true;
    context = false;
  }

  function showFrame(selected, scrollThumbnail = false) {
    stopVideo();
    if (dialog.open) dialog.close();
    frame = selected;
    detail = Boolean(frame && validFocus(frame, key));
    imageReady = false;
    $('#proof-media-status').textContent = '';
    $('#proof-image-error').hidden = true;
    $('#proof-context').textContent = '▶ Play this moment';
    $('#proof-context').disabled = !frame || !evidenceMedia(frame);
    $('#proof-enlarge').disabled = true;
    image.hidden = true;
    image.removeAttribute('src');
    $('#proof-image-loading').hidden = !frame || !media(frame.image, 'jpg');
    if (frame) {
      image.alt = `${row.corner.camera} sampled at ${sampleClock(seconds(frame))}; ${label(key)} ${frame.counts[key] > 0 ? 'detected' : 'not detected'} by the vision model.`;
      if (media(frame.image, 'jpg')) image.src = frame.image;
      else {
        image.removeAttribute('src');
        $('#proof-image-error').hidden = false;
      }
      $('#proof-time').textContent = sampleClock(seconds(frame));
      $('#proof-selection').textContent = frame.counts[key] > 0
        ? `${label(key)} detected in this sampled frame · model estimate`
        : `${label(key)} not detected in this sample.`;
      $('#proof-source').textContent = `${sampleClock(seconds(frame))} sampled from ${frame.source}`;
      $('#proof-recorded').textContent = `${row.corner.camera} · Source timestamp: ${row.corner.recorded} · timezone not supplied`;
    } else {
      image.removeAttribute('src');
      $('#proof-time').textContent = '';
      $('#proof-selection').textContent = 'No sampled frames are available in this recording.';
      $('#proof-source').textContent = '';
      $('#proof-recorded').textContent = '';
    }
    for (const button of panel.querySelectorAll('[data-proof-frame]')) {
      const selected = button.dataset.proofFrame === frame?.segment_id;
      button.setAttribute('aria-pressed', String(selected));
      if (selected && scrollThumbnail && button.closest('#proof-thumbnails')) {
        // Scroll the rail without moving the page or hiding the proof controls.
        const rail = $('#proof-thumbnails');
        if (button.offsetLeft < rail.scrollLeft || button.offsetLeft + button.offsetWidth > rail.scrollLeft + rail.clientWidth) {
          rail.scrollLeft = button.offsetLeft - rail.clientWidth / 2 + button.offsetWidth / 2;
        }
      }
    }
    showCrop();
  }

  function showCrop() {
    const focus = frame && validFocus(frame, key);
    const cropped = focus && detail && !context;
    image.style.cssText = '';
    if (cropped) {
      const scale = 1 / Math.max(focus.width, focus.height);
      const clamp = value => Math.max(100 - 100 * scale, Math.min(0, value));
      image.style.width = `${scale * 100}%`;
      image.style.height = `${scale * 100}%`;
      image.style.left = `${clamp(50 - (focus.x + focus.width / 2) * scale * 100)}%`;
      image.style.top = `${clamp(50 - (focus.y + focus.height / 2) * scale * 100)}%`;
    }
    $('#proof-view-label').textContent = context ? 'Surrounding footage' : cropped ? 'Detail crop' : 'Full frame';
    $('#proof-detail').hidden = !focus || context;
    $('#proof-detail').textContent = cropped ? 'Full frame' : 'Detail crop';
    $('#proof-detail').setAttribute('aria-pressed', String(Boolean(cropped)));
    $('#proof-view-label').title = cropped ? 'Manually selected detail; the full sampled frame remains available.' : '';
  }

  function renderSelection() {
    const matches = proofFrames(row.corner, key);
    $('#proof-heading').textContent = `Frame evidence · ${row.corner.camera}`;
    $('#proof-claim').textContent = `${label(key)} detected in ${matches.length} of ${row.frames} sampled frames`;
    $('#proof-positive-count').textContent = `${matches.length} matching frame${matches.length === 1 ? '' : 's'}`;
    $('#proof-sample-count').textContent = `All ${row.frames} samples`;
    $('#proof-sampling-note').textContent = key === 'dog'
      ? 'Sampled every five seconds. The same animal may appear in several frames.'
      : 'Sampled every five seconds. The same person or vehicle may appear in several frames.';
    $('#proof-corners').innerHTML = rows.map((item, index) => `<button type="button" data-proof-corner="${escape(item.corner.id)}" aria-pressed="${item === row}">${index ? 'Runner-up' : 'Top recording'} · ${escape(item.corner.camera)}</button>`).join('');
    $('#proof-audiences').innerHTML = audiences.map(audience => `<button type="button" data-proof-audience="${escape(audience)}" aria-pressed="${audience === key}">${escape(label(audience))}</button>`).join('');
    $('#proof-audiences').hidden = audiences.length < 2;
    $('#proof-thumbnails').innerHTML = matches.length ? matches.map(item => `<button type="button" data-proof-frame="${escape(item.segment_id)}" data-sample-seconds="${seconds(item)}" aria-pressed="false" aria-label="Inspect ${escape(label(key))} sample at ${sampleClock(seconds(item))}">${media(item.image, 'jpg') ? `<img src="${escape(item.image)}" loading="lazy" alt=""><span class="cs-thumb-error" hidden>Image unavailable</span>` : '<span class="cs-thumb-error">Image unavailable</span>'}<span>${sampleClock(seconds(item))}</span></button>`).join('')
      : `<p class="cs-proof-empty">No ${escape(label(key).toLowerCase())} detected in the sampled frames in this recording. Inspect any sample below.</p>`;
    $('#proof-thumbnails').scrollLeft = 0;
    $('#proof-markers').innerHTML = row.corner.frames.map((item, index) => `<button type="button" data-proof-frame="${escape(item.segment_id)}" data-sample-seconds="${seconds(item)}" class="${item.counts[key] > 0 ? 'cs-proof-positive' : ''}" aria-pressed="false" aria-label="${sampleClock(seconds(item))}: ${escape(label(key))} ${item.counts[key] > 0 ? 'detected' : 'not detected'}"><span>${index + 1}</span></button>`).join('');
    showFrame(initialProofFrame(row.corner, key));
  }

  function selectAudience(audience) {
    if (!audiences.includes(audience)) return;
    row = rows[0];
    key = audience;
    renderSelection();
    $('#proof-heading').focus({ preventScroll: true });
    panel.scrollIntoView({ behavior: 'instant', block: 'nearest' });
  }

  panel.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.proofCorner) {
      row = rows.find(item => item.corner.id === button.dataset.proofCorner);
      renderSelection();
      $('#proof-corners').querySelector(`[aria-pressed="true"]`)?.focus({ preventScroll: true });
    } else if (button.dataset.proofAudience) {
      key = button.dataset.proofAudience;
      renderSelection();
      $('#proof-audiences').querySelector(`[aria-pressed="true"]`)?.focus({ preventScroll: true });
    } else if (button.dataset.proofFrame) {
      const selected = row.corner.frames.find(item => item.segment_id === button.dataset.proofFrame);
      if (selected) showFrame(selected, true);
    } else if (button.id === 'proof-detail') {
      detail = !detail;
      showCrop();
    } else if (button.id === 'proof-context' && frame) {
      if (context) showFrame(frame);
      else {
        context = true;
        const playing = ++mediaSequence;
        image.hidden = true;
        $('#proof-image-loading').hidden = true;
        $('#proof-image-error').hidden = true;
        video.hidden = false;
        playback.set(evidenceMedia(frame), true);
        video.setAttribute('aria-label', `${row.corner.camera}, ${sampleClock(frame.start)} to ${sampleClock(frame.end)}`);
        $('#proof-context').textContent = 'Back to frame';
        $('#proof-time').textContent = `${sampleClock(frame.start)}–${sampleClock(frame.end)}`;
        showCrop();
        video.play().catch(() => {
          if (context && playing === mediaSequence) $('#proof-media-status').textContent = video.error
            ? 'This clip could not be loaded. Select Back to frame to inspect the sampled image.'
            : 'Press play on the video to watch this moment.';
        });
      }
    } else if (button.id === 'proof-enlarge' && frame && imageReady) {
      video.pause();
      $('#proof-enlarged-image').src = frame.image;
      $('#proof-enlarged-image').alt = image.alt;
      $('#proof-enlarged-caption').textContent = `${row.corner.camera} · Full sampled frame at ${sampleClock(seconds(frame))}`;
      dialog.showModal();
    } else if (button.id === 'proof-close') dialog.close();
  });
  panel.addEventListener('load', event => {
    if (event.target === image && frame && image.getAttribute('src') === frame.image && image.naturalWidth > 0) {
      imageReady = true;
      image.hidden = context;
      $('#proof-image-loading').hidden = true;
      $('#proof-image-error').hidden = true;
      $('#proof-enlarge').disabled = false;
    }
  }, true);
  panel.addEventListener('error', event => {
    if (event.target === image && frame && image.getAttribute('src') === frame.image) {
      imageReady = false;
      image.hidden = true;
      $('#proof-image-loading').hidden = true;
      $('#proof-image-error').hidden = context;
      $('#proof-enlarge').disabled = true;
    } else if (event.target === video && context) {
      $('#proof-media-status').textContent = 'This clip could not be loaded. Select Back to frame to inspect the sampled image.';
    } else if (event.target.matches('#proof-thumbnails img')) {
      event.target.hidden = true;
      event.target.nextElementSibling.hidden = false;
    }
  }, true);
  panel.addEventListener('keydown', event => {
    const button = event.target.closest('[data-proof-frame]');
    if (!button || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const buttons = [...button.parentElement.querySelectorAll('button')];
    const current = buttons.indexOf(button);
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : Math.max(0, Math.min(buttons.length - 1, current + (event.key === 'ArrowLeft' ? -1 : 1)));
    event.preventDefault();
    buttons[target].focus({ preventScroll: true });
    buttons[target].click();
  });
  renderSelection();
  return { selectAudience, destroy: () => { stopVideo(); if (dialog.open) dialog.close(); } };
}
