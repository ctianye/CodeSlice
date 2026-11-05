import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Citation, Project } from '../types'
import { usePersistedState } from '../hooks/useAsync'

export type AppTheme = 'dark' | 'light'

/** Sidebar views, shared so a code card can jump to the notes panel. */
export type SidebarTab = 'files' | 'chats' | 'notes' | 'anchors'

/** A request to reveal + highlight a line range in the editor. */
export interface RevealTarget extends Citation {
  /** Bumped on every request so repeated jumps to the same range still re-run. */
  nonce: number
}

export interface OpenFileState {
  path: string
  /** Line range to highlight (1-based, inclusive). */
  highlight: { start: number; end: number } | null
}

interface AppState {
  project: Project | null
  projects: Project[]
  setProject: (project: Project | null) => void
  setProjects: (projects: Project[]) => void
  openFile: OpenFileState | null
  /** Open a file in the editor, optionally highlighting a line range. */
  reveal: (path: string, start?: number, end?: number) => void
  revealTarget: RevealTarget | null
  activeConversationId: string | null
  setActiveConversationId: (id: string | null) => void
  /** Bumped whenever conversations/notes should be re-fetched. */
  refreshKey: number
  bumpRefresh: () => void
  /** Which sidebar tab is showing; owned here so code cards can switch to notes. */
  sidebarTab: SidebarTab
  setSidebarTab: (tab: SidebarTab) => void
  /** Note the notes panel should open; the panel clears it after focusing. */
  focusNoteId: number | null
  focusNote: (noteId: number | null) => void
  theme: AppTheme
  setTheme: (theme: AppTheme) => void
}

const AppContext = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }): ReactNode {
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProjectState] = useState<Project | null>(null)
  const [openFile, setOpenFile] = useState<OpenFileState | null>(null)
  const [revealTarget, setRevealTarget] = useState<RevealTarget | null>(null)
  const [activeConversationId, setActiveConversationIdState] = usePersistedState<string | null>(
    'codeslice.activeConversation',
    null,
  )
  const [refreshKey, setRefreshKey] = useState(0)
  const [theme, setTheme] = usePersistedState<AppTheme>('codeslice.theme', 'light')
  const [sidebarTab, setSidebarTabState] = usePersistedState<SidebarTab>('codeslice.sidebarTab', 'files')
  const [focusNoteId, setFocusNoteId] = useState<number | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const setProject = useCallback(
    (next: Project | null) => {
      setProjectState((prev) => {
        if (prev?.id !== next?.id) {
          setActiveConversationIdState(null)
          setOpenFile(null)
          setRevealTarget(null)
        }
        return next
      })
    },
    [setActiveConversationIdState],
  )

  const reveal = useCallback((path: string, start?: number, end?: number) => {
    const from = start && start > 0 ? start : 1
    const to = end && end >= from ? end : from
    setOpenFile({ path, highlight: start || end ? { start: from, end: to } : null })
    setRevealTarget((prev) => ({ path, start_line: from, end_line: to, symbol: null, nonce: (prev?.nonce ?? 0) + 1 }))
  }, [])

  const bumpRefresh = useCallback(() => setRefreshKey((n) => n + 1), [])

  const setActiveConversationId = useCallback(
    (id: string | null) => setActiveConversationIdState(id),
    [setActiveConversationIdState],
  )

  const setSidebarTab = useCallback((next: SidebarTab) => setSidebarTabState(next), [setSidebarTabState])

  const focusNote = useCallback((noteId: number | null) => {
    setFocusNoteId(noteId)
    if (noteId !== null) setSidebarTabState('notes')
  }, [setSidebarTabState])

  const value = useMemo<AppState>(
    () => ({
      project,
      projects,
      setProject,
      setProjects,
      openFile,
      reveal,
      revealTarget,
      activeConversationId,
      setActiveConversationId,
      refreshKey,
      bumpRefresh,
      sidebarTab,
      setSidebarTab,
      focusNoteId,
      focusNote,
      theme,
      setTheme,
    }),
    [
      project,
      projects,
      setProject,
      openFile,
      reveal,
      revealTarget,
      activeConversationId,
      setActiveConversationId,
      refreshKey,
      bumpRefresh,
      sidebarTab,
      setSidebarTab,
      focusNoteId,
      focusNote,
      theme,
      setTheme,
    ],
  )

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp(): AppState {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used inside <AppProvider>')
  return ctx
}
