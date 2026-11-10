/**
 * Force-directed graph canvas, modelled on graphify's `graph.html`.
 *
 * Three differences from the previous static layout matter:
 *
 * - **Physics, settled once.** A force-atlas layout lets communities pull
 *   together instead of pinning them to pre-computed rays. Stabilisation runs on
 *   first paint, then physics is switched off; the settled coordinates are cached
 *   in this module so a tab switch does not shake the graph again.
 * - **Filters hide, never remove.** Every node/edge stays in the DataSet with a
 *   `hidden` flag, so toggling a filter cannot reassign positions or restart the
 *   layout.
 * - **Labels are earned by degree.** A few high-degree anchors carry names at the
 *   overview zoom; the rest stay dots with tooltips until the reader zooms in.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { DataSet, Network } from 'vis-network/standalone'
import type { Edge, Node, Options } from 'vis-network'
import { Icon } from '../Icon'
import { confidenceLabel, isInferred, kindLabel, relationLabel } from './graphMeta'
import type { VisibleEdge, VisibleNode } from './graphFilter'
import {
  canvasTheme, communityColor, edgeStyle, isStructuralRelation,
  type CanvasTheme, type GraphTheme,
} from './graphTheme'

interface Props {
  nodes: VisibleNode[]
  edges: VisibleEdge[]
  theme: GraphTheme
  scope: 'code' | 'notes'
  selectedId: string | null
  focus: { id: string; nonce: number } | null
  /** Identifies one build so settled coordinates survive a tab switch. */
  layoutKey: string
  /** Coordinates restored from SQLite for this topology. */
  initialPositions?: Record<string, { x: number; y: number }>
  /** Called after the first settle and after the user drags a node. */
  onSettled?: (positions: Record<string, { x: number; y: number }>) => void
  onSelect: (id: string | null) => void
  onActivate?: (id: string) => void
}

/** Zoom used when a search/citation/locate request focuses one node. */
const FOCUS_SCALE = 0.7

const LABEL_FACE = 'system-ui, "Microsoft YaHei UI", "Segoe UI", sans-serif'

/** Relations with no direction; an arrowhead would imply a call that does not exist. */
const UNDIRECTED = new Set(['same_folder', 'same_project', 'shared_reference'])
const ARROWED = new Set(['calls', 'indirect_call', 'imports', 'imports_from', 'inherits', 'implements'])

/**
 * How many node labels stay visible in the current viewport. Labels keep a
 * constant on-screen size, so a fixed global rank either drowns the overview or
 * leaves a zoomed-in neighbourhood nameless. Candidates are clipped to the
 * viewport and ranked by degree, which is what makes a focused hub readable
 * without labelling every neighbour it has.
 */
function labelLimit(scale: number): number {
  if (scale >= 0.7) return 22
  if (scale >= 0.45) return 26
  if (scale >= 0.28) return 24
  if (scale >= 0.15) return 16
  if (scale >= 0.08) return 10
  return 6
}

/** Settled positions per build, newest last. Reset by page reload. */
const POSITION_CACHE = new Map<string, Map<string, { x: number; y: number }>>()
const POSITION_CACHE_MAX = 4

function rememberPositions(layoutKey: string, positions: Map<string, { x: number; y: number }>): void {
  POSITION_CACHE.delete(layoutKey)
  POSITION_CACHE.set(layoutKey, positions)
  while (POSITION_CACHE.size > POSITION_CACHE_MAX) {
    const oldest = POSITION_CACHE.keys().next().value
    if (oldest === undefined) break
    POSITION_CACHE.delete(oldest)
  }
}

function recallPositions(layoutKey: string, ids: string[]): Map<string, { x: number; y: number }> | null {
  const cached = POSITION_CACHE.get(layoutKey)
  if (!cached) return null
  const hits = ids.reduce((count, id) => count + (cached.has(id) ? 1 : 0), 0)
  return ids.length && hits / ids.length >= 0.8 ? cached : null
}

function edgeInk(theme: GraphTheme, relation: string, sourceCommunity: number | undefined): string {
  if (isStructuralRelation(relation)) return canvasTheme(theme).fallbackEdge
  return sourceCommunity === undefined
    ? canvasTheme(theme).fallbackEdge
    : communityColor(theme, sourceCommunity)
}

function applyLabels(
  network: Network,
  dataset: DataSet<Node>,
  nodes: VisibleNode[],
  vars: CanvasTheme,
  selectedId: string | null,
  viewport: { width: number; height: number },
): void {
  const scale = Math.max(network.getScale(), 0.01)
  const limit = labelLimit(scale)
  const view = network.getViewPosition()
  const positions = network.getPositions()
  const halfWidth = (viewport.width / scale / 2) * 1.2
  const halfHeight = (viewport.height / scale / 2) * 1.2
  const candidates = nodes.filter(node => {
    if (node.hidden) return false
    const position = positions[node.id]
    return Boolean(position)
      && Math.abs(position.x - view.x) <= halfWidth
      && Math.abs(position.y - view.y) <= halfHeight
  })
  candidates.sort((a, b) => b.degree - a.degree)
  const labelled = new Set(candidates.slice(0, limit).map(node => node.id))
  if (selectedId) labelled.add(selectedId)

  const fontPx = Math.round((vars.labelPx / scale) * 10) / 10
  const halo = Math.round((1.1 / scale) * 10) / 10
  dataset.update(nodes.map(node => {
    const visible = !node.hidden && labelled.has(node.id)
    return {
      id: node.id,
      font: {
        size: visible ? fontPx : 0,
        color: vars.label,
        face: LABEL_FACE,
        strokeWidth: visible ? halo : 0,
        strokeColor: vars.labelHalo,
      },
    }
  }))
}

export function GraphCanvas({
  nodes, edges, theme, scope, selectedId, focus, layoutKey, initialPositions, onSettled, onSelect, onActivate,
}: Props): ReactNode {
  const hostRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const appliedFocus = useRef<Props['focus']>(null)
  const fitted = useRef(false)
  const selectRef = useRef(onSelect)
  const activateRef = useRef(onActivate)
  const settledRef = useRef(onSettled)
  selectRef.current = onSelect
  activateRef.current = onActivate
  settledRef.current = onSettled
  const [settling, setSettling] = useState(false)
  const persistedPositions = useMemo(
    () => new Map(Object.entries(initialPositions ?? {})),
    [initialPositions],
  )

  const datasets = useMemo(() => ({ nodes: new DataSet<Node>(), edges: new DataSet<Edge>() }), [])
  const vars = useMemo(() => canvasTheme(theme), [theme])
  const varsRef = useRef(vars)
  varsRef.current = vars
  const selectedRef = useRef(selectedId)
  selectedRef.current = selectedId
  const focusRef = useRef(focus)
  focusRef.current = focus
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  const labelFrame = useRef(0)
  /** vis emits `stabilizationIterationsDone` only for a pass it started itself. */
  const settleDone = useRef(false)
  const settleRef = useRef<(() => void) | null>(null)
  const settleTimer = useRef(0)
  const stabilizePending = useRef(false)
  const viewport = () => ({
    width: hostRef.current?.clientWidth ?? 1200,
    height: hostRef.current?.clientHeight ?? 800,
  })

  /**
   * Fit only what the filters currently show. vis-network's own `fit()` also
   * frames hidden nodes, which sat unused far outside the visible clusters and
   * made the graph look tiny in the corner.
   */
  const fitVisible = (network: Network) => {
    const ids = nodesRef.current.filter(node => !node.hidden).map(node => node.id)
    network.fit(ids.length ? { nodes: ids, animation: false } : { animation: false })
    applyLabels(network, datasets.nodes, nodesRef.current, varsRef.current, selectedRef.current, viewport())
  }

  // Seed coordinates from the last settled layout before the network exists, so
  // a remount can skip a multi-second stabilisation entirely. Recomputed once per
  // build: a new `layoutKey` means a different topology, so the previous build's
  // coordinates (and cached map) do not apply to it.
  const seed = useRef<{ key: string; positions: Map<string, { x: number; y: number }> | null }>({
    key: '',
    positions: null,
  })
  if (seed.current.key !== layoutKey) {
    const ids = nodes.map(node => node.id)
    const cached = recallPositions(layoutKey, ids)
    const hits = ids.reduce((count, id) => count + (persistedPositions.has(id) ? 1 : 0), 0)
    seed.current = {
      key: layoutKey,
      positions: cached ?? (ids.length && hits / ids.length >= 0.8 ? persistedPositions : null),
    }
  }

  useEffect(() => {
    if (!hostRef.current) return
    const usePhysics = !seed.current.positions
    const options: Options = {
      physics: {
        enabled: usePhysics,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -60,
          centralGravity: 0.005,
          springLength: 120,
          springConstant: 0.08,
          damping: 0.4,
          avoidOverlap: 0.8,
        },
        // The settled result is persisted, so this is the only expensive pass.
        // Large graphs get a shorter pass: 200 iterations took ~20s on a 2k-node
        // graph, while 80 still produces a readable community layout.
        stabilization: {
          enabled: true,
          iterations: nodes.length > 1200 ? 80 : 140,
          updateInterval: 25,
          fit: false,
        },
      },
      layout: { improvedLayout: false, randomSeed: 42 },
      interaction: {
        hover: true,
        tooltipDelay: 120,
        hideEdgesOnDrag: true,
        keyboard: false,
        multiselect: false,
      },
      nodes: { shape: 'dot', borderWidth: 1.5, chosen: { node: true, label: false } },
      edges: { smooth: { enabled: true, type: 'continuous', roundness: 0.15 }, selectionWidth: 3 },
    }
    const network = new Network(hostRef.current, datasets, options)
    networkRef.current = network
    fitted.current = false
    appliedFocus.current = null
    settleDone.current = false
    stabilizePending.current = usePhysics
    setSettling(usePhysics)
    if (usePhysics) {
      // Backstop for a build that never receives nodes: without it the overlay
      // would stay up and the simulation would keep running on an empty canvas.
      settleTimer.current = window.setTimeout(() => settleRef.current?.(), 15_000)
    }
    network.on('zoom', () => {
      if (labelFrame.current) return
      labelFrame.current = window.requestAnimationFrame(() => {
        labelFrame.current = 0
        applyLabels(network, datasets.nodes, nodesRef.current, varsRef.current, selectedRef.current, viewport())
      })
    })
    network.on('click', event => selectRef.current(typeof event.nodes[0] === 'string' ? event.nodes[0] : null))
    network.on('doubleClick', event => {
      if (typeof event.nodes[0] === 'string') activateRef.current?.(event.nodes[0])
    })
    const settle = () => {
      if (settleDone.current) return
      settleDone.current = true
      stabilizePending.current = false
      if (settleTimer.current) {
        window.clearTimeout(settleTimer.current)
        settleTimer.current = 0
      }
      network.setOptions({ physics: { enabled: false } })
      const positions = network.getPositions()
      rememberPositions(layoutKey, new Map(Object.entries(positions)))
      settledRef.current?.(positions)
      setSettling(false)
      fitVisible(network)
      // A locate/search request can arrive before physics settles. fitVisible()
      // would otherwise reset that camera to the whole graph, leaving the
      // selected node in a corner; re-apply the requested focus last.
      const wanted = focusRef.current
      if (wanted && datasets.nodes.get(wanted.id)) {
        network.focus(wanted.id, { scale: FOCUS_SCALE, animation: false, locked: false })
        appliedFocus.current = wanted
        applyLabels(network, datasets.nodes, nodesRef.current, varsRef.current, selectedRef.current, viewport())
      }
    }
    settleRef.current = settle
    // The event only fires for a stabilization pass vis started itself. The
    // network is constructed empty on purpose (data arrives in the next effect),
    // and `stabilize()` bails out on an empty body without emitting — which is
    // exactly how the canvas used to spin forever with physics running. The
    // explicit pass below plus `stabilized` and the timer make settling certain.
    network.once('stabilizationIterationsDone', settle)
    network.once('stabilized', settle)
    network.on('dragEnd', () => {
      const positions = network.getPositions()
      rememberPositions(layoutKey, new Map(Object.entries(positions)))
      settledRef.current?.(positions)
    })
    return () => {
      if (labelFrame.current) window.cancelAnimationFrame(labelFrame.current)
      labelFrame.current = 0
      if (settleTimer.current) window.clearTimeout(settleTimer.current)
      settleTimer.current = 0
      settleRef.current = null
      settleDone.current = true
      network.destroy()
      networkRef.current = null
    }
    // The canvas is rebuilt only for a different graph build; theme and data
    // changes update datasets in place so the camera is never reset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasets, layoutKey])

  useEffect(() => {
    const network = networkRef.current
    if (!network) return
    const ids = new Set(nodes.map(node => node.id))
    datasets.nodes.remove(datasets.nodes.getIds().filter(id => !ids.has(String(id))))
    const maxDegree = Math.max(1, ...nodes.map(node => node.degree))
    const positions = seed.current.positions ?? new Map(Object.entries(network.getPositions()))
    datasets.nodes.update(nodes.map((node, index): Node => {
      const color = communityColor(theme, node.community)
      const title = document.createElement('div')
      title.textContent = [
        node.label,
        kindLabel(node.kind),
        node.path ? node.path + ':' + node.start_line : '',
        node.community_label ? '社区：' + node.community_label : '',
        '连接：' + node.degree,
      ].filter(Boolean).join('\n')
      const position = positions.get(node.id)
      return {
        id: node.id,
        label: node.label.length > 42 ? node.label.slice(0, 41) + '…' : node.label,
        title,
        hidden: node.hidden,
        shape: 'dot',
        size: 7 + 17 * Math.sqrt(node.degree / maxDegree),
        color: {
          background: color,
          border: color,
          highlight: { background: vars.selectionFill, border: color },
          hover: { background: vars.selectionFill, border: color },
        },
        font: { size: 0, color: vars.label, face: LABEL_FACE },
        ...(position ?? {
          x: 40 * Math.sqrt(index) * Math.cos(index * 2.4),
          y: 40 * Math.sqrt(index) * Math.sin(index * 2.4),
        }),
      }
    }))
    const byId = new Map(nodes.map(node => [node.id, node]))
    datasets.edges.clear()
    datasets.edges.add(edges.map((edge, index): Edge => {
      const inferred = isInferred(edge.relation, edge.confidence)
      const style = edgeStyle(edge.relation)
      const ink = edgeInk(theme, edge.relation, byId.get(edge.source)?.community)
      const title = document.createElement('div')
      title.textContent = relationLabel(edge.relation) + ' · ' + confidenceLabel(edge.confidence)
        + (edge.line ? ' · L' + edge.line : '')
      return {
        id: 'e' + index,
        from: edge.source,
        to: edge.target,
        hidden: edge.hidden,
        title,
        dashes: inferred,
        width: inferred ? style.width * 0.7 : style.width,
        color: { color: ink, opacity: inferred ? style.opacity * 0.6 : style.opacity },
        arrows: {
          to: {
            enabled: !UNDIRECTED.has(edge.relation) && ARROWED.has(edge.relation),
            scaleFactor: 0.45,
          },
        },
      }
    }))
    if (!fitted.current && nodes.some(node => !node.hidden)) {
      fitVisible(network)
      fitted.current = true
    }
    // The network was built before any node existed, so the stabilization pass
    // vis ran at construction had nothing to move and never reported back. Ask
    // for a real pass now that the data is in. The timer is the safety net: if
    // vis swallows the event (or the graph is too big to finish), physics still
    // gets switched off instead of drifting for the rest of the session.
    if (stabilizePending.current && nodes.length) {
      stabilizePending.current = false
      const iterations = nodes.length > 1200 ? 80 : 140
      window.requestAnimationFrame(() => {
        if (settleDone.current) return
        networkRef.current?.stabilize(iterations)
      })
      if (settleTimer.current) window.clearTimeout(settleTimer.current)
      settleTimer.current = window.setTimeout(
        () => settleRef.current?.(),
        Math.min(30_000, 8_000 + nodes.length * 5),
      )
    }
    applyLabels(network, datasets.nodes, nodes, vars, selectedId, viewport())
    seed.current = { key: layoutKey, positions: null }
  }, [nodes, edges, theme, vars, selectedId, datasets])

  // Dim everything except the selected node's one-hop neighbourhood.
  useEffect(() => {
    const network = networkRef.current
    if (!network) return
    const active = selectedId && datasets.nodes.get(selectedId) ? selectedId : null
    network.selectNodes(active ? [active] : [], false)
    const connected = new Set(active ? [active, ...network.getConnectedNodes(active).map(String)] : [])
    datasets.nodes.update(nodes.map(node => ({
      id: node.id,
      opacity: !active || node.hidden || connected.has(node.id) ? 1 : 0.42,
    })))
    datasets.edges.update(datasets.edges.get().map(edge => {
      const base = typeof edge.color === 'object' && edge.color ? Number(edge.color.opacity ?? 0.6) : 0.6
      return {
        id: edge.id!,
        color: {
          ...(typeof edge.color === 'object' ? edge.color : {}),
          opacity: !active ? undefined
            : edge.from === active || edge.to === active ? Math.max(base, 0.75) : 0.08,
        },
      }
    }))
    applyLabels(network, datasets.nodes, nodes, vars, selectedId, viewport())
  }, [selectedId, nodes, edges, datasets, vars])

  useEffect(() => {
    if (!focus) { appliedFocus.current = null; return }
    if (appliedFocus.current === focus || !datasets.nodes.get(focus.id)) return
    networkRef.current?.focus(focus.id, { scale: FOCUS_SCALE, animation: false, locked: false })
    appliedFocus.current = focus
    if (networkRef.current) {
      applyLabels(networkRef.current, datasets.nodes, nodes, vars, selectedId, viewport())
      // vis applies the camera on its next draw; make sure labels use the new
      // scale even if that path does not emit a zoom event.
      window.requestAnimationFrame(() => {
        const current = networkRef.current
        if (current) {
          applyLabels(current, datasets.nodes, nodesRef.current, varsRef.current, selectedRef.current, viewport())
        }
      })
    }
  }, [focus, nodes, datasets, vars, selectedId])

  const zoom = (factor: number) => {
    const network = networkRef.current
    if (!network) return
    network.moveTo({ scale: Math.max(0.03, Math.min(4, network.getScale() * factor)), animation: false })
    applyLabels(network, datasets.nodes, nodes, vars, selectedId, viewport())
  }
  const fit = () => {
    const network = networkRef.current
    if (!network) return
    fitVisible(network)
  }

  const visibleCount = nodes.reduce((count, node) => count + (node.hidden ? 0 : 1), 0)

  return <div className="graph-canvas-wrap" data-graph-theme={theme}>
    <div className="graph-canvas" ref={hostRef} aria-label="知识图谱画布"
      data-settling={settling ? 'true' : undefined} />
    {settling ? <div className="graph-settling"><span className="spinner" />正在排布图谱…</div> : null}
    {!visibleCount && <div className="graph-canvas-empty">
      <Icon name="layers" size={15} />
      <span>当前筛选下没有可显示的节点</span>
    </div>}
    <div className="graph-canvas-tools" role="group" aria-label="画布缩放">
      <button className="graph-tool-btn" onClick={fit} title="适应窗口" aria-label="适应窗口">
        <Icon name="fit" size={14} />
      </button>
      <button className="graph-tool-btn" onClick={() => zoom(1.25)} title="放大" aria-label="放大">
        <Icon name="plus" size={14} />
      </button>
      <button className="graph-tool-btn" onClick={() => zoom(0.8)} title="缩小" aria-label="缩小">
        <Icon name="minus" size={14} />
      </button>
    </div>
    <div className="graph-canvas-hint">
      滚轮缩放 · 拖拽平移 · 单击查看 · 双击{scope === 'code' ? '打开代码' : '打开笔记'}
    </div>
  </div>
}
