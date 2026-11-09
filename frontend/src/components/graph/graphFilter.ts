/**
 * Pure visibility projection for the graph canvas.
 *
 * The canvas keeps every node and edge in its DataSet so the force layout is
 * computed once; filters toggle `hidden` instead of removing items. Re-adding a
 * filtered node would otherwise drop it at a new random position and shove the
 * whole layout around on every chip click.
 */

import type { GraphEdge, GraphNode, GraphPayload } from '../../types'
import { relationFamily, type RelationFamily } from './graphMeta'

export type VisibleNode = GraphNode & { hidden: boolean }
export type VisibleEdge = GraphEdge & { hidden: boolean }

export interface GraphFilters {
  /** Enabled relation families; `meaning` also controls rationale nodes. */
  families: ReadonlySet<RelationFamily>
  hiddenCommunities: ReadonlySet<number>
}

/**
 * Everything except `meaning`: rationale nodes are commentary, and showing 270
 * of them by default turns the call graph into a wall of prose dots.
 */
export const DEFAULT_FAMILIES: readonly RelationFamily[] = ['behaviour', 'dependency', 'structure', 'other']

export function defaultFilters(): GraphFilters {
  return { families: new Set(DEFAULT_FAMILIES), hiddenCommunities: new Set() }
}

export function isDefaultFilters(filters: GraphFilters): boolean {
  return filters.hiddenCommunities.size === 0
    && filters.families.size === DEFAULT_FAMILIES.length
    && DEFAULT_FAMILIES.every(family => filters.families.has(family))
}

/** Annotation nodes inherit visibility from the relation family they belong to. */
function nodeHidden(node: GraphNode, filters: GraphFilters): boolean {
  if (filters.hiddenCommunities.has(node.community)) return true
  if (node.kind === 'rationale') return !filters.families.has('meaning')
  if (node.kind === 'external') return !filters.families.has('dependency')
  return false
}

export function filterGraph(payload: GraphPayload, filters: GraphFilters): {
  nodes: VisibleNode[]
  edges: VisibleEdge[]
} {
  const hiddenById = new Map<string, boolean>()
  const nodes = payload.nodes.map(node => {
    const hidden = nodeHidden(node, filters)
    hiddenById.set(node.id, hidden)
    return { ...node, hidden }
  })
  const edges = payload.edges.map(edge => ({
    ...edge,
    hidden: Boolean(hiddenById.get(edge.source) || hiddenById.get(edge.target)
      || !filters.families.has(relationFamily(edge.relation))),
  }))
  return { nodes, edges }
}
