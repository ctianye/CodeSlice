import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { ApprovalDecision, Message } from '../types'
import { useChatStream } from '../hooks/useChatStream'
import { baseName } from '../lib/citations'
import { useApp } from '../state/AppState'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'
import { MessageBubble } from './MessageBubble'
import { StreamingBubble } from './StreamingBubble'

interface ChatPaneProps {
  /** Set when the user asked about a code selection; creates a snippet conversation. */
  snippet: { path: string; start: number; end: number; code: string } | null
  onSnippetConsumed: () => void
}

/** Starter prompts for an empty project conversation. */
const SUGGESTIONS = [
  '这个项目的入口在哪里？整体是怎么分层的？',
  '解释一下 Agent 的审批流程是怎么实现的',
  '检索和索引部分用了什么方案？',
]

export function ChatPane({ snippet, onSnippetConsumed }: ChatPaneProps): ReactNode {
  const { project, activeConversationId, setActiveConversationId, bumpRefresh, focusNote } = useApp()
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null)
  const [noteBusyId, setNoteBusyId] = useState<string | null>(null)
  const [noteError, setNoteError] = useState<string | null>(null)
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
        // Refresh so the new snippet shows up as a card in the editor right away.
        bumpRefresh()
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

  const startProjectConversation = (seed?: string) => {
    if (!project) return
    setCreating(true)
    setCreateError(null)
    api
      .createConversation({ project_id: project.id, mode: 'project', title: '全项目对话' })
      .then((conversation) => {
        setActiveConversationId(conversation.id)
        if (seed) setDraft({ text: seed, nonce: Date.now() })
      })
      .catch((err: unknown) => setCreateError(err instanceof Error ? err.message : String(err)))
      .finally(() => setCreating(false))
  }

  /**
   * Turn the newest answer into a note.
   *
   * The note comes back already indexed, so the reply is reachable from project
   * search right away; the notes panel is opened on it so the user sees what was
   * written. When the model fails the backend still saves the raw answer and
   * flags `summary_failed`, which we surface instead of pretending it worked.
   */
  const generateNote = async (message: Message) => {
    if (!activeConversationId) return
    setNoteBusyId(message.id)
    setNoteError(null)
    try {
      const note = await api.createNoteFromMessage(activeConversationId, message.id)
      bumpRefresh()
      focusNote(note.id)
      if (note.summary_failed) setNoteError(`笔记已保存，但模型整理失败：${note.summary_error ?? '未知原因'}`)
    } catch (err) {
      setNoteError(err instanceof Error ? err.message : String(err))
    } finally {
      setNoteBusyId(null)
    }
  }

  const lastAssistantId = (() => {
    for (let i = chat.messages.length - 1; i >= 0; i -= 1) {
      if (chat.messages[i].role === 'assistant') return chat.messages[i].id
    }
    return null
  })()

  if (!project) {
    return (
      <div className="chat-pane">
        <div className="chat-header">
          <span className="title">对话</span>
        </div>
        <EmptyState icon="message" title="尚未选择项目" hint="添加或选择一个项目后即可开始提问。" />
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
        <span className="title" title={conversation?.title}>
          {conversation ? conversation.title : '对话'}
        </span>
        {conversation ? (
          <span className={`mode-pill${conversation.mode === 'project' ? ' project' : ''}`}>
            <Icon name={conversation.mode === 'snippet' ? 'code' : 'folder'} size={9} />
            {conversation.mode === 'snippet' ? '片段' : '全项目'}
          </span>
        ) : null}
        <span className="spacer" />
        <div className="header-actions">
          <button
            className="ghost icon"
            title="新建全项目对话"
            aria-label="新建全项目对话"
            onClick={() => startProjectConversation()}
            disabled={creating}
            type="button"
          >
            <Icon name="message-plus" size={14} />
          </button>
        </div>
      </div>

      {createError ? <ErrorNote message={createError} /> : null}
      {noteError ? <ErrorNote message={noteError} /> : null}

      <div className="chat-scroll" ref={scrollRef}>
        {!activeConversationId ? (
          <div className="chat-empty">
            <span className="empty-mark">
              <Icon name="sparkles" size={20} />
            </span>
            <span className="empty-title">开始一次新的阅读对话</span>
            <span className="empty-hint">选中代码后点「就这段提问」，或直接从这些问题开始。</span>
            <div className="suggest">
              {SUGGESTIONS.map((text) => (
                <button
                  key={text}
                  onClick={() => startProjectConversation(text)}
                  disabled={creating}
                  type="button"
                >
                  <Icon name="arrow-right" size={11} />
                  {text}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {chat.loading ? <Loading text="正在加载对话…" /> : null}
        {chat.loadError ? <ErrorNote message={chat.loadError} onRetry={chat.reload} /> : null}

        {chat.messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            onGenerateNote={
              message.id === lastAssistantId ? () => void generateNote(message) : undefined
            }
            noteBusy={noteBusyId === message.id}
          />
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
        draft={draft}
        onSend={chat.send}
        onStop={chat.stop}
      />
    </div>
  )
}
