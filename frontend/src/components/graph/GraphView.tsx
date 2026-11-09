import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api'
import type { GraphNode, GraphPayload } from '../../types'
import { useApp } from '../../state/AppState'
import { EmptyState, ErrorNote, Loading } from '../Feedback'
import { Icon } from '../Icon'
import { GraphCanvas } from './GraphCanvas'
import { communityGraph, communityId } from './communityGraph'
import { confidenceLabel, kindLabel, relationLabel } from './graphMeta'
import { clearGraphCache, graphCacheKey, isFresh, readGraphCache, writeGraphCache } from './graphCache'
import { COMMUNITY_PALETTE, communityColor, type GraphTheme } from './graphTheme'
import { RelationshipSummary } from './RelationshipSummary'

interface Props {
  scope?: 'code' | 'notes'
  initialNodeId?: string | null
  focusNonce?: number
}

/** Kinds the scope can turn off; both are "annotation" layers over real code. */
const FILTERABLE_KINDS = ['rationale', 'external'] as const

/**
 * The knowledge-graph tab.
 *
 * Layout: a 48px command bar, then a canvas that owns every remaining pixel and
 * a fixed-width inspector rail. The rail is the opposite of the old bottom dock:
 * a graph needs width, and a 332px column costs less of it than an 38vh band.
 * Below ~760px of container width the rail becomes a bottom panel instead, so a
 * narrow pane can never hide its own controls behind a horizontal scroll.
 */
export function GraphView({ scope = 'code', initialNodeId, focusNonce = 0 }: Props): ReactNode {
  const { project, reveal, openNoteView, askAboutNode, openGraph, refreshKey, theme } = useApp()
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
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [hidden, setHidden] = useState<Set<number>>(new Set())
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set())
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null)
  const focusCounter = useRef(0)
  const appliedFocus = useRef<string | null>(null)
  const firstLoad = useRef(true)
  const operation = useRef<AbortController | null>(null)
  useEffect(() => () => operation.current?.abort(), [])

  /** Adopt a payload and, on the first one, open small graphs so they are not a pile of dots. */
  const adopt = useCallback((data: GraphPayload) => {
    setPayload(data)
    writeGraphCache(cacheKey, data)
    if (firstLoad.current) {
      setExpanded(new Set(data.nodes.length <= 120 ? data.communities.map(c => c.id) : []))
      firstLoad.current = false
    }
  }, [cacheKey])

  useEffect(() => {
    if (projectId === null) return
    // Repaint from the in-memory cache first, then always revalidate: a tab switch
    // should never show an empty canvas for the length of a whole-project query.
    const cached = readGraphCache(cacheKey)
    if (cached) {
      setPayload(cached.payload)
      setLoading(false)
      setRefreshing(!isFresh(cached))
      if (firstLoad.current) {
        setExpanded(new Set(cached.payload.nodes.length <= 120 ? cached.payload.communities.map(c => c.id) : []))
        firstLoad.current = false
      }
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
  const choose = useCallback((id: string, zoom = true) => {
    const node = byId.get(id)
    if (!node) return
    setSelectedId(id)
    setExpanded(current => new Set(current).add(node.community))
    setHidden(current => { const next = new Set(current); next.delete(node.community); return next })
    setHiddenKinds(current => { const next = new Set(current); next.delete(node.kind); return next })
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
    }
    else setError('要定位的节点已不存在，请重建图谱或重新选择对话中的代码引用。')
  }, [initialNodeId, focusNonce, payload, byId, choose])

  const selected = selectedId ? byId.get(selectedId) ?? null : null
  const activeCommunity = payload?.communities.find(c => communityId(c.id) === selectedId) ?? null
  const view = useMemo(() => payload ? communityGraph(payload, expanded, hidden, hiddenKinds)
    : { nodes: [], edges: [] }, [payload, expanded, hidden, hiddenKinds])
  const connections = useMemo(() => {
    if (!selected || !payload) return []
    return payload.edges.filter(edge => edge.source === selected.id || edge.target === selected.id).flatMap(edge => {
      const other = byId.get(edge.source === selected.id ? edge.target : edge.source)
      return other ? [{ edge, other, outgoing: edge.source === selected.id }] : []
    })
  }, [selected, payload, byId])
  const neighborCount = new Set(connections.map(c => c.other.id)).size
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return needle ? (payload?.nodes ?? []).filter(node =>
      [node.label, node.path, node.content_md ?? ''].join(' ').toLowerCase().includes(needle)).slice(0, 30) : []
  }, [query, payload])

  const toggleCluster = (id: number) => {
    setExpanded(current => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setHidden(current => { const next = new Set(current); next.delete(id); return next })
    setSelectedId(communityId(id))
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
    } else if (node.path) reveal(node.path, node.start_line, node.end_line)
  }
  const rebuild = async () => {
    if (projectId === null) return
    setRebuilding(true)
    setError(null)
    try {
      if (scope === 'code') await api.rebuildGraph(projectId)
      // The rebuilt projection must win the next read; a cached copy would.
      clearGraphCache(cacheKey)
      setRevision(v => v + 1)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setRebuilding(false) }
  }

  if (projectId === null) return <EmptyState icon="folder" title="还没有打开项目" hint="选择项目后查看图谱。" />
  if (loading && !payload) return <Loading text="正在加载图谱…" />
  if (error && !payload) return <ErrorNote message={error} onRetry={() => setRevision(v => v + 1)} />

  const nodes = payload?.nodes.length ?? 0
  const edges = payload?.edges.length ?? 0
  const communities = payload?.communities.length ?? 0
  const stats = payload?.stats
  const graphTheme: GraphTheme = theme

  return <div className="graph-view" data-graph-scope={scope}>
    <header className="graph-head">
      <div className="graph-head-id">
        <span className="graph-glyph" aria-hidden="true"><Icon name="network" size={15} /></span>
        <span className="graph-head-name">知识图谱</span>
        {refreshing ? <span className="graph-syncing" title="正在刷新图谱"><span className="spinner" />更新中…</span> : null}
      </div>
      <nav className="graph-scope" aria-label="切换图谱">
        <button aria-pressed={scope === 'code'} onClick={() => openGraph(null, 'code')}>代码图谱</button>
        <button aria-pressed={scope === 'notes'} onClick={() => openGraph(null, 'notes')}>笔记图谱</button>
      </nav>
      <div className="graph-head-stats" aria-label="图谱规模">
        <span className="graph-stat"><b>{nodes.toLocaleString('zh-CN')}</b><span>节点</span></span>
        <span className="graph-stat"><b>{edges.toLocaleString('zh-CN')}</b><span>关系</span></span>
        <span className="graph-stat"><b>{communities.toLocaleString('zh-CN')}</b><span>社区</span></span>
      </div>
      <div className="graph-head-acts">
        <button className="graph-act" onClick={() => { setExpanded(new Set()); setSelectedId(null); setFocus(null) }}>全部收起</button>
        <button className="graph-act" onClick={() => { setExpanded(new Set(payload?.communities.map(c => c.id))); setFocus(null) }}>全部展开</button>
        <button className="graph-act primary" disabled={rebuilding || loading} onClick={() => void rebuild()}>
          <Icon name="refresh" size={12} />{rebuilding ? '处理中…' : scope === 'code' ? '重建' : '刷新'}
        </button>
      </div>
    </header>

    {error && payload ? <div className="graph-banner"><ErrorNote message={error} onRetry={() => setRevision(v => v + 1)} /></div> : null}

    <div className="graph-body">
      <div className="graph-stage">
        {!payload?.nodes.length
          ? <GraphBlank scope={scope} busy={loading} onRebuild={() => void rebuild()} />
          : <GraphCanvas nodes={view.nodes} edges={view.edges} theme={graphTheme}
            selectedId={selectedId} focus={focus}
            onSelect={id => {
              if (id?.startsWith('community:')) toggleCluster(Number(id.slice(10)))
              else if (id) choose(id, false)
              else { setSelectedId(null); setFocus(null) }
            }}
            onActivate={id => { const node = byId.get(id); if (node) void openNode(node) }} />}
      </div>

      <aside className="graph-rail" aria-label="图谱检查器">
        <div className="graph-rail-search">
          <div className="graph-search">
            <Icon name="search" size={13} />
            <input value={query} onChange={event => setQuery(event.target.value)}
              placeholder={scope === 'code' ? '搜索名称 / 文件' : '搜索笔记 / 内容'} aria-label="搜索图谱节点" />
            {query && <button className="graph-search-clear" onClick={() => setQuery('')}
              title="清除搜索" aria-label="清除搜索"><Icon name="x" size={11} /></button>}
          </div>
          {query.trim() ? <div className="graph-results" aria-label="搜索结果">
            {!matches.length && <p className="graph-empty-line">没有匹配的节点。</p>}
            {matches.map(node => <button className="graph-result" key={node.id} onClick={() => choose(node.id)}
              title={node.path || node.content_md?.slice(0, 120)} aria-pressed={selectedId === node.id}>
              <span className="graph-dot" style={{ background: communityColor(graphTheme, node.community) }} />
              <span className="graph-result-label">{node.label}</span>
              <span className="graph-result-kind">{kindLabel(node.kind)}</span>
            </button>)}
          </div> : <p className="graph-hint">搜索会在定位时自动展开所属社区并解除筛选。</p>}
        </div>

        {scope === 'code' && <div className="graph-filters" role="group" aria-label="节点类型筛选">
          <span className="graph-filters-label">隐藏</span>
          {FILTERABLE_KINDS.map(kind => <button className="graph-chip" key={kind}
            aria-pressed={!hiddenKinds.has(kind)} onClick={() => {
              setHiddenKinds(current => { const next = new Set(current); if (next.has(kind)) next.delete(kind); else next.add(kind); return next })
              if (selected?.kind === kind) { setSelectedId(null); setFocus(null) }
            }} title={hiddenKinds.has(kind) ? '显示' + kindLabel(kind) + '节点' : '隐藏' + kindLabel(kind) + '节点'}>
            <Icon name={hiddenKinds.has(kind) ? 'eye-off' : 'eye'} size={11} />
            {kindLabel(kind)}
          </button>)}
        </div>}

        <section className="graph-section graph-rail-scroll" aria-label="社区目录">
          <div className="graph-section-head">
            <span>社区</span>
            <small>{communities}</small>
          </div>
          <div className="graph-comm-list">
            {payload?.communities.map(c => <div className="graph-comm" key={c.id}
              data-selected={activeCommunity?.id === c.id || undefined}
              data-expanded={expanded.has(c.id) || undefined}>
              <button className="graph-comm-toggle" aria-expanded={expanded.has(c.id)}
                onClick={() => toggleCluster(c.id)}
                title={(expanded.has(c.id) ? '收起 ' : '展开 ') + c.label}>
                <Icon name={expanded.has(c.id) ? 'chevron-down' : 'chevron-right'} size={12} />
                <span className="graph-dot" style={{ background: communityColor(graphTheme, c.id) }} />
                <span className="graph-comm-label">{c.label}</span>
                <span className="graph-count">{c.size}</span>
              </button>
              <button className="graph-visibility" aria-pressed={!hidden.has(c.id)}
                aria-label={'显示社区 ' + c.label}
                title={hidden.has(c.id) ? '显示这个社区' : '隐藏这个社区'}
                onClick={() => {
                  setHidden(current => { const next = new Set(current); if (next.has(c.id)) next.delete(c.id); else next.add(c.id); return next })
                  if (selected?.community === c.id || activeCommunity?.id === c.id) { setSelectedId(null); setFocus(null) }
                }}>
                <Icon name={hidden.has(c.id) ? 'eye-off' : 'eye'} size={12} />
              </button>
            </div>)}
          </div>
        </section>

        <section className="graph-detail" aria-label="节点信息">
          {selected ? <>
            <div className="graph-detail-head">
              <span className="graph-kind">{kindLabel(selected.kind)}</span>
              <span className="graph-kind-note">{selected.community_label}</span>
            </div>
            <h2 className="graph-detail-title" title={selected.label}>{selected.label}</h2>
            <p className="graph-detail-meta mono" title={selected.path ? selected.path + ':' + selected.start_line : undefined}>
              {scope === 'code' ? (selected.path ? selected.path + ':' + selected.start_line : '项目内节点') : selected.project_name}
              <span className="graph-sep">·</span>{neighborCount} 邻点
              <span className="graph-sep">·</span>{connections.length} 关系
            </p>
            <div className="graph-detail-acts">
              <button className="graph-act" onClick={() => askAboutNode({
                label: selected.label, node_id: selected.id, path: selected.path,
                start_line: selected.start_line, community_label: selected.community_label,
              })}><Icon name="message" size={12} />继续提问</button>
              {selected.path && !selected.external && <button className="graph-act" onClick={() => void openNode(selected)}>
                <Icon name={scope === 'code' ? 'file-code' : 'notes'} size={12} />
                {scope === 'code' ? '打开代码' : '打开笔记'}
              </button>}
            </div>
            {selected.content_md && <p className="graph-note-body">{selected.content_md.slice(0, 250)}</p>}
            <div className="graph-section-head graph-relations-head">
              <span>关系</span><small>{connections.length}</small>
            </div>
            <div className="graph-relations">
              {connections.length ? connections.map(({ edge, other, outgoing }, index) =>
                <button className="graph-relation" key={index} onClick={() => choose(other.id)}
                  title={[other.path, ...(edge.evidence ?? [])].join(' · ')}>
                  <span className="graph-relation-dir" data-dir={scope === 'notes' ? 'both' : outgoing ? 'out' : 'in'}
                    aria-hidden="true">{scope === 'notes' ? '↔' : outgoing ? '→' : '←'}</span>
                  <span className="graph-relation-label">{other.label}</span>
                  <span className="graph-relation-rel" style={{ color: communityColor(graphTheme, other.community) }}>
                    {relationLabel(edge.relation)}
                  </span>
                  {scope === 'code' && <span className={'graph-tag ' + edge.confidence.toLowerCase()}>
                    {confidenceLabel(edge.confidence)}</span>}
                </button>) : <p className="graph-empty-line">此节点没有直接关联。</p>}
            </div>
            <RelationshipSummary key={scope + ':' + selected.id + ':' + revision + ':' + refreshKey}
              scope={scope} projectId={projectId} nodeId={selected.id} neighborCount={neighborCount} />
          </> : activeCommunity ? <>
            <div className="graph-detail-head">
              <span className="graph-kind">社区</span>
              <span className="graph-kind-note" style={{ color: communityColor(graphTheme, activeCommunity.id) }}>
                {activeCommunity.label}
              </span>
            </div>
            <h2 className="graph-detail-title">{activeCommunity.size} 个成员</h2>
            <div className="graph-detail-acts">
              <button className="graph-act" onClick={() => toggleCluster(activeCommunity.id)}>
                <Icon name="layers" size={12} />
                {expanded.has(activeCommunity.id) ? '收起社区' : '展开社区'}
              </button>
            </div>
            <p className="graph-hint">选择成员后可查看基础信息，并让 AI 总结它与全部直接邻点的关系。</p>
            <div className="graph-members">
              {payload?.nodes.filter(n => n.community === activeCommunity.id).map(node =>
                <button className="graph-member" key={node.id} onClick={() => choose(node.id)}>
                  <span className="graph-member-label">{node.label}</span>
                  <span className="graph-result-kind">{kindLabel(node.kind)}</span>
                </button>)}
            </div>
          </> : <div className="graph-detail-idle">
            <span className="graph-detail-idle-icon"><Icon name="locate" size={14} /></span>
            <div>
              <strong>从一个节点开始探索</strong>
              <p>点选画布中的节点查看信息，再让 AI 总结它与全部直接邻点的关系。</p>
            </div>
          </div>}
        </section>

        <footer className="graph-rail-foot">
          <div className="graph-legend" aria-label="连线颜色说明">
            {/* Edges inherit their source community's colour (ported from the
                reference report), so the legend explains that binding instead
                of listing relation families. */}
            <span className="graph-legend-item">
              <span className="graph-legend-swatch" style={{ background: COMMUNITY_PALETTE[0] }} />
              连线颜色跟随来源社区
            </span>
            <span className="graph-legend-item">
              <span className="graph-legend-swatch graph-legend-dashed" />
              推断 / 存疑（虚线）
            </span>
          </div>
          <div className="graph-foot-stats">
            <span><b>{nodes.toLocaleString('zh-CN')}</b> 节点</span>
            <span><b>{edges.toLocaleString('zh-CN')}</b> 关系</span>
            {stats ? <><span><b>{stats.extracted.toLocaleString('zh-CN')}</b> 直读</span>
              <span><b>{stats.inferred.toLocaleString('zh-CN')}</b> 推断</span></> : null}
          </div>
        </footer>
      </aside>
    </div>
  </div>
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
