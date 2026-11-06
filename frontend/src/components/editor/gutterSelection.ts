import type { editor as MonacoEditor } from 'monaco-editor'

/** A drag that ends within this many pixels of its start is a click, not a select. */
const DRAG_THRESHOLD_PX = 3

export interface GutterSelectionDeps {
  editor: MonacoEditor.IStandaloneCodeEditor
  /**
   * Applied when a single line number is clicked.
   *
   * `additive` is true when the hit should add to (or remove from) the set of
   * already-picked lines. Clicking line numbers is a picker: clicks accumulate,
   * and clicking the same number again removes it.
   */
  onSelectLine: (line: number, additive: boolean) => void
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
  /** Pending rAF handle so a drag writes at most one selection per frame. */
  let frame: number | null = null
  let pending: { from: number; to: number } | null = null

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

  const applyRange = (from: number, to: number) => {
    const model = editor.getModel()
    if (!model) return
    const first = Math.min(from, to)
    const last = Math.max(from, to)
    editor.setSelection({
      startLineNumber: first,
      startColumn: 1,
      endLineNumber: last,
      endColumn: model.getLineMaxColumn(last),
    })
  }

  const select = (from: number, to: number, additive = false) => {
    const first = Math.min(from, to)
    const last = Math.max(from, to)
    if (first === last) {
      onSelectLine(first, additive)
      return
    }
    applyRange(first, last)
    onSelectRange(first, last)
    editor.focus()
  }

  const onMouseDown = (event: MouseEvent) => {
    if (event.button !== 0 || !insideGutter(event)) return
    const line = lineAt(event)
    if (event.shiftKey) {
      // Shift-click extends the existing range instead of starting over.
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
    // Track the drag live so the selection bar updates while sweeping, but only
    // once per frame: `setSelection` re-renders the pane, and mouse moves arrive
    // far faster than the display can show them.
    pending = { from: pressed.line, to: lineAt(event) }
    if (frame === null) {
      frame = window.requestAnimationFrame(() => {
        frame = null
        if (pending) applyRange(pending.from, pending.to)
      })
    }
    event.preventDefault()
  }

  const onMouseUp = (event: MouseEvent) => {
    if (!pressed) return
    const start = pressed
    pressed = null
    if (frame !== null) {
      window.cancelAnimationFrame(frame)
      frame = null
      pending = null
    }
    if (dragging) {
      dragging = false
      select(start.line, lineAt(event))
      return
    }
    // Line numbers behave like a picker: every click adds that line to the set,
    // and clicking a line that is already picked removes it. Requiring ctrl/cmd
    // made accumulating two distant lines an obscure gesture; a drag over the
    // text (or the "clear" button) still resets the set.
    select(start.line, start.line, true)
  }

  gutter.addEventListener('mousedown', onMouseDown)
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)

  return () => {
    if (frame !== null) window.cancelAnimationFrame(frame)
    gutter.removeEventListener('mousedown', onMouseDown)
    window.removeEventListener('mousemove', onMouseMove)
    window.removeEventListener('mouseup', onMouseUp)
  }
}
