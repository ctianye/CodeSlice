import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { ApprovalDecision } from '../types'
import { useChatStream } from '../hooks/useChatStream'
import { baseName } from '../lib/citations'
import { useApp } from '../state/AppState'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { MessageBubble } from './MessageBubble'
import { StreamingBubble } from './StreamingBubble'

interface ChatPaneProps {
  /** Set when the user asked about a code selection; creates a snippet conversation. */
  snippet: { path: string; start: number; end: number; code: string } | null
  onSnippetConsumed: () => void
}

export function ChatPane({ snippet, onSnippetConsumed }: ChatPaneProps): ReactNode {
  const { project, activeConversationId, setActiveConversationId, bumpRefresh } = useApp()
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const chat = useChatStream(activeConversationId, () => bumpRefresh())

  // A new selection turns into a snippet conversation, then the user types.
  useEffect(() => {
    if (!snippet || !project) return
    let cancelled = false
    setCreating(true)
    setCreateError(null)
    api
      .createConversation({
        project_id: project.id,
        title: `关于 ${baseName(snippet.path)} 第 ${snippet.start}-${snippet.end} 行`,
        file_path: snippet.path,
        start_line: snippet.start,
        end_line: snippet.end,
        selected_code: snippet.code,
        mode: 'snippet',
      })
      .then((conversation) => {
        if (cancelled) return
        setActiveConversationId(conversation.id)
        onSnippetConsumed()
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setCreateError(err instanceof Error ? err.message : String(err))
        onSnippetConsumed()
      })
      .finally(() => {
        if (!cancelled) setCreating(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snippet, project?.id])

  // Keep the newest content in view while streaming.
  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [chat.messages.length, chat.stream?.text, chat.interrupt])

  const startProjectConversation = () => {
    if (!project) return
    setCreating(true)
    setCreateError(null)
    api
      .createConversation({ project_id: project.id, mode: 'project', title: '全项目对话' })
      .then((conversation) => setActiveConversationId(conversation.id))
      .catch((err: unknown) => setCreateError(err instanceof Error ? err.message : String(err)))
      .finally(() => setCreating(false))
  }

  if (!project) {
    return (
      <div className="chat-pane">
        <div className="chat-header">
          <span className="title">对话</span>
        </div>
        <EmptyState title="尚未选择项目" hint="添加或选择一个项目后即可开始提问。" />
      </div>
    )
  }

  const conversation = chat.conversation
  const snippetHint =
    conversation?.mode === 'snippet' && conversation.file_path
      ? `${baseName(conversation.file_path)} 第 ${conversation.start_line ?? '?'}-${conversation.end_line ?? '?'} 行`
      : null

  return (
    <div className="chat-pane">
      <div className="chat-header">
        <span className="title">{conversation ? conversation.title : '对话'}</span>
        {conversation ? (
          <span className="mode-pill">{conversation.mode === 'snippet' ? '片段' : '全项目'}</span>
        ) : null}
        <button className="small ghost" onClick={startProjectConversation} disabled={creating}>
          新对话
        </button>
      </div>

      {createError ? <ErrorNote message={createError} /> : null}

      <div className="chat-scroll" ref={scrollRef}>
        {!activeConversationId ? (
          <EmptyState title="开始一次新的阅读对话" hint="选中代码后点击「就这段提问」，或直接新建一个全项目对话。">
            <button className="primary" onClick={startProjectConversation} disabled={creating}>
              {creating ? '创建中…' : '新建全项目对话'}
            </button>
          </EmptyState>
        ) : null}

        {chat.loading ? <Loading text="正在加载对话…" /> : null}
        {chat.loadError ? <ErrorNote message={chat.loadError} onRetry={chat.reload} /> : null}

        {chat.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {chat.stream ? <StreamingBubble state={chat.stream} /> : null}

        {chat.interrupt ? (
          <ApprovalCard
            interrupt={chat.interrupt}
            busy={chat.busy}
            onDecide={(decision: ApprovalDecision) => chat.resume(decision)}
          />
        ) : null}
      </div>

      <Composer
        busy={chat.busy}
        disabled={!activeConversationId || chat.busy}
        selectionHint={snippetHint ? `片段模式 · ${snippetHint}` : null}
        onSend={chat.send}
        onStop={chat.stop}
      />
    </div>
  )
}
