import type { ReactNode } from 'react'
import { useState } from 'react'
import { getAgentGraph } from '../apiGraph'
import type { AgentGraph, GraphEdge, GraphNode, GraphNodeStats, NodeKind } from '../apiGraph'
import { useAsync } from '../hooks/useAsync'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { GraphNodeForm } from './GraphNodeForm'
import '../styles/graph.css'

const KIND_ICON: Record<NodeKind, IconName> = {
  input: 'folder-open',
  llm: 'cpu',
  tool: 'terminal',
  output: 'check',
}

const KIND_LABEL: Record<NodeKind, string> = {
  input: '输入',
  llm: '模型',
  tool: '工具',
  output: '输出',
}

function formatTokens(count: number): string {
  if (count >= 10_000) return `${Math.round(count / 1000)}k`
  if (count >= 1_000) return `${(count / 1000).toFixed(1)}k`
  return String(count)
}

/** A collapsed card summarises the last run in at most three short facts. */
function statsSummary(stats: GraphNodeStats): string[] {
  const bits: string[] = []
  const tokens = stats.last_tokens
  if (typeof tokens === 'number') bits.push(`${formatTokens(tokens)} tokens`)
  const duration = stats.last_duration_ms
  if (typeof duration === 'number') {
    bits.push(duration >= 1000 ? `${(duration / 1000).toFixed(1)} s` : `${Math.round(duration)} ms`)
  }
  const tool = stats.last_tool
  if (typeof tool === 'string' && tool) bits.push(`工具 ${tool}`)
  const rounds = stats.last_round
  if (typeof rounds === 'number' && bits.length < 2) bits.push(`第 ${rounds} 轮`)
  const calls = stats.last_tool_calls
  if (typeof calls === 'number' && calls > 0 && bits.length < 2) bits.push(`${calls} 个工具调用`)
  const chunks = stats.last_chunks
  if (typeof chunks === 'number' && bits.length < 2) bits.push(`检索 ${chunks} 段`)
  if (typeof stats.last_error === 'string' && stats.last_error) bits.push('上次失败')
  return bits.slice(0, 3)
}

interface ConnectorProps {
  edges: GraphEdge[]
}

/** The vertical line between two cards, annotated with its condition labels. */
function Connector({ edges }: ConnectorProps): ReactNode {
  if (edges.length === 0) {
    // run_one does not reach finalize directly; the answer finishes after another
    // agent pass, so draw a muted hint instead of implying an edge that is not there.
    return (
      <div className="graph-connector">
        <span className="graph-line dashed" />
        <span className="graph-edge-label">无直接连线</span>
      </div>
    )
  }
  return (
    <div className="graph-connector">
      <span className="graph-line" />
      {edges.map((edge, index) => (
        <span className="graph-edge-label" key={`${edge.from}-${edge.to}-${index}`}>
          {edge.label ?? (edge.conditional ? '按条件' : '顺序')}
        </span>
      ))}
      <span className="graph-arrow">
        <Icon name="chevron-down" size={12} />
      </span>
    </div>
  )
}

interface CardProps {
  node: GraphNode
  expanded: boolean
  onToggle: () => void
  onSaved: (node: GraphNode) => void
  loopLabels: string[]
}

function NodeCard({ node, expanded, onToggle, onSaved, loopLabels }: CardProps): ReactNode {
  const summary = statsSummary(node.stats)
  return (
    <div className={`graph-node${expanded ? ' expanded' : ''}`}>
      <button type="button" className="graph-node-head" onClick={onToggle} aria-expanded={expanded}>
        <span className={`graph-node-icon kind-${node.kind}`}>
          <Icon name={KIND_ICON[node.kind]} size={14} />
        </span>
        <span className="graph-node-title">
          <span className="graph-node-label">{node.label}</span>
          <span className="graph-node-kind">{KIND_LABEL[node.kind]}</span>
        </span>
        <span className="graph-node-chevron">
          <Icon name={expanded ? 'chevron-down' : 'chevron-right'} size={12} />
        </span>
      </button>

      <p className="graph-node-desc">{node.description}</p>

      {!expanded && summary.length > 0 ? (
        <div className="graph-node-stats">
          {summary.map((bit) => (
            <span className="graph-stat" key={bit}>
              {bit}
            </span>
          ))}
        </div>
      ) : null}

      {!expanded && loopLabels.length > 0 ? (
        <div className="graph-node-loop">
          {loopLabels.map((label) => (
            <span className="graph-loop-pill" key={label}>
              <Icon name="refresh" size={10} /> {label}
            </span>
          ))}
        </div>
      ) : null}

      {expanded ? <GraphNodeForm node={node} onSaved={onSaved} /> : null}
    </div>
  )
}

/** Edge labels that always return to the same node: shown as a pill on the card. */
function selfLoopLabels(edges: GraphEdge[], nodeId: string): string[] {
  return edges.filter((edge) => edge.from === nodeId && edge.to === nodeId).map((edge) => edge.label ?? '循环')
}

/**
 * Conditional edges that the vertical chain cannot draw: a jump forward (agent →
 * finalize) or a step back up (run_one → agent). Self loops stay on the card.
 */
function extraEdges(graph: AgentGraph): GraphEdge[] {
  const ids = new Set(graph.nodes.map((node) => node.id))
  const chain = new Set<string>()
  for (let i = 0; i < graph.nodes.length - 1; i += 1) {
    const from = graph.nodes[i]
    const to = graph.nodes[i + 1]
    if (from && to) chain.add(`${from.id}->${to.id}`)
  }
  return graph.edges.filter((edge) => {
    if (!ids.has(edge.from) || !ids.has(edge.to)) return false
    if (edge.from === edge.to) return false
    return !chain.has(`${edge.from}->${edge.to}`)
  })
}

function flowEdges(graph: AgentGraph, from: string, to: string): GraphEdge[] {
  return graph.edges.filter((edge) => edge.from === from && edge.to === to)
}

/**
 * The agent workflow, as the user can actually see and change it.
 *
 * The topology is fixed (prepare → agent ⇄ run_one → finalize), so this is a
 * read-only diagram with an editor per node rather than a canvas: the only thing
 * that can change is how each node behaves, and a change applies to the next turn.
 */
export function GraphPanel({ onClose }: { onClose: () => void }): ReactNode {
  const { data, error, loading, reload } = useAsync<AgentGraph>((signal) => getAgentGraph(signal), [])
  const [graph, setGraph] = useState<AgentGraph | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  // The fetched graph is the source of truth; local overrides only carry a saved
  // node's fresh config/stats so the panel updates without a refetch.
  const live = graph ?? data

  const applyNode = (updated: GraphNode) => {
    setGraph((prev) => {
      const base = prev ?? data
      if (!base) return prev
      return { ...base, nodes: base.nodes.map((node) => (node.id === updated.id ? updated : node)) }
    })
  }

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar graph-toolbar">
        <span className="graph-toolbar-title">
          <Icon name="git-branch" size={13} /> 工作流
        </span>
        <span className="spacer" />
        <button
          type="button"
          className="ghost icon"
          title="刷新"
          aria-label="刷新"
          onClick={() => {
            setGraph(null)
            setExpanded(null)
            reload()
          }}
        >
          <Icon name="refresh" size={13} />
        </button>
        <button type="button" className="ghost icon" title="关闭" aria-label="关闭" onClick={onClose}>
          <Icon name="x" size={13} />
        </button>
      </div>

      <div className="sidebar-scroll padded graph-scroll">
        {loading ? <Loading text="读取工作流…" /> : null}
        {error ? <ErrorNote message={error} onRetry={reload} /> : null}
        {!loading && !error && live && live.nodes.length === 0 ? <EmptyState title="没有可显示的节点" /> : null}

        {live
          ? live.nodes.map((node, index) => {
              const next = live.nodes[index + 1]
              return (
                <div className="graph-step" key={node.id}>
                  <NodeCard
                    node={node}
                    expanded={expanded === node.id}
                    onToggle={() => setExpanded(expanded === node.id ? null : node.id)}
                    onSaved={applyNode}
                    loopLabels={selfLoopLabels(live.edges, node.id)}
                  />
                  {next ? <Connector edges={flowEdges(live, node.id, next.id)} /> : null}
                </div>
              )
            })
          : null}

        {live && extraEdges(live).length > 0 ? (
          <div className="graph-loops">
            <div className="graph-loops-title">分支与回路</div>
            {extraEdges(live).map((edge, index) => {
              const from = live.nodes.find((node) => node.id === edge.from)
              const to = live.nodes.find((node) => node.id === edge.to)
              return (
                <div className="graph-loop-row" key={`${edge.from}-${edge.to}-${index}`}>
                  <Icon name="refresh" size={11} />
                  <span>
                    {from?.label ?? edge.from} → {to?.label ?? edge.to}
                  </span>
                  {edge.label ? <span className="graph-loop-label">{edge.label}</span> : null}
                </div>
              )
            })}
          </div>
        ) : null}

        {live ? <p className="graph-footnote">节点拓扑固定，修改仅影响下一轮对话。</p> : null}
      </div>
    </div>
  )
}
