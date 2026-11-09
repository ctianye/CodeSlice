import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { AgentStreamEvent, ApprovalDecision, Citation, Conversation, Interrupt, Message } from '../types'
import { mergeCitation } from '../lib/citations'

const STAGE_LABELS: Record<string, string> = {
  classify: '理解问题',
  retrieve: '检索相关代码',
  plan: '规划处理步骤',
  execute: '执行工具调用',
  verify: '校验结果',
  summarize: '整理回答',
  title: '起标题',
}

export interface ActivityItem {
  id: string
  label: string
  detail: string | null
  state: 'running' | 'ok' | 'err'
}

export interface StreamState {
  text: string
  citations: Citation[]
  activity: ActivityItem[]
  error: string | null
}

export interface ChatController {
  conversation: Conversation | null
  messages: Message[]
  interrupt: Interrupt | null
  stream: StreamState | null
  busy: boolean
  loading: boolean
  loadError: string | null
  send: (content: string, options?: { useContext?: boolean; graphAnchor?: string | null }) => void
  resume: (decision: ApprovalDecision) => void
  stop: () => void
  reload: () => void
}

let activitySeq = 0

function nextActivityId(prefix: string): string {
  activitySeq += 1
  return `${prefix}-${activitySeq}`
}

/** Appends a message, replacing an optimistic local echo of the same user turn. */
function upsertMessage(list: Message[], incoming: Message): Message[] {
  if (list.some((m) => m.id === incoming.id)) return list
  if (incoming.role === 'user') {
    const echoIndex = list.findIndex((m) => m.id.startsWith('local-') && m.role === 'user' && m.content === incoming.content)
    if (echoIndex >= 0) {
      const copy = list.slice()
      copy[echoIndex] = incoming
      return copy
    }
  }
  return [...list, incoming]
}

/**
 * Owns one conversation's message list plus the live SSE turn state.
 * `send()` and `resume()` both drive the same rendering pipeline so an
 * approval-gated turn looks identical before and after the interrupt.
 */
export function useChatStream(conversationId: string | null, onSettled?: () => void): ChatController {
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [interrupt, setInterrupt] = useState<Interrupt | null>(null)
  const [stream, setStream] = useState<StreamState | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const toolNames = useRef<Record<string, string>>({})
  const settledRef = useRef(onSettled)
  settledRef.current = onSettled

  // Load (or clear) the conversation whenever the selection changes.
  useEffect(() => {
    abortRef.current?.abort()
    setStream(null)
    setInterrupt(null)
    setBusy(false)
    if (!conversationId) {
      setConversation(null)
      setMessages([])
      setLoading(false)
      setLoadError(null)
      return
    }
    const controller = new AbortController()
    setLoading(true)
    setLoadError(null)
    api
      .getConversation(conversationId, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted) return
        setConversation(detail.conversation)
        setMessages(detail.messages)
        setInterrupt(detail.pending_interrupt)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setLoadError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
    return () => controller.abort()
  }, [conversationId, nonce])

  const apply = useCallback((event: AgentStreamEvent) => {
    switch (event.type) {
      case 'token': {
        const delta = event.delta
        setStream((prev) =>
          prev
            ? { ...prev, text: prev.text + delta }
            : { text: delta, citations: [], activity: [], error: null },
        )
        break
      }
      case 'status': {
        // The backend renames a placeholder conversation once the first answer
        // exists, so the title names what was learned. The event carries the new
        // title; mirror it here so the header does not wait for a full reload.
        if (event.stage === 'title' && event.detail) {
          const title = event.detail
          setConversation((prev) => (prev ? { ...prev, title } : prev))
          break
        }
        const label = STAGE_LABELS[event.stage] ?? event.stage
        setStream((prev) => {
          const base: StreamState = prev ?? { text: '', citations: [], activity: [], error: null }
          const activity = base.activity.map((item) =>
            item.state === 'running' ? { ...item, state: 'ok' as const } : item,
          )
          activity.push({ id: nextActivityId('status'), label, detail: event.detail ?? null, state: 'running' })
          return { ...base, activity }
        })
        break
      }
      case 'citation': {
        const citation: Citation = {
          path: event.path,
          start_line: event.start_line,
          end_line: event.end_line,
          symbol: event.symbol ?? null,
        }
        setStream((prev) => {
          const base: StreamState = prev ?? { text: '', citations: [], activity: [], error: null }
          return { ...base, citations: mergeCitation(base.citations, citation) }
        })
        break
      }
      case 'tool_call': {
        toolNames.current[event.call_id] = event.name
        setStream((prev) => {
          const base: StreamState = prev ?? { text: '', citations: [], activity: [], error: null }
          let detail: string | null = null
          try {
            detail = JSON.stringify(event.args)
          } catch {
            detail = null
          }
          const activity = base.activity.map((item) =>
            item.state === 'running' ? { ...item, state: 'ok' as const } : item,
          )
          activity.push({ id: `call:${event.call_id}`, label: `调用工具 ${event.name}`, detail, state: 'running' })
          return { ...base, activity }
        })
        break
      }
      case 'tool_result': {
        setStream((prev) => {
          if (!prev) return prev
          const activity = prev.activity.map((item) =>
            item.id === `call:${event.call_id}`
              ? { ...item, state: event.ok ? ('ok' as const) : ('err' as const), detail: event.summary }
              : item,
          )
          return { ...prev, activity }
        })
        break
      }
      case 'approval_required': {
        setInterrupt({ id: event.id, kind: event.kind, payload: event.payload })
        break
      }
      case 'message': {
        const incoming = event.message
        setMessages((prev) => upsertMessage(prev, incoming))
        if (incoming.role === 'assistant') setStream(null)
        break
      }
      case 'error': {
        setStream((prev) => {
          const base: StreamState = prev ?? { text: '', citations: [], activity: [], error: null }
          return { ...base, error: event.detail }
        })
        break
      }
      case 'done':
        break
      default:
        break
    }
  }, [])

  const drive = useCallback(
    async (factory: (signal: AbortSignal) => AsyncGenerator<AgentStreamEvent>, optimistic?: Message) => {
      if (optimistic) setMessages((prev) => [...prev.filter((m) => m.id !== optimistic.id), optimistic])
      const controller = new AbortController()
      abortRef.current = controller
      setBusy(true)
      setStream((prev) => prev ?? { text: '', citations: [], activity: [], error: null })
      try {
        for await (const event of factory(controller.signal)) {
          apply(event)
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        setBusy(false)
        setStream((prev) => {
          if (!prev) return prev
          const blank = prev.text.trim() === '' && prev.activity.length === 0
          return blank ? null : prev
        })
        settledRef.current?.()
      }
    },
    [apply],
  )

  const send = useCallback(
    (content: string, options?: { useContext?: boolean; graphAnchor?: string | null }) => {
      if (!conversationId) return
      const trimmed = content.trim()
      if (!trimmed) return
      const optimistic: Message = {
        id: `local-${Date.now()}`,
        role: 'user',
        content: trimmed,
        created_at: new Date().toISOString(),
        citations: null,
        meta: null,
      }
      void drive(
        (signal) =>
          api.streamChat(conversationId, trimmed, signal, {
            useContext: options?.useContext,
            graphAnchor: options?.graphAnchor,
          }),
        optimistic,
      )
    },
    [conversationId, drive],
  )

  const resume = useCallback(
    (decision: ApprovalDecision) => {
      if (!conversationId || !interrupt) return
      const body = {
        interrupt_id: interrupt.id,
        decision,
        remember_pattern:
          decision === 'approve_always' || decision === 'approve_session'
            ? (interrupt.payload.command ?? interrupt.payload.path ?? null)
            : null,
      }
      setInterrupt(null)
      void drive((signal) => api.streamResume(conversationId, body, signal))
    },
    [conversationId, interrupt, drive],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
  }, [])

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => () => abortRef.current?.abort(), [])

  return {
    conversation,
    messages,
    interrupt,
    stream,
    busy,
    loading,
    loadError,
    send,
    resume,
    stop,
    reload,
  }
}
