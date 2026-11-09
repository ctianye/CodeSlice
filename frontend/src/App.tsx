import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import * as api from './api'
import { usePersistedState } from './hooks/useAsync'
import { ChatPane } from './components/ChatPane'
import { CodePane } from './components/CodePane'
import type { SnippetSelection } from './components/CodePane'
import { EditorTabs } from './components/EditorTabs'
import { NoteReader } from './components/NoteReader'
import { GraphView } from './components/graph/GraphView'
import { SettingsModal } from './components/SettingsModal'
import { ReleaseNotice } from './components/ReleaseNotice'
import { checkForRelease } from './lib/releases'
import type { AppRelease } from './lib/releases'
import { Sidebar } from './components/Sidebar'
import { Splitter } from './components/Splitter'
import { TerminalPanel } from './components/TerminalPanel'
import { TopBar } from './components/TopBar'
import { WelcomePage } from './components/WelcomePage'
import { useApp } from './state/AppState'

/**
 * App shell: top bar + three panes (sidebar / editor / chat) joined by
 * draggable splitters. Shows the welcome screen while no project is open.
 *
 * The editor pane is a tab strip plus one body. A note opened from the sidebar
 * becomes a tab like any file, so jumping from a note to the code it quotes
 * leaves the note reachable instead of destroying it.
 */
export function App(): ReactNode {
  const {
    project,
    setProjects,
    setProject,
    setActiveConversationId,
    noteView,
    graphFocusNodeId,
    activeGraphScope,
    tabs,
    activeTabIndex,
  } = useApp()
  const graphTabActive = tabs[activeTabIndex]?.kind === 'graph'
  const [showWelcome, setShowWelcome] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [newRelease, setNewRelease] = useState<AppRelease | null>(null)
  const [snippet, setSnippet] = useState<SnippetSelection | null>(null)
  const [sidebarEpoch, setSidebarEpoch] = useState(0)
  const [terminalOpen, setTerminalOpen] = usePersistedState('codeslice.terminalOpen', false)

  const [sidebarWidth, setSidebarWidth] = usePersistedState('codeslice.sidebarWidth', 268)
  const [chatWidth, setChatWidth] = usePersistedState('codeslice.chatWidth', 430)

  useEffect(() => {
    const controller = new AbortController()
    void checkForRelease(controller.signal).then(({ release }) => {
      if (!controller.signal.aborted) setNewRelease(release)
    }).catch(() => { /* Automatic checks stay quiet offline; About offers retry. */ })
    return () => controller.abort()
  }, [])

  // Load the project list once so a restart can restore the last project.
  useEffect(() => {
    let cancelled = false
    api
      .listProjects()
      .then((list) => {
        if (cancelled) return
        setProjects(list)
        const lastId = readLastProjectId()
        const restored = list.find((item) => item.id === lastId)
        if (restored) {
          setProject(restored)
          // Restoring the last project must go through the open lifecycle too:
          // the backend starts its file watcher and backfills missing vectors.
          // Without this call a restarted app never runs either.
          void api.openProject(restored.id)
            .then((opened) => {
              if (!cancelled) setProject(opened)
            })
            .catch(() => {
              /* the project may have moved; the editor will surface it */
            })
        }
      })
      .catch(() => {
        /* the welcome screen surfaces its own error */
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Remember which project was last open.
  useEffect(() => {
    if (!project) return
    try {
      localStorage.setItem('codeslice.lastProjectId', String(project.id))
    } catch {
      /* storage may be unavailable */
    }
  }, [project])

  /** Ask the sidebar to show the file tab again. */
  const openFileList = () => {
    try {
      localStorage.setItem('codeslice.sidebarTab', JSON.stringify('files'))
    } catch {
      /* ignore */
    }
    setSidebarEpoch((n) => n + 1)
  }

  const openSettings = () => setSettingsOpen(true)

  if (!project || showWelcome) {
    return (
      <div className="app-shell">
        <TopBar onOpenSettings={openSettings} onOpenWelcome={() => setShowWelcome(true)} />
        {project ? (
          <div className="welcome-return">
            <span>当前项目：{project.name}</span>
            <button className="small ghost" onClick={() => setShowWelcome(false)}>
              返回当前项目
            </button>
          </div>
        ) : null}
        <WelcomePage onProjectOpened={() => setShowWelcome(false)} />
        {settingsOpen ? <SettingsModal onClose={() => setSettingsOpen(false)} /> : null}
        {newRelease ? <ReleaseNotice release={newRelease} onClose={() => setNewRelease(null)} /> : null}
      </div>
    )
  }

  return (
    <div className="app-shell">
      <TopBar onOpenSettings={openSettings} onOpenWelcome={() => setShowWelcome(true)} />

      <div className="workspace">
        <div className="pane" style={{ flex: `0 0 ${sidebarWidth}px` }}>
          <Sidebar key={sidebarEpoch} onNewConversation={() => setActiveConversationId(null)} />
        </div>

        <Splitter value={sidebarWidth} onChange={setSidebarWidth} min={200} max={520} label="调整侧边栏宽度" />

        <div className="pane editor-pane-host" style={{ flex: '1 1 auto' }}>
          {/* The strip only appears once something is open; with no tabs the
              pane falls through to the editor's own empty state. */}
          <EditorTabs onToggleTerminal={() => setTerminalOpen((open) => !open)} terminalOpen={terminalOpen} />
          <div className="editor-pane-body">
            {/* Three-way: a graph is a whole-project view, notes are read in
                place, everything else is the code editor. The two graphs are
                distinct tabs because their edges mean different things. */}
            {graphTabActive && activeGraphScope === 'code' ? (
              <GraphView key={`${project.id}:code`} initialNodeId={graphFocusNodeId}
                focusNonce={tabs[activeTabIndex]?.kind === 'graph' ? (tabs[activeTabIndex] as { focusNonce?: number }).focusNonce : 0} />
            ) : graphTabActive && activeGraphScope === 'notes' ? (
              <GraphView key={`${project.id}:notes`} scope="notes" />
            ) : noteView ? (
              <NoteReader />
            ) : (
              <CodePane onAskSelection={setSnippet} onOpenFile={openFileList} />
            )}
          </div>
          {/* The terminal docks under the editor, not under the whole shell: it
              is about the code the editor is showing. */}
          <TerminalPanel
            projectId={project.id}
            open={terminalOpen}
            onToggle={() => setTerminalOpen(false)}
          />
        </div>

        <Splitter
          value={chatWidth}
          onChange={setChatWidth}
          min={320}
          max={760}
          invert
          label="调整对话面板宽度"
        />

        <div className="pane" style={{ flex: `0 0 ${chatWidth}px` }}>
          <ChatPane snippet={snippet} onSnippetConsumed={() => setSnippet(null)} />
        </div>
      </div>

      {settingsOpen ? <SettingsModal onClose={() => setSettingsOpen(false)} /> : null}
      {newRelease ? <ReleaseNotice release={newRelease} onClose={() => setNewRelease(null)} /> : null}
    </div>
  )
}

function readLastProjectId(): number | null {
  try {
    const raw = localStorage.getItem('codeslice.lastProjectId')
    if (!raw) return null
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? parsed : null
  } catch {
    return null
  }
}
