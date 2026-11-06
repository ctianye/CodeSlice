import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelTerminal, getTerminalHistory, runTerminal } from '../apiTerminal'
import type { TerminalHistoryItem } from '../apiTerminal'
import { usePersistedState } from '../hooks/useAsync'
import { EmptyState } from './Feedback'
import { Icon } from './Icon'
// Imported here (not in main.tsx) so the dock stays self-contained.
import '../styles/terminal.css'

const HEIGHT_KEY = 'codeslice.terminalHeight'
const MIN_HEIGHT = 120
const MAX_HEIGHT = 480
const DEFAULT_HEIGHT = 220
/** Older lines are dropped so a chatty command cannot blow up the DOM. */
const MAX_LINES = 2000

interface TerminalPanelProps {
  projectId: number
  open: boolean
  onToggle: () => void
  onHeightChange?: (height: number) => void
}

type LineKind = 'command' | 'stdout' | 'stderr' | 'exit' | 'error' | 'notice'

interface TerminalLine {
  id: number
  kind: LineKind
  text?: string
  code?: number
  durationMs?: number
  truncated?: boolean
}

function clampHeight(value: number): number {
  return Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, value)))
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/**
 * Bottom dock running shell commands against the project root.
 *
 * Output arrives as SSE frames, so lines appear while a long command runs
 * instead of when it finishes. The panel keeps its own scrollback; the backend
 * only stores a short tail, which is what the header's history count is for.
 */
export function TerminalPanel({ projectId, open, onToggle, onHeightChange }: TerminalPanelProps): ReactNode {
  const [height, setHeight] = usePersistedState<number>(HEIGHT_KEY, DEFAULT_HEIGHT)
  const [lines, setLines] = useState<TerminalLine[]>([])
  const [history, setHistory] = useState<TerminalHistoryItem[]>([])
  const [input, setInput] = useState('')
  const [running, setRunning] = useState(false)
  const [dragging, setDragging] = useState(false)

  const nextId = useRef(0)
  const outputRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const runRef = useRef<AbortController | null>(null)
  /** Commands typed in this session, oldest first, for ArrowUp/ArrowDown. */
  const typedRef = useRef<string[]>([])
  const cursorRef = useRef(-1)
  const dragStart = useRef({ y: 0, height: DEFAULT_HEIGHT })

  const heightSafe = clampHeight(height)

  const changeHeight = useCallback(
    (next: number) => {
      const clamped = clampHeight(next)
      setHeight(clamped)
      onHeightChange?.(clamped)
    },
    [setHeight, onHeightChange],
  )

  useEffect(() => {
    onHeightChange?.(heightSafe)
  }, [heightSafe, onHeightChange])

  const append = useCallback((line: Omit<TerminalLine, 'id'>) => {
    setLines((prev) => {
      const next = [...prev, { ...line, id: nextId.current }]
      nextId.current += 1
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next
    })
  }, [])

  /** Silent refresh of the header count; a failure must not disturb the panel. */
  const refreshHistory = useCallback(() => {
    getTerminalHistory(projectId)
      .then(setHistory)
      .catch(() => {
        /* the count is cosmetic */
      })
  }, [projectId])

  // A different project means a different shell: drop everything.
  useEffect(() => {
    runRef.current?.abort()
    runRef.current = null
    setLines([])
    setHistory([])
    setInput('')
    setRunning(false)
    typedRef.current = []
    cursorRef.current = -1
  }, [projectId])

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    getTerminalHistory(projectId, controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setHistory(items)
      })
      .catch(() => {
        /* ignore: the header count is optional chrome */
      })
    return () => controller.abort()
  }, [projectId, open])

  useEffect(
    () => () => {
      runRef.current?.abort()
    },
    [],
  )

  // Follow the tail unless the user scrolled up to read something.
  useEffect(() => {
    const node = outputRef.current
    if (!node) return
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 60
    if (nearBottom) node.scrollTop = node.scrollHeight
  }, [lines])

  useEffect(() => {
    if (!dragging) return
    const onMove = (event: MouseEvent) => {
      // Dragging up grows the panel.
      changeHeight(dragStart.current.height + (dragStart.current.y - event.clientY))
    }
    const onUp = () => setDragging(false)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [dragging, changeHeight])

  const execute = useCallback(
    async (raw: string) => {
      const command = raw.trim()
      if (!command || running) return

      typedRef.current = [...typedRef.current, command].slice(-100)
      cursorRef.current = -1
      setInput('')

      const controller = new AbortController()
      runRef.current = controller
      setRunning(true)
      append({ kind: 'command', text: command })

      try {
        await runTerminal(
          projectId,
          command,
          undefined,
          {
            onOutput: (event) => append({ kind: event.stream, text: event.text }),
            onExit: (event) =>
              append({
                kind: 'exit',
                code: event.code,
                durationMs: event.duration_ms,
                truncated: event.truncated,
              }),
            onError: (event) => append({ kind: 'error', text: event.detail }),
            onDone: () => setRunning(false),
          },
          controller.signal,
        )
      } finally {
        if (runRef.current === controller) runRef.current = null
        setRunning(false)
        void refreshHistory()
      }
    },
    [append, projectId, refreshHistory, running],
  )

  const stop = useCallback(() => {
    const controller = runRef.current
    if (!controller) return
    controller.abort()
    runRef.current = null
    setRunning(false)
    append({ kind: 'notice', text: '已停止' })
    // The server kills the child when the stream closes; this covers the case
    // where the disconnect has not been noticed yet.
    void cancelTerminal(projectId)
  }, [append, projectId])

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      void execute(input)
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      setInput('')
      cursorRef.current = -1
      return
    }
    if (event.key === 'ArrowUp') {
      const list = typedRef.current
      if (list.length === 0) return
      event.preventDefault()
      cursorRef.current = cursorRef.current < 0 ? list.length - 1 : Math.max(0, cursorRef.current - 1)
      setInput(list[cursorRef.current] ?? '')
      return
    }
    if (event.key === 'ArrowDown') {
      const list = typedRef.current
      if (list.length === 0 || cursorRef.current < 0) return
      event.preventDefault()
      cursorRef.current = cursorRef.current + 1 >= list.length ? -1 : cursorRef.current + 1
      setInput(cursorRef.current < 0 ? '' : (list[cursorRef.current] ?? ''))
    }
  }

  if (!open) return null

  return (
    <section className="terminal-panel" style={{ height: heightSafe }} aria-label="终端">
      <div
        className={`terminal-drag${dragging ? ' dragging' : ''}`}
        role="separator"
        aria-orientation="horizontal"
        aria-label="调整终端高度"
        onMouseDown={(event: ReactMouseEvent<HTMLDivElement>) => {
          event.preventDefault()
          dragStart.current = { y: event.clientY, height: heightSafe }
          setDragging(true)
        }}
      />

      <header className="terminal-head">
        <span className="terminal-title">
          <Icon name="terminal" size={13} />
          终端
        </span>
        <span className="terminal-meta mono" title="工作目录（项目根）">
          ./ 项目 #{projectId}
        </span>
        <span className="terminal-meta" title="后端保存的最近命令条数">
          历史 {history.length} 条
        </span>
        <span className="spacer" />

        {running ? (
          <button type="button" className="small danger" onClick={stop}>
            <Icon name="stop" size={11} />
            停止
          </button>
        ) : null}
        <button
          type="button"
          className="icon"
          title="清空终端"
          aria-label="清空终端"
          onClick={() => setLines([])}
        >
          <Icon name="trash" size={13} />
        </button>
        <button type="button" className="icon" title="最小化终端" aria-label="最小化终端" onClick={onToggle}>
          <Icon name="minimize" size={13} />
        </button>
      </header>

      <div className="terminal-out" ref={outputRef}>
        {lines.length === 0 ? (
          <EmptyState
            icon="terminal"
            title="终端就绪"
            hint="在这里直接运行命令，例如 pytest -q、git status。AI 在对话里执行的命令也会出现在这里。"
          />
        ) : null}

        {lines.map((line) => {
          if (line.kind === 'command') {
            return (
              <div className="terminal-line command" key={line.id}>
                <span className="terminal-prompt-mark">$</span>
                <span className="terminal-command">{line.text}</span>
              </div>
            )
          }
          if (line.kind === 'exit') {
            const code = line.code ?? 0
            return (
              <div className="terminal-line exit" key={line.id}>
                <span className={`terminal-exit-badge${code === 0 ? '' : ' bad'}`}>退出码 {code}</span>
                <span className="terminal-duration">耗时 {formatDuration(line.durationMs ?? 0)}</span>
                {line.truncated ? <span className="terminal-duration">输出已截断</span> : null}
              </div>
            )
          }
          if (line.kind === 'error') {
            return (
              <div className="terminal-line error" key={line.id}>
                {line.text}
              </div>
            )
          }
          if (line.kind === 'notice') {
            return (
              <div className="terminal-line notice" key={line.id}>
                {line.text}
              </div>
            )
          }
          return (
            <div className={`terminal-line ${line.kind}`} key={line.id}>
              {line.text}
            </div>
          )
        })}
      </div>

      <div className="terminal-input-row">
        <span className="terminal-caret">{running ? '…' : '>'}</span>
        <input
          ref={inputRef}
          className="terminal-input mono"
          value={input}
          spellCheck={false}
          autoComplete="off"
          placeholder={running ? '命令执行中…' : '输入命令后回车执行，↑/↓ 翻历史'}
          aria-label="终端命令输入"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="small"
          disabled={running || input.trim() === ''}
          onClick={() => void execute(input)}
        >
          <Icon name="command" size={11} />
          执行
        </button>
      </div>
    </section>
  )
}
