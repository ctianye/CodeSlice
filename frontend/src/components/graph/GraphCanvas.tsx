/** Static network shared by code and notes. Only explicit gestures move the view. */
import { useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { DataSet, Network } from 'vis-network/standalone'
import type { Edge, Node, Options } from 'vis-network'
import { Icon } from '../Icon'
import { confidenceLabel, isInferred, kindLabel, relationLabel } from './graphMeta'
import { EDGE_STYLE, canvasTheme, communityColor, type CanvasTheme, type GraphTheme } from './graphTheme'

export interface CanvasNode {
  id: string; label: string; degree: number; community: number; kind: string; start_line: number; path: string
  communityLabel?: string; origin?: string; tooltip?: string; x?: number; y?: number
}
export interface CanvasEdge { source: string; target: string; confidence: string; relation: string }
interface Props {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  theme: GraphTheme
  selectedId: string | null
  focus: { id: string; nonce: number } | null
  onSelect: (id: string | null) => void
  onActivate?: (id: string) => void
}

/** Relations with no direction; an arrowhead on these would imply a call that does not exist. */
const UNDIRECTED = new Set(['same_folder', 'same_project', 'shared_reference'])

const LABEL_FACE = 'system-ui, "Microsoft YaHei UI", "Segoe UI", sans-serif'

/**
 * How many node labels stay visible at a given zoom.
 *
 * Labels are drawn at a constant 12px on screen (see scaleLabels), which means
 * their *world* size grows as the view zooms out. On a 1,800-node project that
 * turns fit-to-window into a wall of overlapping text. Rather than shrink the
 * type below readability, name only the top few anchors when zoomed out and let
 * the rest stay dots with hover tooltips; zooming in progressively brings more
 * names back. `rank` is the node's index in degree-descending order.
 */
function labelLimit(scale: number): number {
  if (scale >= 0.5) return Number.POSITIVE_INFINITY
  if (scale >= 0.3) return 40
  if (scale >= 0.18) return 16
  if (scale >= 0.1) return 8
  return 4
}

/**
 * Repaint node labels so they keep a constant on-screen size.
 *
 * vis-network scales the whole canvas, which would shrink labels to specks at
 * 0.15×. Dividing by the current scale cancels that out, so the graph can be
 * zoomed out to read its shape without losing the community names. The halo
 * (strokeWidth/strokeColor) keeps a label legible where an edge crosses it.
 */
function scaleLabels(network: Network, nodes: DataSet<Node>, vars: CanvasTheme, rank: Map<string, number>) {
  const scale = network.getScale()
  const limit = labelLimit(scale)
  nodes.update(nodes.get().filter(node => node.label).map(node => {
    const visible = (rank.get(String(node.id)) ?? Number.MAX_SAFE_INTEGER) < limit
    return {
      id: node.id!,
      font: {
        size: visible ? vars.labelPx / scale : 0,
        color: vars.label,
        face: LABEL_FACE,
        strokeWidth: visible ? Math.round((2.4 / scale) * 10) / 10 : 0,
        strokeColor: vars.labelHalo,
      },
    }
  }))
}

export function GraphCanvas({ nodes, edges, theme, selectedId, focus, onSelect, onActivate }: Props): ReactNode {
  const hostRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const appliedFocus = useRef<Props['focus']>(null)
  const firstFit = useRef(false)
  const selectRef = useRef(onSelect)
  const activateRef = useRef(onActivate)
  selectRef.current = onSelect
  activateRef.current = onActivate

  const datasets = useMemo(() => ({ nodes: new DataSet<Node>(), edges: new DataSet<Edge>() }), [])
  const vars = useMemo(() => canvasTheme(theme), [theme])
  // Labels are ranked by degree so the overview keeps the anchors and drops the
  // tail. Only labelled (community) nodes take part, since ordinary nodes have
  // no label at all.
  const rank = useMemo(() => {
    const order = nodes.filter(n => n.label).sort((a, b) => b.degree - a.degree)
    return new Map(order.map((n, index) => [n.id, index]))
  }, [nodes])
  // The mount effect owns the zoom listener for the lifetime of the canvas, so
  // it reads the palette through a ref: a theme swap must not re-create the
  // network, which would reset the camera the user positioned.
  const varsRef = useRef(vars)
  varsRef.current = vars
  const rankRef = useRef(rank)
  rankRef.current = rank

  useEffect(() => {
    if (!hostRef.current) return
    const options: Options = {
      physics: { enabled: false },
      layout: { improvedLayout: false, randomSeed: 42 },
      interaction: { hover: true, tooltipDelay: 120, hideEdgesOnDrag: true,
        hideEdgesOnZoom: true, keyboard: false, multiselect: false },
      nodes: { chosen: { node: true, label: false }, borderWidth: 1.5 },
      edges: { smooth: { enabled: true, type: 'continuous', roundness: 0.2 }, selectionWidth: 3 },
    }
    const network = new Network(hostRef.current, datasets, options)
    firstFit.current = false
    appliedFocus.current = null
    networkRef.current = network
    network.on('zoom', () => scaleLabels(network, datasets.nodes, varsRef.current, rankRef.current))
    network.on('click', event => selectRef.current(typeof event.nodes[0] === 'string' ? event.nodes[0] : null))
    network.on('doubleClick', event => {
      if (typeof event.nodes[0] === 'string') activateRef.current?.(event.nodes[0])
    })
    return () => { network.destroy(); networkRef.current = null }
  }, [datasets])

  useEffect(() => {
    const network = networkRef.current
    if (!network) return
    const positions = network.getPositions()
    const ids = new Set(nodes.map(n => n.id))
    datasets.nodes.remove(datasets.nodes.getIds().filter(id => !ids.has(String(id))))
    const maxDegree = Math.max(1, ...nodes.map(n => n.degree))
    // Ported from the reference: every node is a plain dot whose border equals
    // its fill, so the shape reads as one solid disc of community colour; a
    // selected or hovered node inverts to a white disc with the same border.
    datasets.nodes.update(nodes.map((node, index): Node => {
      const color = communityColor(theme, node.community)
      const center = node.kind === 'community'
      // String titles can contain HTML. textContent keeps source and notes literal.
      const title = document.createElement('div')
      title.textContent = [node.label, kindLabel(node.kind), node.path ? node.path + ':' + node.start_line : '',
        node.communityLabel, (center ? '成员：' : '连接：') + node.degree, node.tooltip].filter(Boolean).join('\n')
      return {
        id: node.id, label: center ? (node.label.length > 32 ? node.label.slice(0, 31) + '…' : node.label) : '', title,
        shape: 'dot',
        size: center ? 19 + 12 * Math.sqrt(node.degree / maxDegree) : 6 + 9 * Math.sqrt(node.degree / maxDegree),
        color: {
          background: color, border: color,
          highlight: { background: vars.selectionFill, border: color },
          hover: { background: vars.selectionFill, border: color },
        },
        font: center
          ? { size: vars.labelPx, color: vars.label, face: LABEL_FACE, strokeWidth: 2.4, strokeColor: vars.labelHalo }
          : { size: 0, color: vars.label, face: LABEL_FACE },
        borderWidth: center ? 3 : 0,
        ...(positions[node.id] ?? { x: node.x ?? 40 * Math.sqrt(index) * Math.cos(index * 2.4),
          y: node.y ?? 40 * Math.sqrt(index) * Math.sin(index * 2.4) }),
      }
    }))
    // Edge ink = the SOURCE node's community colour (the reference sets no
    // explicit edge colour, so vis inherits from-node). Proof level drives the
    // weight/alpha: extracted facts 2px @0.7, deductions dashed 1px @0.35.
    datasets.edges.clear()
    datasets.edges.add(edges.map((edge, index): Edge => {
      const membership = edge.relation === 'community_member'
      const inferred = !membership && isInferred(edge.relation, edge.confidence)
      const style = membership ? EDGE_STYLE.member : inferred ? EDGE_STYLE.inferred : EDGE_STYLE.extracted
      const source = nodes.find(n => n.id === edge.source)
      const ink = source ? communityColor(theme, source.community) : vars.fallbackEdge
      const title = document.createElement('div')
      title.textContent = relationLabel(edge.relation) + ' · ' + confidenceLabel(edge.confidence)
      return { id: 'e' + index, from: edge.source, to: edge.target, title,
        dashes: inferred, width: style.width,
        color: { color: ink, opacity: style.opacity },
        arrows: { to: { enabled: !membership && !UNDIRECTED.has(edge.relation), scaleFactor: 0.5 } },
      }
    }))
    if (!firstFit.current && nodes.length) {
      network.fit({ animation: false })
      firstFit.current = true
    }
    scaleLabels(network, datasets.nodes, vars, rank)
  }, [nodes, edges, theme, vars, rank, datasets])

  useEffect(() => {
    const network = networkRef.current
    if (!network) return
    const exists = selectedId && datasets.nodes.get(selectedId)
    network.selectNodes(exists ? [selectedId] : [], false)
    const connected = new Set(exists ? [selectedId, ...network.getConnectedNodes(selectedId).map(String)] : [])
    datasets.nodes.update(nodes.map(node => ({ id: node.id,
      opacity: !exists || connected.has(node.id) || node.kind === 'community' ? 1 : 0.25 })))
    datasets.edges.update(datasets.edges.get().map(edge => ({ id: edge.id!,
      color: { ...(typeof edge.color === 'object' ? edge.color : {}),
        opacity: !exists ? undefined
          : edge.from === selectedId || edge.to === selectedId ? 0.85 : 0.08 } })))
  }, [selectedId, nodes, edges, datasets])

  useEffect(() => {
    if (!focus) { appliedFocus.current = null; return }
    if (appliedFocus.current === focus || !datasets.nodes.get(focus.id)) return
    networkRef.current?.focus(focus.id, { scale: 1.25, animation: false, locked: false })
    appliedFocus.current = focus
    if (networkRef.current) scaleLabels(networkRef.current, datasets.nodes, vars, rank)
  }, [focus, nodes, datasets, vars, rank])

  const zoom = (factor: number) => {
    const network = networkRef.current
    if (network) {
      network.moveTo({ scale: Math.max(0.03, Math.min(4, network.getScale() * factor)), animation: false })
      scaleLabels(network, datasets.nodes, vars, rank)
    }
  }
  const fit = () => {
    const network = networkRef.current
    if (network) { network.fit({ animation: false }); scaleLabels(network, datasets.nodes, vars, rank) }
  }

  return <div className="graph-canvas-wrap" data-graph-theme={theme}>
    <div className="graph-canvas" ref={hostRef} aria-label="静态知识图谱画布" />
    {!nodes.length && <div className="graph-canvas-empty">
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
    <div className="graph-canvas-hint">静态模式 · 拖拽调整 · 滚轮缩放 · 点击社区展开 / 收起 · 悬浮查看详情</div>
  </div>
}
