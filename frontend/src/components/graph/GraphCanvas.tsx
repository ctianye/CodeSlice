/**
 * Force-directed graph canvas built on vis-network.
 *
 * This replaces an earlier hand-rolled canvas renderer. The hand-rolled version
 * could draw circles and lines, but a graph view is judged on things a bare canvas
 * does not give you for free: nodes that spring apart on drag, curved edges that
 * route around each other, arrowheads, hover tooltips and smooth zoom. vis-network
 * is the same library graphify ships in its graph.html, so the interaction here
 * matches what a user coming from that tool already expects.
 *
 * Layout choices worth knowing:
 *
 * - Physics runs once, then stops (stabilizationIterationsDone). A graph that keeps
 *   drifting is impossible to click and burns frames while nothing changes.
 * - Edge style encodes confidence: solid + wide means the relationship is in the
 *   source, dashed + thin means it was inferred. A guess must look like a guess.
 * - Node size is degree, so hubs read as hubs without needing a legend.
 * - Labels show for hubs only; a label on every node turns a dense graph into a
 *   grey smear. Hover and selection reveal the rest.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { DataSet, Network } from 'vis-network/standalone'
import type { Options } from 'vis-network'

import {
  confidenceLabel,
  isInferred,
  kindLabel,
  relationColor,
  relationLabel,
} from './graphMeta'

export interface CanvasNode {
  id: string
  label: string
  /** Drives node radius: hubs must be visibly larger. */
  degree: number
  /** Index into the colour palette. */
  community: number
  /** Community display name, shown in the tooltip. */
  communityLabel?: string
  /** `file` nodes are drawn as boxes so structure reads at a glance. */
  kind: string
  start_line: number
  path: string
  /** For `rationale` nodes: whether the text came from a docstring or a comment. */
  origin?: string
  /** Extra line for the hover tooltip. */
  tooltip?: string
}

export interface CanvasEdge {
  source: string
  target: string
  /** `EXTRACTED` edges are solid, `INFERRED` dotted. */
  confidence: string
  relation: string
}

interface GraphCanvasProps {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  /** Palette, indexed by community id modulo its length. */
  colors: string[]
  selectedId: string | null
  /** ids currently visible; null means all. */
  visibleIds: Set<string> | null
  onSelect: (id: string | null) => void
  onHover?: (id: string | null) => void
  onActivate?: (id: string) => void
  emptyHint?: string
  className?: string
}

interface VisNode {
  id: string
  label: string
  color: { background: string; border: string; highlight: { background: string; border: string } }
  size: number
  font: { size: number; color: string; face?: string }
  title: string
  shape: string
  borderWidth: number
  /** Per-node overrides vis-network understands (used for the dotted outline). */
  shapeProperties?: { borderDashes: boolean | number[] }
  x: number
  y: number
  /** Filled in by the selection effect, not part of the base dataset. */
  opacity?: number
}

interface VisEdge {
  id: string
  from: string
  to: string
  title: string
  dashes: boolean
  width: number
  color: { color: string; opacity: number }
  arrows: { to: { enabled: boolean; scaleFactor: number } }
  smooth: { enabled: boolean; type: string; roundness: number }
}

export function GraphCanvas({
  nodes,
  edges,
  colors,
  selectedId,
  visibleIds,
  onSelect,
  onHover,
  onActivate,
  emptyHint = '没有可显示的内容',
  className,
}: GraphCanvasProps): ReactNode {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const networkRef = useRef<Network | null>(null)
  const nodesDSRef = useRef<DataSet<VisNode> | null>(null)
  const edgesDSRef = useRef<DataSet<VisEdge> | null>(null)
  const [, setHoverId] = useState<string | null>(null)

  // Callbacks live in refs so the network is built once; rebuilding it on every
  // parent re-render would restart physics and lose the user's viewport.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onHoverRef = useRef(onHover)
  onHoverRef.current = onHover
  const onActivateRef = useRef(onActivate)
  onActivateRef.current = onActivate

  const visibleSet = useMemo(() => {
    if (visibleIds) return visibleIds
    return new Set(nodes.map((node) => node.id))
  }, [visibleIds, nodes])

  const maxDegree = useMemo(() => Math.max(1, ...nodes.map((n) => n.degree)), [nodes])

  const visNodes = useMemo<VisNode[]>(
    () =>
      nodes.map((node, index) => {
        const color = colors[node.community % colors.length] ?? colors[0]
        const kind = node.kind
        const isExternal = kind === 'external'
        const isRationale = kind === 'rationale'

        // Size by sqrt so a 189-degree hub is large without swallowing the canvas.
        // Rationale and external nodes are capped: they are supporting evidence, and
        // letting a much-cited docstring grow to hub size misrepresents the shape of
        // the code.
        const raw = 8 + 22 * Math.sqrt(node.degree / maxDegree)
        const size = isRationale ? Math.min(raw, 13) : isExternal ? Math.min(raw, 9) : raw

        // Labels: a rationale's whole value is its text, so it always shows a short
        // form. External nodes are named only on hover - there can be hundreds.
        const fontPx = isRationale
          ? 11
          : isExternal
            ? 0
            : node.degree >= maxDegree * 0.35 || nodes.length <= 40
              ? 13
              : 0

        const titleLines = [node.label]
        if (isRationale) {
          titleLines.push(`说明 · ${node.path}:${node.start_line}`)
          if (node.origin) titleLines.push(`来源：${node.origin === 'docstring' ? '文档字符串' : '注释'}`)
        } else if (isExternal) {
          titleLines.push('外部依赖 · 定义不在本项目内')
        } else if (kind === 'file') {
          titleLines.push(`文件 · ${node.path}`)
        } else {
          titleLines.push(`${kindLabel(kind)} · ${node.path}:${node.start_line}`)
        }
        if (node.communityLabel) titleLines.push(`社区：${node.communityLabel}`)
        titleLines.push(`连接数：${node.degree}`)
        if (node.tooltip) titleLines.push(node.tooltip)

        return {
          id: node.id,
          // A rationale's label is a sentence; 60 chars is enough to recognise it.
          label:
            node.label.length > (isRationale ? 60 : 28)
              ? `${node.label.slice(0, isRationale ? 59 : 27)}…`
              : node.label,
          color: {
            background: isRationale ? '#3a3320' : color,
            border: isRationale ? '#c9a227' : isExternal ? '#4a5260' : color,
            highlight: { background: '#ffffff', border: color },
          },
          size,
          font: {
            size: fontPx,
            // Rationale text in the accent colour so a "why" node is recognisable
            // without reading it.
            color: isRationale ? '#e5cd85' : isExternal ? '#7d8794' : '#e8eef6',
            face: isRationale
              ? 'ui-serif, Georgia, serif'
              : 'ui-sans-serif, system-ui, sans-serif',
          },
          // A string title renders as a native tooltip. vis-network paints it with
          // innerText, so raw characters show literally and must not be escaped.
          title: titleLines.join('\n'),
          // Shape carries kind, so the picture is readable without a legend:
          // file = box, class = diamond, rationale = note, external = hollow dot.
          shape: isRationale
            ? 'box'
            : isExternal
              ? 'dot'
              : kind === 'file'
                ? 'box'
                : kind === 'class'
                  ? 'diamond'
                  : 'dot',
          borderWidth: isExternal ? 1 : kind === 'file' || isRationale ? 2 : 1.5,
          // External nodes: a dotted outline reads as "referenced, not defined
          // here". The key is only set when needed - vis-network dereferences
          // ``shapeProperties`` unconditionally, so passing ``undefined`` for every
          // other node crashes the whole canvas.
          ...(isExternal ? { shapeProperties: { borderDashes: [3, 3] } } : {}),
          // Golden-angle spiral seeds. Without spread-out starting points the
          // repulsion solver sees near-zero distances for coincident nodes and
          // blows up on large graphs (graphify hit exactly this).
          x: 40 * Math.sqrt(index) * Math.cos(index * 2.4),
          y: 40 * Math.sqrt(index) * Math.sin(index * 2.4),
        }
      }),
    [nodes, colors, maxDegree],
  )

  const visEdges = useMemo<VisEdge[]>(
    () =>
      edges.map((edge, index) => {
        const dotted = isInferred(edge.relation, edge.confidence)
        const colour = relationColor(edge.relation)
        return {
          id: `e${index}`,
          from: edge.source,
          to: edge.target,
          title: `${relationLabel(edge.relation)} · ${confidenceLabel(edge.confidence)}`,
          dashes: dotted,
          // A rationale edge is the "why" between two nodes; drawing it as thin as
          // a call edge would bury the one thing a reader asks about most.
          width: edge.relation === 'rationale_for' ? 1.2 : dotted ? 1 : 1.6,
          color: { color: colour, opacity: dotted ? 0.38 : 0.6 },
          // Only relation kinds that have a direction worth reading get an arrow.
          // Containment is obvious from layout and an arrow on it is just clutter.
          arrows: {
            to: {
              enabled: !['contains', 'method', 'declares', 'rationale_for'].includes(edge.relation),
              scaleFactor: 0.45,
            },
          },
          // Curved edges keep parallel relations between the same pair readable.
          smooth: { enabled: true, type: 'continuous', roundness: 0.2 },
        }
      }),
    [edges],
  )

  /** Build the network once, then only feed it data. */
  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const nodesDS = new DataSet<VisNode>(visNodes)
    const edgesDS = new DataSet<VisEdge>(visEdges)
    nodesDSRef.current = nodesDS
    edgesDSRef.current = edgesDS

    const options: Options = {
      physics: {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -62,
          centralGravity: 0.008,
          springLength: 130,
          springConstant: 0.075,
          damping: 0.42,
          avoidOverlap: 0.85,
        },
        stabilization: { enabled: true, iterations: 220, updateInterval: 25, fit: true },
      },
      interaction: {
        hover: true,
        tooltipDelay: 120,
        // Hiding edges while dragging is what makes a big graph draggable at all.
        hideEdgesOnDrag: true,
        hideEdgesOnZoom: true,
        navigationButtons: false,
        keyboard: false,
        multiselect: false,
      },
      nodes: {
        shape: 'dot',
        borderWidth: 1.5,
        chosen: { node: true, label: true },
        shadow: { enabled: true, color: 'rgba(0,0,0,0.35)', size: 8, x: 0, y: 2 },
      },
      edges: { selectionWidth: 2.5, smooth: { enabled: true, type: 'continuous', roundness: 0.2 } },
      layout: { improvedLayout: false, randomSeed: 42 },
    }

    const network = new Network(host, { nodes: nodesDS, edges: edgesDS }, options)
    networkRef.current = network

    network.once('stabilizationIterationsDone', () => {
      // Freeze after settling: a permanently drifting layout cannot be clicked.
      network.setOptions({ physics: { enabled: false } })
      network.fit({ animation: { duration: 320, easingFunction: 'easeInOutQuad' } })
    })

    network.on('click', (params) => {
      const id = params.nodes?.[0]
      onSelectRef.current(typeof id === 'string' ? id : null)
    })

    network.on('doubleClick', (params) => {
      const id = params.nodes?.[0]
      if (typeof id === 'string') onActivateRef.current?.(id)
    })

    network.on('hoverNode', (params) => {
      setHoverId(params.node as string)
      onHoverRef.current?.(params.node as string)
    })

    network.on('blurNode', () => {
      setHoverId(null)
      onHoverRef.current?.(null)
    })

    return () => {
      network.destroy()
      networkRef.current = null
      nodesDSRef.current = null
      edgesDSRef.current = null
    }
    // Built once on purpose: the data effects below update the datasets in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Swap the data when the graph itself changes (aggregate toggle, new build). */
  useEffect(() => {
    const nodesDS = nodesDSRef.current
    const edgesDS = edgesDSRef.current
    const network = networkRef.current
    if (!nodesDS || !edgesDS || !network) return
    nodesDS.clear()
    edgesDS.clear()
    nodesDS.add(visNodes)
    edgesDS.add(visEdges)
    network.setOptions({ physics: { enabled: true } })
    network.stabilize(160)
  }, [visNodes, visEdges])

  /** Dim everything except the selection and its neighbours. */
  useEffect(() => {
    const nodesDS = nodesDSRef.current
    const edgesDS = edgesDSRef.current
    const network = networkRef.current
    if (!nodesDS || !edgesDS || !network) return

    const all = nodesDS.get()
    if (!selectedId) {
      nodesDS.update(
        all.map((node) => ({
          id: node.id,
          opacity: 1,
          font: { ...node.font, color: '#e8eef6' },
        })),
      )
      edgesDS.update(
        edgesDS.get().map((edge) => ({
          id: edge.id,
          color: { ...edge.color, opacity: edge.dashes ? 0.4 : 0.62 },
        })),
      )
      return
    }

    const connected = new Set<string>([selectedId, ...network.getConnectedNodes(selectedId).map(String)])
    nodesDS.update(
      all.map((node) => {
        const on = connected.has(node.id)
        return {
          id: node.id,
          opacity: on ? 1 : 0.22,
          font: {
            ...node.font,
            // Bring the selected node's label back even when degree hid it.
            size: node.id === selectedId ? 13 : node.font.size,
            color: on ? '#e8eef6' : 'rgba(232,238,246,0.25)',
          },
        }
      }),
    )
    edgesDS.update(
      edgesDS.get().map((edge) => {
        const touches = edge.from === selectedId || edge.to === selectedId
        const base = edge.dashes ? 0.4 : 0.62
        return {
          id: edge.id,
          color: { ...edge.color, opacity: touches ? Math.min(1, base + 0.3) : 0.08 },
        }
      }),
    )
  }, [selectedId])

  /** Hide filtered-out nodes (community toggles). */
  useEffect(() => {
    const nodesDS = nodesDSRef.current
    const edgesDS = edgesDSRef.current
    if (!nodesDS || !edgesDS) return
    nodesDS.update(
      nodesDS.get().map((node) => ({ id: node.id, hidden: !visibleSet.has(node.id) })),
    )
    edgesDS.update(
      edgesDS.get().map((edge) => ({
        id: edge.id,
        hidden: !visibleSet.has(edge.from) || !visibleSet.has(edge.to),
      })),
    )
  }, [visibleSet])

  /** Fit the view whenever the graph size changes. */
  useEffect(() => {
    if (nodes.length === 0) return
    // Small delay: the dataset swap above has to land before fit can measure.
    const timer = window.setTimeout(() => {
      networkRef.current?.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } })
    }, 60)
    return () => window.clearTimeout(timer)
  }, [nodes.length, edges.length])

  const resetView = useCallback(() => {
    networkRef.current?.fit({ animation: { duration: 320, easingFunction: 'easeInOutQuad' } })
  }, [])

  const zoomBy = useCallback((factor: number) => {
    const network = networkRef.current
    if (!network) return
    const scale = network.getScale()
    const next = Math.max(0.08, Math.min(4, scale * factor))
    network.moveTo({ scale: next, animation: { duration: 150, easingFunction: 'easeInOutQuad' } })
  }, [])

  const hasContent = nodes.length > 0 && visibleSet.size > 0

  return (
    <div className={`graph-canvas-wrap${className ? ` ${className}` : ''}`}>
      <div className="graph-canvas" ref={hostRef} />
      {!hasContent ? <div className="graph-canvas-empty">{emptyHint}</div> : null}
      <div className="graph-canvas-tools">
        <button type="button" className="graph-tool-btn" onClick={resetView} title="适应窗口" aria-label="适应窗口">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
          </svg>
        </button>
        <button type="button" className="graph-tool-btn" onClick={() => zoomBy(1.25)} title="放大" aria-label="放大">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <button type="button" className="graph-tool-btn" onClick={() => zoomBy(0.8)} title="缩小" aria-label="缩小">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M5 12h14" />
          </svg>
        </button>
      </div>
      <div className="graph-canvas-hint">拖动节点调整位置 · 滚轮缩放 · 点节点看关系 · 双击打开</div>
    </div>
  )
}
