import type { editor as MonacoEditor } from 'monaco-editor'

/** A drag that ends within this many pixels of its start is a click, not a select. */
const DRAG_THRESHOLD_PX = 3

export interface GutterSelectionDeps {
  editor: MonacoEditor.IStandaloneCodeEditor
  /** Applied when a plain gutter click selects a single line. */
  onSelectLine: (line: number) => void
  /** Applied when a gutter drag or shift-click spans several lines. */
  onSelectRange: (from: number, to: number) => void
}

/**
 * Make the line-number gutter support click, shift-click and drag selection.
 *
 * Monaco can select a line when its number is clicked (`selectOnLineNumbers`),
 * but that path only ever selects one line and it fights the editor's own drag
 * handling. This reads the gutter's own mouse events instead and drives
 * `setSelection` directly, so a press-move-release over the numbers sweeps a
 * range the same way dragging over the text does.
 *
 * The line under the cursor comes from `getTargetAtClientPoint`, not from
 * dividing pixels by a line height: padding, wrapping and scroll are already
 * accounted for there.
 *
 * Returns a disposer; the caller must run it before the editor is disposed.
 */
export function attachGutterSelection({
  editor,
  onSelectLine,
  onSelectRange,
}: GutterSelectionDeps): () => void {
  const node = editor.getDomNode()
  const gutter = node?.querySelector<HTMLElement>('.margin-view-overlays') ?? null
  if (!node || !gutter) return () => {}

  let pressed: { line: number; x: number; y: number } | null = null
  let dragging = false

  const lineAt = (event: MouseEvent): number => {
    const target = editor.getTargetAtClientPoint(event.clientX, event.clientY)
    const line = target?.position?.lineNumber ?? pressed?.line ?? 1
    const maxLine = editor.getModel()?.getLineCount() ?? 1
    return Math.max(1, Math.min(line, maxLine))
  }

  const insideGutter = (event: MouseEvent): boolean => {
    const rect = gutter.getBoundingClientRect()
    return event.clientX >= rect.left && event.clientX <= rect.right + 2 && event.clientY >= rect.top
  }

  const select = (from: number, to: number) => {
    const model = editor.getModel()
    if (!model) return
    const first = Math.min(from, to)
    const last = Math.max(from, to)
    if (first === last) {
      onSelectLine(first)
      return
    }
    editor.setSelection({
      startLineNumber: first,
      startColumn: 1,
      endLineNumber: last,
      endColumn: model.getLineMaxColumn(last),
    })
    onSelectRange(first, last)
    editor.focus()
  }

  const onMouseDown = (event: MouseEvent) => {
    if (event.button !== 0 || !insideGutter(event)) return
    const line = lineAt(event)
    if (event.shiftKey) {
      // Shift-click extends the existing selection instead of starting over.
      const anchor = editor.getSelection()?.positionLineNumber ?? line
      select(anchor, line)
      event.preventDefault()
      pressed = null
      return
    }
    pressed = { line, x: event.clientX, y: event.clientY }
    dragging = false
  }

  const onMouseMove = (event: MouseEvent) => {
    if (!pressed) return
    if (!dragging) {
      const moved =
        Math.abs(event.clientX - pressed.x) > DRAG_THRESHOLD_PX ||
        Math.abs(event.clientY - pressed.y) > DRAG_THRESHOLD_PX
      if (!moved) return
      dragging = true
    }
    // Track the drag live so the selection bar updates while sweeping.
    const model = editor.getModel()
    const line = lineAt(event)
    if (!model) return
    editor.setSelection({
      startLineNumber: Math.min(pressed.line, line),
      startColumn: 1,
      endLineNumber: Math.max(pressed.line, line),
      endColumn: model.getLineMaxColumn(Math.max(pressed.line, line)),
    })
    event.preventDefault()
  }

  const onMouseUp = (event: MouseEvent) => {
    if (!pressed) return
    const start = pressed
    pressed = null
    if (dragging) {
      dragging = false
      select(start.line, lineAt(event))
      return
    }
    select(start.line, start.line)
  }

  gutter.addEventListener('mousedown', onMouseDown)
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)

  return () => {
    gutter.removeEventListener('mousedown', onMouseDown)
    window.removeEventListener('mousemove', onMouseMove)
    window.removeEventListener('mouseup', onMouseUp)
  }
}
