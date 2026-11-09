/** Group a node's edges into the buckets the inspector shows. */

import type { GraphEdge, GraphNode } from '../../types'

export interface Connection {
  edge: GraphEdge
  other: GraphNode
  outgoing: boolean
}

export type RelationGroupKey = 'callers' | 'calls' | 'dependencies' | 'structure' | 'meaning' | 'related'

export interface RelationGroup {
  key: RelationGroupKey
  label: string
  items: Connection[]
}

const CALL_RELATIONS = new Set(['calls', 'indirect_call', 'references'])
const DEPENDENCY_RELATIONS = new Set(['imports', 'imports_from', 'uses', 'inherits', 'implements'])
const STRUCTURE_RELATIONS = new Set(['contains', 'method', 'declares'])
const MEANING_RELATIONS = new Set(['rationale_for'])

/**
 * Calls are split by direction because "who calls this" and "what this calls"
 * are different questions; every other family reads better grouped together
 * with a per-row arrow.
 */
export function groupConnections(
  selectedId: string,
  edges: GraphEdge[],
  byId: ReadonlyMap<string, GraphNode>,
  scope: 'code' | 'notes',
): RelationGroup[] {
  const groups: Record<RelationGroupKey, Connection[]> = {
    callers: [], calls: [], dependencies: [], structure: [], meaning: [], related: [],
  }
  for (const edge of edges) {
    if (edge.source !== selectedId && edge.target !== selectedId) continue
    const outgoing = edge.source === selectedId
    const other = byId.get(outgoing ? edge.target : edge.source)
    if (!other || other.id === selectedId) continue
    const connection = { edge, other, outgoing }
    if (scope === 'notes') {
      groups.related.push(connection)
    } else if (CALL_RELATIONS.has(edge.relation)) {
      ;(outgoing ? groups.calls : groups.callers).push(connection)
    } else if (DEPENDENCY_RELATIONS.has(edge.relation)) {
      groups.dependencies.push(connection)
    } else if (STRUCTURE_RELATIONS.has(edge.relation)) {
      groups.structure.push(connection)
    } else if (MEANING_RELATIONS.has(edge.relation)) {
      groups.meaning.push(connection)
    } else {
      groups.dependencies.push(connection)
    }
  }
  const byLabel = (a: Connection, b: Connection) => a.other.label.localeCompare(b.other.label, 'zh-CN')
  const byRelation = (a: Connection, b: Connection) =>
    a.edge.relation.localeCompare(b.edge.relation) || byLabel(a, b)
  for (const key of ['dependencies', 'structure', 'meaning'] as const) groups[key].sort(byRelation)
  groups.callers.sort(byLabel)
  groups.calls.sort(byLabel)
  groups.related.sort(byRelation)

  const ordered: RelationGroup[] = [
    { key: 'callers', label: '调用者', items: groups.callers },
    { key: 'calls', label: '调用', items: groups.calls },
    { key: 'dependencies', label: '依赖', items: groups.dependencies },
    { key: 'structure', label: '结构', items: groups.structure },
    { key: 'meaning', label: '说明', items: groups.meaning },
    { key: 'related', label: '关联', items: groups.related },
  ]
  return ordered.filter(group => group.items.length > 0)
}
