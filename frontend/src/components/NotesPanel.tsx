import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import * as api from '../api'
import type { Note } from '../types'
import { useAsync } from '../hooks/useAsync'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

/** The note body starts with its own ``# 标题``; the list already shows it. */
function preview(content: string): string {
  const body = content.replace(/^#\s+.*(\r?\n)+/, '').trim()
  return body || content.trim()
}

/** Notes for the current project, with inline create/edit and Markdown export. */
export function NotesPanel(): ReactNode {
  const { project, activeConversationId, refreshKey, bumpRefresh, focusNoteId, focusNote } = useApp()
  const projectId = project?.id ?? null
  const { data, error, loading, reload } = useAsync(
    (signal) => (projectId ? api.listNotes(projectId, signal) : Promise.resolve([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const [editing, setEditing] = useState<Note | 'new' | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [exportingId, setExportingId] = useState<number | null>(null)

  // A code card can ask "open this note"; honour it once the list has loaded.
  useEffect(() => {
    if (focusNoteId === null || !data) return
    const target = data.find((note) => note.id === focusNoteId)
    if (target) setEditing(target)
    focusNote(null)
  }, [focusNoteId, data, focusNote])

  if (projectId === null) {
    return <EmptyState icon="notes" title="还没有打开项目" hint="先添加一个本地代码目录，再记录笔记。" />
  }

  const save = async (title: string, content: string) => {
    setSaving(true)
    setActionError(null)
    try {
      if (editing === 'new' || editing === null) {
        await api.createNote({
          project_id: projectId,
          title,
          content_md: content,
          conversation_id: activeConversationId,
        })
      } else {
        await api.updateNote(editing.id, { title, content_md: content })
      }
      setEditing(null)
      bumpRefresh()
      reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (note: Note) => {
    if (!window.confirm(`删除笔记「${note.title}」？`)) return
    setActionError(null)
    try {
      await api.deleteNote(note.id)
      if (editing !== null && editing !== 'new' && editing.id === note.id) setEditing(null)
      bumpRefresh()
      reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const exportNote = async (note: Note) => {
    setExportingId(note.id)
    setActionError(null)
    setNotice(null)
    try {
      const saved = await api.exportNote(note.id)
      setNotice(`已导出到 ${saved.path}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setExportingId(null)
    }
  }

  const notes = data ?? []

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button
          type="button"
          className="small"
          onClick={() => setEditing('new')}
          disabled={saving}
          title="新建一条阅读笔记"
        >
          <Icon name="plus" size={12} />
          新建笔记
        </button>
        <span className="spacer" />
        <span className="tag" title="笔记数量">
          {notes.length}
        </span>
        <button
          type="button"
          className="icon"
          onClick={reload}
          title="刷新笔记"
          aria-label="刷新笔记"
        >
          <Icon name="refresh" size={13} />
        </button>
      </div>

      <div className="sidebar-scroll padded">
        {actionError ? <ErrorNote message={actionError} /> : null}
        {notice ? (
          <div className="notice">
            <Icon name="check" size={12} />
            <span className="notice-text">{notice}</span>
          </div>
        ) : null}

        {editing !== null ? (
          <NoteEditor
            key={editing === 'new' ? 'new' : editing.id}
            initial={editing === 'new' ? null : editing}
            saving={saving}
            onCancel={() => setEditing(null)}
            onSave={save}
          />
        ) : null}

        {loading && !data ? <Loading /> : null}
        {error ? <ErrorNote message={error} onRetry={reload} /> : null}

        {!loading && !error && notes.length === 0 && editing === null ? (
          <EmptyState
            icon="notes"
            title="还没有笔记"
            hint="阅读过程中把结论记下来，之后可以随时回到这里。"
          >
            <button type="button" className="small primary" onClick={() => setEditing('new')}>
              新建笔记
            </button>
          </EmptyState>
        ) : null}

        {notes.map((note) => (
          <div
            className="note-item"
            key={note.id}
            onClick={() => setEditing(note)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setEditing(note)
            }}
            role="button"
            tabIndex={0}
            title="点击编辑这条笔记"
          >
            <div className="note-head">
              <span className="note-title">{note.title}</span>
              <div className="row-actions">
                <button
                  type="button"
                  className="icon"
                  onClick={(event) => {
                    event.stopPropagation()
                    void exportNote(note)
                  }}
                  disabled={exportingId === note.id}
                  title="导出为 Markdown 文件"
                  aria-label={`导出笔记 ${note.title}`}
                >
                  <Icon name="download" size={12} />
                </button>
                <button
                  type="button"
                  className="icon"
                  onClick={(event) => {
                    event.stopPropagation()
                    setEditing(note)
                  }}
                  title="编辑笔记"
                  aria-label={`编辑笔记 ${note.title}`}
                >
                  <Icon name="pencil" size={12} />
                </button>
                <button
                  type="button"
                  className="icon danger"
                  onClick={(event) => {
                    event.stopPropagation()
                    void remove(note)
                  }}
                  title="删除笔记"
                  aria-label={`删除笔记 ${note.title}`}
                >
                  <Icon name="trash" size={12} />
                </button>
              </div>
            </div>

            <div className="note-body">{preview(note.content_md)}</div>

            <div className="note-time">
              {formatRelative(note.updated_at)}
              {note.conversation_id ? ' · 来自对话' : ''}
              {note.indexed === false ? ' · 未入知识库' : ''}
              {note.indexed ? ' · 已入知识库' : ''}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

interface NoteEditorProps {
  initial: Note | null
  saving: boolean
  onCancel: () => void
  onSave: (title: string, content: string) => void
}

function NoteEditor({ initial, saving, onCancel, onSave }: NoteEditorProps): ReactNode {
  const [title, setTitle] = useState(initial?.title ?? '')
  const [content, setContent] = useState(initial?.content_md ?? '')

  const canSave = title.trim() !== '' && content.trim() !== '' && !saving

  return (
    <div className="note-editor">
      <input
        placeholder="笔记标题"
        value={title}
        aria-label="笔记标题"
        onChange={(event) => setTitle(event.target.value)}
        autoFocus
      />
      <textarea
        placeholder="支持 Markdown 的笔记内容"
        aria-label="笔记内容"
        rows={5}
        value={content}
        onChange={(event) => setContent(event.target.value)}
      />
      <div className="actions">
        <button type="button" className="small ghost" onClick={onCancel} disabled={saving}>
          取消
        </button>
        <span className="spacer" />
        <button
          type="button"
          className="small primary"
          disabled={!canSave}
          onClick={() => onSave(title.trim(), content)}
        >
          {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </div>
  )
}
