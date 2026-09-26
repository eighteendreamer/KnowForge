import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { after, before, test } from 'node:test';
import { KnowForge, KnowForgeError } from './index.js';

let server;
let baseUrl;
let behavior;
let requests;
before(async () => {
  server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    requests.push({ url: req.url, headers: req.headers, body: body ? JSON.parse(body) : undefined });
    behavior(req, res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}/v1`;
});
after(() => new Promise(resolve => server.close(resolve)));

function setup(handler) {
  requests = [];
  behavior = handler ?? ((_req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ code: 0, data: { results: [{ score: 0.032, score_calibrated: false }] } }));
  });
  return new KnowForge({ baseUrl, apiKey: 'test-key' });
}

test('search, lookup, tags and categories preserve the public contract', async () => {
  const sdk = setup();
  const result = await sdk.search('缓存穿透', { filters: { tags: ['Redis'] }, options: { rerank: false } });
  assert.equal(result.results[0].score, 0.032);
  assert.equal(result.results[0].score_calibrated, false);
  assert.equal(requests[0].url, '/v1/knowledge/search');
  assert.equal(requests[0].headers.authorization, 'Bearer test-key');
  assert.deepEqual(requests[0].body, { query: '缓存穿透', search_type: 'hybrid', top_k: 8, filters: { tags: ['Redis'] }, options: { rerank: false } });
  await sdk.lookup('doc_test', { page_start: 1, page_end: 2, include_chunks: false });
  assert.equal(requests[1].body.page_end, 2);
  await sdk.tags('后端开发/缓存');
  assert.equal(new URL(requests[2].url, baseUrl).searchParams.get('category'), '后端开发/缓存');
  await sdk.categories();
  assert.equal(requests[3].url, '/v1/knowledge/categories');
});

test('API error codes, request IDs and Retry-After survive without automatic retries', async () => {
  for (const [status, code] of [[400, 1002], [401, 2001], [403, 2002], [429, 3001], [503, 5002]]) {
    const sdk = setup((_req, res) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'X-Request-ID': 'trace-test', 'Retry-After': '60' });
      res.end(JSON.stringify({ code, message: '请求失败' }));
    });
    await assert.rejects(sdk.search('Redis'), error => {
      assert.ok(error instanceof KnowForgeError);
      assert.equal(error.status, status);
      assert.equal(error.code, code);
      assert.equal(error.requestId, 'trace-test');
      assert.equal(error.retryAfter, '60');
      return true;
    });
    assert.equal(requests.length, 1);
  }
});

test('invalid envelopes and non-JSON errors are rejected', async () => {
  for (const payload of [[], { code: false }, { code: 0 }, {}]) {
    const sdk = setup((_req, res) => res.end(JSON.stringify(payload)));
    await assert.rejects(sdk.categories(), KnowForgeError);
  }
  const sdk = setup((_req, res) => { res.writeHead(502); res.end('upstream private response'); });
  await assert.rejects(sdk.categories(), error => error.status === 502 && !error.message.includes('private'));
});

test('redirects never forward API keys and source paths cannot escape', async () => {
  const sdk = setup((_req, res) => { res.writeHead(302, { Location: baseUrl + '/redirected' }); res.end(); });
  await assert.rejects(sdk.categories(), KnowForgeError);
  assert.equal(requests.length, 1);
  for (const docId of ['..', '../api-keys', 'abc/def', 'a?key=123', '']) {
    assert.throws(() => sdk.source(docId), TypeError);
  }
  assert.equal(requests.length, 1);
  assert.ok(!JSON.stringify(sdk).includes('test-key'));
});

test('source download returns bytes', async () => {
  const sdk = setup((_req, res) => res.end('%PDF-1.7'));
  assert.equal(new TextDecoder().decode(await sdk.source('doc_test')), '%PDF-1.7');
});

test('timeouts and caller cancellation are distinguishable', async () => {
  setup((_req, res) => setTimeout(() => res.end('{}'), 60));
  const sdk = new KnowForge({ baseUrl, apiKey: 'test-key', timeout: 10 });
  await assert.rejects(sdk.categories(), /timed out/);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(sdk.categories({ signal: controller.signal }), /cancelled/);
});

test('unsafe remote HTTP and credential-bearing URLs are rejected', () => {
  for (const url of ['http://remote.example/v1', 'https://user:password@example.test/v1', 'https://example.test/v1?key=secret', 'https://example.test/v1#fragment']) {
    assert.throws(() => new KnowForge({ baseUrl: url, apiKey: 'test-key' }), TypeError);
  }
});
