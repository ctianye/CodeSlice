import Editor, { loader } from '@monaco-editor/react'
import type { editor as MonacoEditor } from 'monaco-editor'
import type { ReactNode } from 'react'
import { memo } from 'react'
import { useApp } from '../state/AppState'

// Serve Monaco from the bundle (scripts/copy-monaco.mjs copies node_modules →
// public/monaco/vs) so the editor works with no network access.
loader.config({ paths: { vs: './monaco/vs' } })

interface CodeViewerProps {
  value: string
  language: string
  onMountEditor?: (instance: MonacoEditor.IStandaloneCodeEditor) => void
}

/**
 * Read-only Monaco wrapper. Highlighting and selection bookkeeping live in
 * CodePane so this component stays a thin, memoised mount point.
 */
export const CodeViewer = memo(function CodeViewer({
  value,
  language,
  onMountEditor,
}: CodeViewerProps): ReactNode {
  const { theme } = useApp()
  return (
    <Editor
      className="editor-surface"
      value={value}
      language={language}
      theme={theme === 'dark' ? 'codeslice-dark' : 'codeslice-light'}
      options={{
        readOnly: true,
        domReadOnly: true,
        lineNumbers: 'on',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontFamily: "'JetBrains Mono', 'Cascadia Code', Consolas, monospace",
        fontSize: 12.5,
        lineHeight: 20,
        renderLineHighlight: 'all',
        smoothScrolling: true,
        wordWrap: 'off',
        contextmenu: false,
        selectOnLineNumbers: false,
        padding: { top: 10, bottom: 10 },
        automaticLayout: true,
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
      }}
      beforeMount={(monaco) => {
        monaco.editor.defineTheme('codeslice-dark', {
          base: 'vs-dark',
          inherit: true,
          rules: [],
          colors: {
            'editor.background': '#0d1117',
            'editor.lineHighlightBackground': '#141b25',
            'editorLineNumber.foreground': '#4b5563',
            'editorLineNumber.activeForeground': '#8b98a8',
            'editor.selectionBackground': '#264a7a',
            'editorIndentGuide.background1': '#1b2430',
          },
        })
        monaco.editor.defineTheme('codeslice-light', {
          base: 'vs',
          inherit: true,
          rules: [],
          colors: {
            'editor.background': '#ffffff',
            'editor.lineHighlightBackground': '#f2f5f8',
            'editorLineNumber.foreground': '#a1aab5',
            'editorLineNumber.activeForeground': '#596575',
            'editor.selectionBackground': '#b9d8ff',
            'editorIndentGuide.background1': '#e5e9ee',
          },
        })
      }}
      onMount={(instance) => onMountEditor?.(instance)}
      loading={<div className="editor-placeholder">正在加载编辑器…</div>}
    />
  )
})
