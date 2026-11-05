import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'
import type { Conversation } from '../types'
import { formatRelative } from '../lib/format'
import { Icon } from './Icon'

const MAX_VISIBLE = 3

export interface ConversationCardsProps {
  /** Conversations bound to the file currently open, ordered by start line. */
  cards: Conversation[]
  activeConversationId?: string | null
  /** Conversation whose note is being generated right now. */
  noteBusyId?: string | null
  onOpen: (conversation: Conversation) => void
  /** Generate a note from the conversation's last answer. */
  onGenerateNote: (conversation: Conversation) => void
  /** Jump to the note the conversation already produced. */
  onOpenNote: (conversation: Conversation) => void
  /** Hovering a card highlights the lines it covers. */
  onHoverRange: (range: { start: number; end: number } | null) => void
}

interface RangeGroup {
  key: string
  start: number | null
  end: number | null
  items: Conversation[]
}

/**
 * Strip of "conversation cards" for the open file.
 *
 * Rendered above the editor rather than floating over it, so no code is ever
 * covered — the covered lines are instead tinted in the editor while a card is
 * hovered. Purely presentational: every piece of data arrives via props, which
 * keeps the editor mount free of fetch logic.
 */
export function ConversationCards({
  cards,
  activeConversationId,
  noteBusyId,
  onOpen,
  onGenerateNote,
  onOpenNote,
  onHoverRange,
}: ConversationCardsProps): ReactNode {
  const [expanded, setExpanded] = useState<string | null>(null)

  const groups = useMemo<RangeGroup[]>(() => {
    const map = new Map<string, RangeGroup>()
    for (const card of cards) {
      const start = card.start_line ?? null
      const end = card.end_line ?? start
      const key = start === null ? 'none' : `${start}-${end}`
      const group = map.get(key)
      if (group) group.items.push(card)
      else map.set(key, { key, start, end, items: [card] })
    }
    return [...map.values()].sort((a, b) => (a.start ?? 0) - (b.start ?? 0))
  }, [cards])

  if (groups.length === 0) return null

  return (
    <div className="code-cards" role="group" aria-label="这个文件的对话">
      <span className="code-cards-label" title="这个文件上已有的片段对话">
        <Icon name="message" size={11} />
        文件对话
        <span className="count">{cards.length}</span>
      </span>

      <div className="code-cards-list">
        {groups.map((group) => {
          const open = expanded === group.key
          const visible = open ? group.items : group.items.slice(0, MAX_VISIBLE)
          const hidden = group.items.length - visible.length
          return (
            <div className="code-card-group" key={group.key}>
              <span className="code-card-range" title="这段对话覆盖的行">
                {group.start === null ? '整文件' : `L${group.start}${group.end && group.end !== group.start ? `–${group.end}` : ''}`}
              </span>

              {visible.map((conversation) => (
                <div
                  key={conversation.id}
                  className={`code-card${conversation.id === activeConversationId ? ' active' : ''}`}
                  role="button"
                  tabIndex={0}
                  title={`${conversation.title} · ${formatRelative(conversation.updated_at)}`}
                  onClick={() => onOpen(conversation)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onOpen(conversation)
                    }
                  }}
                  onMouseEnter={() =>
                    group.start !== null && onHoverRange({ start: group.start, end: group.end ?? group.start })
                  }
                  onMouseLeave={() => onHoverRange(null)}
                  onFocus={() =>
                    group.start !== null && onHoverRange({ start: group.start, end: group.end ?? group.start })
                  }
                  onBlur={() => onHoverRange(null)}
                >
                  <span className="code-card-title">
                    {conversation.note_title ?? conversation.title}
                  </span>
                  {conversation.note_title ? (
                    <span className="code-card-kind" title="这条卡片显示的是它生成的知识点笔记标题">
                      <Icon name="notes" size={10} />
                      知识点
                    </span>
                  ) : null}

                  {conversation.has_note && conversation.note_id ? (
                    <button
                      type="button"
                      className="icon note"
                      title="打开这条对话生成的笔记"
                      aria-label="打开笔记"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenNote(conversation)
                      }}
                    >
                      <Icon name="notes" size={11} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="icon note"
                      title="把这段讲解整理成一条阅读笔记"
                      aria-label="生成笔记"
                      disabled={noteBusyId === conversation.id}
                      onClick={(event) => {
                        event.stopPropagation()
                        onGenerateNote(conversation)
                      }}
                    >
                      <Icon name={noteBusyId === conversation.id ? 'refresh' : 'sparkles'} size={11} />
                    </button>
                  )}
                </div>
              ))}

              {hidden > 0 ? (
                <button
                  type="button"
                  className="code-card more"
                  onClick={() => setExpanded(group.key)}
                  title={`还有 ${hidden} 条同一行范围的对话`}
                >
                  +{hidden}
                </button>
              ) : null}
              {open && group.items.length > MAX_VISIBLE ? (
                <button
                  type="button"
                  className="code-card more"
                  onClick={() => setExpanded(null)}
                  title="收起"
                >
                  收起
                </button>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
