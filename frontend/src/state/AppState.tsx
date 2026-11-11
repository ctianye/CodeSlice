import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { Citation, Note, Project } from '../types'
import { usePersistedState } from '../hooks/useAsync'

export type AppTheme = 'dark' | 'light'

/** Sidebar views, shared so a code card can jump to the notes panel. */
export type SidebarTab = 'files' | 'chats' | 'notes'

/** Runtime list of the valid tab ids, used to validate the persisted value. */
const SIDEBAR_TABS: SidebarTab[] = ['files', 'chats', 'notes']

/** A request to reveal + highlight a line range in the editor. */
export interface RevealTarget extends Citation {
  /** Bumped on every request so repeated jumps to the same range still re-run. */
  nonce: number
  /**
   * Why the range is highlighted. Citations show the "引用第 x 行" ask bar;
   * navigation jumps keep the line highlighted but stay out of that bar, like
   * VS Code's go-to-definition.
   */
  source?: 'citation' | 'navigation'
}

export interface OpenFileState {
  path: string
  /** Line range to highlight (1-based, inclusive). */
  highlight: { start: number; end: number } | null
}

/**
 * One entry in the editor tab strip.
 *
 * Files are identified by path and notes by id, so opening the same file twice
 * focuses the existing tab instead of stacking duplicates. A note tab keeps the
 * whole `Note` (not just its id) because renaming or moving it has to update the
 * reader without a refetch.
 */
export type EditorTab =
  | { kind: 'file'; path: string; highlight: { start: number; end: number } | null }
  | { kind: 'note'; note: Note }
  /**
   * A project-wide graph. Both graphs are single-instance: two code-graph tabs
   * would render the same payload twice and only confuse "which one am I on".
   *
   * `code` is the project's structure, `notes` is the knowledge base. They are
   * separate views because they answer different questions and their edges mean
   * different things.
   */
  | { kind: 'graph'; scope: 'code'; focusNodeId: string | null; focusNonce?: number }
  | { kind: 'graph'; scope: 'notes'; focusNodeId: null }

/** A node the user asked the agent about from a graph view. */
export interface GraphAsk {
  label: string
  node_id: string
  path: string
  start_line: number
  community_label: string
}

/** One entry in the editor's back/forward stack. */
export interface NavHistoryEntry {
  path: string
  start_line: number
  end_line: number
}

/** A reading session should not grow an unbounded list of jump targets. */
const MAX_NAV_HISTORY = 40

interface AppState {
  project: Project | null
  projects: Project[]
  setProject: (project: Project | null) => void
  setProjects: (projects: Project[]) => void
  /**
   * The open *file* tab, or `null` when the active tab is a note (or there is no
   * tab at all). Kept as a projection of `tabs` so consumers that only care
   * about "which file is in the editor" do not have to understand tabs.
   */
  openFile: OpenFileState | null
  /**
   * Open a file in the editor, optionally highlighting a line range.
   *
   * Reuses the tab for that path when one is already open, and never closes the
   * note tabs: jumping to code from a note leaves the note in the strip.
   */
  reveal: (path: string, start?: number, end?: number) => void
  /**
   * Jump to a definition like an IDE: opens/reuses the tab, highlights the
   * range, and pushes onto the back/forward stack. Unlike `reveal`, jumping to
   * the same place twice keeps the highlight instead of toggling it off.
   */
  navigateTo: (path: string, start?: number, end?: number) => void
  navHistory: NavHistoryEntry[]
  navIndex: number
  canGoBack: boolean
  canGoForward: boolean
  goBack: () => void
  goForward: () => void
  /** Every open tab, in strip order. */
  tabs: EditorTab[]
  activeTabIndex: number
  activateTab: (index: number) => void
  closeTab: (index: number) => void
  closeOtherTabs: (index: number) => void
  /** Reorder the strip (drag and drop); both indices must be in range. */
  moveTab: (from: number, to: number) => void
  /**
   * Range currently outlined as "you jumped here from a citation".
   *
   * Owned here rather than inside the editor pane because a local ref would be
   * reset whenever the pane remounts (opening a note tab, for example), and
   * "click the same chip again to clear it" could then never work. `null` means
   * nothing is highlighted.
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
   * Notes get the full pane rather than a cramped textarea in the sidebar: a
   * distilled knowledge point is meant to be read. This is a projection of the
   * active note tab, so `null` means "the active tab is code or nothing is open".
   */
  noteView: Note | null
  /**
   * Open a note in a tab, or update the one that is already open.
   *
   * `null` closes the active note tab, which is what the reader's close button
   * and the sidebar's "click the selected note again" both want.
   */
  openNoteView: (note: Note | null) => void
  /**
   * The graph tab's node focus, when that tab is active.
   *
   * Kept separate from `openFile`/`noteView` because the graph is neither: it is a
   * whole-project view that happens to be able to focus one node.
   */
  graphFocusNodeId: string | null
  /** Which graph tab is active, or null when neither is. */
  activeGraphScope: 'code' | 'notes' | null
  /** Open a graph tab (reusing it when already open), optionally focused on a node. */
  openGraph: (focusNodeId?: string | null, scope?: 'code' | 'notes') => void
  openConversationGraph: (projectId: number, conversationId: string, nodeId: string) => boolean
  /**
   * Ask the agent about a graph node.
   *
   * Sets `graphAsk`, which the composer picks up to seed the next message. Routing
   * through state rather than calling the chat directly keeps the graph view free
   * of conversation concerns.
   */
  askAboutNode: (ask: GraphAsk) => void
  /** Consumed once by the composer. */
  graphAsk: GraphAsk | null
  clearGraphAsk: () => void
  theme: AppTheme
  setTheme: (theme: AppTheme) => void
}

const AppContext = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }): ReactNode {
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProjectState] = useState<Project | null>(null)
  const [tabs, setTabs] = useState<EditorTab[]>([])
  const [activeTabIndex, setActiveTabIndex] = useState(0)
  const [activeConversationId, setActiveConversationIdState] = usePersistedState<string | null>(
    'codeslice.activeConversation',
    null,
  )
  const [refreshKey, setRefreshKey] = useState(0)
  const [conversationFocusNonce, setConversationFocusNonce] = useState(0)
  const [theme, setTheme] = usePersistedState<AppTheme>('codeslice.theme', 'light')
  const [storedSidebarTab, setSidebarTabState] = usePersistedState<SidebarTab>('codeslice.sidebarTab', 'files')
  /**
   * The tab id is persisted, so a build that once had a now-removed tab can
   * leave that id in localStorage. Falling back keeps the sidebar from
   * rendering nothing at all with no tab highlighted.
   */
  const sidebarTab: SidebarTab = SIDEBAR_TABS.includes(storedSidebarTab) ? storedSidebarTab : 'files'
  const [focusNoteId, setFocusNoteId] = useState<number | null>(null)
  const [navHistory, setNavHistory] = useState<NavHistoryEntry[]>([])
  const [navIndex, setNavIndex] = useState(-1)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const setProject = useCallback(
    (next: Project | null) => {
      setProjectState((prev) => {
        if (prev?.id !== next?.id) {
          setActiveConversationIdState(null)
          // Tabs belong to a project: a path from another project would 404 and a
          // note would point at code the user can no longer see.
          setTabs([])
          setActiveTabIndex(0)
          setCitationHighlight(null)
          setNavHistory([])
          setNavIndex(-1)
        }
        return next
      })
    },
    [setActiveConversationIdState],
  )

  /**
   * Toggle a citation highlight, remembering which range is currently shown.
   *
   * The highlight has to survive a pane teardown: switching to a note tab
   * unmounts the editor, so a ref inside it was reset before the second click
   * arrived and "click again to clear" never worked.
   */
  const [citationHighlight, setCitationHighlight] = useState<RevealTarget | null>(null)

  const openFileTab = useCallback(
    (path: string, highlight: { start: number; end: number } | null) => {
      setTabs((prev) => {
        const existing = prev.findIndex((tab) => tab.kind === 'file' && tab.path === path)
        if (existing >= 0) {
          const next = [...prev]
          next[existing] = { kind: 'file', path, highlight }
          setActiveTabIndex(existing)
          return next
        }
        setActiveTabIndex(prev.length)
        return [...prev, { kind: 'file', path, highlight }]
      })
    },
    [],
  )

  const reveal = useCallback(
    (path: string, start?: number, end?: number) => {
      const hasRange = Boolean((start && start > 0) || (end && end > 0))
      // Opening a file from the tree is not a citation jump. Without this guard
      // every plain open produced a "引用第 1–1 行" bar, because the missing
      // range defaulted to line 1.
      if (!hasRange) {
        openFileTab(path, null)
        setCitationHighlight(null)
        return
      }
      const from = start && start > 0 ? start : 1
      const to = end && end >= from ? end : from
      openFileTab(path, { start: from, end: to })
      setCitationHighlight((prev) => {
        const same = prev && prev.path === path && prev.start_line === from && prev.end_line === to
        // Second click on the same chip is a request to drop the highlight again.
        return same
          ? null
          : { path, start_line: from, end_line: to, symbol: null, nonce: 0, source: 'citation' }
      })
    },
    [openFileTab],
  )

  const clearCitation = useCallback(() => setCitationHighlight(null), [])

  const showNavEntry = useCallback(
    (entry: NavHistoryEntry) => {
      openFileTab(entry.path, { start: entry.start_line, end: entry.end_line })
      setCitationHighlight({
        path: entry.path,
        start_line: entry.start_line,
        end_line: entry.end_line,
        symbol: null,
        // A fresh nonce makes clicking the same definition twice re-paint the
        // range instead of looking like nothing happened.
        nonce: Date.now(),
        source: 'navigation',
      })
    },
    [openFileTab],
  )

  const navigateTo = useCallback(
    (path: string, start?: number, end?: number) => {
      const from = start && start > 0 ? start : 1
      const to = end && end >= from ? end : from
      const entry = { path, start_line: from, end_line: to }
      showNavEntry(entry)
      setNavHistory((prev) => {
        const base = navIndex >= 0 ? prev.slice(0, navIndex + 1) : []
        const last = base[base.length - 1]
        if (last && last.path === path && last.start_line === from && last.end_line === to) {
          setNavIndex(base.length - 1)
          return base
        }
        const next = [...base, entry].slice(-MAX_NAV_HISTORY)
        setNavIndex(next.length - 1)
        return next
      })
    },
    [navIndex, showNavEntry],
  )

  const goBack = useCallback(() => {
    if (navIndex <= 0) return
    const target = navHistory[navIndex - 1]
    if (!target) return
    setNavIndex(navIndex - 1)
    showNavEntry(target)
  }, [navHistory, navIndex, showNavEntry])

  const goForward = useCallback(() => {
    if (navIndex < 0 || navIndex >= navHistory.length - 1) return
    const target = navHistory[navIndex + 1]
    if (!target) return
    setNavIndex(navIndex + 1)
    showNavEntry(target)
  }, [navHistory, navIndex, showNavEntry])

  const canGoBack = navIndex > 0
  const canGoForward = navIndex >= 0 && navIndex < navHistory.length - 1

  const bumpRefresh = useCallback(() => setRefreshKey((n) => n + 1), [])

  const setActiveConversationId = useCallback(
    (id: string | null) => setActiveConversationIdState(id),
    [setActiveConversationIdState],
  )

  const setSidebarTab = useCallback((next: SidebarTab) => setSidebarTabState(next), [setSidebarTabState])

  const activateTab = useCallback((index: number) => {
    setTabs((prev) => {
      setActiveTabIndex(Math.max(0, Math.min(index, prev.length - 1)))
      return prev
    })
  }, [])

  /**
   * Close one tab and pick a neighbour to focus.
   *
   * The neighbour is the tab that took its place (so closing a middle tab lands
   * on the next one), falling back to the previous when the last tab closes.
   */
  const closeTab = useCallback((index: number) => {
    setTabs((prev) => {
      if (index < 0 || index >= prev.length) return prev
      const next = prev.filter((_, position) => position !== index)
      setActiveTabIndex((current) => {
        if (next.length === 0) return 0
        if (index < current) return current - 1
        if (index > current) return current
        return Math.min(index, next.length - 1)
      })
      return next
    })
  }, [])

  const closeOtherTabs = useCallback((index: number) => {
    setTabs((prev) => {
      const keep = prev[index]
      if (!keep) return prev
      setActiveTabIndex(0)
      return [keep]
    })
  }, [])

  const moveTab = useCallback((from: number, to: number) => {
    setTabs((prev) => {
      if (from === to || from < 0 || to < 0 || from >= prev.length || to >= prev.length) return prev
      const next = [...prev]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      // Follow the tab the user just dragged.
      setActiveTabIndex((current) => {
        if (current === from) return to
        if (from < current && to >= current) return current - 1
        if (from > current && to <= current) return current + 1
        return current
      })
      return next
    })
  }, [])

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

  const focusNote = useCallback(
    (noteId: number | null) => {
      setFocusNoteId(noteId)
      if (noteId !== null) setSidebarTabState('notes')
    },
    [setSidebarTabState],
  )

  // A ref mirror of the active index so the tab callbacks stay referentially
  // stable instead of being re-created on every tab change.
  const activeTabIndexRef = useRef(activeTabIndex)
  activeTabIndexRef.current = activeTabIndex

  const openNoteView = useCallback(
    (note: Note | null) => {
      if (!note) {
        // Close whichever note tab is active; a code tab is left alone.
        setTabs((prev) => {
          const index = activeTabIndexRef.current
          const current = prev[index]
          if (!current || current.kind !== 'note') return prev
          const next = prev.filter((_, position) => position !== index)
          setActiveTabIndex(next.length === 0 ? 0 : Math.min(index, next.length - 1))
          return next
        })
        return
      }
      setFocusNoteId(note.id)
      setTabs((prev) => {
        const existing = prev.findIndex((tab) => tab.kind === 'note' && tab.note.id === note.id)
        if (existing >= 0) {
          const next = [...prev]
          next[existing] = { kind: 'note', note }
          setActiveTabIndex(existing)
          return next
        }
        setActiveTabIndex(prev.length)
        return [...prev, { kind: 'note', note }]
      })
    },
    [],
  )

  const activeTab = tabs[activeTabIndex] ?? null
  const openFile = useMemo<OpenFileState | null>(
    () =>
      activeTab && activeTab.kind === 'file'
        ? { path: activeTab.path, highlight: activeTab.highlight }
        : null,
    [activeTab],
  )
  const noteView = activeTab && activeTab.kind === 'note' ? activeTab.note : null
  const graphFocusNodeId = activeTab && activeTab.kind === 'graph' ? activeTab.focusNodeId : null
  const activeGraphScope = activeTab && activeTab.kind === 'graph' ? activeTab.scope : null

  /**
   * Open (or focus) a graph tab.
   *
   * Passing a node id focuses it, which is how a citation or a "show me this
   * symbol" jump arrives here. The scope picks which graph; each is a separate tab
   * so both can be open at once.
   */
  const openGraph = useCallback((focusNodeId: string | null = null, scope: 'code' | 'notes' = 'code') => {
    setTabs((prev) => {
      const existing = prev.findIndex((tab) => tab.kind === 'graph' && tab.scope === scope)
      if (existing >= 0) {
        const next = [...prev]
        next[existing] =
          scope === 'code'
            ? { kind: 'graph', scope, focusNodeId, focusNonce: Date.now() }
            : { kind: 'graph', scope, focusNodeId: null }
        setActiveTabIndex(existing)
        return next
      }
      setActiveTabIndex(prev.length)
      return [
        ...prev,
        scope === 'code'
          ? { kind: 'graph' as const, scope, focusNodeId, focusNonce: Date.now() }
          : { kind: 'graph' as const, scope, focusNodeId: null },
      ]
    })
  }, [])

  const [graphAsk, setGraphAsk] = useState<GraphAsk | null>(null)
  const openConversationGraph = useCallback((projectId: number, conversationId: string, nodeId: string) => {
    const owner = project?.id === projectId ? project : projects.find(item => item.id === projectId)
    if (!owner) return false
    if (project?.id !== projectId) {
      setProjectState(owner)
      setTabs([{ kind: 'graph', scope: 'code', focusNodeId: nodeId, focusNonce: Date.now() }])
      setActiveTabIndex(0)
      setCitationHighlight(null)
      setNavHistory([])
      setNavIndex(-1)
    } else openGraph(nodeId, 'code')
    setActiveConversationIdState(conversationId)
    setGraphAsk(null)
    return true
  }, [projects, project?.id, openGraph, setActiveConversationIdState])
  const askAboutNode = useCallback((ask: GraphAsk) => setGraphAsk(ask), [])
  const clearGraphAsk = useCallback(() => setGraphAsk(null), [])

  const value = useMemo<AppState>(
    () => ({
      project,
      projects,
      setProject,
      setProjects,
      openFile,
      reveal,
      navigateTo,
      navHistory,
      navIndex,
      canGoBack,
      canGoForward,
      goBack,
      goForward,
      tabs,
      activeTabIndex,
      activateTab,
      closeTab,
      closeOtherTabs,
      moveTab,
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
      graphFocusNodeId,
      activeGraphScope,
      openGraph,
      openConversationGraph,
      askAboutNode,
      graphAsk,
      clearGraphAsk,
      theme,
      setTheme,
    }),
    [
      project,
      projects,
      setProject,
      openFile,
      reveal,
      navigateTo,
      navHistory,
      navIndex,
      canGoBack,
      canGoForward,
      goBack,
      goForward,
      tabs,
      activeTabIndex,
      activateTab,
      closeTab,
      closeOtherTabs,
      moveTab,
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
      graphFocusNodeId,
      activeGraphScope,
      openGraph,
      openConversationGraph,
      askAboutNode,
      graphAsk,
      clearGraphAsk,
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
