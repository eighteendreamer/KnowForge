import { KnowForge, KnowForgeError } from './index.js';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { baseUrl, apiKey } = JSON.parse(input);
const sdk = new KnowForge({ baseUrl, apiKey, timeout: 180_000 });
const runs = [];
for (const search_type of ['semantic', 'keyword', 'fuzzy', 'hybrid']) {
  const started = performance.now();
  const result = await sdk.search('Redis 缓存穿透如何解决', { search_type, top_k: 5 });
  if (!result.results.length) throw new Error(`No real results for ${search_type}`);
  const first = result.results[0];
  const source = await sdk.source(first.doc_id);
  const detail = await sdk.lookup(first.doc_id);
  if (!source.length || !detail.content) throw new Error('Source traceability failed');
  runs.push({ mode: search_type, results: result.results.length, source_bytes: source.length, took_ms: Math.round(performance.now() - started) });
}
const tags = await sdk.tags();
const categories = await sdk.categories();
try {
  await sdk.search(' ');
  throw new Error('Invalid query was accepted');
} catch (error) {
  if (!(error instanceof KnowForgeError) || error.code !== 1002) throw error;
}
process.stdout.write(JSON.stringify({ language: 'javascript', runs, tags: tags.tags.length, categories: categories.tree.length, validation_error: 1002 }));
