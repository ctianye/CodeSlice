import { apiBase, getToken } from './bridge'

/**
 * Workflow-node API.
 *
 * Deliberately separate from `api.ts`: the graph panel is a small, self-contained
 * feature and keeps its own types and error type here rather than growing the
 * shared client (and its `types.ts`) for one screen.
 */
export class GraphApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'GraphApiError'
    this.status = status
    this.detail = detail
  }
}

function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    // FastAPI 422 puts an array of field errors here.
    if (Array.isArray(detail)) {
      const parts = detail.map((item) => {
        if (item && typeof item === 'object') {
          const loc = (item as { loc?: unknown }).loc
          const msg = (item as { msg?: unknown }).msg
          const where = Array.isArray(loc) ? loc.join('.') : ''
          return where ? `${where}: ${String(msg ?? '')}` : String(msg ?? '')
        }
        return String(item)
      })
      return parts.filter(Boolean).join('; ') || fallback
    }
  }
  return fallback
}

async function parseError(res: Response): Promise<GraphApiError> {
  let body: unknown = null
  const text = await res.text().catch(() => '')
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  return new GraphApiError(res.status, detailFromBody(body, text || `请求失败（HTTP ${res.status}）`))
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const token = getToken()
  if (token) h['X-Session-Token'] = token
  return h
}

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  const init: RequestInit = {
    method,
    headers: headers(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    signal,
  }
  if (body !== undefined) init.body = JSON.stringify(body)

  const res = await fetch(`${apiBase()}${path}`, init)
  if (!res.ok) throw await parseError(res)
  const text = await res.text()
  if (!text) return undefined as T
  return JSON.parse(text) as T
}

/* ---------------------------------- types ---------------------------------- */

export type NodeKind = 'input' | 'llm' | 'tool' | 'output'

/** One editable config field, as described by the backend. */
export interface EditableField {
  type: 'int' | 'float' | 'bool' | 'string' | 'text'
  min?: number
  max?: number
}

export type ConfigValue = string | number | boolean
export type GraphNodeConfig = Record<string, ConfigValue>
export type StatValue = string | number | boolean | null
export type GraphNodeStats = Record<string, StatValue>

export interface GraphNode {
  id: string
  label: string
  kind: NodeKind
  description: string
  editable: Record<string, EditableField>
  /** Backend defaults; used by 恢复默认 so the UI needs no second request. */
  defaults: GraphNodeConfig
  config: GraphNodeConfig
  stats: GraphNodeStats
}

export interface GraphEdge {
  from: string
  to: string
  label?: string
  conditional?: boolean
}

export interface AgentGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/* --------------------------------- requests -------------------------------- */

/** GET /api/agent/graph — node definitions, current config, last-run stats. */
export const getAgentGraph = (signal?: AbortSignal): Promise<AgentGraph> =>
  request<AgentGraph>('/api/agent/graph', { signal })

/**
 * PUT /api/agent/graph/nodes/{id} — persist one node's config.
 *
 * A partial patch is fine: omitted keys keep their stored value. The response is
 * the node as the GET would return it.
 */
export const updateAgentNode = (nodeId: string, config: GraphNodeConfig): Promise<GraphNode> =>
  request<GraphNode>(`/api/agent/graph/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'PUT',
    body: { config },
  })
