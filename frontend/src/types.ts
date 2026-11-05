/**
 * Renderer-side type mirror of docs/API_CONTRACT.md (V1).
 * Keep this file in sync with the contract; the backend is the source of truth.
 */

/* ---------------------------------- system --------------------------------- */

export interface Health {
  status: string
  version: string
  data_dir: string
  /** Sent by the backend but absent from the contract. */
  app?: string
}

/* -------------------------------- projects --------------------------------- */

export interface Project {
  id: number
  name: string
  path: string
  /** Extra ignore globs, stored as JSON text. Present but undocumented. */
  excluded: string | null
  created_at: string
  last_opened_at: string | null
  indexed_at: string | null
}

export interface TreeNode {
  name: string
  path: string
  is_dir: boolean
  children: TreeNode[] | null
}

export interface FileContent {
  path: string
  language: string
  content: string
  size: number
  mtime: number
  truncated: boolean
}

export interface GitStatus {
  is_repo: boolean
  branch: string | null
  head: string | null
  dirty: boolean
  staged: string[]
  modified: string[]
  untracked: string[]
}

export interface GitDiff {
  path: string | null
  diff: string
}

/* ---------------------------- indexing / search ----------------------------- */

export type JobStatus = 'pending' | 'running' | 'done' | 'error'

export interface Job {
  id: number
  kind: string
  status: JobStatus
  progress: number
  message: string
  /** `skipped` and `project_id` are returned but not part of the contract. */
  stats: { files: number; chunks: number; vectors: number; skipped?: number } | null
  error: string | null
}

export type Retriever = 'keyword' | 'vector' | 'hybrid'

/** GET /api/projects/{id}/index — beyond the contract, needed for model switches. */
export interface IndexStatus {
  project: Project
  last_job: Job | null
  files: number
  indexed_at: string | null
  /** Distinct embed_model values present in this project's vectors. */
  embed_models: string[]
  /** Model the backend would use right now (null when unconfigured). */
  active_embed_model: string | null
  embedding_configured: boolean
}

export interface RetrievedChunk {
  path: string
  start_line: number
  end_line: number
  symbol: string | null
  kind: string
  content: string
  score: number
  retriever: Retriever
  /** Sent by the backend (used for editor syntax highlighting). */
  language?: string
}

/* --------------------------------- settings -------------------------------- */

export type ModelProtocol = 'openai-compatible' | 'anthropic'

export interface ModelConfig {
  protocol: ModelProtocol
  model: string
  base_url: string | null
  temperature: number
  /** Extra output tokens. Returned by the backend but absent from the contract. */
  max_tokens: number
  api_key_configured: boolean
}

export interface EmbeddingConfig {
  protocol: ModelProtocol
  model: string
  base_url: string | null
  /** Never populated by the server; write-only on PUT. */
  api_key?: string | null
  api_key_configured?: boolean
  dimensions?: number | null
}

export interface ModelSettings {
  chat: ModelConfig
  embedding: EmbeddingConfig | null
}

/** PUT body: an api_key here is write-only and never echoed back. */
export interface ModelSettingsUpdate {
  /** Omit to leave the chat model untouched (the backend treats a missing key as "no change"). */
  chat?: Partial<ModelConfig> & { api_key?: string | null }
  embedding?: (Partial<EmbeddingConfig> & { api_key?: string | null }) | null
}

export interface ModelTestResult {
  ok: boolean
  latency_ms: number | null
  detail: string
}

/* ---------------------------------- ollama --------------------------------- */

/** One curated local embedding model the settings screen can offer. */
export interface OllamaPreset {
  id: string
  model: string
  label: string
  /** Human-readable download size, e.g. "1.2 GB". */
  size: string
  size_bytes: number
  context: string
  params: string
  description: string
  /** e.g. "CPU 可跑". */
  requirement: string
}

export interface OllamaStatus {
  installed: boolean
  version: string | null
  /** Model names reported by the local daemon, e.g. "bge-m3:latest". */
  models: string[]
  base_url: string
  /** The OpenAI-compatible prefix to store as the embedding base_url. */
  openai_base_url: string
  presets: OllamaPreset[]
  /** Why the probe failed; null when installed. */
  error: string | null
}

export interface OllamaPullEvent {
  type: 'progress' | 'error' | 'done'
  model: string
  status?: string
  detail?: string
  total?: number
  completed?: number
  percent?: number
}

/* -------------------------------- conversations ----------------------------- */

export type ConversationMode = 'snippet' | 'project'

export interface Conversation {
  id: string
  project_id: number
  title: string
  mode: ConversationMode
  file_path: string | null
  start_line: number | null
  end_line: number | null
  created_at: string
  updated_at: string
  /** Owner project's display name; present on list responses. */
  project_name?: string | null
  /** True when a note was already generated from this conversation. */
  has_note?: boolean
  /** Id of that note, so the card can jump straight to it. */
  note_id?: number | null
}

export interface Citation {
  path: string
  start_line: number
  end_line: number
  symbol: string | null
}

export type MessageRole = 'user' | 'assistant' | 'tool'

export interface Message {
  id: string
  role: MessageRole
  content: string
  created_at: string
  citations: Citation[] | null
  meta: Record<string, unknown> | null
}

export type InterruptKind = 'write_file' | 'run_command'

export interface InterruptPayload {
  path?: string
  diff?: string
  command?: string
  cwd?: string
  reason: string
  tool_call_id: string
}

export interface Interrupt {
  id: string
  kind: InterruptKind
  payload: InterruptPayload
}

export interface ConversationDetail {
  conversation: Conversation
  messages: Message[]
  pending_interrupt: Interrupt | null
}

export interface CreateConversationBody {
  project_id: number
  title?: string | null
  file_path?: string | null
  start_line?: number | null
  end_line?: number | null
  selected_code?: string | null
  mode: ConversationMode
}

export type ApprovalDecision = 'approve' | 'deny' | 'approve_session' | 'approve_always'

export interface ResumeBody {
  interrupt_id: string
  decision: ApprovalDecision
  remember_pattern?: string | null
  note?: string | null
}

/* ----------------------------------- SSE ----------------------------------- */

export type AgentStage =
  | 'classify'
  | 'retrieve'
  | 'plan'
  | 'execute'
  | 'verify'
  | 'summarize'

export interface AgentEventToken {
  type: 'token'
  delta: string
}
export interface AgentEventStatus {
  type: 'status'
  stage: AgentStage
  detail?: string
}
export interface AgentEventCitation extends Citation {
  type: 'citation'
}
export interface AgentEventToolCall {
  type: 'tool_call'
  name: string
  args: Record<string, unknown>
  call_id: string
}
export interface AgentEventToolResult {
  type: 'tool_result'
  call_id: string
  ok: boolean
  summary: string
}
export interface AgentEventApprovalRequired {
  type: 'approval_required'
  id: string
  kind: InterruptKind
  payload: InterruptPayload
}
export interface AgentEventMessage {
  type: 'message'
  message: Message
}
export interface AgentEventError {
  type: 'error'
  detail: string
}
export interface AgentEventDone {
  type: 'done'
}

export type AgentEvent =
  | AgentEventToken
  | AgentEventStatus
  | AgentEventCitation
  | AgentEventToolCall
  | AgentEventToolResult
  | AgentEventApprovalRequired
  | AgentEventMessage
  | AgentEventError
  | AgentEventDone

/** Unknown event types are surfaced instead of dropped, so the UI can stay honest. */
export interface AgentEventUnknown {
  type: 'unknown'
  raw: unknown
}

export type AgentStreamEvent = AgentEvent | AgentEventUnknown

/* ----------------------------------- notes --------------------------------- */

export interface Note {
  id: number
  project_id: number
  conversation_id: string | null
  title: string
  content_md: string
  created_at: string
  updated_at: string
  /** True when the note owns a retrieval row, i.e. the agent can cite it. */
  indexed?: boolean
}

/** Returned by the note-from-message endpoint, which also reports how it went. */
export interface GeneratedNote extends Note {
  summary_failed: boolean
  summary_error: string | null
  refs: number
  indexed: boolean
}

export interface NoteExport {
  path: string
  directory: string
  filename: string
  bytes: number
}

export interface NoteCreateBody {
  project_id: number
  title: string
  content_md: string
  conversation_id?: string | null
}

/* ---------------------------------- anchors -------------------------------- */

export type AnchorStatus = 'unchanged' | 'modified' | 'moved' | 'deleted'

export interface Anchor {
  id: number
  conversation_id: string
  file_path: string
  commit_sha: string | null
  symbol: string | null
  start_line: number
  end_line: number
  snippet: string | null
  code_hash: string | null
  status: AnchorStatus
  checked_at: string | null
}

export interface AnchorRefreshResult {
  updated: number
  deleted: number
  moved: number
  /** Not in the contract; the backend also reports how many are still fine. */
  unchanged?: number
}

/* ------------------------------ command rules ------------------------------ */

export type RuleScope = 'once' | 'session' | 'always'

export interface CommandRule {
  id: number
  project_id: number
  pattern: string
  scope: RuleScope
  needs_confirm: boolean
  created_at: string
}

export interface CommandRuleCreateBody {
  pattern: string
  scope: RuleScope
  needs_confirm: boolean
}
