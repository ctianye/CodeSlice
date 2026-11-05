import type { ReactNode } from 'react'
import type { StreamState } from '../hooks/useChatStream'
import { CitationChip } from './CitationChip'

/** Live (in-flight) assistant turn: activity lines, streaming text, citations. */
export function StreamingBubble({ state }: { state: StreamState }): ReactNode {
  return (
    <div className="msg assistant">
      <div className="msg-role">CodeSlice</div>

      {state.activity.length > 0 ? (
        <div className="activity">
          {state.activity.map((item) => (
            <div className={`activity-row ${item.state}`} key={item.id}>
              <span className="dot" />
              <span>{item.label}</span>
              {item.detail ? <span className="muted">· {truncate(item.detail, 120)}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {state.text ? (
        <div className="bubble">
          {state.text}
          <span className="streaming-caret" />
        </div>
      ) : null}

      {state.citations.length > 0 ? (
        <div className="citation-chips">
          {state.citations.map((citation, index) => (
            <CitationChip key={`${citation.path}-${citation.start_line}-${index}`} citation={citation} />
          ))}
        </div>
      ) : null}

      {state.error ? <div className="error-note">{state.error}</div> : null}
    </div>
  )
}

function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`
}
