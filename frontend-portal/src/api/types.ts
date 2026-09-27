export type Role = 'super_admin' | 'content_admin' | 'end_user'

export interface Account {
  id: number
  username: string
  role: Role
  status: 'active' | 'disabled'
}

export interface ApiKeyRow {
  id: number
  name: string
  key_prefix: string
  status: 'active' | 'disabled' | 'revoked'
  scopes: string[]
  rate_limit_per_day: number
  rate_limit_per_minute: number
  total_calls: number
  expires_at: string | null
  created_at: string
  last_used_at: string | null
}

export interface ApiLogRow {
  id: number
  api_key_id: number | null
  endpoint: string
  method: string
  query: string | null
  search_type: string | null
  top_k: number | null
  result_count: number | null
  latency_ms: number | null
  status_code: number | null
  error_message: string | null
  created_at: string
}

export interface OverviewData {
  counts: {
    balance_cent: number
    active_keys: number
    total_calls: number
    today_calls: number
    avg_latency_ms: number
    p95_latency_ms: number
  }
  trend: { date: string; count: number }[]
  by_search_type: { search_type: string; count: number }[]
  by_status_code: { status_code: number; count: number }[]
  top_queries: { query: string; count: number }[]
}

export interface TransactionRow {
  id: number
  amount_cent: number
  channel: string
  operator_id: number | null
  note: string | null
  created_at: string
}

export interface RechargePackageOption {
  id: number
  label: string
  amount_cent: number
  bonus_cent: number
}

export interface RechargeChannelOption {
  code: string
  display_name: string
  channel_type: string
  orderable: boolean
}

export interface WalletData {
  balance_cent: number
  transactions: TransactionRow[]
  packages: RechargePackageOption[]
  channels: RechargeChannelOption[]
}

export interface PaymentOrderData {
  out_trade_no: string
  status: 'created' | 'pending' | 'paid' | 'expired' | 'failed'
  channel_name: string
  channel_code: string
  package_id: number | null
  amount_cent: number
  bonus_cent: number
  discount_cent: number
  payable_cent: number
  credited_cent: number
  currency: string
  code_url: string | null
  redirect_url: string | null
  provider_trade_no: string | null
  created_at: string
  expires_at: string
  paid_at: string | null
}

export interface PromoQuote {
  applied: boolean
  label: string
  amount_cent: number
  bonus_cent: number
  discount_cent: number
  payable_cent: number
  credited_cent: number
  reason: string
}

export interface OrderSyncData extends PaymentOrderData {
  sync: { ok: boolean; detail: string }
  balance_cent?: number
}

export interface Page<T> {
  items: T[]
  total: number
}

export interface SearchResult {
  id: string
  doc_id: string
  title: string
  content: string
  score: number
  score_type: string
  score_calibrated: boolean
  source: string
  page: number | null
  merged_ids?: string[]
  highlight?: string | null
  section?: string
  tags?: string[]
  category?: string
  difficulty?: string | null
  page_end?: number | null
  url?: string
}

export interface SearchResponse {
  query: string
  query_rewritten: string | null
  search_type_used: string
  total: number
  results: SearchResult[]
  suggested_tags: string[]
  took_ms: number
}

export interface PublicTag {
  name: string
  count: number
  category: string | null
}

export interface CategoryNode {
  name: string
  path: string
  count: number
  children: CategoryNode[]
}

export interface DocsField {
  field: string
  type: string
  meaning: string
}

export interface DocsRequestField {
  name: string
  type: string
  required: boolean
  default: unknown
  description: string
}

export interface DocsEndpoint {
  method: string
  path: string
  summary: string
  purpose: string
  notes: string[]
  auth_required: boolean
  request_fields: DocsRequestField[]
  response_fields: DocsField[]
  result_fields: DocsField[]
  response_examples: { success?: string; failure?: string }
  examples: { language: string; code: string }[]
  responses: string[]
}

export interface DocsData {
  base_url: string
  version: string
  auth: string
  envelope: { success: string; error: string }
  error_codes: { code: number; http: number; meaning: string }[]
  rate_limits: {
    scope: string
    counters: { name: string; default: number; unit: string; meaning: string }[]
    on_exceed: { http: number; code: number; header: string; client_rule: string }
    shared_budget: string
    quota_note: string
  }
  performance: {
    measure: string
    throughput: { scenario: string; sustainable: string; p95: string; note: string }[]
    latency: { scenario: string; value: string; note: string }[]
    bottleneck: string
  }
  quickstart: { step: number; title: string; detail: string }[]
  sdks: {
    language: string
    package: string
    install: string
    requires: string
    client: string
    methods: string[]
    note: string
  }[]
  changelog: { date: string; changes: string[] }[]
  endpoints: DocsEndpoint[]
}
