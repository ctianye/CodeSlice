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

import { relationFamily } from './graphMeta'

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

/** Kept for callers that still import the old name (tests, rail swatches). */
export const GRAPH_PALETTE = COMMUNITY_PALETTE

/**
 * Edge alpha / weight by proof level, from the reference data:
 * solid 2px @0.7 for extracted facts, hairline dashed @0.35 for deductions.
 */
export const EDGE_STYLE = {
  extracted: { width: 2, opacity: 0.7 },
  inferred: { width: 1, opacity: 0.35 },
  /** Synthetic membership links stay fainter than any real edge. */
  member: { width: 0.5, opacity: 0.18 },
} as const

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

/**
 * Ink for one edge: the **source** node's community colour, exactly as the
 * reference binds edges (no explicit edge colour → vis inherits from-node).
 *
 * The relation family is kept in the signature so call sites stay honest about
 * the data they pass, and so a future per-family override has one choke point.
 */
export function edgeColor(
  theme: GraphTheme,
  _relation: string,
  sourceCommunity?: number,
): string {
  if (sourceCommunity === undefined || Number.isNaN(sourceCommunity)) {
    return CANVAS_THEMES[theme].fallbackEdge
  }
  return communityColor(theme, sourceCommunity)
}

/** Relation family, kept for the rail's relation rows. */
export function familyColor(theme: GraphTheme, family: string): string {
  return edgeColor(theme, family)
}

export { relationFamily as _relationFamilyRef }
