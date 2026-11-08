import type { editor as MonacoEditor } from 'monaco-editor'

/** Above this many lines a range is outlined at its edges only. */
const MAX_FRAMED_LINES = 400

export interface RangeSpan {
  start: number
  end: number
}

/**
 * A persistent outline around a conversation's line range.
 *
 * Every line in the range gets the same side border and tint so the frame reads
 * as one continuous box, while only the first and last line carry the top and
 * bottom edges. Drawing one decoration per line (instead of one block) is what
 * lets Monaco clip the box correctly when part of the range is scrolled away.
 */
export function frameDecorations(
  model: MonacoEditor.ITextModel,
  spans: RangeSpan[],
  frameClass = 'range-frame',
): MonacoEditor.IModelDeltaDecoration[] {
  const maxLine = model.getLineCount()
  const decorations: MonacoEditor.IModelDeltaDecoration[] = []
  // Ranges can overlap (two conversations over the same lines); keep the frame
  // edges of the union so they do not draw over each other twice.
  const edges = new Map<number, { top: boolean; bottom: boolean }>()
  const covered = new Set<number>()

  for (const span of spans) {
    const from = Math.max(1, Math.min(span.start, maxLine))
    const to = Math.max(from, Math.min(span.end, maxLine))
    if (to - from > MAX_FRAMED_LINES) {
      covered.add(from)
      covered.add(to)
      remember(edges, from, true, false)
      remember(edges, to, false, true)
      continue
    }
    for (let line = from; line <= to; line += 1) {
      covered.add(line)
      remember(edges, line, line === from, line === to)
    }
  }

  const lines = [...covered].sort((a, b) => a - b)
  for (const line of lines) {
    const edge = edges.get(line)
    const classes = [frameClass]
    if (edge?.top) classes.push('range-frame-top')
    if (edge?.bottom) classes.push('range-frame-bottom')
    decorations.push({
      range: { startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 1 },
      options: { isWholeLine: true, className: classes.join(' ') },
    })
  }
  return decorations
}

function remember(
  edges: Map<number, { top: boolean; bottom: boolean }>,
  line: number,
  top: boolean,
  bottom: boolean,
): void {
  const current = edges.get(line) ?? { top: false, bottom: false }
  edges.set(line, { top: current.top || top, bottom: current.bottom || bottom })
}

/** The lighter tint used while a code card is hovered. */
export function hoverDecorations(
  model: MonacoEditor.ITextModel,
  span: RangeSpan | null,
): MonacoEditor.IModelDeltaDecoration[] {
  if (!span) return []
  const maxLine = model.getLineCount()
  const from = Math.max(1, Math.min(span.start, maxLine))
  const to = Math.max(from, Math.min(span.end, maxLine))
  return [
    {
      range: { startLineNumber: from, startColumn: 1, endLineNumber: to, endColumn: model.getLineMaxColumn(to) },
      options: { isWholeLine: true, className: 'conversation-card-highlight' },
    },
  ]
}
