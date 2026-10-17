/** Local publishing helper. Tokens come from the environment, never a browser request. */
import { createReadStream } from 'node:fs';
import { list, put } from '@vercel/blob';

let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const job = JSON.parse(input);
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  if (!token) throw new Error('Set BLOB_READ_WRITE_TOKEN in the local environment.');
  if (job.action === 'list') {
    const blobs = [];
    let cursor;
    do {
      const page = await list({ token, limit: 1000, cursor });
      blobs.push(...page.blobs.map(({ pathname, url, size }) => ({ pathname, url, size })));
      cursor = page.hasMore ? page.cursor : undefined;
    } while (cursor);
    process.stdout.write(JSON.stringify({ blobs }));
  } else if (job.action === 'upload') {
    if (!/^(recordings\/[a-f0-9]{64}\.mp4|frames\/[a-f0-9]{64}\.jpg)$/.test(job.pathname)) throw new Error('Invalid media pathname.');
    const result = await put(job.pathname, createReadStream(job.path), {
      token, access: 'public', addRandomSuffix: false, allowOverwrite: false,
      multipart: false, contentType: job.pathname.endsWith('.jpg') ? 'image/jpeg' : 'video/mp4', cacheControlMaxAge: 31536000,
    });
    process.stdout.write(JSON.stringify({ url: result.url, pathname: result.pathname }));
  } else throw new Error('Unknown media operation.');
} catch (error) {
  // Provider errors can contain headers or URLs; expose only their type to the caller.
  process.stderr.write(`Blob operation failed (${error.name || 'Error'}). Check local credentials and quota.\n`);
  process.exitCode = 1;
}
