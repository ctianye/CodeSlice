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
  /** True for images/archives/databases; `content` is empty and Monaco is not mounted. */
  binary: boolean
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
  /** Files currently in the index (cumulative, not just this run's changes). */
  files: number
  /** Chunks currently in the index (cumulative). */
  chunks: number
  /** Chunks carrying a vector; 0 while running in keyword-only mode. */
  vectors: number
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
  system_prompt: string
  qa_prompt: string
  note_prompt: string
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
  default_system_prompt: string
  default_qa_prompt: string
  default_note_prompt: string
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

export interface OllamaPullStatus {
  state: 'idle' | 'running' | 'done' | 'error'
  model: string | null
  percent: number
  detail: string
  total: number
  completed: number
  error: string | null
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
  /**
   * Title of that note, shown on the floating label of a code range so a
   * snippet that has been distilled reads as its knowledge point rather than
   * its "关于 X 第 N-M 行" conversation title.
   */
  note_title?: string | null
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
  /** The conversation was renamed after its first answer, so the title names
   *  the knowledge point instead of the code location. */
  | 'title'

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

/* ----------------------------------- graph --------------------------------- */

/**
 * Node kinds the AST builder emits.
 *
 * `external` is a dependency outside the corpus (a base class or imported module
 * whose definition is not here). `rationale` is a docstring or marked comment that
 * explains the code it hangs off. Both are first-class so the view can style them
 * apart from ordinary symbols instead of pretending they are the same thing.
 */
export type GraphNodeKind = 'file' | 'class' | 'function' | 'module' | 'rationale' | 'external'

/** One node of the code knowledge graph. */
export interface GraphNode {
  id: string
  label: string
  kind: GraphNodeKind | string
  path: string
  language?: string
  start_line: number
  end_line: number
  /** Id of the containing class or file node; null for a file node itself. */
  parent?: string | null
  /** Total number of edges touching this node, computed by the backend. */
  degree: number
  community: number
  community_label: string
  /** Set on `external` nodes: the dependency is not defined inside this project. */
  external?: boolean
  /** Set on `rationale` nodes: whether it came from a docstring or a comment. */
  origin?: string
  note_id?: number
  content_md?: string
  project_name?: string
}

/**
 * Relation kinds emitted by the builder.
 *
 * Split rather than merged on purpose: `imports` (a whole module) and
 * `imports_from` (one name out of it) answer different questions, and `method`
 * (a class owns a function) is not the same as `contains` (lexical nesting).
 */
export type GraphRelation =
  | 'same_folder'
  | 'same_project'
  | 'shared_reference'
  | 'contains'
  | 'imports'
  | 'imports_from'
  | 'calls'
  | 'indirect_call'
  | 'references'
  | 'inherits'
  | 'implements'
  | 'method'
  | 'declares'
  | 'rationale_for'
  | 'uses'

/**
 * How sure the builder is about an edge.
 *
 * `EXTRACTED` means the relationship is literally in the source (a containment, an
 * import line, a call whose target was proven). `INFERRED` means it was deduced.
 * The UI must show the difference so a guess never reads as a fact.
 */
export type GraphConfidence = 'EXTRACTED' | 'INFERRED' | 'AMBIGUOUS'

export interface GraphEdge {
  source: string
  target: string
  relation: GraphRelation
  confidence: GraphConfidence
  line?: number | null
  evidence?: string[]
}

export interface GraphSummary {
  node_id: string
  scope: 'code' | 'notes'
  summary: string
  neighbor_count: number
  edge_count: number
}

/** One frame of the streaming relationship summary. */
export type GraphSummaryEvent =
  | { type: 'delta'; text: string }
  | ({ type: 'done' } & GraphSummary)
  | { type: 'error'; detail: string }

export interface GraphCommunity {
  id: number
  label: string
  size: number
  /** Node id of the most connected member, used as the community's anchor. */
  hub: string
  kind: string
  /** True for the pooled bucket of unconnected nodes. */
  pooled: boolean
}

export interface GraphStats {
  files: number
  symbols: number
  edges: number
  extracted: number
  inferred: number
  communities: number
  /** Nodes that record a docstring or marked comment. */
  rationale?: number
  /** Nodes standing in for a dependency defined outside the project. */
  external?: number
  /** Edge count per relation kind, for the header and the legend. */
  by_relation?: Record<string, number>
  built_hash?: string
  schema_version?: number
  /** True when the last read had to rebuild because the index moved on. */
  rebuilt?: boolean
}

export interface GraphPayload {
  nodes: GraphNode[]
  edges: GraphEdge[]
  communities: GraphCommunity[]
  stats: GraphStats
}

/** One connection of a node, as returned by the node-detail endpoint. */
export interface GraphNeighbor {
  direction: 'in' | 'out'
  relation: GraphRelation
  confidence: GraphConfidence
  id: string
  label: string
  kind: string
  path: string
  start_line: number
  community: number
}

export interface GraphNodeDetail {
  node: GraphNode | null
  edges: GraphNeighbor[]
  neighbors: GraphNeighbor[]
}

export type AgentStreamEvent = AgentEvent | AgentEventUnknown

/* ----------------------------------- notes --------------------------------- */

export interface Note {
  id: number
  project_id: number
  conversation_id: string | null
  title: string
  file_name: string
  content_md: string
  created_at: string
  updated_at: string
  /** True when the note owns a retrieval row, i.e. the agent can cite it. */
  indexed?: boolean
  /** Folder the user filed this note under; null means "未分类". */
  folder_id?: number | null
  /**
   * Display name of the project the note was written in. The knowledge base
   * spans every project, so the panel needs to say where a note came from.
   */
  project_name?: string | null
}

export interface CodeAnnotation {
  id: number
  project_id: number
  file_path: string
  start_line: number
  end_line: number
  selected_code: string
  /** Collapsed label; falls back to the body's first line for older rows. */
  title: string
  body: string
  created_at: string
  updated_at: string
}

/** A user-made folder in the knowledge base tree. */
export interface NoteFolder {
  id: number
  parent_id: number | null
  name: string
  created_at: string
  /** How many notes sit directly in this folder. */
  note_count: number
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
  file_name?: string
  content_md: string
  conversation_id?: string | null
  folder_id?: number | null
}

export interface NoteUpdateBody {
  title?: string
  file_name?: string
  content_md?: string
  folder_id?: number | null
  /** Set to true to actually move the note; without it folder_id is ignored. */
  move_folder?: boolean
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
