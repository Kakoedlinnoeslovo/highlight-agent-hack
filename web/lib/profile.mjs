import { Buffer } from 'node:buffer';

const MAX_BODY_BYTES = 4096;
const MAX_PROVIDER_BYTES = 65536;
const PROVIDER_TIMEOUT_MS = 10000;
const DEFAULT_ENDPOINT = 'https://api.inference.wandb.ai/v1';

export class ProfileError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.name = 'ProfileError';
    this.status = status;
  }
}

const length = (value) => [...value].length;
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value, expected) => {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => Object.hasOwn(value, key));
};

export function validateBusiness(value) {
  if (typeof value !== 'string') {
    throw new ProfileError('Describe your business in 3–200 characters.');
  }
  const business = value.trim();
  if (length(business) < 3 || length(business) > 200) {
    throw new ProfileError('Describe your business in 3–200 characters.');
  }
  return business;
}

export function validateProfile(value, config) {
  if (!object(value) || !exactKeys(value, ['customers', 'weights'])) {
    throw new Error('Return only customers and weights.');
  }
  if (typeof value.customers !== 'string' || length(value.customers.trim()) < 3
      || length(value.customers) > 300) {
    throw new Error('Customers must be a sentence of 3–300 characters.');
  }
  if (!Array.isArray(value.weights) || value.weights.length < 1 || value.weights.length > 8) {
    throw new Error('Return one to eight audience weights.');
  }
  const seen = new Set();
  const weights = value.weights.map((item) => {
    if (!object(item) || !exactKeys(item, ['key', 'weight', 'why'])
        || typeof item.key !== 'string' || !Object.hasOwn(config.audiences, item.key)
        || seen.has(item.key)) {
      throw new Error('Use each listed audience key at most once and no other keys.');
    }
    seen.add(item.key);
    if (typeof item.weight !== 'number' || !Number.isFinite(item.weight)
        || item.weight < 0 || item.weight > 1) {
      throw new Error('Audience weights must be numbers from zero to one.');
    }
    if (typeof item.why !== 'string' || length(item.why.trim()) < 1 || length(item.why) > 160) {
      throw new Error('Each audience needs a reason of 1–160 characters.');
    }
    return { key: item.key, weight: item.weight, why: item.why };
  });
  const cores = weights.filter((item) => item.weight >= config.coreThreshold).length;
  if (cores < 1 || cores > 2) {
    throw new Error('Mark one or two core customer audiences with weight one.');
  }
  return { customers: value.customers, weights };
}

export function applyNamedAudience(business, profile, config) {
  const named = Object.entries(config.named)
    .filter(([key, pattern]) => Object.hasOwn(config.audiences, key)
      && new RegExp(pattern, 'iu').test(business))
    .map(([key]) => key);
  const weights = profile.weights.map((item) => ({ ...item }));
  if (named.length) {
    for (const item of weights) {
      if (item.weight >= config.coreThreshold && !named.includes(item.key)) item.weight = 0.5;
    }
    for (const key of named) {
      const item = weights.find((weight) => weight.key === key);
      const override = { key, weight: 1, why: 'Named in your description' };
      if (item) Object.assign(item, override);
      else weights.push(override);
    }
  }
  return { customers: profile.customers, weights };
}

async function providerText(response) {
  const declared = Number(response.headers?.get?.('content-length') || 0);
  if (declared > MAX_PROVIDER_BYTES) throw new Error('Provider response too large.');
  if (!response.body?.getReader) {
    const text = await response.text();
    if (Buffer.byteLength(text) > MAX_PROVIDER_BYTES) throw new Error('Provider response too large.');
    return text;
  }
  const reader = response.body.getReader();
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_PROVIDER_BYTES) {
        await reader.cancel();
        throw new Error('Provider response too large.');
      }
      chunks.push(Buffer.from(value));
    }
    return Buffer.concat(chunks).toString('utf8');
  } finally {
    reader.releaseLock();
  }
}

export async function profileBusiness(business, {
  config, env = process.env, fetchImpl = globalThis.fetch, timeoutMs = PROVIDER_TIMEOUT_MS,
} = {}) {
  business = validateBusiness(business);
  if (!env.WANDB_API_KEY || !env.WANDB_MODEL) {
    throw new ProfileError('Business matching is not configured on this deployment.', 503);
  }
  let endpoint;
  try {
    const base = (env.WANDB_INFERENCE_BASE_URL || DEFAULT_ENDPOINT).replace(/\/+$/, '');
    endpoint = new URL(`${base}/chat/completions`);
    if (!['https:', 'http:'].includes(endpoint.protocol)) throw new Error('Invalid protocol.');
  } catch {
    throw new ProfileError('Business matching is not configured on this deployment.', 503);
  }
  const messages = [
    {
      role: 'system',
      content: `${config.system} Return only JSON matching this schema: ${JSON.stringify(config.schema)}`,
    },
    { role: 'user', content: JSON.stringify({ business }) },
  ];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.min(timeoutMs, PROVIDER_TIMEOUT_MS));
    let content;
    try {
      const headers = {
        authorization: `Bearer ${env.WANDB_API_KEY}`,
        'content-type': 'application/json',
      };
      if (env.WANDB_PROJECT) headers['OpenAI-Project'] = env.WANDB_PROJECT;
      const response = await fetchImpl(endpoint.toString(), {
        method: 'POST', headers, signal: controller.signal,
        body: JSON.stringify({
          model: env.WANDB_MODEL, messages, temperature: 0.1, max_tokens: 1800,
          response_format: { type: 'json_object' },
        }),
      });
      if (!response.ok) {
        await response.body?.cancel?.();
        throw new ProfileError('The business matching service is unavailable. Try again shortly.', 502);
      }
      const responseBody = JSON.parse(await providerText(response));
      content = responseBody?.choices?.[0]?.message?.content;
    } catch (error) {
      if (error instanceof ProfileError) throw error;
      if (controller.signal.aborted) {
        throw new ProfileError('Business matching timed out. Please try again.', 504);
      }
      throw new ProfileError('The business matching service is unavailable. Try again shortly.', 502);
    } finally {
      clearTimeout(timer);
    }
    try {
      if (typeof content !== 'string') throw new Error('Return a JSON object.');
      const profile = validateProfile(JSON.parse(content), config);
      return { ...applyNamedAudience(business, profile, config), model: env.WANDB_MODEL };
    } catch (error) {
      if (attempt === 1) {
        throw new ProfileError('The model could not produce a valid audience profile. Please try again.', 502);
      }
      messages.push(
        { role: 'assistant', content: typeof content === 'string' ? content : '{}' },
        { role: 'user', content: `Repair your JSON. Validation error: ${error.message.slice(0, 500)}` },
      );
    }
  }
  throw new ProfileError('The business matching service is unavailable. Try again shortly.', 502);
}

function header(request, name) {
  return request.headers?.get?.(name) ?? request.headers?.[name];
}

async function requestBody(request) {
  const declared = header(request, 'content-length');
  if (declared !== undefined && (!/^\d+$/.test(String(declared)) || Number(declared) > MAX_BODY_BYTES)) {
    throw new ProfileError('The request is too large.', 413);
  }
  let body = request.body;
  if (body === undefined) {
    const chunks = [];
    let size = 0;
    if (typeof request[Symbol.asyncIterator] === 'function') {
      for await (const chunk of request) {
        const bytes = Buffer.from(chunk);
        size += bytes.length;
        if (size > MAX_BODY_BYTES) throw new ProfileError('The request is too large.', 413);
        chunks.push(bytes);
      }
    }
    body = Buffer.concat(chunks).toString('utf8');
  }
  if (typeof body === 'string' || Buffer.isBuffer(body)) {
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) throw new ProfileError('The request is too large.', 413);
    try { body = JSON.parse(body.toString()); }
    catch { throw new ProfileError('Send a valid JSON object with a business description.'); }
  } else {
    let bytes;
    try { bytes = Buffer.byteLength(JSON.stringify(body) ?? ''); }
    catch { throw new ProfileError('Send a valid JSON object with a business description.'); }
    if (bytes > MAX_BODY_BYTES) throw new ProfileError('The request is too large.', 413);
  }
  if (!object(body)) throw new ProfileError('Send a JSON object with a business description.');
  return validateBusiness(body.business);
}

export function createHandler(config, dependencies = {}) {
  return async function profileHandler(request, response) {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Content-Type', 'application/json; charset=utf-8');
    try {
      if (request.method !== 'POST') {
        response.setHeader('Allow', 'POST');
        throw new ProfileError('Use POST to describe a business.', 405);
      }
      if (!/^\s*application\/json\s*(?:;|$)/i.test(String(header(request, 'content-type') || ''))) {
        throw new ProfileError('Send the business description as application/json.', 415);
      }
      const business = await requestBody(request);
      const result = await profileBusiness(business, { config, ...dependencies });
      response.statusCode = 200;
      response.end(JSON.stringify(result));
    } catch (error) {
      const known = error instanceof ProfileError;
      response.statusCode = known ? error.status : 500;
      response.end(JSON.stringify({ error: known ? error.message : 'Business matching could not complete. Please try again.' }));
    }
  };
}
