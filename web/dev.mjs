import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { stat, access } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import profile from './api/profile.mjs';

const root = fileURLToPath(new URL('./public/', import.meta.url));
const workspace = fileURLToPath(new URL('../', import.meta.url));
const recordingRoot = path.resolve(workspace, process.env.HIGHLIGHT_OUTPUT_DIR || 'outputs', 'collection/media');
const imageRoot = path.resolve(workspace, process.env.HIGHLIGHT_OUTPUT_DIR || 'outputs', 'collection/images');
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css',
  '.js': 'text/javascript', '.json': 'application/json', '.mp4': 'video/mp4',
  '.svg': 'image/svg+xml', '.jpg': 'image/jpeg' };

createServer(async (request, response) => {
  try {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/api/profile') return await profile(request, response);
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' }).end();
      return;
    }
    let filename = path.resolve(root, '.' + decodeURIComponent(
      url.pathname === '/' ? '/index.html' : url.pathname,
    ));
    if (!filename.startsWith(root)) {
      response.writeHead(404).end();
      return;
    }
    if (/^\/recordings\/[a-f0-9]{64}\.mp4$/.test(url.pathname)) {
      filename = path.join(recordingRoot, path.basename(url.pathname));
    } else if (/^\/recording-images\/[a-f0-9]{64}\.jpg$/.test(url.pathname)) {
      filename = path.join(imageRoot, path.basename(url.pathname));
    } else if (url.pathname === '/catalog.json' || url.pathname === '/corners.json') {
      const local = path.join(root, path.basename(url.pathname, '.json') + '.local.json');
      try { await access(local); filename = local; } catch { /* Use the hosted snapshot. */ }
    }
    const info = await stat(filename);
    if (!info.isFile()) {
      response.writeHead(404).end();
      return;
    }
    response.setHeader('Content-Type', types[path.extname(filename)] || 'application/octet-stream');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Accept-Ranges', 'bytes');
    let start = 0, end = info.size - 1;
    if (request.headers.range) {
      const match = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range);
      if (!match || Number(match[1]) >= info.size || (match[2] && Number(match[2]) < Number(match[1]))) {
        response.writeHead(416, { 'Content-Range': `bytes */${info.size}` }).end();
        return;
      }
      start = Number(match[1]);
      end = match[2] ? Math.min(Number(match[2]), end) : end;
      response.statusCode = 206;
      response.setHeader('Content-Range', `bytes ${start}-${end}/${info.size}`);
    }
    response.setHeader('Content-Length', end - start + 1);
    if (request.method === 'HEAD') return response.end();
    createReadStream(filename, { start, end }).on('error', () => response.destroy()).pipe(response);
  } catch {
    if (!response.headersSent) response.writeHead(404);
    response.end();
  }
}).listen(Number(process.env.PORT || 3000), '127.0.0.1', () => {
  console.log(`Corner Scout: http://localhost:${process.env.PORT || 3000}`);
});
