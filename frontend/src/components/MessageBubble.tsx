import type { ReactNode } from 'react'
import type { Message } from '../types'
import { CitationChip } from './CitationChip'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { Markdown } from './Markdown'

const ROLE: Record<string, { className: string; label: string; icon: IconName }> = {
  user: { className: 'user', label: '你', icon: 'user' },
  tool: { className: 'tool', label: '工具', icon: 'terminal' },
  assistant: { className: 'assistant', label: 'CodeSlice', icon: 'sparkles' },
}

interface MessageBubbleProps {
  message: Message
  /** Only the latest answer offers this; older ones stay read-only. */
  onGenerateNote?: () => void
  noteBusy?: boolean
}

export function MessageBubble({ message, onGenerateNote, noteBusy }: MessageBubbleProps): ReactNode {
  const role = ROLE[message.role] ?? ROLE.assistant

  return (
    <div className={`msg ${role.className}`}>
      <div className="msg-role">
        <span className="role-icon">
          <Icon name={role.icon} size={11} />
        </span>
        {role.label}
      </div>

      {role.className === 'assistant' ? (
        <div className="bubble">
          <Markdown text={message.content} />
        </div>
      ) : (
        <div className="bubble">{message.content}</div>
      )}

      {message.citations && message.citations.length > 0 ? (
        <div className="citation-chips">
          {message.citations.map((citation, index) => (
            <CitationChip key={`${citation.path}-${citation.start_line}-${index}`} citation={citation} />
          ))}
        </div>
      ) : null}

      {onGenerateNote ? (
        <div className="msg-actions">
          <button
            type="button"
            className="small ghost"
            onClick={onGenerateNote}
            disabled={noteBusy}
            title="把这段回答整理成一份可检索的笔记"
          >
            <Icon name="notes" size={11} />
            {noteBusy ? '整理中…' : '整理成笔记'}
          </button>
        </div>
      ) : null}
    </div>
  )
}
