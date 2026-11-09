import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as api from '../../api'
import type { GraphNode, GraphPayload } from '../../types'
import { useApp } from '../../state/AppState'
import { EmptyState, ErrorNote, Loading } from '../Feedback'
import { Icon } from '../Icon'
import { GraphCanvas } from './GraphCanvas'
import { communityGraph, communityId, GRAPH_PALETTE } from './communityGraph'
import { confidenceLabel, kindLabel, relationColor, relationLabel } from './graphMeta'
import { RelationshipSummary } from './RelationshipSummary'

interface Props {
  scope?: 'code' | 'notes'
  initialNodeId?: string | null
  focusNonce?: number
}

export function GraphView({ scope = 'code', initialNodeId, focusNonce = 0 }: Props): ReactNode {
  const { project, reveal, openNoteView, askAboutNode, openGraph, refreshKey } = useApp()
  const projectId = project?.id ?? null
  const [payload, setPayload] = useState<GraphPayload | null>(null)
  const [loading, setLoading] = useState(true)
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

  useEffect(() => {
    if (projectId === null) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    const request = scope === 'code' ? api.getGraph(projectId, controller.signal) : api.getNotesGraph(controller.signal)
    void request.then(data => {
      if (controller.signal.aborted) return
      setPayload(data)
      if (firstLoad.current) {
        setExpanded(new Set(data.nodes.length <= 120 ? data.communities.map(c => c.id) : []))
        firstLoad.current = false
      }
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [projectId, scope, revision, refreshKey])

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
      setRevision(v => v + 1)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setRebuilding(false) }
  }

  if (projectId === null) return <EmptyState icon="folder" title="还没有打开项目" hint="选择项目后查看图谱。" />
  if (loading && !payload) return <Loading text="正在加载图谱…" />
  if (error && !payload) return <ErrorNote message={error} onRetry={() => setRevision(v => v + 1)} />

  return <div className="graph-view" data-graph-scope={scope}>
    <header className="graph-view-head">
      <nav className="graph-scope-switch" aria-label="切换图谱">
        <button aria-pressed={scope === 'code'} onClick={() => openGraph(null, 'code')}>代码图谱</button>
        <button aria-pressed={scope === 'notes'} onClick={() => openGraph(null, 'notes')}>笔记图谱</button>
      </nav>
      <span className="graph-view-stats">{payload?.nodes.length ?? 0} 节点 · {payload?.edges.length ?? 0} 关系 · {payload?.communities.length ?? 0} 社区</span>
      <div className="graph-view-actions">
        <button className="small ghost" onClick={() => { setExpanded(new Set()); setSelectedId(null); setFocus(null) }}>全部收起</button>
        <button className="small ghost" onClick={() => { setExpanded(new Set(payload?.communities.map(c => c.id))); setFocus(null) }}>全部展开</button>
        <button className="small ghost" disabled={rebuilding || loading} onClick={() => void rebuild()}>
          <Icon name="refresh" size={12} />{rebuilding ? '处理中…' : scope === 'code' ? '重建' : '刷新'}
        </button>
      </div>
    </header>
    {error && <ErrorNote message={error} onRetry={() => setRevision(v => v + 1)} />}
    <div className="graph-view-stage">
      {!payload?.nodes.length
        ? <EmptyState icon="network" title={scope === 'code' ? '项目还没有代码图谱' : '还没有笔记'}
          hint={scope === 'code' ? '建立项目索引后，点击重建。' : '保存阅读笔记后即可在这里查看关联。'} />
        : <GraphCanvas nodes={view.nodes} edges={view.edges} colors={GRAPH_PALETTE}
          selectedId={selectedId} focus={focus}
          onSelect={id => {
            if (id?.startsWith('community:')) toggleCluster(Number(id.slice(10)))
            else if (id) choose(id, false)
            else { setSelectedId(null); setFocus(null) }
          }}
          onActivate={id => { const node = byId.get(id); if (node) void openNode(node) }} />}
    </div>
    <footer className="graph-dock">
      <section className="graph-dock-search" aria-label="图谱索引">
        <div className="graph-search-field">
          <Icon name="search" size={12} />
          <input value={query} onChange={event => setQuery(event.target.value)}
            placeholder={scope === 'code' ? '搜索名称 / 文件' : '搜索笔记 / 内容'} aria-label="搜索图谱节点" />
          {query && <button className="icon" onClick={() => setQuery('')} title="清除搜索"><Icon name="x" size={11} /></button>}
        </div>
        {query.trim() ? <div className="graph-match-list">
          {!matches.length && <div className="graph-dock-empty">没有匹配的节点。</div>}
          {matches.map(node => <button className="graph-match" key={node.id} onClick={() => choose(node.id)}
            title={node.path} aria-pressed={selectedId === node.id}>
            <span className="graph-match-dot" style={{ background: GRAPH_PALETTE[node.community % GRAPH_PALETTE.length] }} />
            <span className="graph-match-label">{node.label}</span><small>{kindLabel(node.kind)}</small>
          </button>)}
        </div> : <div className="graph-dock-hint">选择社区查看成员；选择节点查看关系。搜索结果会自动展开并定位。</div>}
        <div className="graph-index-title">社区目录 <small>展开 / 收起 · 显示 / 隐藏</small></div>
        {scope === 'code' && <div className="graph-kind-filters">
          {['rationale', 'external'].map(kind => <button className={'graph-kind-chip' + (hiddenKinds.has(kind) ? ' off' : '')}
            key={kind} aria-pressed={!hiddenKinds.has(kind)} onClick={() => {
              setHiddenKinds(current => { const next = new Set(current); if (next.has(kind)) next.delete(kind); else next.add(kind); return next })
              if (selected?.kind === kind) { setSelectedId(null); setFocus(null) }
            }}>{kindLabel(kind)}</button>)}
        </div>}
        <div className="graph-community-directory">
          {payload?.communities.map(c => <div className="graph-community-row" key={c.id}>
            <button className="graph-community-toggle" aria-expanded={expanded.has(c.id)} onClick={() => toggleCluster(c.id)}
              title={(expanded.has(c.id) ? '收起 ' : '展开 ') + c.label}>
              <span style={{ color: GRAPH_PALETTE[c.id % GRAPH_PALETTE.length] }}>{expanded.has(c.id) ? '▾' : '▸'}</span>
              <span className="graph-match-label">{c.label}</span><small>{c.size}</small>
            </button>
            <input type="checkbox" checked={!hidden.has(c.id)} aria-label={'显示社区 ' + c.label} onChange={() => {
              setHidden(current => { const next = new Set(current); if (next.has(c.id)) next.delete(c.id); else next.add(c.id); return next })
              if (selected?.community === c.id || activeCommunity?.id === c.id) { setSelectedId(null); setFocus(null) }
            }} />
          </div>)}
        </div>
      </section>
      <section className="graph-dock-detail" aria-label="节点信息">
        {selected ? <>
          <div className="graph-dock-detail-head">
            <strong title={selected.label}>{selected.label}</strong>
            <span className="graph-dock-community">{selected.community_label}</span>
            <span className="spacer" />
            <button className="small ghost" onClick={() => askAboutNode({
              label: selected.label, node_id: selected.id, path: selected.path,
              start_line: selected.start_line, community_label: selected.community_label,
            })}><Icon name="message" size={12} />继续提问</button>
            {selected.path && !selected.external && <button className="small ghost" onClick={() => void openNode(selected)}>
              {scope === 'code' ? '打开代码' : '打开笔记'}
            </button>}
          </div>
          <div className="graph-dock-meta">{kindLabel(selected.kind)} · {scope === 'code'
            ? selected.path + ':' + selected.start_line : selected.project_name} · {neighborCount} 个直接邻点 · {connections.length} 条关系</div>
          {selected.content_md && <p className="graph-note-body">{selected.content_md.slice(0, 250)}</p>}
          <div className="graph-relations-row">
            {connections.length ? connections.map(({ edge, other, outgoing }, index) =>
              <button className="graph-relation-chip" key={index} onClick={() => choose(other.id)}
                title={[other.path, ...(edge.evidence ?? [])].join(' · ')} style={{ borderLeftColor: relationColor(edge.relation) }}>
                <span>{scope === 'notes' ? '↔' : outgoing ? '→' : '←'}</span>
                <span className="graph-relation-rel">{relationLabel(edge.relation)}</span>
                <span className="graph-relation-label">{other.label}</span>
                {scope === 'code' && <span className={'graph-tag ' + edge.confidence.toLowerCase()}>{confidenceLabel(edge.confidence)}</span>}
              </button>) : <span className="graph-dock-empty">此节点没有直接关联。</span>}
          </div>
          <RelationshipSummary key={scope + ':' + selected.id + ':' + revision + ':' + refreshKey}
            scope={scope} projectId={projectId} nodeId={selected.id} neighborCount={neighborCount} />
        </> : activeCommunity ? <>
          <div className="graph-dock-detail-head"><strong>{activeCommunity.label}</strong>
            <span className="graph-dock-meta">{activeCommunity.size} 个成员</span>
            <button className="small ghost" onClick={() => toggleCluster(activeCommunity.id)}>
              {expanded.has(activeCommunity.id) ? '收起社区' : '展开社区'}</button>
          </div>
          <div className="graph-dock-hint">选择成员后，可查看基础信息并使用 AI 总结它与全部直接邻点的关系。</div>
          <div className="graph-community-members">
            {payload?.nodes.filter(n => n.community === activeCommunity.id).map(node =>
              <button className="graph-match" key={node.id} onClick={() => choose(node.id)}>
                <span className="graph-match-label">{node.label}</span><small>{kindLabel(node.kind)}</small>
              </button>)}
          </div>
        </> : <div className="graph-dock-empty graph-selection-placeholder">
          <Icon name="network" size={24} /><strong>从一个节点开始探索</strong>
          <span>点选节点查看信息，再点「AI 总结关系」。普通节点的信息只在悬浮和这里显示。</span>
        </div>}
      </section>
    </footer>
  </div>
}
