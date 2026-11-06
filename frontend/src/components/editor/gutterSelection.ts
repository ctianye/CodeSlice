import type { editor as MonacoEditor } from 'monaco-editor'

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
 * but that path can only ever pick one line and it fights the picker in
 * `CodePane`. This reads the gutter's own mouse events instead: a click toggles
 * one line in the picked set, and a press-move-release that reaches another line
 * sweeps a contiguous range the same way dragging over the text does.
 *
 * "Another line" is measured by line number, not by pixels. A few pixels of
 * travel during a click is normal, and treating that as a drag would replace the
 * whole picked set with a one-line range — which made accumulating lines look
 * like it silently stopped working.
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

  let pressed: { line: number } | null = null
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
    // Take the press away from Monaco. Its own gutter handling moves the cursor
    // (and, with `selectOnLineNumbers` on, sets a whole-line selection), and a
    // selection change would wipe the picker's accumulated lines before the
    // click is even registered.
    event.preventDefault()
    event.stopPropagation()
    if (event.shiftKey) {
      // Shift-click extends the existing range instead of starting over.
      const anchor = editor.getSelection()?.positionLineNumber ?? line
      select(anchor, line)
      pressed = null
      return
    }
    pressed = { line }
    dragging = false
  }

  const onMouseMove = (event: MouseEvent) => {
    if (!pressed) return
    // A drag means the pointer reached a *different* line, not that the mouse
    // twitched. Measuring pixels would turn a shaky click on one line into a
    // one-line range selection, which silently replaces the picked set — the
    // reason clicking two lines quickly could look like it never accumulated.
    const current = lineAt(event)
    if (!dragging) {
      if (current === pressed.line) return
      dragging = true
    }
    // Track the drag live so the selection bar updates while sweeping, but only
    // once per frame: `setSelection` re-renders the pane, and mouse moves arrive
    // far faster than the display can show them.
    pending = { from: pressed.line, to: current }
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
      // Same line after all (the sweep came back): treat it as a click.
      if (lineAt(event) === start.line) {
        select(start.line, start.line, true)
        return
      }
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
