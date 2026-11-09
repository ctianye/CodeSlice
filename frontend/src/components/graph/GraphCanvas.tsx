/** Static network shared by code and notes. Only explicit gestures move the view. */
import { useEffect, useMemo, useRef } from 'react'
import type { ReactNode } from 'react'
import { DataSet, Network } from 'vis-network/standalone'
import type { Options, Node, Edge } from 'vis-network'
import { confidenceLabel, isInferred, kindLabel, relationColor, relationLabel } from './graphMeta'

export interface CanvasNode {
  id: string; label: string; degree: number; community: number; kind: string; start_line: number; path: string
  communityLabel?: string; origin?: string; tooltip?: string; x?: number; y?: number
}
export interface CanvasEdge { source: string; target: string; confidence: string; relation: string }
interface Props {
  nodes: CanvasNode[]; edges: CanvasEdge[]; colors: string[]; selectedId: string | null
  focus: { id: string; nonce: number } | null
  onSelect: (id: string | null) => void
  onActivate?: (id: string) => void
}

/** Keep community names readable even when the whole graph is zoomed out. */
function scaleLabels(network: Network, nodes: DataSet<Node>) {
  nodes.update(nodes.get().filter(node => node.label).map(node => ({
    id: node.id!, font: { size: 12 / network.getScale(), color: '#e8eef6', face: 'system-ui' },
  })))
}

export function GraphCanvas({ nodes, edges, colors, selectedId, focus, onSelect, onActivate }: Props): ReactNode {
  const hostRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const appliedFocus = useRef<Props['focus']>(null)
  const selectRef = useRef(onSelect)
  const activateRef = useRef(onActivate)
  selectRef.current = onSelect
  activateRef.current = onActivate
  const datasets = useMemo(() => ({ nodes: new DataSet<Node>(), edges: new DataSet<Edge>() }), [])

  useEffect(() => {
    if (!hostRef.current) return
    const options: Options = {
      physics: { enabled: false },
      layout: { improvedLayout: false, randomSeed: 42 },
      interaction: { hover: true, tooltipDelay: 120, hideEdgesOnDrag: true,
        hideEdgesOnZoom: true, keyboard: false, multiselect: false },
      nodes: { chosen: { node: true, label: false }, borderWidth: 1.5 },
      edges: { smooth: { enabled: true, type: 'continuous', roundness: 0.15 }, selectionWidth: 2 },
    }
    const network = new Network(hostRef.current, datasets, options)
    firstFit.current = false
    appliedFocus.current = null
    networkRef.current = network
    network.on('zoom', () => scaleLabels(network, datasets.nodes))
    network.on('click', event => selectRef.current(typeof event.nodes[0] === 'string' ? event.nodes[0] : null))
    network.on('doubleClick', event => {
      if (typeof event.nodes[0] === 'string') activateRef.current?.(event.nodes[0])
    })
    return () => { network.destroy(); networkRef.current = null }
  }, [datasets])

  const firstFit = useRef(false)
  useEffect(() => {
    const network = networkRef.current
    if (!network) return
    const positions = network.getPositions()
    const ids = new Set(nodes.map(n => n.id))
    datasets.nodes.remove(datasets.nodes.getIds().filter(id => !ids.has(String(id))))
    const maxDegree = Math.max(1, ...nodes.map(n => n.degree))
    datasets.nodes.update(nodes.map((node, index): Node => {
      const color = colors[node.community % colors.length]
      const center = node.kind === 'community'
      // String titles can contain HTML. textContent keeps source and notes literal.
      const title = document.createElement('div')
      title.textContent = [node.label, kindLabel(node.kind), node.path ? node.path + ':' + node.start_line : '',
        node.communityLabel, (center ? '成员：' : '连接：') + node.degree, node.tooltip].filter(Boolean).join('\n')
      return {
        id: node.id, label: center ? (node.label.length > 32 ? node.label.slice(0, 31) + '…' : node.label) : '', title,
        shape: center ? 'dot' : node.kind === 'class' ? 'diamond' : 'dot',
        size: center ? 19 + 12 * Math.sqrt(node.degree / maxDegree) : 6 + 9 * Math.sqrt(node.degree / maxDegree),
        color: { background: color, border: color, highlight: { background: '#fff', border: color } },
        font: { size: center ? 13 : 0, color: '#e8eef6', face: 'system-ui' },
        borderWidth: center ? 3 : 1.5,
        ...(positions[node.id] ?? { x: node.x ?? 40 * Math.sqrt(index) * Math.cos(index * 2.4),
          y: node.y ?? 40 * Math.sqrt(index) * Math.sin(index * 2.4) }),
      }
    }))
    datasets.edges.clear()
    datasets.edges.add(edges.map((edge, index): Edge => {
      const membership = edge.relation === 'community_member'
      const title = document.createElement('div')
      title.textContent = relationLabel(edge.relation) + ' · ' + confidenceLabel(edge.confidence)
      return { id: 'e' + index, from: edge.source, to: edge.target, title,
        dashes: membership || isInferred(edge.relation, edge.confidence), width: membership ? 0.5 : 1.2,
        color: { color: relationColor(edge.relation), opacity: membership ? 0.16 : 0.55 },
        arrows: { to: { enabled: !membership && !['same_folder', 'same_project', 'shared_reference'].includes(edge.relation),
          scaleFactor: 0.4 } },
      }
    }))
    if (!firstFit.current && nodes.length) {
      network.fit({ animation: false })
      firstFit.current = true
    }
    scaleLabels(network, datasets.nodes)
  }, [nodes, edges, colors, datasets])

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
        opacity: !exists ? (edge.width === 0.5 ? 0.16 : 0.55)
          : edge.from === selectedId || edge.to === selectedId ? 0.85 : 0.08 } })))
  }, [selectedId, nodes, edges, datasets])

  useEffect(() => {
    if (!focus) { appliedFocus.current = null; return }
    if (appliedFocus.current === focus || !datasets.nodes.get(focus.id)) return
    networkRef.current?.focus(focus.id, { scale: 1.25, animation: false, locked: false })
    appliedFocus.current = focus
    if (networkRef.current) scaleLabels(networkRef.current, datasets.nodes)
  }, [focus, nodes, datasets])

  const zoom = (factor: number) => {
    const network = networkRef.current
    if (network) {
      network.moveTo({ scale: Math.max(0.03, Math.min(4, network.getScale() * factor)), animation: false })
      scaleLabels(network, datasets.nodes)
    }
  }
  return <div className="graph-canvas-wrap">
    <div className="graph-canvas" ref={hostRef} aria-label="静态知识图谱画布" />
    {!nodes.length && <div className="graph-canvas-empty">没有可显示的节点，请调整筛选。</div>}
    <div className="graph-canvas-tools">
      <button className="graph-tool-btn" onClick={() => {
        const network = networkRef.current
        if (network) { network.fit({ animation: false }); scaleLabels(network, datasets.nodes) }
      }} title="适应窗口" aria-label="适应窗口">⤢</button>
      <button className="graph-tool-btn" onClick={() => zoom(1.25)} title="放大" aria-label="放大">+</button>
      <button className="graph-tool-btn" onClick={() => zoom(0.8)} title="缩小" aria-label="缩小">−</button>
    </div>
    <div className="graph-canvas-hint">静态模式 · 拖拽调整 · 滚轮缩放 · 点击社区展开 / 收起 · 悬浮查看详情</div>
  </div>
}
