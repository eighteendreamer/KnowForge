export type SearchType = 'semantic' | 'keyword' | 'fuzzy' | 'hybrid';
export interface SearchFilters {
  tags?: string[];
  category?: string | null;
  difficulty?: '初级' | '中级' | '高级' | null;
  exclude_tags?: string[];
}
export interface SearchOptions {
  highlight?: boolean;
  include_metadata?: boolean;
  rerank?: boolean;
  query_rewrite?: boolean;
  language?: 'zh' | 'en';
}
export interface SearchResult {
  id: string;
  doc_id: string;
  title: string;
  content: string;
  score: number;
  score_type: string;
  score_calibrated: boolean;
  source: string;
  page: number | null;
  highlight?: string;
  section?: string;
  tags?: string[];
  category?: string;
  difficulty?: string | null;
  page_end?: number | null;
  url?: string;
}
export interface SearchResponse {
  query: string;
  query_rewritten: string | null;
  search_type_used: SearchType;
  total: number;
  results: SearchResult[];
  suggested_tags: string[];
  took_ms: number;
}
export interface Category {
  name: string;
  path: string;
  count: number;
  children: Category[];
}
export interface LookupResponse {
  doc_id: string;
  title: string;
  source: string;
  content: string;
  elements: Array<{ type: string; text: string; page: number | null; level: number | null; char_count: number }>;
  chunks?: Array<{ id: string; content: string; page_start: number | null; page_end: number | null }>;
}
export class KnowForgeError extends Error {
  status?: number;
  code?: number;
  requestId?: string;
  retryAfter?: string;
  constructor(message: string, details?: { status?: number; code?: number; requestId?: string; retryAfter?: string });
}
export class KnowForge {
  constructor(config: { baseUrl: string; apiKey: string; timeout?: number });
  search(query: string, input?: { search_type?: SearchType; top_k?: number; filters?: SearchFilters; options?: SearchOptions; signal?: AbortSignal }): Promise<SearchResponse>;
  lookup(docId: string, input?: { page_start?: number | null; page_end?: number | null; include_chunks?: boolean; signal?: AbortSignal }): Promise<LookupResponse>;
  tags(category?: string, input?: { signal?: AbortSignal }): Promise<{ tags: Array<{ name: string; count: number; category: string | null }> }>;
  categories(input?: { signal?: AbortSignal }): Promise<{ tree: Category[] }>;
  source(docId: string, input?: { signal?: AbortSignal }): Promise<Uint8Array>;
}
