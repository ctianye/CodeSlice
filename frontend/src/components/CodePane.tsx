import type { editor as MonacoEditor } from 'monaco-editor'
import type { ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { FileContent, GitStatus } from '../types'
import { useAsync } from '../hooks/useAsync'
import { baseName } from '../lib/citations'
import { formatBytes, languageFromPath } from '../lib/format'
import { useApp } from '../state/AppState'
import { CodeViewer } from './CodeViewer'
import { ErrorNote, Loading } from './Feedback'

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
  const { project, openFile, revealTarget } = useApp()
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

  const editorRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null)
  const decorationsRef = useRef<MonacoEditor.IEditorDecorationsCollection | null>(null)
  const [selection, setSelection] = useState<{ start: number; end: number; code: string } | null>(null)

  const onMount = useCallback((instance: MonacoEditor.IStandaloneCodeEditor) => {
    editorRef.current = instance
    decorationsRef.current = instance.createDecorationsCollection([])
    instance.onDidChangeCursorSelection(() => updateSelection(instance))
  }, [])

  const updateSelection = (instance: MonacoEditor.IStandaloneCodeEditor) => {
    const model = instance.getModel()
    const range = instance.getSelection()
    if (!model || !range) {
      setSelection(null)
      return
    }
    if (range.isEmpty()) {
      setSelection(null)
      return
    }
    // A selection ending at column 1 of a line does not include that line.
    const startLine = range.startLineNumber
    const endLine = range.endColumn === 1 && range.endLineNumber > range.startLineNumber
      ? range.endLineNumber - 1
      : range.endLineNumber
    const code = model.getValueInRange({
      startLineNumber: startLine,
      startColumn: 1,
      endLineNumber: endLine,
      endColumn: model.getLineMaxColumn(endLine),
    })
    setSelection({ start: startLine, end: endLine, code })
  }

  // Reveal a cited range whenever the app asks for it (citation chip click).
  useEffect(() => {
    const instance = editorRef.current
    if (!instance || !revealTarget || !openFile || revealTarget.path !== openFile.path) return
    const model = instance.getModel()
    if (!model) return
    const maxLine = model.getLineCount()
    const from = Math.max(1, Math.min(revealTarget.start_line, maxLine))
    const to = Math.max(from, Math.min(revealTarget.end_line, maxLine))
    decorationsRef.current?.set([
      {
        range: {
          startLineNumber: from,
          startColumn: 1,
          endLineNumber: to,
          endColumn: model.getLineMaxColumn(to),
        },
        options: { isWholeLine: true, className: 'citation-highlight', inlineClassName: 'citation-line-flash' },
      },
    ])
    instance.revealLineInCenter(from)
    instance.setSelection({ startLineNumber: from, startColumn: 1, endLineNumber: to, endColumn: 1 })
  }, [revealTarget, openFile, file.data])

  // Drop stale decorations when switching files.
  useEffect(() => {
    decorationsRef.current?.clear()
    setSelection(null)
  }, [path])

  if (!project) {
    return (
      <div className="editor-pane">
        <div className="editor-placeholder">
          <div className="big">还没有打开项目</div>
          <div>在左侧选择项目，或从欢迎页添加一个本地代码目录。</div>
        </div>
      </div>
    )
  }

  if (!path) {
    return (
      <div className="editor-pane">
        <div className="editor-placeholder">
          <div className="big">选择左侧文件开始阅读</div>
          <div>可以在编辑器中拖选代码行，然后点击「就这段提问」。</div>
          <button onClick={onOpenFile}>打开文件列表</button>
        </div>
      </div>
    )
  }

  const content = file.data
  const dirty = git.data?.dirty ?? false

  return (
    <div className="editor-pane">
      <div className="editor-toolbar">
        <span className="file-path" title={path}>
          {baseName(path)}
        </span>
        {content ? (
          <>
            <span className="tag">{content.language || languageFromPath(path)}</span>
            <span className="tag">{formatBytes(content.size)}</span>
            {content.truncated ? <span className="tag dirty">已截断</span> : null}
          </>
        ) : null}
        <span className="spacer" />
        {git.data?.is_repo ? (
          <>
            <span className="tag branch">{git.data.branch ?? 'detached'}</span>
            {dirty ? <span className="tag dirty">有未提交改动</span> : null}
          </>
        ) : null}
      </div>

      {selection ? (
        <div className="selection-bar">
          <span className="lines">
            已选 第 {selection.start}–{selection.end} 行（{countLines(selection.code)} 行）
          </span>
          <span className="spacer" />
          <button
            className="small primary"
            onClick={() => {
              if (!path) return
              onAskSelection({ path, start: selection.start, end: selection.end, code: selection.code })
            }}
          >
            就这段提问
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
              decorationsRef.current?.clear()
              setSelection(null)
            }}
          >
            取消选择
          </button>
        </div>
      ) : null}

      <div className="editor-mount">
        {file.loading && !content ? <Loading text="正在读取文件…" /> : null}
        {file.error ? <ErrorNote message={file.error} onRetry={file.reload} /> : null}
        {content ? (
          <CodeViewer value={content.content} language={languageFor(content)} onMountEditor={onMount} />
        ) : null}
        {!file.loading && !content && !file.error ? (
          <div className="editor-placeholder">未能读取该文件。</div>
        ) : null}
      </div>
    </div>
  )
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
