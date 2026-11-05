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
 *
 * Theme colours mirror the CSS tokens in styles/base.css; Monaco cannot read
 * CSS variables, so the two hex sets are kept in sync by hand.
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
        lineNumbersMinChars: 4,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontFamily: "'Cascadia Code', 'JetBrains Mono', Consolas, 'Courier New', monospace",
        fontLigatures: true,
        fontSize: 12.5,
        lineHeight: 21,
        renderLineHighlight: 'all',
        renderWhitespace: 'selection',
        smoothScrolling: true,
        cursorBlinking: 'smooth',
        wordWrap: 'off',
        contextmenu: false,
        selectOnLineNumbers: false,
        glyphMargin: false,
        folding: true,
        unicodeHighlight: { ambiguousCharacters: false, invisibleCharacters: false },
        padding: { top: 12, bottom: 24 },
        automaticLayout: true,
        scrollbar: {
          verticalScrollbarSize: 10,
          horizontalScrollbarSize: 10,
          useShadows: false,
        },
      }}
      beforeMount={(monaco) => {
        monaco.editor.defineTheme('codeslice-dark', {
          base: 'vs-dark',
          inherit: true,
          rules: [],
          colors: {
            'editor.background': '#0b0f16',
            'editor.lineHighlightBackground': '#111926',
            'editorLineNumber.foreground': '#41505f',
            'editorLineNumber.activeForeground': '#9aabbe',
            'editor.selectionBackground': '#25456e',
            'editor.inactiveSelectionBackground': '#1b2c44',
            'editorIndentGuide.background1': '#18212d',
            'editorIndentGuide.activeBackground1': '#2c3949',
            'editorGutter.background': '#0b0f16',
            'editorWidget.background': '#151d27',
            'editorWidget.border': '#2c3949',
            'editorCursor.foreground': '#4c8dff',
            'scrollbarSlider.background': '#2c394980',
            'scrollbarSlider.hoverBackground': '#3a4a5e80',
          },
        })
        monaco.editor.defineTheme('codeslice-light', {
          base: 'vs',
          inherit: true,
          rules: [],
          colors: {
            'editor.background': '#ffffff',
            'editor.lineHighlightBackground': '#f4f7fa',
            'editorLineNumber.foreground': '#b0bac6',
            'editorLineNumber.activeForeground': '#4b5a6b',
            'editor.selectionBackground': '#bcd8ff',
            'editor.inactiveSelectionBackground': '#dbe9fd',
            'editorIndentGuide.background1': '#e8edf2',
            'editorIndentGuide.activeBackground1': '#cdd6e0',
            'editorGutter.background': '#ffffff',
            'editorWidget.background': '#f6f8fa',
            'editorWidget.border': '#cdd6e0',
            'editorCursor.foreground': '#2563eb',
            'scrollbarSlider.background': '#cdd6e080',
            'scrollbarSlider.hoverBackground': '#b0bac680',
          },
        })
      }}
      onMount={(instance) => onMountEditor?.(instance)}
      loading={<div className="editor-placeholder">正在加载编辑器…</div>}
    />
  )
})
