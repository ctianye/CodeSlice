import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Citation, Note, Project } from '../types'
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
  /**
   * Range currently outlined as "you jumped here from a citation".
   *
   * Owned here rather than inside the editor pane because opening a citation
   * from a note unmounts the note reader and mounts the pane fresh; a ref in the
   * pane would forget the previous range and "click the same chip again to clear
   * it" could never work. `null` means nothing is highlighted.
   */
  citationHighlight: RevealTarget | null
  /** Drop the citation outline without touching the open file or selection. */
  clearCitation: () => void
  activeConversationId: string | null
  setActiveConversationId: (id: string | null) => void
  /**
   * Bumped when something asks the conversation list to scroll the active row
   * into view (clicking a code-card title, for example). The list is the only
   * component that knows the row's DOM node.
   */
  conversationFocusNonce: number
  /** Activate a conversation and ask the sidebar list to reveal it. */
  focusConversation: (id: string) => void
  /** Bumped whenever conversations/notes should be re-fetched. */
  refreshKey: number
  bumpRefresh: () => void
  /** Which sidebar tab is showing; owned here so code cards can switch to notes. */
  sidebarTab: SidebarTab
  setSidebarTab: (tab: SidebarTab) => void
  /** Note the notes panel should open; the panel clears it after focusing. */
  focusNoteId: number | null
  focusNote: (noteId: number | null) => void
  /**
   * Note currently shown in the main pane, rendered as readable Markdown.
   *
   * Notes used to open in a cramped inline editor inside the sidebar; reading a
   * distilled knowledge point is the point of the feature, so it takes over the
   * editor pane instead. `null` returns the pane to the code.
   */
  noteView: Note | null
  openNoteView: (note: Note | null) => void
  theme: AppTheme
  setTheme: (theme: AppTheme) => void
}

const AppContext = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }): ReactNode {
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProjectState] = useState<Project | null>(null)
  const [openFile, setOpenFile] = useState<OpenFileState | null>(null)
  const [activeConversationId, setActiveConversationIdState] = usePersistedState<string | null>(
    'codeslice.activeConversation',
    null,
  )
  const [refreshKey, setRefreshKey] = useState(0)
  const [conversationFocusNonce, setConversationFocusNonce] = useState(0)
  const [theme, setTheme] = usePersistedState<AppTheme>('codeslice.theme', 'light')
  const [sidebarTab, setSidebarTabState] = usePersistedState<SidebarTab>('codeslice.sidebarTab', 'files')
  const [focusNoteId, setFocusNoteId] = useState<number | null>(null)
  const [noteView, setNoteView] = useState<Note | null>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const setProject = useCallback(
    (next: Project | null) => {
      setProjectState((prev) => {
        if (prev?.id !== next?.id) {
          setActiveConversationIdState(null)
          setOpenFile(null)
          setCitationHighlight(null)
          setNoteView(null)
        }
        return next
      })
    },
    [setActiveConversationIdState],
  )

  /**
   * Toggle a citation highlight, remembering which range is currently shown.
   *
   * The highlight has to survive a pane teardown: opening a citation from a note
   * unmounts the reader and mounts the editor, so a ref inside `CodePane` was
   * reset before the second click arrived and "click again to clear" never
   * worked. Keeping the last reveal here makes the toggle stateless for the pane.
   */
  const [citationHighlight, setCitationHighlight] = useState<RevealTarget | null>(null)

  const reveal = useCallback((path: string, start?: number, end?: number) => {
    const from = start && start > 0 ? start : 1
    const to = end && end >= from ? end : from
    setOpenFile({ path, highlight: start || end ? { start: from, end: to } : null })
    // Jumping to code means leaving the note that was open in the same pane.
    setNoteView(null)
    setCitationHighlight((prev) => {
      const same = prev && prev.path === path && prev.start_line === from && prev.end_line === to
      // Second click on the same chip is a request to drop the highlight again.
      return same ? null : { path, start_line: from, end_line: to, symbol: null, nonce: 0 }
    })
  }, [])

  const clearCitation = useCallback(() => setCitationHighlight(null), [])

  const bumpRefresh = useCallback(() => setRefreshKey((n) => n + 1), [])

  const setActiveConversationId = useCallback(
    (id: string | null) => setActiveConversationIdState(id),
    [setActiveConversationIdState],
  )

  const setSidebarTab = useCallback((next: SidebarTab) => setSidebarTabState(next), [setSidebarTabState])

  /**
   * Open a conversation and make sure the user can see which one it is.
   *
   * Clicking a title strip in the editor used to change `activeConversationId`
   * and nothing else: if the list was showing another tab the row was invisible,
   * and even on the right tab a long list could leave it scrolled out of view.
   */
  const focusConversation = useCallback(
    (id: string) => {
      setActiveConversationIdState(id)
      setSidebarTabState('chats')
      setConversationFocusNonce((n) => n + 1)
    },
    [setActiveConversationIdState, setSidebarTabState],
  )

  const focusNote = useCallback((noteId: number | null) => {
    setFocusNoteId(noteId)
    if (noteId !== null) setSidebarTabState('notes')
  }, [setSidebarTabState])

  const openNoteView = useCallback((note: Note | null) => {
    setNoteView(note)
    if (note) setFocusNoteId(note.id)
  }, [])

  const value = useMemo<AppState>(
    () => ({
      project,
      projects,
      setProject,
      setProjects,
      openFile,
      reveal,
      citationHighlight,
      clearCitation,
      activeConversationId,
      setActiveConversationId,
      conversationFocusNonce,
      focusConversation,
      refreshKey,
      bumpRefresh,
      sidebarTab,
      setSidebarTab,
      focusNoteId,
      focusNote,
      noteView,
      openNoteView,
      theme,
      setTheme,
    }),
    [
      project,
      projects,
      setProject,
      openFile,
      reveal,
      citationHighlight,
      clearCitation,
      activeConversationId,
      setActiveConversationId,
      conversationFocusNonce,
      focusConversation,
      refreshKey,
      bumpRefresh,
      sidebarTab,
      setSidebarTab,
      focusNoteId,
      focusNote,
      noteView,
      openNoteView,
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
