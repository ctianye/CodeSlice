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
import { ConversationGraphLink } from './graph/ConversationGraphLink'

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
  const { project, activeConversationId, setActiveConversationId, bumpRefresh, openNoteView, graphAsk, clearGraphAsk } =
    useApp()
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ text: string; nonce: number } | null>(null)
  const [pendingSendVersion, setPendingSendVersion] = useState(0)
  const [noteBusyId, setNoteBusyId] = useState<string | null>(null)
  const [noteError, setNoteError] = useState<string | null>(null)
  const [wholeNoteBusy, setWholeNoteBusy] = useState(false)
  // Off by default so a snippet answer stays on the selected lines; the user
  // opts in per turn when the question needs the surrounding file.
  const [useContext, setUseContext] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const creatingConversationRef = useRef<Promise<string> | null>(null)
  const pendingSendRef = useRef<{ conversationId: string; text: string; anchor?: string | null } | null>(null)
  /** Graph node id queued by "ask about this node", consumed by the next send. */
  const pendingAnchorRef = useRef<string | null>(null)

  const chat = useChatStream(activeConversationId, () => bumpRefresh())

  useEffect(() => {
    pendingAnchorRef.current = null
    clearGraphAsk()
    setDraft(null)
  }, [activeConversationId, project?.id, clearGraphAsk])

  // "Ask about this node" from the graph view: put a ready-made question in the
  // composer and remember which node it is about. Marking the anchor here (rather
  // than at send time) is what ties the two together when the user edits the text.
  useEffect(() => {
    if (!graphAsk) return
    pendingAnchorRef.current = graphAsk.node_id
    const where = graphAsk.path && graphAsk.path.startsWith('note://')
      ? `笔记「${graphAsk.label}」`
      : `\`${graphAsk.label}\`${graphAsk.path ? `（${graphAsk.path}:${graphAsk.start_line}）` : ''}`
    setDraft({
      text: `请结合知识图谱说明 ${where} 的作用、它和其他部分的联系，以及调用它的地方。`,
      nonce: Date.now(),
    })
  }, [graphAsk])

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
        draftsRef.current.add(conversation.id)
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

  /**
   * Drop a conversation the user opened but never typed into.
   *
   * "新建对话" has to create a row up front so there is something to send into,
   * which leaves an untouched "全项目对话" behind as soon as the user clicks
   * another conversation. Every conversation created here is marked as a draft
   * and unmarked on its first send, so switching away from a still-empty draft
   * removes it. The backend additionally sweeps old leftovers on startup.
   */
  const draftsRef = useRef<Set<string>>(new Set())
  const activeRef = useRef<string | null>(null)

  useEffect(() => {
    const previous = activeRef.current
    activeRef.current = activeConversationId
    if (!previous || previous === activeConversationId) return
    if (!draftsRef.current.delete(previous)) return
    void api
      .deleteConversation(previous)
      .then(() => bumpRefresh())
      .catch(() => {
        /* already gone, or an approval is pending — harmless either way */
      })
  }, [activeConversationId, bumpRefresh])

  const createProjectConversation = (): Promise<string> => {
    if (!project) return Promise.reject(new Error('请先选择一个项目'))

    setCreating(true)
    setCreateError(null)
    const request = api
      .createConversation({ project_id: project.id, mode: 'project', title: '全项目对话' })
      .then((conversation) => {
        draftsRef.current.add(conversation.id)
        setActiveConversationId(conversation.id)
        return conversation.id
      })
      .catch((err: unknown) => {
        setCreateError(err instanceof Error ? err.message : String(err))
        throw err
      })
      .finally(() => {
        creatingConversationRef.current = null
        setCreating(false)
      })
    creatingConversationRef.current = request
    return request
  }

  const ensureProjectConversation = (): Promise<string> => {
    if (activeConversationId) return Promise.resolve(activeConversationId)
    if (creatingConversationRef.current) return creatingConversationRef.current
    return createProjectConversation()
  }

  const startProjectConversation = (seed?: string) => {
    void createProjectConversation()
      .then(() => {
        if (seed) setDraft({ text: seed, nonce: Date.now() })
      })
      .catch(() => undefined)
  }

  useEffect(() => {
    const pending = pendingSendRef.current
    if (!pending || chat.conversation?.id !== pending.conversationId) return
    pendingSendRef.current = null
    draftsRef.current.delete(pending.conversationId)
    chat.send(pending.text, { graphAnchor: pending.anchor ?? null })
  }, [activeConversationId, chat.conversation, chat.send, pendingSendVersion])

  const sendMessage = (text: string) => {
    // A pending graph-node question anchors the turn to that node, so the
    // backend can render its neighbourhood instead of the model guessing.
    const anchor = pendingAnchorRef.current
    pendingAnchorRef.current = null
    if (anchor) clearGraphAsk()

    if (activeConversationId) {
      draftsRef.current.delete(activeConversationId)
      chat.send(text, {
        useContext: useContext && Boolean(snippetHint),
        graphAnchor: anchor,
      })
      return
    }

    void ensureProjectConversation()
      .then((conversationId) => {
        pendingSendRef.current = { conversationId, text, anchor }
        setPendingSendVersion((version) => version + 1)
      })
      .catch(() => undefined)
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
      openNoteView(note)
      if (note.summary_failed) setNoteError(`笔记已保存，但模型整理失败：${note.summary_error ?? '未知原因'}`)
    } catch (err) {
      setNoteError(err instanceof Error ? err.message : String(err))
    } finally {
      setNoteBusyId(null)
    }
  }

  /**
   * Summarise the whole conversation, not just its newest answer.
   *
   * A long reading session drifts across several files, so a per-answer note
   * misses the thread. The backend folds every turn into one knowledge point and
   * still keeps the code references, which land in the note's 关联代码 section.
   */
  const generateWholeNote = async () => {
    if (!activeConversationId) return
    setWholeNoteBusy(true)
    setNoteError(null)
    try {
      const note = await api.createNoteFromConversation(activeConversationId)
      bumpRefresh()
      openNoteView(note)
      if (note.summary_failed) setNoteError(`笔记已保存，但模型整理失败：${note.summary_error ?? '未知原因'}`)
    } catch (err) {
      setNoteError(err instanceof Error ? err.message : String(err))
    } finally {
      setWholeNoteBusy(false)
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
          {/* Per-answer notes cover one explanation; this one covers the session. */}
          {activeConversationId && chat.messages.length > 0 ? (
            <button
              className="ghost small"
              title="把整段对话整理成一条知识点笔记"
              onClick={() => void generateWholeNote()}
              disabled={wholeNoteBusy || chat.busy}
              type="button"
            >
              <Icon name="notes" size={12} />
              {wholeNoteBusy ? '整理中…' : '整理成笔记'}
            </button>
          ) : null}
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

      {activeConversationId && <ConversationGraphLink key={activeConversationId} conversationId={activeConversationId} />}

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
        disabled={chat.busy || creating}
        selectionHint={snippetHint ? `片段模式 · ${snippetHint}` : null}
        contextToggle={Boolean(snippetHint)}
        useContext={useContext}
        onToggleContext={setUseContext}
        draft={draft}
        onSend={sendMessage}
        onStop={chat.stop}
      />
    </div>
  )
}
