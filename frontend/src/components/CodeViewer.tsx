import Editor, { loader } from '@monaco-editor/react'
import type { editor as MonacoEditor } from 'monaco-editor'
import type { ReactNode } from 'react'
import { memo } from 'react'
import { useApp } from '../state/AppState'
import { registerCodeNavigation } from './editor/codeNavigation'

// Serve Monaco from the bundle (scripts/copy-monaco.mjs copies node_modules →
// public/monaco/vs) so the editor works with no network access.
loader.config({ paths: { vs: './monaco/vs' } })

interface CodeViewerProps {
  value: string
  language: string
  /** Project-relative path; gives each file its own Monaco model/URI. */
  path?: string
  onMountEditor?: (instance: MonacoEditor.IStandaloneCodeEditor) => void
  /**
   * Editable mode. When true the editor becomes writable and `value` is fed in
   * as the initial buffer only: `@monaco-editor/react` writes a changed `value`
   * through `executeEdits` while the editor is writable, which would fight the
   * caret on every keystroke. Typing is reported through `onChange` instead, and
   * the owner keeps the draft.
   */
  readOnly?: boolean
  onChange?: (value: string) => void
}

/**
 * Monaco wrapper. Highlighting and selection bookkeeping live in CodePane so
 * this component stays a thin, memoised mount point.
 *
 * Theme colours mirror the CSS tokens in styles/base.css; Monaco cannot read
 * CSS variables, so the two hex sets are kept in sync by hand.
 */
export const CodeViewer = memo(function CodeViewer({
  value,
  language,
  path,
  onMountEditor,
  readOnly = true,
  onChange,
}: CodeViewerProps): ReactNode {
  const { theme } = useApp()
  // Read-only mode stays controlled (the pane swaps files in place). Writable
  // mode switches to an uncontrolled buffer so typing is never overwritten.
  const buffer = readOnly ? { value } : { defaultValue: value }
  // A real URI keeps per-file view state and gives the hover provider a stable
  // model identity. Encode each segment so spaces/#/? in a path cannot truncate
  // the URI.
  const modelPath = path
    ? `file:///${path.replace(/\\/g, '/').split('/').map(encodeURIComponent).join('/')}`
    : undefined
  return (
    <Editor
      className="editor-surface"
      {...buffer}
      path={modelPath}
      keepCurrentModel
      language={language}
      theme={theme === 'dark' ? 'codeslice-dark' : 'codeslice-light'}
      onChange={readOnly ? undefined : (next) => onChange?.(next ?? '')}
      options={{
        readOnly,
        domReadOnly: readOnly,
        lineNumbers: 'on',
        lineNumbersMinChars: 4,
        // Gutter clicks are handled by attachGutterSelection in CodePane: it
        // turns line numbers into a picker (click adds a line, clicking it again
        // removes it) and sweeps a range on drag. Monaco's own
        // `selectOnLineNumbers` must stay off here, because it sets a whole-line
        // selection on every gutter click and that selection change wipes the
        // picked set right before the click handler runs, so lines could never
        // accumulate.
        selectOnLineNumbers: false,
        // Ctrl/Cmd+Click is "go to definition" here, so it must not also add a
        // cursor. Alt remains the multi-cursor modifier.
        multiCursorModifier: 'alt',
        // Hover cards and the reference picker can extend past the editor pane;
        // without this Monaco clips them at the mount's overflow boundary.
        fixedOverflowWidgets: true,
        // VS Code's "box every occurrence of the symbol" effect. Monaco's
        // wordHighlighter is the same contribution VS Code uses: it debounces the
        // whole-file scan and only runs on explicit cursor moves, so a mouse drag
        // does not trigger it. The colors live in editor.css.
        occurrencesHighlight: 'singleFile',
        // Selection highlighting rescans on every selection change; keep it for
        // normal source files and let large generated files opt out so dragging
        // stays responsive.
        selectionHighlight: value.length < 200_000,
        matchBrackets: 'never',
        renderLineHighlight: 'line',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontFamily: "'Cascadia Code', 'JetBrains Mono', Consolas, 'Courier New', monospace",
        fontLigatures: true,
        fontSize: 12.5,
        lineHeight: 21,
        renderWhitespace: 'selection',
        smoothScrolling: true,
        cursorBlinking: 'smooth',
        wordWrap: 'off',
        contextmenu: false,
        glyphMargin: false,
        folding: true,
        unicodeHighlight: {
          ambiguousCharacters: false,
          invisibleCharacters: false,
          nonBasicASCII: false,
        },
        padding: { top: 12, bottom: 24 },
        automaticLayout: true,
        scrollbar: {
          verticalScrollbarSize: 10,
          horizontalScrollbarSize: 10,
          useShadows: false,
        },
      }}
      beforeMount={(monaco) => {
        registerCodeNavigation(monaco)
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
