/**
 * Code knowledge graph view: canvas + search + community legend + node detail.
 *
 * Layout follows the same shape the user already knows from graphify: the graph
 * fills the pane, a sidebar holds search, the community legend and the selected
 * node's connections.
 *
 * One rule this view enforces everywhere: an inferred edge is always *visually*
 * different from an extracted one (dotted line, "推断" tag). The graph is used to
 * decide what to read next, so a guess must not look like a fact.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api'
import type { GraphNode, GraphNodeDetail, GraphPayload } from '../../types'
import { useApp } from '../../state/AppState'
import { EmptyState, ErrorNote, Loading } from '../Feedback'
import { Icon } from '../Icon'
import { GraphCanvas } from './GraphCanvas'
import type { CanvasEdge, CanvasNode } from './GraphCanvas'
import { confidenceLabel, kindLabel, relationColor, relationLabel } from './graphMeta'

/** Palette shared with the notes graph so the two views feel like one product. */
const PALETTE = [
  '#44c2b1', '#e2a64f', '#e7798e', '#75a9ee', '#9c86dc',
  '#83bd73', '#dc83c2', '#58b8d6', '#c9a227', '#7fa6a0',
]

/**
 * Above this node count the canvas becomes a hairball, so the view opens on the
 * communities instead of every symbol. This is graphify's aggregated-view idea:
 * degrade to a coarser picture rather than refuse to draw.
 */
const AGGREGATE_THRESHOLD = 700

/**
 * Kind filters offered in the legend.
 *
 * Rationale and external nodes are both numerous and non-structural. Showing them
 * by default is right - they are the point of the AST rewrite - but a reader who
 * wants the architecture picture needs one click to get it back.
 */
const KIND_FILTERS: Array<{ kind: string; label: string; hint: string }> = [
  { kind: 'rationale', label: '说明', hint: '隐藏/显示 docstring 与标记注释生成的说明节点' },
  { kind: 'external', label: '外部依赖', hint: '隐藏/显示项目之外的依赖与基类' },
]

interface GraphViewProps {
  /** Node to open with, when the user arrived by clicking a citation or a node. */
  initialNodeId?: string | null
}

export function GraphView({ initialNodeId }: GraphViewProps): ReactNode {
  const { project, reveal, askAboutNode } = useApp()
  const projectId = project?.id ?? null

  const [payload, setPayload] = useState<GraphPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)

  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(initialNodeId ?? null)
  const [hiddenCommunities, setHiddenCommunities] = useState<Set<number>>(() => new Set())
  /** Node kinds the user filtered out (rationale / external). */
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(() => new Set())
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [aggregate, setAggregate] = useState<boolean | null>(null)

  const load = useCallback(
    (signal?: AbortSignal) => {
      if (projectId === null) return
      setLoading(true)
      setError(null)
      api
        .getGraph(projectId, signal)
        .then((data) => {
          if (signal?.aborted) return
          setPayload(data)
        })
        .catch((err: unknown) => {
          if (signal?.aborted) return
          setError(err instanceof Error ? err.message : String(err))
        })
        .finally(() => {
          if (!signal?.aborted) setLoading(false)
        })
    },
    [projectId],
  )

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  // Opening on a specific node (from a citation) should also reveal it.
  useEffect(() => {
    if (initialNodeId) setSelectedId(initialNodeId)
  }, [initialNodeId])

  // Fetch the connection list for the selected node. Done server-side rather than
  // filtered from the full edge list so one click does not walk every edge in the
  // browser on a large graph.
  useEffect(() => {
    if (projectId === null || !selectedId) {
      setDetail(null)
      return
    }
    const controller = new AbortController()
    setDetailLoading(true)
    api
      .getGraphNode(projectId, selectedId, 1, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setDetail(data)
      })
      .catch(() => {
        if (!controller.signal.aborted) setDetail(null)
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false)
      })
    return () => controller.abort()
  }, [projectId, selectedId])

  const nodes = payload?.nodes ?? []
  const edges = payload?.edges ?? []
  const communities = payload?.communities ?? []

  /** Whether to draw communities instead of symbols. */
  const isAggregated = useMemo(() => {
    if (aggregate !== null) return aggregate
    return nodes.length > AGGREGATE_THRESHOLD
  }, [aggregate, nodes.length])

  /** Nodes hidden by the community legend or the kind filters. */
  const visibleIds = useMemo(() => {
    const ids = new Set<string>()
    if (isAggregated) {
      // Aggregated nodes are keyed ``community:<id>``, *not* by symbol id. Building
      // this set from symbol ids would filter every community node out and leave a
      // blank canvas — the id spaces are different and must not be mixed.
      for (const community of communities) {
        if (!hiddenCommunities.has(community.id)) ids.add(`community:${community.id}`)
      }
      return ids
    }
    for (const node of nodes) {
      if (hiddenCommunities.has(node.community)) continue
      if (hiddenKinds.has(node.kind)) continue
      ids.add(node.id)
    }
    return ids
  }, [isAggregated, nodes, communities, hiddenCommunities, hiddenKinds])

  /**
   * Canvas input.
   *
   * In aggregated mode each community collapses into one node whose degree is its
   * member count, so the user still sees which subsystem is biggest.
   */
  const canvasNodes = useMemo<CanvasNode[]>(() => {
    if (!isAggregated) {
      return nodes.map((node) => ({
        id: node.id,
        label: node.label,
        degree: node.degree,
        community: node.community,
        communityLabel: node.community_label,
        kind: node.kind,
        start_line: node.start_line,
        path: node.path,
        // Carried through so the canvas can mark a rationale as a docstring or a
        // comment in its tooltip, and style an external node as a reference.
        origin: node.origin,
        tooltip: node.external ? '外部依赖' : undefined,
      }))
    }
    return communities.map((community) => ({
      id: `community:${community.id}`,
      label: `${community.label} (${community.size})`,
      degree: community.size,
      community: community.id,
      kind: 'community',
      start_line: 0,
      path: '',
    }))
  }, [isAggregated, nodes, communities])

  const canvasEdges = useMemo<CanvasEdge[]>(() => {
    if (!isAggregated) {
      return edges.map((edge) => ({
        source: edge.source,
        target: edge.target,
        confidence: edge.confidence,
        relation: edge.relation,
      }))
    }
    // Collapse symbol edges into community edges, counting how many cross each
    // boundary so the meta-graph shows real coupling strength.
    const nodeCommunity = new Map(nodes.map((node) => [node.id, node.community]))
    const counts = new Map<string, number>()
    for (const edge of edges) {
      const a = nodeCommunity.get(edge.source)
      const b = nodeCommunity.get(edge.target)
      if (a === undefined || b === undefined || a === b) continue
      const key = a < b ? `${a}-${b}` : `${b}-${a}`
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].map(([key, count]) => {
      const [a, b] = key.split('-')
      return {
        source: `community:${a}`,
        target: `community:${b}`,
        confidence: 'EXTRACTED',
        relation: `${count} 条跨社区关系`,
      }
    })
  }, [isAggregated, nodes, edges])

  /** Search matches across label and path, so a file name finds its symbols. */
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return []
    return nodes
      .filter((node) => `${node.label} ${node.path}`.toLowerCase().includes(needle))
      .slice(0, 12)
  }, [nodes, query])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedId) ?? null,
    [nodes, selectedId],
  )

  const selectedCommunity = selectedNode
    ? communities.find((community) => community.id === selectedNode.community)
    : null

  const toggleCommunity = (id: number) => {
    setHiddenCommunities((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleKind = (kind: string) => {
    setHiddenKinds((current) => {
      const next = new Set(current)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  const rebuild = async () => {
    if (projectId === null) return
    setRebuilding(true)
    setError(null)
    try {
      await api.rebuildGraph(projectId)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRebuilding(false)
    }
  }

  /** Ask the agent about this node, with the node's neighbourhood as context. */
  const ask = (node: GraphNode) => {
    askAboutNode({
      label: node.label,
      node_id: node.id,
      path: node.path,
      start_line: node.start_line,
      community_label: node.community_label,
    })
  }

  if (projectId === null) {
    return <EmptyState icon="folder" title="还没有打开项目" hint="先选择一个项目，再查看它的知识图谱。" />
  }

  if (loading && !payload) return <Loading text="正在加载知识图谱…" />
  if (error && !payload) return <ErrorNote message={error} onRetry={() => load()} />

  const stats = payload?.stats
  const empty = nodes.length === 0

  return (
    <div className="graph-view">
      <header className="graph-view-head">
        <div className="graph-view-title">
          <Icon name="network" size={14} />
          <strong>知识图谱</strong>
          {stats ? (
            <span className="graph-view-stats">
              {stats.files} 文件 · {stats.symbols} 符号 · {stats.edges} 关系 · {stats.communities} 社区
              {stats.rationale ? ` · ${stats.rationale} 条说明` : ''}
              {stats.rebuilt ? ' · 已重建' : ''}
            </span>
          ) : null}
        </div>
        <div className="graph-view-actions">
          {nodes.length > AGGREGATE_THRESHOLD ? (
            <button
              type="button"
              className="small ghost"
              onClick={() => setAggregate((current) => !(current ?? isAggregated))}
              title={isAggregated ? '展开到单个符号' : '折叠到社区视图'}
            >
              <Icon name={isAggregated ? 'maximize' : 'minimize'} size={12} />
              {isAggregated ? '展开符号' : '社区视图'}
            </button>
          ) : null}
          <button
            type="button"
            className="small ghost"
            onClick={() => void rebuild()}
            disabled={rebuilding}
            title="按当前索引重新生成图谱"
          >
            <Icon name="refresh" size={12} />
            {rebuilding ? '重建中…' : '重建'}
          </button>
        </div>
      </header>

      {error ? <ErrorNote message={error} /> : null}

      {/* Canvas takes the whole width and height above the dock. Nothing is placed
          beside it: a graph is a spatial object, and every pixel of width it keeps
          makes a dense one easier to read than the same pixels given to a side
          column. Search and node detail live in the dock below. */}
      <div className="graph-view-stage">
        {empty ? (
          <EmptyState
            icon="network"
            title="这个项目还没有图谱"
            hint="先建立索引，再点右上角「重建」生成知识图谱。"
          />
        ) : (
          <GraphCanvas
            nodes={canvasNodes}
            edges={canvasEdges}
            colors={PALETTE}
            selectedId={selectedId}
            visibleIds={visibleIds}
            onSelect={(id) => setSelectedId(id)}
            onActivate={(id) => {
              // Double click: in aggregated mode focus that community; in node
              // mode jump to the code the symbol lives in.
              if (id.startsWith('community:')) {
                const cid = Number(id.split(':')[1])
                setAggregate(false)
                const hub = communities.find((community) => community.id === cid)?.hub
                if (hub) setSelectedId(hub)
                return
              }
              const node = nodes.find((item) => item.id === id)
              if (node && node.path) reveal(node.path, node.start_line, node.end_line)
            }}
          />
        )}
      </div>

      {/* The dock: search + selected node's connections + community legend, all in
          one horizontal band. Kept as a fixed-height strip so the canvas above does
          not jump when the selection changes. */}
      <footer className="graph-dock">
        <div className="graph-dock-search">
          <div className="graph-search-field">
            <Icon name="search" size={12} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按名称、文件或目录搜索"
              aria-label="搜索图谱节点"
            />
            {query ? (
              <button type="button" className="icon" onClick={() => setQuery('')} title="清除搜索">
                <Icon name="x" size={11} />
              </button>
            ) : null}
          </div>
          {matches.length > 0 ? (
            <div className="graph-match-list">
              {matches.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  className="graph-match"
                  onClick={() => setSelectedId(node.id)}
                  title={`${node.path}:${node.start_line}`}
                >
                  <span className="graph-match-dot" style={{ background: PALETTE[node.community % PALETTE.length] }} />
                  <span className="graph-match-label">{node.label}</span>
                  <small>{node.kind === 'file' ? '文件' : '符号'}</small>
                </button>
              ))}
            </div>
          ) : (
            <div className="graph-dock-hint">
              拖动平移 · 滚轮缩放 · 点节点看关系 · 双击打开代码
            </div>
          )}
        </div>

        <div className="graph-dock-detail">
          {detailLoading ? (
            <div className="graph-dock-empty">读取关系…</div>
          ) : selectedNode ? (
            <>
              <div className="graph-dock-detail-head">
                <strong title={selectedNode.id}>{selectedNode.label}</strong>
                <span className="graph-dock-meta">
                  {kindLabel(selectedNode.kind)} · {selectedNode.path}:{selectedNode.start_line}
                </span>
                {selectedCommunity ? (
                  <span
                    className="graph-dock-community"
                    style={{ color: PALETTE[selectedCommunity.id % PALETTE.length] }}
                  >
                    {selectedCommunity.label}
                  </span>
                ) : null}
                <span className="spacer" />
                <button type="button" className="small primary" onClick={() => ask(selectedNode)} title="就这个节点向 AI 提问">
                  <Icon name="sparkles" size={11} />
                  提问
                </button>
                {selectedNode.path ? (
                  <button
                    type="button"
                    className="small ghost"
                    onClick={() => reveal(selectedNode.path, selectedNode.start_line, selectedNode.end_line)}
                  >
                    <Icon name="code" size={11} />
                    打开代码
                  </button>
                ) : null}
              </div>
              {/* Relations run horizontally: a function usually has a handful of
                  callers, and a row of chips scans faster than a stacked list.
                  Each chip carries its relation's colour so a "why" edge is
                  distinguishable from a call at a glance. */}
              {detail && detail.edges.length > 0 ? (
                <div className="graph-relations-row">
                  {detail.edges.map((edge, index) => (
                    <button
                      key={`${edge.id}-${edge.relation}-${index}`}
                      type="button"
                      className="graph-relation-chip"
                      onClick={() => setSelectedId(edge.id)}
                      style={{ borderLeftColor: relationColor(edge.relation) }}
                      title={`${edge.path}:${edge.start_line} · ${relationLabel(edge.relation)}`}
                    >
                      <span className={`graph-relation-arrow ${edge.direction}`}>
                        {edge.direction === 'out' ? '→' : '←'}
                      </span>
                      <span className="graph-relation-rel">{relationLabel(edge.relation)}</span>
                      <span className="graph-relation-label">{edge.label}</span>
                      <span className={`graph-tag ${edge.confidence.toLowerCase()}`}>
                        {confidenceLabel(edge.confidence)}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="graph-dock-empty">这个节点没有已建立的关系。</div>
              )}
            </>
          ) : (
            <div className="graph-dock-empty">点击画布上的节点查看它的关系。</div>
          )}
        </div>

        {communities.length > 0 ? (
          <div className="graph-dock-legend">
            {/* Kind filters come first: rationale and external nodes are numerous
                and, while useful, they are not the code's structure. One click to
                hide them turns a busy canvas back into an architecture picture. */}
            <div className="graph-kind-filters">
              {KIND_FILTERS.map((filter) => (
                <button
                  key={filter.kind}
                  type="button"
                  className={`graph-kind-chip${hiddenKinds.has(filter.kind) ? ' off' : ''}`}
                  onClick={() => toggleKind(filter.kind)}
                  title={filter.hint}
                  aria-pressed={!hiddenKinds.has(filter.kind)}
                >
                  <span className={`graph-kind-swatch ${filter.kind}`} />
                  {filter.label}
                </button>
              ))}
            </div>
            <div className="graph-legend-items">
              {communities.map((community) => {
                const hidden = hiddenCommunities.has(community.id)
                return (
                  <button
                    key={community.id}
                    type="button"
                    className={`graph-legend-chip${hidden ? ' off' : ''}`}
                    onClick={() => toggleCommunity(community.id)}
                    title={`${community.label} · ${community.size} 个节点（点击${hidden ? '显示' : '隐藏'}）`}
                    aria-pressed={!hidden}
                  >
                    <span
                      className="graph-legend-dot"
                      style={{ background: PALETTE[community.id % PALETTE.length] }}
                    />
                    <span className="graph-legend-label">{community.label}</span>
                    <span className="graph-legend-count">{community.size}</span>
                  </button>
                )
              })}
            </div>
            <button
              type="button"
              className="graph-legend-all"
              onClick={() =>
                setHiddenCommunities(
                  hiddenCommunities.size === 0 ? new Set(communities.map((c) => c.id)) : new Set(),
                )
              }
              title={hiddenCommunities.size === 0 ? '隐藏全部社区' : '显示全部社区'}
            >
              {hiddenCommunities.size === 0 ? '全隐藏' : '全显示'}
            </button>
          </div>
        ) : null}
      </footer>
    </div>
  )
}
