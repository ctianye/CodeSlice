import type { ReactNode } from 'react'
import type { Message } from '../types'
import { CitationChip } from './CitationChip'

export function MessageBubble({ message }: { message: Message }): ReactNode {
  const roleClass = message.role === 'user' ? 'user' : message.role === 'tool' ? 'tool' : 'assistant'
  const roleLabel = message.role === 'user' ? '你' : message.role === 'tool' ? '工具' : 'CodeSlice'

  return (
    <div className={`msg ${roleClass}`}>
      <div className="msg-role">{roleLabel}</div>
      <div className="bubble">{message.content}</div>
      {message.citations && message.citations.length > 0 ? (
        <div className="citation-chips">
          {message.citations.map((citation, index) => (
            <CitationChip key={`${citation.path}-${citation.start_line}-${index}`} citation={citation} />
          ))}
        </div>
      ) : null}
    </div>
  )
}
