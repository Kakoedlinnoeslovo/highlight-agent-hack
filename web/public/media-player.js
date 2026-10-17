/** Only generated same-origin media and HTTPS Blob objects are playable. */
export function mediaUrl(value, extension = 'mp4') {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value, 'http://corner-scout.local');
    const local = value.startsWith('/') && !value.startsWith('//') && url.origin === 'http://corner-scout.local';
    const blob = url.protocol === 'https:' && url.hostname.endsWith('.blob.vercel-storage.com');
    if ((!local && !blob) || url.username || url.password || !url.pathname.toLowerCase().endsWith(`.${extension}`)) return null;
    return value;
  } catch { return null; }
}

/** Legacy clips start at zero; full recording references use source timestamps. */
export function evidenceMedia(frame) {
  const recording = mediaUrl(frame?.mediaUrl || frame?.media?.url);
  if (recording) return { url: recording, start: Number(frame.start), end: Number(frame.end) };
  const clip = mediaUrl(frame?.clip);
  if (!clip) return null;
  const fragment = /#t=(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)$/.exec(clip);
  return fragment ? { url: clip.replace(/#.*$/, ''), start: Number(fragment[1]), end: Number(fragment[2]) }
    : { url: clip, start: 0, end: Number(frame.end) - Number(frame.start) };
}

/** One resource at a time. Remove listeners before changing source to ignore stale loads. */
export function intervalPlayer(video, onStatus = () => {}) {
  let generation = 0;
  let cleanup = () => {};
  let selection = null;
  function clear() {
    generation += 1;
    cleanup();
    cleanup = () => {};
    selection = null;
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  function set(interval, autoplay = false) {
    clear();
    const url = mediaUrl(interval?.url);
    const start = Number(interval?.start), end = Number(interval?.end);
    if (!url || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
      onStatus('This interval has no playable video.');
      return false;
    }
    const current = generation;
    selection = { url, start, end };
    let ready = false;
    let startPending = autoplay;
    const handlers = [];
    const on = (event, fn) => { video.addEventListener(event, fn); handlers.push([event, fn]); };
    cleanup = () => handlers.forEach(([event, fn]) => video.removeEventListener(event, fn));
    const active = () => current === generation;
    const fail = message => { if (active()) { video.pause(); onStatus(message); } };
    const play = () => {
      if (!active() || !startPending) return;
      startPending = false;
      const attempt = video.play();
      attempt?.catch(() => { if (active()) onStatus('Press play to watch this interval.'); });
    };
    const atStart = () => {
      if (!active() || ready) return;
      if (Number.isFinite(video.duration) && start >= video.duration) {
        fail('This interval is outside the available video.');
        return;
      }
      ready = true;
      if (Number.isFinite(video.duration)) selection.end = Math.min(end, video.duration);
      try { video.currentTime = start; } catch { fail('This video could not seek to the selected interval.'); return; }
      if (!video.seeking) play();
    };
    on('loadedmetadata', atStart);
    on('seeked', () => { if (active() && ready) play(); });
    on('play', () => {
      if (!active() || !ready) return;
      if (video.currentTime < start || video.currentTime >= selection.end - 0.02) video.currentTime = start;
    });
    on('timeupdate', () => {
      if (!active() || !ready) return;
      if (video.currentTime >= selection.end - 0.02) {
        video.pause();
        if (video.currentTime !== selection.end) video.currentTime = selection.end;
      } else if (video.currentTime < start - 0.05) video.currentTime = start;
    });
    on('error', () => fail('This video could not be loaded. Try another interval.'));
    onStatus('');
    video.src = url;
    video.preload = 'metadata';
    video.load();
    if (video.readyState >= 1) atStart();
    return true;
  }
  return { set, clear, destroy: clear, get selection() { return selection; } };
}
