import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

interface ComposerProps {
  busy: boolean
  disabled: boolean
  selectionHint: string | null
  /** Show the 「联系上下文」 toggle: only meaningful for a bound code snippet. */
  contextToggle: boolean
  useContext: boolean
  onToggleContext: (next: boolean) => void
  /** Text pushed in from outside (e.g. a suggested prompt). The nonce forces a
   *  re-fill when the same suggestion is picked twice. */
  draft?: { text: string; nonce: number } | null
  onSend: (text: string) => void
  onStop: () => void
}

/** Message input; Enter sends, Shift+Enter inserts a newline. */
export function Composer({
  busy,
  disabled,
  selectionHint,
  contextToggle,
  useContext,
  onToggleContext,
  draft,
  onSend,
  onStop,
}: ComposerProps): ReactNode {
  const [text, setText] = useState('')
  const area = useRef<HTMLTextAreaElement | null>(null)

  // A suggestion fills the box (but never auto-sends — the user still decides).
  useEffect(() => {
    if (!draft) return
    setText(draft.text)
    const node = area.current
    node?.focus()
    node?.setSelectionRange(draft.text.length, draft.text.length)
  }, [draft])

  // Grow with the content up to the CSS max-height, then scroll.
  useEffect(() => {
    const node = area.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`
  }, [text])

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  return (
    <div className="composer">
      <div className={`selection-hint${selectionHint ? ' active' : ''}`}>
        <span className="dot" />
        {selectionHint ? (
          <span>{selectionHint}</span>
        ) : (
          <span>未选中代码 · 将以全项目模式提问</span>
        )}
      </div>

      <div className="composer-box">
        <textarea
          ref={area}
          placeholder="向 CodeSlice 提问，Enter 发送，Shift+Enter 换行"
          value={text}
          disabled={disabled}
          rows={2}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
        />

        <div className="composer-actions">
          {contextToggle ? (
            <button
              className={`small context-toggle${useContext ? ' on' : ''}`}
              onClick={() => onToggleContext(!useContext)}
              title={
                useContext
                  ? '当前会读取同文件的其他片段来补充背景；点击只看选中的代码'
                  : '当前只讲选中的这段代码；点击允许模型参考上下文'
              }
              aria-pressed={useContext}
              type="button"
            >
              <Icon name="anchor" size={11} />
              联系上下文
            </button>
          ) : null}
          <span className="grow">
            {busy ? '正在生成回答…' : (
              <span className="row" style={{ gap: 4 }}>
                <kbd>Enter</kbd> 发送
              </span>
            )}
          </span>
          {busy ? (
            <button className="small stop" onClick={onStop} type="button">
              <Icon name="stop" size={11} />
              停止
            </button>
          ) : (
            <button
              className="small send"
              onClick={submit}
              disabled={disabled || text.trim() === ''}
              type="button"
            >
              <Icon name="send" size={11} />
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
