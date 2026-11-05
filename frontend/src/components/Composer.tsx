import type { ReactNode } from 'react'
import { useState } from 'react'

interface ComposerProps {
  busy: boolean
  disabled: boolean
  selectionHint: string | null
  onSend: (text: string) => void
  onStop: () => void
}

/** Message input; Enter sends, Shift+Enter inserts a newline. */
export function Composer({ busy, disabled, selectionHint, onSend, onStop }: ComposerProps): ReactNode {
  const [text, setText] = useState('')

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
  }

  return (
    <div className="composer">
      <div className="selection-hint">
        {selectionHint ? (
          <>
            <span className="ok">●</span>
            <span>{selectionHint}</span>
          </>
        ) : (
          <span>未选中代码 · 将以全项目模式提问</span>
        )}
      </div>

      <textarea
        placeholder="向 CodeSlice 提问，Enter 发送，Shift+Enter 换行"
        value={text}
        disabled={disabled}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            submit()
          }
        }}
      />

      <div className="composer-actions">
        <span className="grow">{busy ? '正在生成回答…' : '回答会附带可点击的代码引用'}</span>
        {busy ? (
          <button className="small stop" onClick={onStop}>
            停止
          </button>
        ) : (
          <button className="small primary" onClick={submit} disabled={disabled || text.trim() === ''}>
            发送
          </button>
        )}
      </div>
    </div>
  )
}
