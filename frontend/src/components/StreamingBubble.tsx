import type { ReactNode } from 'react'
import type { StreamState } from '../hooks/useChatStream'
import { CitationChip } from './CitationChip'
import { Icon } from './Icon'
import { Markdown } from './Markdown'

/** Live (in-flight) assistant turn: activity lines, streaming text, citations. */
export function StreamingBubble({ state }: { state: StreamState }): ReactNode {
  const running = state.activity.some((item) => item.state === 'running')

  return (
    <div className="msg assistant">
      <div className="msg-role">
        <span className="role-icon">
          <Icon name="sparkles" size={11} />
        </span>
        CodeSlice
      </div>

      {state.activity.length > 0 ? (
        <div className="activity">
          {state.activity.map((item) => (
            <div className={`activity-row ${item.state}`} key={item.id}>
              <span className="activity-dot" />
              <span className="activity-label">{item.label}</span>
              {item.detail ? <span className="activity-detail">{truncate(item.detail, 120)}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {state.text ? (
        <div className="bubble">
          <Markdown text={state.text} />
          {running ? <span className="streaming-caret" /> : null}
        </div>
      ) : null}

      {state.citations.length > 0 ? (
        <div className="citation-chips">
          {state.citations.map((citation, index) => (
            <CitationChip key={`${citation.path}-${citation.start_line}-${index}`} citation={citation} />
          ))}
        </div>
      ) : null}

      {state.error ? (
        <div className="error-note" role="alert">
          <span className="err-icon">
            <Icon name="alert" size={13} />
          </span>
          <span className="err-body">{state.error}</span>
        </div>
      ) : null}
    </div>
  )
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`
}
