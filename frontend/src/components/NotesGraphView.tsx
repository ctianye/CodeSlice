/**
 * Notes graph: a 2D map of how the knowledge base hangs together.
 *
 * Rendered as a full pane tab rather than a floating modal, so it matches the code
 * graph: same header shape, same legend rail under the canvas, same interaction.
 * Two graphs that look different for no reason force the user to relearn the same
 * gestures twice.
 *
 * Separate from the code graph on purpose. The code graph answers "how is this
 * project built"; this one answers "what have I learned, and what is related to
 * what". The edges here are *structural* (same folder, or both quoting the same
 * file) and are deliberately not presented as semantic similarity — nothing here
 * inferred meaning.
 */

import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useApp } from '../state/AppState'
import type { Note, NoteFolder } from '../types'
import { EmptyState, Loading } from './Feedback'
import { Icon } from './Icon'
import { GraphCanvas } from './graph/GraphCanvas'
import type { CanvasEdge, CanvasNode } from './graph/GraphCanvas'
import { buildNoteConnections, noteCategory } from './notesGraph'

const PALETTE = ['#44c2b1', '#e2a64f', '#e7798e', '#75a9ee', '#9c86dc', '#83bd73', '#dc83c2', '#58b8d6']

interface GraphCategory {
  id: string
  label: string
  color: string
  count: number
}

interface NotesGraphViewProps {
  notes: Note[]
  folders: NoteFolder[]
  loading?: boolean
  onOpen: (note: Note) => void
}

export function NotesGraphView({ notes, folders, loading = false, onOpen }: NotesGraphViewProps): ReactNode {
  const { askAboutNode } = useApp()
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(() => new Set())

  const folderNames = useMemo(() => new Map(folders.map((folder) => [folder.id, folder.name])), [folders])

  const categories = useMemo<GraphCategory[]>(() => {
    const grouped = new Map<string, { label: string; count: number }>()
    for (const note of notes) {
      const id = noteCategory(note)
      const label =
        note.folder_id != null ? folderNames.get(note.folder_id) ?? '未分类' : note.project_name ?? '未分类'
      const value = grouped.get(id) ?? { label, count: 0 }
      value.count += 1
      grouped.set(id, value)
    }
    return [...grouped].map(([id, item], index) => ({
      ...item,
      id,
      color: PALETTE[index % PALETTE.length],
    }))
  }, [notes, folderNames])

  const categoryIndex = useMemo(() => {
    const map = new Map<string, number>()
    categories.forEach((category, index) => map.set(category.id, index))
    return map
  }, [categories])

  /** Notes that pass the search box and the category filter. */
  const visibleNotes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return notes.filter((note) => {
      if (hiddenCategories.has(noteCategory(note))) return false
      if (!needle) return true
      return `${note.title}\n${note.content_md}\n${note.project_name ?? ''}`.toLocaleLowerCase().includes(needle)
    })
  }, [notes, hiddenCategories, query])

  const allConnections = useMemo(() => buildNoteConnections(notes), [notes])

  const canvasEdges = useMemo<CanvasEdge[]>(() => {
    const visible = new Set(visibleNotes.map((note) => String(note.id)))
    return allConnections
      .filter(([a, b]) => visible.has(String(a)) && visible.has(String(b)))
      .map(([a, b]) => ({
        source: String(a),
        target: String(b),
        // Structural links are facts about the corpus, not inferences.
        confidence: 'EXTRACTED',
        relation: '关联',
      }))
  }, [allConnections, visibleNotes])

  /** Degree counted from the visible edges, so hub sizing matches the picture. */
  const sizedNodes = useMemo<CanvasNode[]>(() => {
    const counts = new Map<string, number>()
    for (const edge of canvasEdges) {
      counts.set(edge.source, (counts.get(edge.source) ?? 0) + 1)
      counts.set(edge.target, (counts.get(edge.target) ?? 0) + 1)
    }
    return visibleNotes.map((note) => {
      const id = String(note.id)
      const category = noteCategory(note)
      return {
        id,
        label: note.title,
        degree: Math.max(1, counts.get(id) ?? 1),
        community: categoryIndex.get(category) ?? 0,
        kind: 'note',
        start_line: 0,
        path: '',
      }
    })
  }, [visibleNotes, categoryIndex, canvasEdges])

  const visibleIds = useMemo(
    () => new Set(visibleNotes.map((note) => String(note.id))),
    [visibleNotes],
  )

  const selectedNote = selectedId ? notes.find((note) => String(note.id) === selectedId) ?? null : null

  const selectedCategory = selectedNote
    ? categories.find((category) => category.id === noteCategory(selectedNote))
    : null

  const toggleCategory = (id: string) => {
    setHiddenCategories((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const searchMatches = useMemo(() => {
    const needle = query.trim()
    if (!needle) return []
    return visibleNotes.slice(0, 10)
  }, [query, visibleNotes])

  if (loading && notes.length === 0) return <Loading text="正在加载笔记…" />

  return (
    <div className="graph-view">
      <header className="graph-view-head">
        <div className="graph-view-title">
          <Icon name="network" size={14} />
          <strong>笔记图谱</strong>
          <span className="graph-view-stats">
            {visibleNotes.length} 篇笔记 · {categories.length} 个分类 · {canvasEdges.length} 条关联
          </span>
        </div>
      </header>

      <div className="graph-view-stage">
        {notes.length === 0 ? (
          <EmptyState
            icon="notes"
            title="还没有笔记"
            hint="在对话里把一次解释整理成笔记，它们就会出现在这张图上。"
          />
        ) : (
          <GraphCanvas
            nodes={sizedNodes}
            edges={canvasEdges}
            colors={PALETTE}
            selectedId={selectedId}
            visibleIds={visibleIds}
            onSelect={(id) => setSelectedId(id)}
            onActivate={(id) => {
              const note = notes.find((item) => String(item.id) === id)
              if (note) onOpen(note)
            }}
            emptyHint="没有匹配的笔记"
          />
        )}
      </div>

      {/* Same dock shape as the code graph: search, details and the legend share one
          horizontal band under the canvas. */}
      <footer className="graph-dock">
        <div className="graph-dock-search">
          <div className="graph-search-field">
            <Icon name="search" size={12} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索笔记"
              aria-label="搜索笔记图谱"
            />
            {query ? (
              <button type="button" className="icon" onClick={() => setQuery('')} title="清除搜索">
                <Icon name="x" size={11} />
              </button>
            ) : null}
          </div>
          {searchMatches.length > 0 ? (
            <div className="graph-match-list">
              {searchMatches.map((note) => (
                <button
                  key={note.id}
                  type="button"
                  className="graph-match"
                  onClick={() => setSelectedId(String(note.id))}
                  title={note.project_name ?? '本地知识库'}
                >
                  <span
                    className="graph-match-dot"
                    style={{ background: PALETTE[(categoryIndex.get(noteCategory(note)) ?? 0) % PALETTE.length] }}
                  />
                  <span className="graph-match-label">{note.title}</span>
                  <small>{note.folder_id != null ? folderNames.get(note.folder_id) ?? '' : note.project_name ?? ''}</small>
                </button>
              ))}
            </div>
          ) : (
            <div className="graph-dock-hint">拖动平移 · 滚轮缩放 · 点节点看笔记 · 双击打开</div>
          )}
        </div>

        <div className="graph-dock-detail">
          {selectedNote ? (
            <>
              <div className="graph-dock-detail-head">
                <strong>{selectedNote.title}</strong>
                <span className="graph-dock-meta">{selectedNote.project_name ?? '本地知识库'}</span>
                {selectedCategory ? (
                  <span className="graph-dock-community" style={{ color: selectedCategory.color }}>
                    {selectedCategory.label}
                  </span>
                ) : null}
                <span className="spacer" />
                <button type="button" className="small primary" onClick={() => onOpen(selectedNote)}>
                  <Icon name="book" size={11} />
                  打开笔记
                </button>
                <button
                  type="button"
                  className="small ghost"
                  onClick={() =>
                    askAboutNode({
                      label: selectedNote.title,
                      node_id: `note://${selectedNote.id}`,
                      path: `note://${selectedNote.id}`,
                      start_line: 0,
                      community_label: selectedCategory?.label ?? '',
                    })
                  }
                  title="就这篇笔记向 AI 提问"
                >
                  <Icon name="sparkles" size={11} />
                  提问
                </button>
              </div>
              <p className="graph-note-body">
                {selectedNote.content_md
                  .replace(/^#.*\n/, '')
                  .replace(/[#*`]/g, '')
                  .trim()
                  .slice(0, 480)}
              </p>
            </>
          ) : (
            <div className="graph-dock-empty">点击画布上的节点查看笔记。</div>
          )}
        </div>

        {categories.length > 0 ? (
          <div className="graph-dock-legend">
            <div className="graph-legend-items">
              {categories.map((category) => {
                const hidden = hiddenCategories.has(category.id)
                return (
                  <button
                    key={category.id}
                    type="button"
                    className={`graph-legend-chip${hidden ? ' off' : ''}`}
                    onClick={() => toggleCategory(category.id)}
                    title={`${category.label} · ${category.count} 篇（点击${hidden ? '显示' : '隐藏'}）`}
                    aria-pressed={!hidden}
                  >
                    <span className="graph-legend-dot" style={{ background: category.color }} />
                    <span className="graph-legend-label">{category.label}</span>
                    <span className="graph-legend-count">{category.count}</span>
                  </button>
                )
              })}
            </div>
            <button
              type="button"
              className="graph-legend-all"
              onClick={() =>
                setHiddenCategories(
                  hiddenCategories.size === 0 ? new Set(categories.map((category) => category.id)) : new Set(),
                )
              }
              title={hiddenCategories.size === 0 ? '隐藏全部分类' : '显示全部分类'}
            >
              {hiddenCategories.size === 0 ? '全隐藏' : '全显示'}
            </button>
          </div>
        ) : null}
      </footer>
    </div>
  )
}
