import type { Citation } from '../types'

/** Adds a citation unless the exact same range is already listed. */
export function mergeCitation(list: Citation[], next: Citation): Citation[] {
  const exists = list.some(
    (c) => c.path === next.path && c.start_line === next.start_line && c.end_line === next.end_line,
  )
  return exists ? list : [...list, next]
}

/** "main.py:L12-30" for a citation chip. */
export function citationLabel(citation: Citation): string {
  const name = citation.path.split(/[\\/]/).pop() ?? citation.path
  const lines =
    citation.start_line === citation.end_line
      ? `L${citation.start_line}`
      : `L${citation.start_line}-${citation.end_line}`
  return `${name}:${lines}`
}

/** Extracts a file name from a project-relative path. */
export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}
