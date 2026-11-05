import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Conversation } from '../types'
import { useAsync } from '../hooks/useAsync'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'

interface ConversationListProps {
  onNewConversation: () => void
}

export function ConversationList({ onNewConversation }: ConversationListProps): ReactNode {
  const { project, activeConversationId, setActiveConversationId, refreshKey, bumpRefresh } = useApp()
  const projectId = project?.id ?? null

  const { data, error, loading, reload } = useAsync(
    (signal) => (projectId ? api.listConversations(projectId, signal) : Promise.resolve([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  if (projectId === null) return <div className="list-empty">请先选择项目。</div>
  if (loading && !data) return <Loading />
  if (error) return <ErrorNote message={error} onRetry={reload} />

  const items = data ?? []
  if (items.length === 0) {
    return (
      <div className="list-empty">
        还没有对话。
        <br />
        在编辑器中选中代码后点击「就这段提问」，或直接新建一次对话。
        <div style={{ marginTop: 10 }}>
          <button className="small primary" onClick={onNewConversation}>
            新建对话
          </button>
        </div>
      </div>
    )
  }

  return (
    <div>
      {items.map((item) => (
        <ConversationRow
          key={item.id}
          conversation={item}
          active={item.id === activeConversationId}
          onSelect={() => setActiveConversationId(item.id)}
          onDeleted={() => {
            if (item.id === activeConversationId) setActiveConversationId(null)
            bumpRefresh()
            reload()
          }}
        />
      ))}
      <div style={{ padding: '10px 10px 0' }}>
        <button className="small" onClick={reload}>
          刷新列表
        </button>
      </div>
    </div>
  )
}

interface ConversationRowProps {
  conversation: Conversation
  active: boolean
  onSelect: () => void
  onDeleted: () => void
}

function ConversationRow({ conversation, active, onSelect, onDeleted }: ConversationRowProps): ReactNode {
  const [deleting, setDeleting] = useState(false)

  const remove = async (event: React.MouseEvent) => {
    event.stopPropagation()
    if (!window.confirm(`删除对话「${conversation.title}」？此操作不可撤销。`)) return
    setDeleting(true)
    try {
      await api.deleteConversation(conversation.id)
      onDeleted()
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err))
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className={`list-row${active ? ' active' : ''}`} onClick={onSelect}>
      <div className="body">
        <div className="title">{conversation.title}</div>
        <div className="sub">
          {conversation.mode === 'snippet' ? `片段 · ${conversation.file_path ?? ''}` : '全项目'} ·{' '}
          {formatRelative(conversation.updated_at)}
        </div>
      </div>
      <div className="row-actions">
        <button className="small ghost danger" disabled={deleting} onClick={remove} title="删除对话">
          删
        </button>
      </div>
    </div>
  )
}
