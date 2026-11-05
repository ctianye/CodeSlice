import type { ReactNode } from 'react'
import type { Citation } from '../types'
import { citationLabel } from '../lib/citations'
import { useApp } from '../state/AppState'

interface CitationChipProps {
  citation: Citation
}

/** Clickable chip that jumps the editor to the cited range. */
export function CitationChip({ citation }: CitationChipProps): ReactNode {
  const { reveal } = useApp()
  return (
    <button
      type="button"
      className="citation-chip"
      title={`${citation.path} 第 ${citation.start_line}-${citation.end_line} 行`}
      onClick={() => reveal(citation.path, citation.start_line, citation.end_line)}
    >
      <span>{citationLabel(citation)}</span>
      {citation.symbol ? <span className="sym">{citation.symbol}</span> : null}
    </button>
  )
}
