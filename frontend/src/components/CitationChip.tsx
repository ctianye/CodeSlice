import type { ReactNode } from 'react'
import type { Citation } from '../types'
import { citationLabel } from '../lib/citations'
import { useApp } from '../state/AppState'
import { Icon } from './Icon'

interface CitationChipProps {
  citation: Citation
}

/** Notes are cited as `note://<id>`; they open the notes panel, not the editor. */
const NOTE_PREFIX = 'note://'

function noteIdFromPath(path: string): number | null {
  if (!path.startsWith(NOTE_PREFIX)) return null
  const id = Number(path.slice(NOTE_PREFIX.length))
  return Number.isInteger(id) && id > 0 ? id : null
}

/** Clickable chip that jumps the editor to the cited range. */
export function CitationChip({ citation }: CitationChipProps): ReactNode {
  const { reveal, focusNote } = useApp()
  const noteId = noteIdFromPath(citation.path)

  if (noteId !== null) {
    return (
      <button
        type="button"
        className="citation-chip note"
        title="这是你之前保存的笔记，点击打开"
        onClick={() => focusNote(noteId)}
      >
        <Icon name="notes" size={10} />
        <span className="lbl">笔记</span>
        {citation.symbol ? <span className="sym">{citation.symbol}</span> : null}
      </button>
    )
  }

  return (
    <button
      type="button"
      className="citation-chip"
      title={`${citation.path} 第 ${citation.start_line}-${citation.end_line} 行`}
      onClick={() => reveal(citation.path, citation.start_line, citation.end_line)}
    >
      <Icon name="code" size={10} />
      <span className="lbl">{citationLabel(citation)}</span>
      {citation.symbol ? <span className="sym">{citation.symbol}</span> : null}
    </button>
  )
}
