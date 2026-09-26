export type AccountRole = 'super_admin' | 'content_admin' | 'end_user'

export interface User {
  id: number
  username: string
  role: AccountRole
  status: 'active' | 'disabled'
  balance_cent?: number
  created_at?: string
}

export interface DocumentRow {
  id: string
  doc_id: string
  title: string
  source: string
  file_type: 'pdf' | 'html'
  file_size: number
  status: string
  upload_time: string
  total_pages: number | null
  total_chunks: number
  category_id: number | null
  is_public: boolean
  parse_error: string | null
  tags: string[]
  tag_total: number
}

export interface ChunkRow {
  chunk_id: string
  chunk_index: number
  text: string
  section_path: string[]
  page_start: number | null
  page_end: number | null
  token_count: number
  difficulty: string | null
}

export interface TaskRow {
  id: string
  doc_id: string
  title: string
  task_type: string
  status: string
  stage: string | null
  progress: number
  attempts: number
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface TagRow {
  id: number
  name: string
  color: string
  auto_generated: boolean
  confidence: number
  review_status: 'pending' | 'approved' | 'rejected'
  document_count: number
}

export interface CategoryRow {
  id: number
  name: string
  path: string
  parent_id: number | null
  sort_order: number
  document_count: number
  children?: CategoryRow[]
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
  owner_username?: string | null
  owner_role?: AccountRole | null
}

export interface ApiLogRow {
  id: number
  endpoint: string
  query: string | null
  search_type: string | null
  status_code: number
  latency_ms: number
  created_at: string
  api_key_id?: number | null
  method?: string
  top_k?: number | null
  result_count?: number | null
  error_message?: string | null
  key_name?: string
  owner_username?: string | null
  owner_role?: AccountRole | null
}

export interface TransactionRow {
  id: number
  amount_cent: number
  channel: string
  operator_id: number | null
  note: string | null
  created_at: string
}

export interface RechargePackageRow {
  id: number
  label: string
  amount_cent: number
  bonus_cent: number
  enabled: boolean
  sort_order: number
}

export interface CredentialFieldSpec {
  key: string
  label: string
  help: string
  secret: boolean
  required: boolean
  editable: boolean
  default: string | null
  options: string[]
  max_length: number
  multiline: boolean
}

export interface ChannelTypeSpec {
  channel_type: string
  display: string
  payable: boolean
  fields: CredentialFieldSpec[]
}

export interface CredentialRowView {
  key: string
  label: string
  secret: boolean
  configured: boolean
  fingerprint: string
  key_version: number
  set_at: string
  value: string | null
}

export interface ChannelConfiguration {
  payable: boolean
  complete: boolean
  missing: string[]
  problem: string | null
}

export interface RechargeChannelRow {
  id: number
  code: string
  display_name: string
  channel_type: string
  enabled: boolean
  updated_at: string
  credentials: CredentialRowView[]
  configuration: ChannelConfiguration
}

export interface SelfCheckResult {
  channel_id: number
  channel_type: string
  mode: string
  passed: boolean
  checks: { name: string; ok: boolean; detail: string }[]
}

export interface KeyOwnerRow {
  id: number
  username: string
  role: AccountRole
  keys: number
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
  highlight?: string
  section?: string
  tags?: string[]
  category?: string
  difficulty?: string
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

export interface EvaluationRow {
  id: number
  query: string
  chunk_id: string
  judgment: number
  judge_type: string
  notes: string | null
  created_at: string
}

export interface AuditRow {
  id: number
  operator_id: number | null
  action: string
  target_type: string
  target_id: string
  client_ip: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export interface DashboardData {
  counts: { documents: number; chunks: number; tags: number; today_calls: number }
  document_statuses: Record<string, number>
  trend: { date: string; count: number }[]
  popular_queries: { query: string; count: number }[]
  recent_tasks: Pick<TaskRow, 'id' | 'title' | 'status' | 'stage' | 'progress' | 'updated_at'>[]
}

export interface Page<T> { items: T[]; total: number }
