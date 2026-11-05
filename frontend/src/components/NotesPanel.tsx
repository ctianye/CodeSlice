import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Note } from '../types'
import { useAsync } from '../hooks/useAsync'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'

/** Notes for the current project, with inline create/edit. */
export function NotesPanel(): ReactNode {
  const { project, activeConversationId, refreshKey, bumpRefresh } = useApp()
  const projectId = project?.id ?? null
  const { data, error, loading, reload } = useAsync(
    (signal) => (projectId ? api.listNotes(projectId, signal) : Promise.resolve([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const [editing, setEditing] = useState<Note | 'new' | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  if (projectId === null) return <div className="list-empty">请先选择项目。</div>

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

  return (
    <div>
      <div className="sidebar-toolbar">
        <button className="small primary" onClick={() => setEditing('new')} disabled={saving}>
          新建笔记
        </button>
        <span className="grow" />
        <button className="small ghost" onClick={reload}>
          刷新
        </button>
      </div>

      {actionError ? <ErrorNote message={actionError} /> : null}
      {editing !== null ? (
        <NoteEditor
          initial={editing === 'new' ? null : editing}
          saving={saving}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      ) : null}

      {loading && !data ? <Loading /> : null}
      {error ? <ErrorNote message={error} onRetry={reload} /> : null}
      {data && data.length === 0 && editing === null ? (
        <div className="list-empty">还没有笔记。阅读过程中随时把结论记下来。</div>
      ) : null}

      {(data ?? []).map((note) => (
        <div className="note-item" key={note.id}>
          <div className="note-head">
            <span className="note-title" onClick={() => setEditing(note)} title="点击编辑">
              {note.title}
            </span>
            <button className="small ghost" onClick={() => setEditing(note)}>
              编辑
            </button>
            <button className="small ghost danger" onClick={() => remove(note)}>
              删
            </button>
          </div>
          <div className="note-body" onClick={() => setEditing(note)}>
            {note.content_md}
          </div>
          <div className="note-time">
            {formatRelative(note.updated_at)}
            {note.conversation_id ? ' · 来自对话' : ''}
          </div>
        </div>
      ))}
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
        onChange={(event) => setTitle(event.target.value)}
        autoFocus
      />
      <textarea
        placeholder="支持 Markdown 的笔记内容"
        rows={5}
        value={content}
        onChange={(event) => setContent(event.target.value)}
      />
      <div className="actions">
        <button className="small ghost" onClick={onCancel} disabled={saving}>
          取消
        </button>
        <button
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
