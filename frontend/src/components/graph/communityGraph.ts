import type { GraphPayload } from '../../types'
import type { CanvasEdge, CanvasNode } from './GraphCanvas'

export const GRAPH_PALETTE = ['#44c2b1', '#e2a64f', '#e7798e', '#75a9ee', '#9c86dc', '#83bd73', '#dc83c2', '#58b8d6']
export const communityId = (id: number): string => `community:${id}`

/** Presentation only: synthetic membership links never enter AI evidence. */
export function communityGraph(
  payload: GraphPayload, expanded: Set<number>, hidden: Set<number>, hiddenKinds: Set<string>,
): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const nodes: CanvasNode[] = []
  const edges: CanvasEdge[] = []
  const mapped = new Map<string, string>()
  const groups = new Map<number, typeof payload.nodes>()
  for (const node of payload.nodes) {
    if (hidden.has(node.community) || hiddenKinds.has(node.kind)) continue
    const members = groups.get(node.community) ?? []
    members.push(node)
    groups.set(node.community, members)
  }
  // Fixed anchors keep unrelated clusters still when a single cluster opens.
  const spacing = Math.max(280, ...payload.communities.map(c => 65 * Math.sqrt(c.size) + 130))
  payload.communities.forEach((community, index) => {
    const members = groups.get(community.id) ?? []
    if (!members.length) return
    const id = communityId(community.id)
    const x = spacing * Math.sqrt(index) * Math.cos(index * 2.4)
    const y = spacing * Math.sqrt(index) * Math.sin(index * 2.4)
    nodes.push({ id, label: `${community.label} · ${members.length}`, kind: 'community',
      community: community.id, degree: members.length, path: '', start_line: 0, x, y,
      tooltip: expanded.has(community.id) ? '点击收起社区' : '点击展开社区',
    })
    members.forEach((node, memberIndex) => {
      mapped.set(node.id, expanded.has(community.id) ? node.id : id)
      if (!expanded.has(community.id)) return
      const radius = 65 + 26 * Math.sqrt(memberIndex)
      nodes.push({ ...node, communityLabel: node.community_label,
        x: x + radius * Math.cos(memberIndex * 2.4), y: y + radius * Math.sin(memberIndex * 2.4),
        tooltip: node.content_md?.slice(0, 200),
      })
      edges.push({ source: id, target: node.id, relation: 'community_member', confidence: 'EXTRACTED' })
    })
  })
  const seen = new Set<string>()
  for (const edge of payload.edges) {
    const source = mapped.get(edge.source)
    const target = mapped.get(edge.target)
    if (!source || !target || source === target) continue
    const key = JSON.stringify([source, target, edge.relation, edge.confidence])
    if (seen.has(key)) continue
    seen.add(key)
    edges.push({ ...edge, source, target })
  }
  return { nodes, edges }
}
