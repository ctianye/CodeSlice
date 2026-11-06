import type { ReactNode } from 'react'
import { useApp } from '../state/AppState'
import type { SidebarTab } from '../state/AppState'
import { AnchorsPanel } from './AnchorsPanel'
import { ConversationList } from './ConversationList'
import { FilePanel } from './FilePanel'
import { GraphPanel } from './GraphPanel'
import { Icon } from './Icon'
import type { IconName } from './Icon'
import { NotesPanel } from './NotesPanel'

const TABS: Array<{ id: SidebarTab; label: string; icon: IconName; hint: string }> = [
  { id: 'files', label: '文件', icon: 'folder', hint: '文件树与项目搜索' },
  { id: 'chats', label: '对话', icon: 'message', hint: '本项目与其他项目的对话' },
  { id: 'notes', label: '笔记', icon: 'notes', hint: '阅读笔记' },
  { id: 'anchors', label: '锚点', icon: 'anchor', hint: '代码锚点状态' },
  { id: 'graph', label: '工作流', icon: 'workflow', hint: 'Agent 流程与节点设置' },
]

interface SidebarProps {
  onNewConversation: () => void
}

/**
 * Left pane: file tree / conversations / notes / anchors behind a tab rail.
 *
 * Each panel owns its own `.sidebar-section` (toolbar + scroll region) so the
 * toolbar of one tab never scrolls away with another tab's content.
 *
 * The active tab lives in AppState (persisted) rather than here, because a code
 * card in the editor needs to switch the sidebar to a note it just opened.
 */
export function Sidebar({ onNewConversation }: SidebarProps): ReactNode {
  const { sidebarTab, setSidebarTab } = useApp()

  return (
    <div className="sidebar">
      <div className="sidebar-tabs" role="tablist" aria-label="侧边栏视图">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={sidebarTab === item.id}
            className={sidebarTab === item.id ? 'active' : ''}
            title={item.hint}
            onClick={() => setSidebarTab(item.id)}
          >
            <Icon name={item.icon} size={14} />
            {item.label}
          </button>
        ))}
      </div>

      {sidebarTab === 'files' ? <FilePanel /> : null}
      {sidebarTab === 'chats' ? <ConversationList onNewConversation={onNewConversation} /> : null}
      {sidebarTab === 'notes' ? <NotesPanel /> : null}
      {sidebarTab === 'anchors' ? <AnchorsPanel /> : null}
      {sidebarTab === 'graph' ? <GraphPanel onClose={() => setSidebarTab('files')} /> : null}
    </div>
  )
}
