import type { Note } from '../types'

export const noteCategory = (note: Note): string => note.folder_id != null
  ? `folder:${note.folder_id}`
  : `project:${note.project_id}`

/** Structural links, deliberately not presented as AI-inferred semantic facts. */
export function buildNoteConnections(notes: Note[]): Array<[number, number]> {
  const groups = new Map<string, number[]>()
  const references = new Map<string, number[]>()
  const edges = new Map<string, [number, number]>()
  const addTo = (map: Map<string, number[]>, key: string, id: number) => {
    const group = map.get(key) ?? []
    group.push(id)
    map.set(key, group)
  }
  for (const note of notes) {
    addTo(groups, noteCategory(note), note.id)
    // Restrict suffixes to files; decimal numbers and domain names are not code references.
    const paths = note.content_md.match(/[\w./\\-]+\.(?:tsx?|jsx?|py|rs|go|java|kt|swift|c|cpp|h|hpp|cs|vue|svelte|html|css|scss|json|ya?ml|toml|md|sql|sh|ps1|rb|php)\b/gi) ?? []
    for (const path of new Set(paths.map((value) => value.replace(/\\/g, '/').replace(/^\.\//, '')))) {
      // Project identity is part of the reference: common names across projects are unrelated.
      addTo(references, `${note.project_id}:${path}`, note.id)
    }
  }
  const linkGroups = (map: Map<string, number[]>) => {
    for (const ids of map.values()) {
      ids.sort((a, b) => a - b)
      // A stable chain keeps large categories linear in size without claiming every pair is related.
      for (let index = 1; index < ids.length; index += 1) {
        const a = ids[index - 1]
        const b = ids[index]
        edges.set(`${a}:${b}`, [a, b])
      }
    }
  }
  linkGroups(groups)
  linkGroups(references)
  return [...edges.values()]
}
