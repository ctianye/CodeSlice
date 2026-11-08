import type { editor as MonacoEditor } from 'monaco-editor'
import { editor as monacoEditor, Selection } from 'monaco-editor'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api'
import type { CodeAnnotation, Conversation, FileContent, GitStatus } from '../types'
import { useAsync } from '../hooks/useAsync'
import { baseName } from '../lib/citations'
import { formatBytes, languageFromPath } from '../lib/format'
import { useApp } from '../state/AppState'
import { CodeViewer } from './CodeViewer'
import { ConversationCards } from './ConversationCards'
import { ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'
import { MarkdownDoc } from './MarkdownDoc'
import type { FrameZoneItem } from './editor/frameZones'
import { createFrameZones } from './editor/frameZones'
import { frameDecorations, hoverDecorations } from './editor/decorations'
import { attachGutterSelection } from './editor/gutterSelection'

export interface SnippetSelection {
  path: string
  start: number
  end: number
  code: string
}

interface CodePaneProps {
  onAskSelection: (selection: SnippetSelection) => void
  onOpenFile: () => void
}

/** Center pane: read-only Monaco plus the "就这段提问" selection action. */
export function CodePane({ onAskSelection, onOpenFile }: CodePaneProps): ReactNode {
  const {
    project,
    openFile,
    citationHighlight,
    clearCitation,
    activeConversationId,
    focusConversation,
    refreshKey,
    bumpRefresh,
    openNoteView,
  } = useApp()
  const projectId = project?.id ?? null
  const path = openFile?.path ?? null

  const file = useAsync(
    (signal) =>
      projectId !== null && path !== null ? api.getFile(projectId, path, signal) : Promise.resolve(null),
    [projectId, path],
    { enabled: projectId !== null && path !== null },
  )

  const git = useAsync(
    (signal) =>
      projectId !== null ? getGitStatusSafe(projectId, signal) : Promise.resolve<GitStatus | null>(null),
    [projectId],
    { enabled: projectId !== null },
  )

  // Conversations anchored to the open file — the data behind the code cards.
  const cards = useAsync(
    (signal) =>
      projectId !== null && path !== null
        ? api.listConversationsByFile(projectId, path, signal)
        : Promise.resolve<Conversation[]>([]),
    [projectId, path, refreshKey],
    { enabled: projectId !== null && path !== null },
  )
  const annotations = useAsync(
    (signal) => projectId !== null && path !== null ? api.listAnnotations(projectId, path, signal) : Promise.resolve<CodeAnnotation[]>([]),
    [projectId, path, refreshKey],
    { enabled: projectId !== null && path !== null },
  )

  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null)
  const citationRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const hoverRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const frameRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const annotationRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const pickRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const zonesRef = useRef<{ update: (items: FrameZoneItem[]) => void; dispose: () => void } | null>(null)
  const detachRef = useRef<(() => void) | null>(null)
  /** Pending rAF handle for selection updates; null when nothing is scheduled. */
  const selectionFrame = useRef<number | null>(null)
  /** Keep React layout updates out of the native mouse-drag selection loop. */
  const mouseSelectingRef = useRef(false)
  /** Lines toggled by clicking the gutter — survives until a text drag or a reset. */
  const pickedRef = useRef<number[]>([])
  // Flipped once Monaco is mounted; the decoration effects depend on it because
  // `editorRef` alone is not a dependency React can track.
  const [ready, setReady] = useState(false)
  const [selection, setSelection] = useState<{ start: number; end: number; code: string } | null>(null)
  /** Gutter-picked lines when the user is assembling a multi-line question. */
  const [picked, setPicked] = useState<{ lines: number[]; code: string } | null>(null)
  const [hoverRange, setHoverRange] = useState<{ start: number; end: number } | null>(null)
  const [noteBusyId, setNoteBusyId] = useState<string | null>(null)
  const [cardError, setCardError] = useState<string | null>(null)
  const [annotationDraft, setAnnotationDraft] = useState<{ projectId: number; path: string; start: number; end: number; code: string } | null>(null)
  const [annotationText, setAnnotationText] = useState('')
  const [annotationSaving, setAnnotationSaving] = useState(false)
  const [expandedAnnotations, setExpandedAnnotations] = useState<Record<number, boolean>>({})
  const annotationTargetRef = useRef('')
  annotationTargetRef.current = `${projectId}:${path}`
  /**
   * Markdown documents open rendered, not as source. `docDraft` is null while
   * the buffer still matches the file; once the user types it holds the unsaved
   * text, which is what makes `docDirty` cheap to compute.
   */
  const [docEditing, setDocEditing] = useState(false)
  const [docDraft, setDocDraft] = useState<string | null>(null)
  /**
   * Unsaved buffers keyed by path.
   *
   * Tabs do not keep a component per file, so switching away would otherwise
   * throw the edit away without a word. Keeping the draft here lets the tab come
   * back with its text intact; the map entry is dropped as soon as it is saved.
   */
  const docDraftsRef = useRef<Map<string, string>>(new Map())
  const [docSaving, setDocSaving] = useState(false)
  const [docNotice, setDocNotice] = useState<string | null>(null)
  const [docError, setDocError] = useState<string | null>(null)

  const updateSelection = useCallback((instance: MonacoEditor.IStandaloneCodeEditor) => {
    const model = instance.getModel()
    const range = instance.getSelection()
    if (!model || !range || range.isEmpty()) {
      setSelection(null)
      return
    }
    // A selection ending at column 1 of a line does not include that line.
    const endLine =
      range.endColumn === 1 && range.endLineNumber > range.startLineNumber
        ? range.endLineNumber - 1
        : range.endLineNumber
    const start = range.startLineNumber
    // Dragging over text takes over from gutter picking.
    if (pickedRef.current.length > 0) {
      pickedRef.current = []
      pickRef.current?.clear()
      setPicked(null)
    }
    setSelection((prev) => {
      // Dragging fires this on every mouse move; skip the re-render (and the
      // model.getValueInRange call) while the line span is unchanged.
      if (prev && prev.start === start && prev.end === endLine) return prev
      const code = model
        .getValueInRange(new Selection(start, 1, endLine, model.getLineMaxColumn(endLine)))
        .replace(/\n+$/, '')
      return { start, end: endLine, code }
    })
  }, [])

  /**
   * Add or remove one line from the "ask about these lines" set.
   *
   * Gutter clicks are additive: line numbers act as a picker, so two distant
   * lines can be gathered without holding a modifier, and clicking a number
   * that is already picked removes it. Passing `additive: false` would wipe the
   * set and fall back to a plain single-line answer.
   */
  const applyLinePick = useCallback(
    (instance: MonacoEditor.IStandaloneCodeEditor, line: number, additive: boolean) => {
      const model = instance.getModel()
      if (!model) return
      const maxLine = model.getLineCount()
      const target = Math.max(1, Math.min(line, maxLine))
      const existing = additive ? pickedRef.current : []
      const next = existing.includes(target)
        ? existing.filter((item) => item !== target)
        : [...existing, target].sort((a, b) => a - b)

      pickedRef.current = next
      if (next.length === 0) {
        pickRef.current?.clear()
        setPicked(null)
        instance.setSelection({ startLineNumber: target, startColumn: 1, endLineNumber: target, endColumn: 1 })
        instance.focus()
        return
      }

      pickRef.current?.set(
        next.map((item) => ({
          range: { startLineNumber: item, startColumn: 1, endLineNumber: item, endColumn: model.getLineMaxColumn(item) },
          options: { isWholeLine: true, className: 'pick-line' },
        })),
      )
      const code = next.map((item) => model.getLineContent(item)).join('\n')
      setSelection(null)
      setPicked({ lines: next, code })
      // Park the caret on the picked line without creating a text selection, so
      // the two selection sources never disagree.
      instance.setSelection({ startLineNumber: target, startColumn: 1, endLineNumber: target, endColumn: 1 })
      instance.focus()
    },
    [],
  )

  /**
   * Coalesce selection updates to one per animation frame.
   *
   * Without this every mouse move during a drag wrote React state and re-rendered
   * the pane — cards, view zones and all — which is what made selecting feel
   * sticky once the file had conversations.
   */
  const scheduleSelection = useCallback(
    (instance: MonacoEditor.IStandaloneCodeEditor) => {
      if (selectionFrame.current !== null) return
      selectionFrame.current = window.requestAnimationFrame(() => {
        selectionFrame.current = null
        if (mouseSelectingRef.current || editorRef.current !== instance) return
        updateSelection(instance)
      })
    },
    [updateSelection],
  )

  const onMount = useCallback(
    (instance: MonacoEditor.IStandaloneCodeEditor) => {
      editorRef.current = instance
      citationRef.current = instance.createDecorationsCollection([])
      hoverRef.current = instance.createDecorationsCollection([])
      frameRef.current = instance.createDecorationsCollection([])
      annotationRef.current = instance.createDecorationsCollection([])
      pickRef.current = instance.createDecorationsCollection([])
      zonesRef.current = createFrameZones(instance)
      setReady(true)

      const selectionListener = instance.onDidChangeCursorSelection(() => {
        if (!mouseSelectingRef.current) scheduleSelection(instance)
      })
      const editorNode = instance.getDomNode()
      const onMouseDownCapture = (event: MouseEvent) => {
        if (event.button !== 0) return
        const target = instance.getTargetAtClientPoint(event.clientX, event.clientY)?.type
        mouseSelectingRef.current =
          target === monacoEditor.MouseTargetType.CONTENT_TEXT ||
          target === monacoEditor.MouseTargetType.CONTENT_EMPTY ||
          target === monacoEditor.MouseTargetType.GUTTER_LINE_NUMBERS ||
          target === monacoEditor.MouseTargetType.GUTTER_GLYPH_MARGIN ||
          target === monacoEditor.MouseTargetType.GUTTER_LINE_DECORATIONS
        if (mouseSelectingRef.current && selectionFrame.current !== null) {
          window.cancelAnimationFrame(selectionFrame.current)
          selectionFrame.current = null
        }
      }
      const onMouseUpCapture = () => {
        if (!mouseSelectingRef.current) return
        mouseSelectingRef.current = false
        scheduleSelection(instance)
      }
      editorNode?.addEventListener('mousedown', onMouseDownCapture, true)
      window.addEventListener('mouseup', onMouseUpCapture, true)
      window.addEventListener('blur', onMouseUpCapture)
      // Line numbers are a picker: each click adds a line to the set, clicking it
      // again removes it, and dragging over the numbers sweeps a range. Monaco's
      // own `selectOnLineNumbers` is off (see CodeViewer) because it would set a
      // whole-line selection on every press and that would clear the set.
      const detachGutter = attachGutterSelection({
        editor: instance,
        onSelectLine: (line, additive) => {
          applyLinePick(instance, line, additive)
        },
        // The live drag already moved the model selection; the listener above
        // turns it into the selection bar, so nothing extra is needed here.
        onSelectRange: () => undefined,
      })
      detachRef.current = () => {
        selectionListener.dispose()
        editorNode?.removeEventListener('mousedown', onMouseDownCapture, true)
        window.removeEventListener('mouseup', onMouseUpCapture, true)
        window.removeEventListener('blur', onMouseUpCapture)
        mouseSelectingRef.current = false
        if (selectionFrame.current !== null) {
          window.cancelAnimationFrame(selectionFrame.current)
          selectionFrame.current = null
        }
        detachGutter()
      }

      // The pane can be laid out before the mount box has its final height (the
      // card strip above it renders a frame later); one explicit layout pass
      // avoids a zero-sized editor in that case.
      requestAnimationFrame(() => {
        if (!instance.getModel()) return
        instance.layout()
      })
    },
    [scheduleSelection],
  )

  // Release Monaco listeners before the instance is disposed, otherwise a
  // listener can fire into a torn-down editor when the pane swaps to NoteReader.
  useEffect(
    () => () => {
      detachRef.current?.()
      detachRef.current = null
      zonesRef.current?.dispose()
      zonesRef.current = null
      editorRef.current = null
      setReady(false)
    },
    [],
  )

  // Paint the citation outline the app says is current.
  //
  // The state lives in `AppState` rather than a ref here: opening a citation from
  // a note unmounts the reader and mounts this pane fresh, so a local ref would be
  // empty on the second click and "click the same chip again to clear it" could
  // never work. `null` means the user asked to drop the highlight.
  useEffect(() => {
    const instance = editorRef.current
    const collection = citationRef.current
    if (!instance || !collection) return
    const model = instance.getModel()
    if (!model) return
    if (!citationHighlight || !openFile || citationHighlight.path !== openFile.path) {
      collection.clear()
      return
    }
    const maxLine = model.getLineCount()
    const from = Math.max(1, Math.min(citationHighlight.start_line, maxLine))
    const to = Math.max(from, Math.min(citationHighlight.end_line, maxLine))
    collection.set([
      {
        range: { startLineNumber: from, startColumn: 1, endLineNumber: to, endColumn: model.getLineMaxColumn(to) },
        options: { isWholeLine: true, className: 'citation-highlight', inlineClassName: 'citation-line-flash' },
      },
    ])
    instance.revealLineInCenter(from)
    // `ready` is in the deps because clicking a citation from a note view mounts
    // this pane from scratch: the target is already set by the time Monaco hands
    // over an editor, so without `ready` the effect ran once against a null ref
    // and never painted the highlight.
  }, [citationHighlight, openFile, file.data, ready])

  // Outline every conversation range so anchored code is visible without a
  // selection. Runs on card data, file changes and editor (re)creation.
  useEffect(() => {
    const instance = editorRef.current
    const collection = frameRef.current
    const model = instance?.getModel()
    if (!instance || !collection || !model) return
    const spans = (cards.data ?? []).flatMap((card) =>
      card.start_line === null
        ? []
        : [{ start: card.start_line, end: card.end_line ?? card.start_line }],
    )
    collection.set(frameDecorations(model, spans))
  }, [cards.data, file.data, ready])

  useEffect(() => {
    const model = editorRef.current?.getModel()
    if (!model || !annotationRef.current) return
    annotationRef.current.set(frameDecorations(model, (annotations.data ?? []).filter((item) => item.project_id === projectId && item.file_path === path).map((item) => ({ start: item.start_line, end: item.end_line })), 'annotation-frame'))
  }, [annotations.data, file.data, ready, projectId, path])

  // Tint the lines a hovered code card covers, without touching the citation
  // highlight collection.
  useEffect(() => {
    const instance = editorRef.current
    const collection = hoverRef.current
    const model = instance?.getModel()
    if (!instance || !collection || !model) return
    collection.set(hoverDecorations(model, hoverRange))
  }, [hoverRange, file.data, ready])

  // Drop stale decorations when switching files.
  useEffect(() => {
    citationRef.current?.clear()
    hoverRef.current?.clear()
    frameRef.current?.clear()
    annotationRef.current?.clear()
    pickRef.current?.clear()
    pickedRef.current = []
    // Zones survive a model swap, so drop the old file's strips before the new
    // cards arrive; otherwise they would sit on lines the new file may not have.
    zonesRef.current?.update([])
    setSelection(null)
    setPicked(null)
    setHoverRange(null)
    setAnnotationDraft(null)
    setAnnotationText('')
    setExpandedAnnotations({})
    // The document buffer belongs to the previous path; leaving it would offer to
    // save one file's text into another.
    setDocEditing(false)
    setDocDraft(null)
    setDocNotice(null)
    setDocError(null)
  }, [path, projectId])

  // Restore an unsaved document buffer when the tab comes back.
  useEffect(() => {
    if (!path || !file.data || file.data.language !== 'markdown') return
    const kept = docDraftsRef.current.get(path)
    if (kept === undefined) return
    setDocDraft(kept)
    setDocEditing(true)
  }, [path, file.data])

  const zoneItems = useMemo<FrameZoneItem[]>(() => {
    const list = cards.data ?? []
    // One title per range: several conversations over the same lines would
    // otherwise stack strips on top of each other.
    const seen = new Set<string>()
    const items: FrameZoneItem[] = []
    for (const card of list) {
      if (card.start_line === null) continue
      const end = card.end_line ?? card.start_line
      const key = `${card.start_line}-${end}`
      if (seen.has(key)) continue
      seen.add(key)
      items.push({
        key,
        start: card.start_line,
        label: card.note_title ?? card.title,
        active: card.id === activeConversationId,
        onClick: () => focusConversation(card.id),
      })
    }
    for (const annotation of annotations.data ?? []) {
      if (annotation.project_id !== projectId || annotation.file_path !== path) continue
      items.push({
        key: `annotation-${annotation.id}`,
        start: annotation.start_line,
        label: `${expandedAnnotations[annotation.id] ? '▾' : '▸'} 批注 · ${annotation.body.slice(0, 42)}`,
        active: false,
        onClick: () => undefined,
        annotation: annotation.body,
        expanded: Boolean(expandedAnnotations[annotation.id]),
        onToggle: () => setExpandedAnnotations((prev) => ({ ...prev, [annotation.id]: !prev[annotation.id] })),
        onDelete: () => {
          void api.deleteAnnotation(annotation.id).then(() => annotations.reload()).catch((error: unknown) => setCardError(error instanceof Error ? error.message : String(error)))
        },
      })
    }
    return items
  }, [cards.data, annotations.data, annotations.reload, activeConversationId, focusConversation, expandedAnnotations, projectId, path])

  // Reserve a title strip above every framed range. The zone belongs to the
  // view, not to the model, so it has to be refreshed whenever the cards change
  // — and once more when Monaco finishes mounting.
  useEffect(() => {
    zonesRef.current?.update(zoneItems)
  }, [zoneItems, file.data, ready])

  const content = file.data
  const dirty = git.data?.dirty ?? false
  /** Markdown opens rendered; the toggle is the only way into the source. */
  const isDoc = content?.language === 'markdown'
  const docText = docDraft ?? content?.content ?? ''
  const docDirty = isDoc && docDraft !== null && docDraft !== content?.content

  const saveAnnotation = async () => {
    if (!annotationDraft || !annotationText.trim() || annotationSaving) return
    const target = annotationDraft
    setAnnotationSaving(true)
    try {
      await api.createAnnotation(target.projectId, { file_path: target.path, start_line: target.start, end_line: target.end, selected_code: target.code, body: annotationText.trim() })
      if (annotationTargetRef.current === `${target.projectId}:${target.path}`) {
        setAnnotationDraft(null)
        setAnnotationText('')
      }
      annotations.reload()
    } catch (error) {
      setCardError(error instanceof Error ? error.message : String(error))
    } finally {
      setAnnotationSaving(false)
    }
  }

  // Hooks below run before the placeholder returns: the pane keeps its hook order
  // when a project is closed and reopened, and React throws on a mismatch.

  /**
   * Write the edited document back.
   *
   * `expected_mtime` is the mtime the buffer was read with, so the backend can
   * refuse the write when the file changed underneath us. That refusal is shown
   * verbatim: it tells the user to reopen rather than silently overwrite someone
   * else's edit.
   */
  const saveDoc = useCallback(async () => {
    if (projectId === null || !path || !content || docDraft === null) return
    if (docDraft === content.content) {
      setDocDraft(null)
      docDraftsRef.current.delete(path)
      setDocNotice('没有改动')
      return
    }
    setDocSaving(true)
    setDocError(null)
    setDocNotice(null)
    try {
      await api.saveFile(projectId, path, docDraft, content.mtime)
      docDraftsRef.current.delete(path)
      setDocDraft(null)
      setDocNotice('已保存')
      file.reload()
      // The file list shows sizes, and the git badge may flip to "modified".
      git.reload()
    } catch (err) {
      setDocError(saveErrorText(err))
    } finally {
      setDocSaving(false)
    }
  }, [projectId, path, content, docDraft, file, git])

  // Ctrl/Cmd+S saves instead of letting the browser open its own dialog.
  useEffect(() => {
    if (!docEditing) return
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void saveDoc()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [docEditing, saveDoc])

  // Drop the "已保存" note on its own so the toolbar does not keep a stale badge.
  useEffect(() => {
    if (!docNotice) return
    const timer = window.setTimeout(() => setDocNotice(null), 1800)
    return () => window.clearTimeout(timer)
  }, [docNotice])

  const updateDocDraft = useCallback(
    (next: string) => {
      if (!path) return
      setDocDraft(next)
      if (file.data && next === file.data.content) {
        // Typing back to the original text clears the dirty state, so drop the
        // parked buffer instead of remembering a no-op edit.
        docDraftsRef.current.delete(path)
      } else {
        docDraftsRef.current.set(path, next)
      }
    },
    [path, file],
  )

  if (!project) {
    return (
      <div className="editor-pane">
        <div className="editor-placeholder">
          <span className="placeholder-icon">
            <Icon name="code" size={20} />
          </span>
          <span className="big">还没有打开项目</span>
          <p>在左侧选择项目，或从欢迎页添加一个本地代码目录。</p>
        </div>
      </div>
    )
  }

  if (!path) {
    return (
      <div className="editor-pane">
        <div className="editor-placeholder">
          <span className="placeholder-icon">
            <Icon name="file-code" size={20} />
          </span>
          <span className="big">选择左侧文件开始阅读</span>
          <p>可以在编辑器中拖选代码行，或者点击行号选中整行。</p>
          <button onClick={onOpenFile} type="button">
            <Icon name="panel-left" size={12} />
            打开文件列表
          </button>
        </div>
      </div>
    )
  }

  const openConversation = (conversation: Conversation) => {
    focusConversation(conversation.id)
  }

  /**
   * Generate a note from a conversation's latest assistant answer.
   *
   * The card only knows the conversation, not the message id, so the detail is
   * fetched first. Failures are surfaced inline instead of silently doing
   * nothing, and the result opens in the note reader.
   */
  const generateNote = async (conversation: Conversation) => {
    setNoteBusyId(conversation.id)
    setCardError(null)
    try {
      const detail = await api.getConversation(conversation.id)
      const answer = [...detail.messages].reverse().find((message) => message.role === 'assistant')
      if (!answer) throw new Error('这条对话还没有可以整理的回答')
      const note = await api.createNoteFromMessage(conversation.id, answer.id)
      bumpRefresh()
      cards.reload()
      openNoteView(note)
    } catch (err) {
      setCardError(err instanceof Error ? err.message : String(err))
    } finally {
      setNoteBusyId(null)
    }
  }

  const openNote = async (conversation: Conversation) => {
    if (!conversation.note_id) return
    setCardError(null)
    try {
      const note = await api.getNote(conversation.note_id)
      openNoteView(note)
    } catch (err) {
      setCardError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="editor-pane">
      <div className="editor-toolbar">
        <span className="file-path" title={path}>
          <Icon name="file-code" size={12} />
          {baseName(path)}
        </span>
        {content ? (
          <>
            <span className="tag accent">{content.language || languageFromPath(path)}</span>
            <span className="tag">{formatBytes(content.size)}</span>
            {content.truncated ? <span className="tag warn">已截断</span> : null}
            {docDirty ? <span className="tag warn">未保存</span> : null}
            {docNotice ? <span className="tag ok">{docNotice}</span> : null}
          </>
        ) : null}
        <span className="spacer" />
        {isDoc ? (
          <div className="doc-mode" role="group" aria-label="文档显示方式">
            <button
              className={docEditing ? '' : 'active'}
              onClick={() => setDocEditing(false)}
              title="渲染后的文档（默认）"
              aria-pressed={!docEditing}
              type="button"
            >
              <Icon name="book" size={12} />
              阅读
            </button>
            <button
              className={docEditing ? 'active' : ''}
              onClick={() => setDocEditing(true)}
              title="查看并编辑 Markdown 源码"
              aria-pressed={docEditing}
              type="button"
            >
              <Icon name="pencil" size={12} />
              编辑
            </button>
          </div>
        ) : null}
        {isDoc && docEditing ? (
          <button
            className="small primary doc-save"
            onClick={() => void saveDoc()}
            disabled={docSaving || !docDirty}
            title="保存（Ctrl+S）"
            type="button"
          >
            <Icon name="check" size={11} />
            {docSaving ? '保存中…' : '保存'}
          </button>
        ) : null}
        <div className="toolbar-actions">
          <button
            className="ghost icon"
            title="在文件中搜索（Ctrl+F）"
            aria-label="在文件中搜索"
            onClick={() => editorRef.current?.getAction('actions.find')?.run()}
            type="button"
          >
            <Icon name="search" size={13} />
          </button>
        </div>
        {git.data?.is_repo ? (
          <>
            <span className="tag branch">
              <Icon name="git-branch" size={10} />
              {git.data.branch ?? 'detached'}
            </span>
            {dirty ? <span className="tag dirty">有未提交改动</span> : null}
          </>
        ) : null}
      </div>

      {picked ? (
        <div className="selection-bar">
          <span className="lines">
            <span className="dot" />
            已选 {picked.lines.length} 行 · {picked.lines.join(', ')}
          </span>
          <span className="spacer" />
          <button
            className="small primary"
            onClick={() => {
              if (!path) return
              const first = picked.lines[0] ?? 1
              const last = picked.lines[picked.lines.length - 1] ?? first
              onAskSelection({ path, start: first, end: last, code: picked.code })
            }}
            type="button"
          >
            <Icon name="message-plus" size={11} />
            就这些行提问
          </button>
          <button className="small annotation-action" type="button" onClick={() => { if (projectId === null || path === null) return; const start = picked.lines[0] ?? 1; const end = picked.lines[picked.lines.length - 1] ?? start; setAnnotationText(''); setAnnotationDraft({ projectId, path, start, end, code: picked.code }) }}>
            <Icon name="pencil" size={11} />就这段批注
          </button>
          <button
            className="small ghost"
            onClick={() => {
              pickedRef.current = []
              pickRef.current?.clear()
              setPicked(null)
            }}
            type="button"
          >
            清除
          </button>
        </div>
      ) : selection ? (
        <div className="selection-bar">
          <span className="lines">
            <span className="dot" />
            第 {selection.start}–{selection.end} 行 · {countLines(selection.code)} 行
          </span>
          <span className="spacer" />
          <button
            className="small primary"
            onClick={() => {
              if (!path) return
              onAskSelection({ path, start: selection.start, end: selection.end, code: selection.code })
            }}
            type="button"
          >
            <Icon name="message-plus" size={11} />
            就这段提问
          </button>
          <button className="small annotation-action" type="button" onClick={() => { if (projectId === null || path === null) return; setAnnotationText(''); setAnnotationDraft({ projectId, path, start: selection.start, end: selection.end, code: selection.code }) }}>
            <Icon name="pencil" size={11} />就这段批注
          </button>
          <button
            className="small ghost"
            onClick={() => {
              editorRef.current?.setSelection({
                startLineNumber: selection.start,
                startColumn: 1,
                endLineNumber: selection.start,
                endColumn: 1,
              })
              citationRef.current?.clear()
              clearCitation()
              setSelection(null)
            }}
            type="button"
          >
            取消选择
          </button>
        </div>
      ) : null}

      {annotationDraft ? <div className="annotation-editor">
        <span>批注 · 第 {annotationDraft.start}-{annotationDraft.end} 行</span>
        <textarea autoFocus rows={2} maxLength={10000} value={annotationText} onChange={(event) => setAnnotationText(event.target.value)} placeholder="写下这段代码的批注…" />
        <button className="small ghost" type="button" onClick={() => setAnnotationDraft(null)}>取消</button>
        <button className="small primary" type="button" disabled={!annotationText.trim() || annotationSaving} onClick={() => void saveAnnotation()}><Icon name="check" size={11} />{annotationSaving ? '保存中…' : '保存批注'}</button>
      </div> : null}

      {cardError ? <ErrorNote message={cardError} /> : null}

      <ConversationCards
        cards={cards.data ?? []}
        activeConversationId={activeConversationId}
        noteBusyId={noteBusyId}
        onOpen={openConversation}
        onGenerateNote={(conversation) => void generateNote(conversation)}
        onOpenNote={(conversation) => void openNote(conversation)}
        onHoverRange={setHoverRange}
      />

      <div className="editor-mount">
        {file.loading && !content ? <Loading text="正在读取文件…" /> : null}
        {file.error ? <ErrorNote message={file.error} onRetry={file.reload} /> : null}
        {docError ? (
          <div className="doc-error">
            <ErrorNote message={`保存失败：${docError}`} />
            <button
              className="icon small"
              onClick={() => setDocError(null)}
              title="关闭"
              type="button"
            >
              <Icon name="x" size={12} />
            </button>
          </div>
        ) : null}
        {content && isDoc && !docEditing ? (
          <MarkdownDoc path={content.path} projectId={projectId ?? 0} text={docText} />
        ) : null}
        {content && (!isDoc || docEditing) ? (
          <CodeViewer
            value={isDoc ? docText : content.content}
            language={languageFor(content)}
            onMountEditor={onMount}
            readOnly={!docEditing}
            onChange={(next) => updateDocDraft(next)}
          />
        ) : null}
        {!file.loading && !content && !file.error ? (
          <div className="editor-placeholder">未能读取该文件。</div>
        ) : null}
      </div>
    </div>
  )
}

/*
 * Backend write errors arrive in English and name the reason precisely; the
 * panel is otherwise Chinese, so translate the cases a user can actually hit and
 * fall back to the original text for anything else (never lose the detail).
 */
function saveErrorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  if (raw.includes('file changed on disk since it was opened')) {
    return '文件在打开后被改动过，请重新打开再编辑'
  }
  if (raw.includes('file too large')) {
    return '文件太大，无法保存'
  }
  if (raw.includes('not a file')) {
    return '目标不是文件'
  }
  if (raw.includes('file not found')) {
    return '文件不存在'
  }
  if (raw.includes('escapes project root')) {
    return '路径超出项目范围'
  }
  return raw
}

function languageFor(file: FileContent): string {
  return file.language && file.language !== 'plaintext' ? file.language : languageFromPath(file.path)
}

function countLines(code: string): number {
  if (!code) return 0
  return code.split('\n').length
}

async function getGitStatusSafe(projectId: number, signal: AbortSignal): Promise<GitStatus | null> {
  try {
    const status = await api.getGitStatus(projectId)
    return signal.aborted ? null : status
  } catch {
    return null
  }
}
