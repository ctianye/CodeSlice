import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api'
import type { GraphNode, GraphPayload } from '../../types'
import { usePersistedState } from '../../hooks/useAsync'
import { useApp } from '../../state/AppState'
import { EmptyState, ErrorNote, Loading } from '../Feedback'
import { Icon } from '../Icon'
import { GraphCanvas } from './GraphCanvas'
import { confidenceLabel, kindLabel, relationLabel, type RelationFamily } from './graphMeta'
import {
  defaultFilters, filterGraph, isDefaultFilters, type GraphFilters,
} from './graphFilter'
import { groupConnections, type RelationGroup } from './graphRelations'
import { communityColor, type GraphTheme } from './graphTheme'
import { clearGraphCache, graphCacheKey, isFresh, readGraphCache, writeGraphCache } from './graphCache'
import { RelationshipSummary } from './RelationshipSummary'

interface Props {
  scope?: 'code' | 'notes'
  initialNodeId?: string | null
  focusNonce?: number
}

/** One chip per relation family; the label also names the nodes it reveals. */
const FAMILY_CHIPS: { family: RelationFamily; label: string; hint: string }[] = [
  { family: 'behaviour', label: '调用', hint: '显示调用与被调用关系' },
  { family: 'dependency', label: '依赖', hint: '显示导入、外部依赖与继承关系' },
  { family: 'structure', label: '结构', hint: '显示包含与方法归属；关闭后画面更像调用图' },
  { family: 'meaning', label: '说明', hint: '显示文档字符串和注释说明节点' },
]

/** Rows per group before the rail tells the user how many it hid. */
const MAX_ROWS = 60

/** Keep in sync with the `@container graph (max-width: 760px)` block in graph.css. */
const NARROW_GRAPH_WIDTH = 760
const DEFAULT_RAIL_PCT = 46
const MIN_RAIL_PCT = 20
const MAX_RAIL_PCT = 72

/**
 * The knowledge-graph tab.
 *
 * The canvas owns the space; the rail is one mode at a time (search results,
 * community filters, or one node's inspector) instead of stacking a directory,
 * a detail panel and two summaries. The tab strip already names the graph, so
 * the header carries stats, relation filters and the rebuild action only.
 */
export function GraphView({ scope = 'code', initialNodeId, focusNonce = 0 }: Props): ReactNode {
  const { project, reveal, openNoteView, askAboutNode, refreshKey, theme } = useApp()
  const projectId = project?.id ?? null
  const cacheKey = graphCacheKey(projectId, scope)
  const [payload, setPayload] = useState<GraphPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [rebuilding, setRebuilding] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filters, setFilters] = useState<GraphFilters>(defaultFilters)
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null)
  const [railPct, setRailPct] = usePersistedState('codeslice.graphRailPct', DEFAULT_RAIL_PCT)
  const [narrow, setNarrow] = useState(false)
  const [draggingRail, setDraggingRail] = useState(false)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const focusCounter = useRef(0)
  const appliedFocus = useRef<string | null>(null)
  const operation = useRef<AbortController | null>(null)
  useEffect(() => () => operation.current?.abort(), [])

  const railPercent = Math.min(
    MAX_RAIL_PCT,
    Math.max(MIN_RAIL_PCT, Number.isFinite(railPct) ? railPct : DEFAULT_RAIL_PCT),
  )

  /** The rail is a bottom sheet only while the graph body is too narrow for two columns. */
  useLayoutEffect(() => {
    const body = bodyRef.current
    if (!body) return
    const measure = () => setNarrow(body.clientWidth <= NARROW_GRAPH_WIDTH)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(body)
    return () => observer.disconnect()
  }, [payload])

  useEffect(() => {
    if (!draggingRail) return
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [draggingRail])

  const railPercentAt = (clientY: number): number => {
    const body = bodyRef.current
    if (!body) return railPercent
    const rect = body.getBoundingClientRect()
    if (!rect.height) return railPercent
    const next = ((rect.bottom - clientY) / rect.height) * 100
    return Math.min(MAX_RAIL_PCT, Math.max(MIN_RAIL_PCT, next))
  }
  const nudgeRail = (delta: number) => setRailPct(
    Math.min(MAX_RAIL_PCT, Math.max(MIN_RAIL_PCT, railPercent + delta)),
  )

  const adopt = useCallback((data: GraphPayload) => {
    setPayload(data)
    writeGraphCache(cacheKey, data)
  }, [cacheKey])

  useEffect(() => {
    if (projectId === null) return
    const cached = readGraphCache(cacheKey)
    if (cached) {
      setPayload(cached.payload)
      setLoading(false)
      setRefreshing(!isFresh(cached))
    } else {
      setLoading(true)
      setRefreshing(false)
    }
    setError(null)
    const controller = new AbortController()
    const request = scope === 'code'
      ? api.getGraph(projectId, controller.signal)
      : api.getNotesGraph(controller.signal)
    void request.then(data => {
      if (controller.signal.aborted) return
      adopt(data)
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    }).finally(() => {
      if (controller.signal.aborted) return
      setLoading(false)
      setRefreshing(false)
    })
    return () => controller.abort()
  }, [projectId, scope, revision, refreshKey, cacheKey, adopt])

  const byId = useMemo(() => new Map(payload?.nodes.map(node => [node.id, node]) ?? []), [payload])
  const view = useMemo(
    () => payload ? filterGraph(payload, filters) : { nodes: [], edges: [] },
    [payload, filters],
  )
  const visibleCount = useMemo(() => view.nodes.reduce((count, node) => count + (node.hidden ? 0 : 1), 0), [view.nodes])
  const visibleEdgeCount = useMemo(
    () => view.edges.reduce((count, edge) => count + (edge.hidden ? 0 : 1), 0),
    [view.edges],
  )

  /** Select a node, revealing the family/community a filter may have hidden. */
  const choose = useCallback((id: string, zoom = true) => {
    const node = byId.get(id)
    if (!node) return
    setFilters(current => {
      const families = new Set(current.families)
      if (node.kind === 'rationale') families.add('meaning')
      if (node.kind === 'external') families.add('dependency')
      const hiddenCommunities = new Set(current.hiddenCommunities)
      hiddenCommunities.delete(node.community)
      return { families, hiddenCommunities }
    })
    setSelectedId(id)
    setQuery('')
    if (zoom) setFocus({ id, nonce: ++focusCounter.current })
    else setFocus(null)
  }, [byId])

  useEffect(() => {
    if (!initialNodeId) { appliedFocus.current = null; return }
    if (!payload) return
    const requestKey = initialNodeId + ':' + focusNonce
    if (appliedFocus.current === requestKey) return
    if (byId.has(initialNodeId)) {
      appliedFocus.current = requestKey
      choose(initialNodeId)
    } else {
      setError('要定位的节点已不存在，请重建图谱或重新选择代码引用。')
    }
  }, [initialNodeId, focusNonce, payload, byId, choose])

  const selected = selectedId ? byId.get(selectedId) ?? null : null
  const connections = useMemo(() => {
    if (!selected || !payload) return []
    return payload.edges.filter(edge => edge.source === selected.id || edge.target === selected.id)
  }, [selected, payload])
  const neighborCount = useMemo(() => {
    const ids = new Set<string>()
    for (const edge of connections) ids.add(edge.source === selected?.id ? edge.target : edge.source)
    ids.delete(selected?.id ?? '')
    return ids.size
  }, [connections, selected?.id])
  const groups = useMemo(
    () => selected ? groupConnections(selected.id, connections, byId, scope) : [],
    [selected, connections, byId, scope],
  )
  const hasCalls = groups.some(group => group.key === 'calls' || group.key === 'callers')
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle ? (payload?.nodes ?? []).filter(node =>
      [node.label, node.path, node.content_md ?? ''].join(' ').toLowerCase().includes(needle)).slice(0, 30) : []
  }, [query, payload])

  const toggleFamily = (family: RelationFamily) => {
    setFilters(current => {
      const families = new Set(current.families)
      if (families.has(family)) families.delete(family)
      else families.add(family)
      return { ...current, families }
    })
  }
  const toggleCommunity = (id: number) => {
    const hiding = !filters.hiddenCommunities.has(id)
    setFilters(current => {
      const hiddenCommunities = new Set(current.hiddenCommunities)
      if (hiddenCommunities.has(id)) hiddenCommunities.delete(id)
      else hiddenCommunities.add(id)
      return { ...current, hiddenCommunities }
    })
    if (hiding && selected?.community === id) {
      setSelectedId(null)
      setFocus(null)
    }
  }
  const allHidden = Boolean(payload?.communities.length)
    && payload!.communities.every(community => filters.hiddenCommunities.has(community.id))
  const toggleAllCommunities = () => {
    setFilters(current => ({
      ...current,
      hiddenCommunities: allHidden ? new Set() : new Set((payload?.communities ?? []).map(community => community.id)),
    }))
    setSelectedId(null)
    setFocus(null)
  }

  const openNode = async (node: GraphNode) => {
    if (node.note_id !== undefined) {
      operation.current?.abort()
      const controller = new AbortController()
      operation.current = controller
      try {
        const note = await api.getNote(node.note_id, controller.signal)
        if (!controller.signal.aborted) openNoteView(note)
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
      }
    } else if (node.path) {
      reveal(node.path, node.start_line, node.end_line)
    }
  }
  const rebuild = async () => {
    if (projectId === null) return
    setRebuilding(true)
    setError(null)
    try {
      if (scope === 'code') await api.rebuildGraph(projectId)
      clearGraphCache(cacheKey)
      setRevision(revisionValue => revisionValue + 1)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setRebuilding(false) }
  }

  if (projectId === null) return <EmptyState icon="folder" title="还没有打开项目" hint="选择项目后查看图谱。" />
  if (loading && !payload) return <Loading text="正在加载图谱…" />
  if (error && !payload) return <ErrorNote message={error} onRetry={() => setRevision(revisionValue => revisionValue + 1)} />

  const graphTheme: GraphTheme = theme
  const layoutKey = `${cacheKey}:${revision}:${payload?.nodes.length ?? 0}:${payload?.edges.length ?? 0}`
  const communities = payload?.communities ?? []

  return <div className="graph-view" data-graph-scope={scope}>
    <header className="graph-head">
      <div className="graph-head-stats" aria-label="图谱规模">
        <span className="graph-stat"><b>{visibleCount.toLocaleString('zh-CN')}</b><span>可见节点</span></span>
        <span className="graph-stat"><b>{visibleEdgeCount.toLocaleString('zh-CN')}</b><span>关系</span></span>
        <span className="graph-stat"><b>{communities.length.toLocaleString('zh-CN')}</b><span>社区</span></span>
        {refreshing ? <span className="graph-syncing" title="正在刷新图谱"><span className="spinner" />更新中…</span> : null}
      </div>
      {scope === 'code' ? <div className="graph-head-filters" role="group" aria-label="关系筛选">
        {FAMILY_CHIPS.map(chip => <button className="graph-chip" key={chip.family}
          aria-pressed={filters.families.has(chip.family)} title={chip.hint}
          onClick={() => toggleFamily(chip.family)}>{chip.label}</button>)}
      </div> : null}
      <div className="graph-head-acts">
        {!isDefaultFilters(filters) ? <button className="graph-act" onClick={() => setFilters(defaultFilters())}>重置筛选</button> : null}
        <button className="graph-act primary" disabled={rebuilding || loading} onClick={() => void rebuild()}>
          <Icon name="refresh" size={12} />{rebuilding ? '处理中…' : scope === 'code' ? '重建' : '刷新'}
        </button>
      </div>
    </header>

    {error && payload ? <div className="graph-banner"><ErrorNote message={error} onRetry={() => setRevision(revisionValue => revisionValue + 1)} /></div> : null}

    <div className="graph-body" ref={bodyRef} data-dragging={draggingRail || undefined}
      style={narrow ? {
        gridTemplateColumns: 'minmax(0, 1fr)',
        gridTemplateRows: `minmax(0, 1fr) 7px minmax(0, ${railPercent}%)`,
      } : undefined}>
      <div className="graph-stage">
        {!payload?.nodes.length
          ? <GraphBlank scope={scope} busy={loading} onRebuild={() => void rebuild()} />
          : <GraphCanvas nodes={view.nodes} edges={view.edges} theme={graphTheme} scope={scope}
            selectedId={selectedId} focus={focus} layoutKey={layoutKey}
            onSelect={id => {
              if (id) choose(id, false)
              else { setSelectedId(null); setFocus(null) }
            }}
            onActivate={id => { const node = byId.get(id); if (node) void openNode(node) }} />}
      </div>

      {narrow ? <div
        className={'graph-splitter' + (draggingRail ? ' dragging' : '')}
        role="separator"
        aria-orientation="horizontal"
        aria-label="调整图谱检查器高度"
        aria-valuemin={MIN_RAIL_PCT}
        aria-valuemax={MAX_RAIL_PCT}
        aria-valuenow={Math.round(railPercent)}
        title="拖动调整检查器高度，双击恢复默认"
        tabIndex={0}
        onPointerDown={event => {
          event.preventDefault()
          event.currentTarget.setPointerCapture(event.pointerId)
          setDraggingRail(true)
        }}
        onPointerMove={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            setRailPct(railPercentAt(event.clientY))
          }
        }}
        onPointerUp={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
          setDraggingRail(false)
        }}
        onPointerCancel={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
          setDraggingRail(false)
        }}
        onDoubleClick={() => setRailPct(DEFAULT_RAIL_PCT)}
        onKeyDown={event => {
          if (event.key === 'ArrowUp') {
            event.preventDefault()
            nudgeRail(event.shiftKey ? 8 : 3)
          } else if (event.key === 'ArrowDown') {
            event.preventDefault()
            nudgeRail(event.shiftKey ? -8 : -3)
          } else if (event.key === 'Home') {
            event.preventDefault()
            setRailPct(DEFAULT_RAIL_PCT)
          }
        }}
      /> : null}

      <aside className="graph-rail" aria-label="图谱检查器">
        <div className="graph-rail-search">
          <div className="graph-search">
            <Icon name="search" size={13} />
            <input value={query} onChange={event => setQuery(event.target.value)}
              placeholder={scope === 'code' ? '搜索函数 / 文件 / 社区' : '搜索笔记 / 内容'} aria-label="搜索图谱节点" />
            {query && <button className="graph-search-clear" onClick={() => setQuery('')}
              title="清除搜索" aria-label="清除搜索"><Icon name="x" size={11} /></button>}
          </div>
        </div>

        {query.trim() ? <div className="graph-results graph-rail-scroll" aria-label="搜索结果">
          {!matches.length && <p className="graph-empty-line">没有匹配的节点。</p>}
          {matches.map(node => <button className="graph-result" key={node.id} onClick={() => choose(node.id)}
            title={node.path || node.content_md?.slice(0, 120)} aria-pressed={selectedId === node.id}>
            <span className="graph-dot" style={{ background: communityColor(graphTheme, node.community) }} />
            <span className="graph-result-label">{node.label}</span>
            <span className="graph-result-kind">{kindLabel(node.kind)}</span>
          </button>)}
        </div> : selected ? <NodeInspector
          node={selected}
          groups={groups}
          hasCalls={hasCalls}
          neighborCount={neighborCount}
          scope={scope}
          theme={graphTheme}
          projectId={projectId}
          revision={revision}
          onBack={() => { setSelectedId(null); setFocus(null) }}
          onSelect={id => choose(id, false)}
          onOpen={node => void openNode(node)}
          onAsk={() => askAboutNode({
            label: selected.label, node_id: selected.id, path: selected.path,
            start_line: selected.start_line, community_label: selected.community_label,
          })}
        /> : <CommunityList
          communities={communities}
          hidden={filters.hiddenCommunities}
          allHidden={allHidden}
          theme={graphTheme}
          scope={scope}
          onToggle={toggleCommunity}
          onToggleAll={toggleAllCommunities}
        />}
      </aside>
    </div>
  </div>
}

function NodeInspector({ node, groups, hasCalls, neighborCount, scope, theme, projectId, revision,
  onBack, onSelect, onOpen, onAsk }: {
  node: GraphNode
  groups: RelationGroup[]
  hasCalls: boolean
  neighborCount: number
  scope: 'code' | 'notes'
  theme: GraphTheme
  projectId: number
  revision: number
  onBack: () => void
  onSelect: (id: string) => void
  onOpen: (node: GraphNode) => void
  onAsk: () => void
}): ReactNode {
  return <section className="graph-detail graph-rail-scroll" aria-label="节点信息">
    <button className="graph-detail-back" onClick={onBack} type="button">
      <Icon name="chevron-left" size={12} />全部社区
    </button>
    <div className="graph-detail-head">
      <span className="graph-kind">{kindLabel(node.kind)}</span>
      <span className="graph-kind-note">{node.community_label}</span>
    </div>
    <h2 className="graph-detail-title" title={node.label}>{node.label}</h2>
    <p className="graph-detail-meta mono" title={node.path ? node.path + ':' + node.start_line : undefined}>
      {scope === 'code' ? (node.path ? node.path + ':' + node.start_line : '项目内节点') : node.project_name}
      <span className="graph-sep">·</span>{neighborCount} 邻点
    </p>
    <div className="graph-detail-acts">
      <button className="graph-act primary" onClick={onAsk}><Icon name="message" size={12} />继续提问</button>
      {node.path && !node.external ? <button className="graph-act" onClick={() => onOpen(node)}>
        <Icon name={scope === 'code' ? 'file-code' : 'notes'} size={12} />
        {scope === 'code' ? '打开代码' : '打开笔记'}
      </button> : null}
    </div>
    {node.content_md ? <p className="graph-note-body">{node.content_md.slice(0, 250)}</p> : null}

    {groups.length ? groups.map((group, index) => <RelationSection
      key={group.key}
      group={group}
      open={group.key === 'callers' || group.key === 'calls' || (!hasCalls && index === 0)}
      theme={theme}
      onSelect={onSelect}
      onOpen={onOpen}
    />) : <p className="graph-empty-line">此节点没有直接关联。</p>}

    <RelationshipSummary key={node.id + ':' + revision}
      scope={scope} projectId={projectId} nodeId={node.id} neighborCount={neighborCount} />
  </section>
}

function RelationSection({ group, open, theme, onSelect, onOpen }: {
  group: RelationGroup
  open: boolean
  theme: GraphTheme
  onSelect: (id: string) => void
  onOpen: (node: GraphNode) => void
}): ReactNode {
  const shown = group.items.slice(0, MAX_ROWS)
  return <details className="graph-rel-group" open={open}>
    <summary>
      <span>{group.label}</span>
      <small>{group.items.length}</small>
    </summary>
    <div className="graph-rel-list">
      {shown.map(({ edge, other, outgoing }) => <div className="graph-rel-row" key={other.id + ':' + edge.relation + ':' + group.key}
        role="button" tabIndex={0}
        title={[other.path, relationLabel(edge.relation), confidenceLabel(edge.confidence), edge.line ? 'L' + edge.line : ''].filter(Boolean).join(' · ')}
        onClick={() => onSelect(other.id)}
        onDoubleClick={() => onOpen(other)}
        onKeyDown={event => { if (event.key === 'Enter') onSelect(other.id) }}>
        <span className="graph-relation-dir" data-dir={outgoing ? 'out' : 'in'} aria-hidden="true">{outgoing ? '→' : '←'}</span>
        <span className="graph-relation-label">{other.label}</span>
        <span className="graph-relation-kind">{kindLabel(other.kind)}</span>
        <span className="graph-relation-loc mono">{other.path ? other.path + ':' + other.start_line : ''}</span>
        {other.path && !other.external ? <button className="graph-rel-open" title="打开代码"
          aria-label={'打开 ' + other.label} onClick={event => { event.stopPropagation(); onOpen(other) }}>
          <Icon name="external" size={11} />
        </button> : null}
        <span className="graph-relation-rel" style={{ color: communityColor(theme, other.community) }}>
          {relationLabel(edge.relation)}
        </span>
      </div>)}
      {group.items.length > shown.length ? <p className="graph-hint">
        还有 {group.items.length - shown.length} 个关系；用搜索定位更具体。
      </p> : null}
    </div>
  </details>
}

function CommunityList({ communities, hidden, allHidden, theme, scope, onToggle, onToggleAll }: {
  communities: GraphPayload['communities']
  hidden: ReadonlySet<number>
  allHidden: boolean
  theme: GraphTheme
  scope: 'code' | 'notes'
  onToggle: (id: number) => void
  onToggleAll: () => void
}): ReactNode {
  return <section className="graph-section graph-rail-scroll" aria-label="社区筛选">
    <div className="graph-section-head">
      <span>社区</span>
      <small>{communities.length}</small>
      <button className="graph-comm-all" onClick={onToggleAll} type="button">
        {allHidden ? '全部显示' : '全部隐藏'}
      </button>
    </div>
    <div className="graph-comm-list">
      {communities.map(community => <label className="graph-comm" key={community.id}
        data-hidden={hidden.has(community.id) || undefined}>
        <input type="checkbox" checked={!hidden.has(community.id)}
          onChange={() => onToggle(community.id)} aria-label={'显示社区 ' + community.label} />
        <span className="graph-dot" style={{ background: communityColor(theme, community.id) }} />
        <span className="graph-comm-label">{community.label}</span>
        <span className="graph-count">{community.size}</span>
      </label>)}
    </div>
    <p className="graph-hint">{scope === 'code'
      ? '颜色代表社区；点击节点查看调用者与被调用者。双击节点直接打开代码。'
      : '颜色代表笔记分组；点击笔记查看关联。双击笔记直接打开。'}</p>
  </section>
}

/** Compact empty panel for a project whose index has no graph yet. */
function GraphBlank({ scope, busy, onRebuild }: {
  scope: 'code' | 'notes'; busy: boolean; onRebuild: () => void
}): ReactNode {
  return <div className="graph-blank">
    <span className="graph-blank-icon"><Icon name="network" size={16} /></span>
    <div className="graph-blank-body">
      <strong>{scope === 'code' ? '项目还没有代码图谱' : '还没有笔记'}</strong>
      <p>{scope === 'code' ? '建立项目索引后，点击重建生成知识图谱。' : '保存阅读笔记后即可在这里查看它们的关联。'}</p>
    </div>
    <button className="graph-act primary" disabled={busy} onClick={onRebuild}>
      <Icon name="refresh" size={12} />{busy ? '处理中…' : scope === 'code' ? '重建' : '刷新'}
    </button>
  </div>
}
