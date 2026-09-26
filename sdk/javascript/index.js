export class KnowForgeError extends Error {
  constructor(message, { status, code, requestId, retryAfter } = {}) {
    super(message);
    this.name = 'KnowForgeError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

export class KnowForge {
  #baseUrl;
  #apiKey;
  #timeout;

  constructor({ baseUrl, apiKey, timeout = 120_000 }) {
    const url = new URL(baseUrl);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && local))
        || url.username || url.password || url.search || url.hash) {
      throw new TypeError('baseUrl must be HTTPS (HTTP is allowed only on loopback), without credentials, query or fragment');
    }
    if (typeof apiKey !== 'string' || !apiKey.trim() || /[\r\n]/.test(apiKey)) {
      throw new TypeError('A non-empty API key is required');
    }
    if (!Number.isFinite(timeout) || timeout <= 0) throw new TypeError('timeout must be positive');
    this.#baseUrl = baseUrl.replace(/\/+$/, '') + '/';
    this.#apiKey = apiKey;
    this.#timeout = timeout;
  }

  async #request(method, path, { body, category, binary = false, signal } = {}) {
    const url = new URL('knowledge/' + path, this.#baseUrl);
    if (category) url.searchParams.set('category', category);
    const timeout = AbortSignal.timeout(this.#timeout);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let response;
    try {
      response = await fetch(url, {
        method,
        redirect: 'error',
        headers: {
          Authorization: 'Bearer ' + this.#apiKey,
          Accept: binary ? 'application/octet-stream' : 'application/json',
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: combined,
      });
      if (binary && response.ok) return new Uint8Array(await response.arrayBuffer());
      const details = {
        status: response.status,
        requestId: response.headers.get('X-Request-ID') ?? undefined,
        retryAfter: response.headers.get('Retry-After') ?? undefined,
      };
      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        if (combined.aborted) throw error;
        throw new KnowForgeError('KnowForge returned a non-JSON response', details);
      }
      if (!payload || typeof payload !== 'object' || !Number.isInteger(payload.code)) {
        throw new KnowForgeError('KnowForge returned an invalid response envelope', details);
      }
      if (!response.ok || payload.code !== 0) {
        throw new KnowForgeError(
          typeof payload.message === 'string' ? payload.message : 'KnowForge request failed',
          { ...details, code: payload.code },
        );
      }
      if (!Object.hasOwn(payload, 'data')) {
        throw new KnowForgeError('KnowForge response is missing data', details);
      }
      return payload.data;
    } catch (error) {
      if (error instanceof KnowForgeError) throw error;
      if (combined.aborted) {
        throw new KnowForgeError(signal?.aborted ? 'KnowForge request cancelled' : 'KnowForge request timed out');
      }
      throw new KnowForgeError('KnowForge connection failed');
    }
  }

  search(query, { search_type = 'hybrid', top_k = 8, filters = {}, options = {}, signal } = {}) {
    return this.#request('POST', 'search', { body: { query, search_type, top_k, filters, options }, signal });
  }

  lookup(doc_id, { page_start = null, page_end = null, include_chunks = true, signal } = {}) {
    return this.#request('POST', 'lookup', { body: { doc_id, page_start, page_end, include_chunks }, signal });
  }

  tags(category, { signal } = {}) {
    return this.#request('GET', 'tags', { category, signal });
  }

  categories({ signal } = {}) {
    return this.#request('GET', 'categories', { signal });
  }

  source(docId, { signal } = {}) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(docId)) throw new TypeError('Invalid document ID');
    return this.#request('GET', `documents/${docId}/file`, { binary: true, signal });
  }
}
