/**
 * Display vocabulary for the knowledge graph.
 *
 * One module so the canvas, the dock and the legend cannot drift: a relation that
 * reads "调用" in the detail panel must not read "calls" in a tooltip, and an
 * inferred edge must look inferred everywhere.
 */

import type { GraphConfidence, GraphNodeKind, GraphRelation } from '../../types'

/** Chinese label for each relation kind. */
export const RELATION_LABEL: Record<string, string> = {
  same_folder: '同一笔记目录',
  same_project: '同项目未分类',
  shared_reference: '共同引用文件',
  community_member: '社区成员（布局索引）',
  contains: '包含',
  imports: '导入模块',
  imports_from: '导入符号',
  calls: '调用',
  indirect_call: '回调引用',
  references: '引用',
  inherits: '继承',
  implements: '实现',
  method: '方法',
  declares: '声明',
  rationale_for: '说明',
  uses: '依赖',
}

/** Short label for a node kind. */
export const KIND_LABEL: Record<string, string> = {
  community: '社区',
  note: '笔记',
  file: '文件',
  class: '类',
  function: '函数',
  module: '模块',
  rationale: '说明',
  external: '外部',
}

export const CONFIDENCE_LABEL: Record<string, string> = {
  EXTRACTED: '源码直读',
  INFERRED: '推断',
  AMBIGUOUS: '存疑',
}

/**
 * How much each relation proves, used to decide whether an edge is drawn solid.
 *
 * `EXTRACTED` already encodes this per-edge; this table is the fallback for the
 * visual style when a relation is known to be structural even if the confidence
 * field is missing.
 */
export const STRUCTURAL_RELATIONS: ReadonlySet<string> = new Set<GraphRelation>([
  'contains',
  'method',
  'imports',
  'imports_from',
  'inherits',
  'implements',
  'declares',
  'rationale_for',
  'uses',
])

export function relationLabel(relation: string): string {
  return RELATION_LABEL[relation] ?? relation
}

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export function confidenceLabel(confidence: string): string {
  return CONFIDENCE_LABEL[confidence] ?? confidence
}

/** True when the edge should be drawn dotted (a deduction, not a reading). */
export function isInferred(relation: string, confidence: GraphConfidence | string): boolean {
  if (confidence === 'INFERRED' || confidence === 'AMBIGUOUS') return true
  // A relation that is structural by nature is solid even without a confidence tag.
  return !STRUCTURAL_RELATIONS.has(relation) && confidence !== 'EXTRACTED'
}

/**
 * Relation family, used to pick an edge colour.
 *
 * Grouped rather than one colour per relation: with a dozen kinds a per-kind
 * palette becomes unreadable, but five families (structure / dependency /
 * behaviour / hierarchy / meaning) stay distinguishable. The family is resolved
 * here, the actual shade per theme in graphTheme.ts.
 */
export type RelationFamily = 'structure' | 'dependency' | 'behaviour' | 'hierarchy' | 'meaning' | 'other'

export function relationFamily(relation: string): RelationFamily {
  switch (relation) {
    case 'contains':
    case 'method':
    case 'declares':
      return 'structure'
    case 'imports':
    case 'imports_from':
    case 'uses':
      return 'dependency'
    case 'calls':
    case 'indirect_call':
    case 'references':
      return 'behaviour'
    case 'inherits':
    case 'implements':
      return 'hierarchy'
    case 'rationale_for':
      return 'meaning'
    default:
      return 'other'
  }
}

/** Colour family label, for the legend rows in the inspector rail. */
export const FAMILY_LABEL: Record<RelationFamily, string> = {
  structure: '结构',
  dependency: '依赖',
  behaviour: '行为',
  hierarchy: '继承',
  meaning: '说明',
  other: '其他',
}

/** Node kinds that carry no code and should not be traversed into. */
export function isLeafKind(kind: GraphNodeKind | string): boolean {
  return kind === 'external' || kind === 'rationale'
}
