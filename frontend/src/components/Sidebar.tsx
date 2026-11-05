import type { ReactNode } from 'react'
import { usePersistedState } from '../hooks/useAsync'
import { AnchorsPanel } from './AnchorsPanel'
import { ConversationList } from './ConversationList'
import { FilePanel } from './FilePanel'
import { NotesPanel } from './NotesPanel'

type Tab = 'files' | 'chats' | 'notes' | 'anchors'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'files', label: '文件' },
  { id: 'chats', label: '对话' },
  { id: 'notes', label: '笔记' },
  { id: 'anchors', label: '锚点' },
]

interface SidebarProps {
  onNewConversation: () => void
}

/** Left pane: file tree / conversations / notes / anchors behind tabs. */
export function Sidebar({ onNewConversation }: SidebarProps): ReactNode {
  const [tab, setTab] = usePersistedState<Tab>('codeslice.sidebarTab', 'files')

  return (
    <div className="sidebar">
      <div className="sidebar-tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? 'active' : ''}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'files' ? <FilePanel /> : null}
      {tab === 'chats' ? (
        <div className="sidebar-section">
          <div className="sidebar-scroll">
            <ConversationList onNewConversation={onNewConversation} />
          </div>
        </div>
      ) : null}
      {tab === 'notes' ? (
        <div className="sidebar-section">
          <div className="sidebar-scroll">
            <NotesPanel />
          </div>
        </div>
      ) : null}
      {tab === 'anchors' ? (
        <div className="sidebar-section">
          <div className="sidebar-scroll">
            <AnchorsPanel />
          </div>
        </div>
      ) : null}
    </div>
  )
}
