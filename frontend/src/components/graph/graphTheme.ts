/**
 * Canvas vocabulary for the knowledge graph.
 *
 * A `<canvas>` cannot read CSS custom properties, so every colour the drawing
 * needs is listed here once per theme. The inspector rail imports the same
 * constants for its swatches, which is what stops a legend dot from drifting
 * away from the node it describes.
 *
 * The palette and the edge-binding rule are ported from the reference report
 * (graphify-out/graph.html): a Tableau-10 classification palette shared by both
 * themes, and edges that inherit their **source** node's community colour, so
 * each cluster reads as a coloured galaxy instead of five grey line families.
 *
 * The app theme is owned by AppState (`data-theme` on <html>) and passed down
 * as a prop instead of read with matchMedia, so an explicit light/dark choice
 * always wins over the OS preference.
 */

export type GraphTheme = 'dark' | 'light'

/**
 * Tableau 10, in the reference's order. Hue identity is the community's
 * identity: it must not drift between the canvas, the rail swatches and the
 * neighbour links, so the light theme reuses the same fills and only the
 * label ink and selection ring change.
 */
export const COMMUNITY_PALETTE = [
  '#4E79A7', // blue
  '#F28E2B', // orange
  '#E15759', // red
  '#76B7B2', // teal
  '#59A14F', // green
  '#EDC948', // yellow
  '#B07AA1', // purple
  '#FF9DA7', // pink
  '#9C755F', // brown
  '#BAB0AC', // grey
] as const

/**
 * Edge weight and alpha are chosen by relation family: a call is the evidence
 * a reader came for, while `contains`/`method`/`rationale_for` are scaffolding.
 * Drawing both at the same weight is what turned the old canvas into rope.
 */
export interface EdgeInk {
  width: number
  opacity: number
}

const CALL_EDGE = { width: 1.5, opacity: 0.58 } as const
const DEPENDENCY_EDGE = { width: 0.8, opacity: 0.3 } as const
const HIERARCHY_EDGE = { width: 0.9, opacity: 0.38 } as const
const STRUCTURE_EDGE = { width: 0.5, opacity: 0.12 } as const
const MEANING_EDGE = { width: 0.5, opacity: 0.12 } as const
const DEFAULT_EDGE = { width: 0.8, opacity: 0.28 } as const

/** Weight and alpha for one edge; inferred edges are halved again by the canvas. */
export function edgeStyle(relation: string): EdgeInk {
  switch (relation) {
    case 'calls':
    case 'indirect_call':
    case 'references':
      return CALL_EDGE
    case 'imports':
    case 'imports_from':
    case 'uses':
      return DEPENDENCY_EDGE
    case 'inherits':
    case 'implements':
      return HIERARCHY_EDGE
    case 'contains':
    case 'method':
    case 'declares':
      return STRUCTURE_EDGE
    case 'rationale_for':
      return MEANING_EDGE
    default:
      return DEFAULT_EDGE
  }
}

/** Structural scaffolding gets neutral ink so call edges keep their colour signal. */
export function isStructuralRelation(relation: string): boolean {
  return relation === 'contains' || relation === 'method' || relation === 'declares' || relation === 'rationale_for'
}

export interface CanvasTheme {
  /** Node fills, indexed by community. */
  nodeColors: readonly string[]
  /** Label ink. */
  label: string
  /** Halo painted behind the label so an edge crossing it cannot eat the text. */
  labelHalo: string
  /** Selection ring: white fill + community border, per the reference highlight. */
  selectionFill: string
  /** Fallback edge ink when no community colour applies (never in practice). */
  fallbackEdge: string
  /** Label size in CSS pixels; scaleLabels keeps it constant while zooming. */
  labelPx: number
}

export const CANVAS_THEMES: Record<GraphTheme, CanvasTheme> = {
  dark: {
    nodeColors: COMMUNITY_PALETTE,
    label: '#ffffff',
    labelHalo: '#0a0f16',
    selectionFill: '#ffffff',
    fallbackEdge: '#8794a6',
    labelPx: 12,
  },
  light: {
    nodeColors: COMMUNITY_PALETTE,
    label: '#222222',
    labelHalo: '#ffffff',
    selectionFill: '#ffffff',
    fallbackEdge: '#8794a6',
    labelPx: 12,
  },
}

export function canvasTheme(theme: GraphTheme): CanvasTheme {
  return CANVAS_THEMES[theme]
}

/** Fill for one community, tolerant of negative or huge ids. */
export function communityColor(theme: GraphTheme, community: number): string {
  const colors = CANVAS_THEMES[theme].nodeColors
  const index = ((community % colors.length) + colors.length) % colors.length
  return colors[index]
}
