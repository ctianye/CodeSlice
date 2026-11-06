import type { ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api'
import type { Conversation } from '../types'
import { useAsync } from '../hooks/useAsync'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

interface ConversationListProps {
  onNewConversation: () => void
}

/**
 * Conversations grouped as "this project" first, other projects below.
 *
 * One request carries both groups (`include_others`) so the split is done by the
 * server in a single ordered query — no double fetch, no client-side sort that
 * could disagree with the pagination of a future version.
 */
export function ConversationList({ onNewConversation }: ConversationListProps): ReactNode {
  const {
    project,
    projects,
    activeConversationId,
    setActiveConversationId,
    conversationFocusNonce,
    refreshKey,
    bumpRefresh,
  } = useApp()
  const projectId = project?.id ?? null
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const { data, error, loading, reload } = useAsync(
    (signal) =>
      projectId ? api.listConversations(projectId, signal, { includeOthers: true }) : Promise.resolve([]),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const items = data ?? []

  const { mine, others } = useMemo(() => {
    const local: Conversation[] = []
    const foreign: Conversation[] = []
    for (const item of items) {
      if (item.project_id === projectId) local.push(item)
      else foreign.push(item)
    }
    return { mine: local, others: foreign }
  }, [items, projectId])

  const projectNames = useMemo(() => {
    const map = new Map<number, string>()
    for (const meta of projects) map.set(meta.id, meta.name)
    for (const item of items) {
      if (item.project_name && !map.has(item.project_id)) map.set(item.project_id, item.project_name)
    }
    return map
  }, [projects, items])

  const removeDeleted = (deletedId: string) => {
    if (deletedId === activeConversationId) setActiveConversationId(null)
    bumpRefresh()
    reload()
  }

  // A code-card title asked for its conversation: bring the row into view so the
  // click has a visible result even in a long list.
  useEffect(() => {
    if (conversationFocusNonce === 0 || !activeConversationId) return
    const host = scrollRef.current
    if (!host) return
    const row = host.querySelector<HTMLElement>(`[data-conversation-id="${CSS.escape(activeConversationId)}"]`)
    row?.scrollIntoView({ block: 'nearest' })
  }, [conversationFocusNonce, activeConversationId, items])

  const renderRow = (item: Conversation, showProject: boolean) => (
    <ConversationRow
      key={item.id}
      conversation={item}
      projectName={showProject ? (projectNames.get(item.project_id) ?? '其他项目') : null}
      active={item.id === activeConversationId}
      onSelect={() => setActiveConversationId(item.id)}
      onDeleted={() => removeDeleted(item.id)}
    />
  )

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button type="button" className="small" onClick={onNewConversation} title="开始一次新的对话">
          <Icon name="message-plus" size={12} />
          新建对话
        </button>
        <span className="spacer" />
        <span className="tag" title="对话数量">
          {items.length}
        </span>
        <button
          type="button"
          className="icon"
          onClick={reload}
          title="刷新对话列表"
          aria-label="刷新对话列表"
        >
          <Icon name="refresh" size={13} />
        </button>
      </div>

      {projectId === null ? (
        <EmptyState icon="message" title="还没有打开项目" hint="先添加一个本地代码目录，再开始对话。" />
      ) : null}

      <div className="sidebar-scroll" ref={scrollRef}>
        {loading && !data ? <Loading /> : null}
        {error ? <ErrorNote message={error} onRetry={reload} /> : null}

        {!loading && !error && items.length === 0 ? (
          <EmptyState
            icon="message"
            title="还没有对话"
            hint="在编辑器中选中代码后点击「就这段提问」，或直接新建一次全项目对话。"
          >
            <button type="button" className="small primary" onClick={onNewConversation}>
              新建对话
            </button>
          </EmptyState>
        ) : null}

        {mine.length > 0 ? (
          <>
            <div className="list-section-head" title={project?.name ?? undefined}>
              <span className="label">本项目对话</span>
              <span className="count">{mine.length}</span>
            </div>
            {mine.map((item) => renderRow(item, false))}
          </>
        ) : null}

        {others.length > 0 ? (
          <>
            <div className="list-section-head divided" title="其他项目里的对话">
              <span className="label">其他对话</span>
              <span className="count">{others.length}</span>
            </div>
            {others.map((item) => renderRow(item, true))}
          </>
        ) : null}
      </div>
    </div>
  )
}

interface ConversationRowProps {
  conversation: Conversation
  /** Shown in the subtitle when the row belongs to another project. */
  projectName: string | null
  active: boolean
  onSelect: () => void
  onDeleted: () => void
}

function ConversationRow({
  conversation,
  projectName,
  active,
  onSelect,
  onDeleted,
}: ConversationRowProps): ReactNode {
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

  const mode = conversation.mode === 'snippet' ? '片段' : '全项目'
  const location = conversation.file_path
    ? `${conversation.file_path}${
        conversation.start_line ? `:L${conversation.start_line}` : ''
      }`
    : ''

  const parts = [mode]
  if (projectName) parts.push(projectName)
  if (location) parts.push(location)
  parts.push(formatRelative(conversation.updated_at))

  return (
    <div
      className={`list-row${active ? ' active' : ''}`}
      data-conversation-id={conversation.id}
      role="button"
      tabIndex={0}
      aria-current={active ? 'true' : undefined}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect()
        }
      }}
      title={location || conversation.title}
    >
      <span className="row-icon">
        <Icon name={conversation.mode === 'snippet' ? 'code' : 'book'} size={13} />
      </span>

      <div className="body">
        <div className="title">{conversation.title}</div>
        <div className="sub">
          {parts.join(' · ')}
          {conversation.has_note ? (
            <span className="note-dot" title="已有笔记">
              <Icon name="notes" size={10} />
            </span>
          ) : null}
        </div>
      </div>

      <div className="row-actions">
        <button
          type="button"
          className="icon del"
          disabled={deleting}
          onClick={remove}
          title="删除对话"
          aria-label={`删除对话 ${conversation.title}`}
        >
          <Icon name="trash" size={12} />
        </button>
      </div>
    </div>
  )
}
