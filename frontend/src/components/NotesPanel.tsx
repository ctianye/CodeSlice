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

/**
 * Notes for the current project.
 *
 * Opening a note hands it to the main pane (see `NoteReader`) instead of growing
 * an editor inside this narrow column: the note is rendered Markdown there, with
 * its code references clickable. The sidebar stays a list plus quick actions.
 */
export function NotesPanel(): ReactNode {
  const { project, activeConversationId, refreshKey, bumpRefresh, focusNoteId, focusNote, noteView, openNoteView } =
    useApp()
  const projectId = project?.id ?? null
  const { data, error, loading, reload } = useAsync(
    (signal) => (projectId ? api.listNotes(projectId, signal) : Promise.resolve([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')

  // A code card or a note citation can ask for one specific note: open it in the
  // reader once the list has loaded, then clear the request.
  useEffect(() => {
    if (focusNoteId === null || !data) return
    const target = data.find((note) => note.id === focusNoteId)
    if (target) openNoteView(target)
    focusNote(null)
  }, [focusNoteId, data, focusNote, openNoteView])

  if (projectId === null) {
    return <EmptyState icon="notes" title="还没有打开项目" hint="先添加一个本地代码目录，再记录笔记。" />
  }

  const startNew = () => {
    setCreating(true)
    setActionError(null)
    setNotice(null)
    setNewTitle('')
    setNewContent('')
  }

  const saveNew = async () => {
    if (newTitle.trim() === '' || newContent.trim() === '') return
    setSaving(true)
    setActionError(null)
    try {
      const note = await api.createNote({
        project_id: projectId,
        title: newTitle.trim(),
        content_md: newContent,
        conversation_id: activeConversationId,
      })
      setCreating(false)
      bumpRefresh()
      reload()
      openNoteView(note)
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
      if (noteView?.id === note.id) openNoteView(null)
      bumpRefresh()
      reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const exportNote = async (note: Note) => {
    setBusyId(note.id)
    setActionError(null)
    setNotice(null)
    try {
      const saved = await api.exportNote(note.id)
      setNotice(`已导出到 ${saved.path}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const retitle = async (note: Note) => {
    setBusyId(note.id)
    setActionError(null)
    setNotice(null)
    try {
      const updated = await api.retitleNote(note.id)
      if (noteView?.id === note.id) openNoteView(updated)
      setNotice(updated.retitle_failed ? '模型没能给出更好的标题，已保留原标题' : `标题已更新为「${updated.title}」`)
      bumpRefresh()
      reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const notes = data ?? []

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button type="button" className="small" onClick={startNew} disabled={saving} title="新建一条阅读笔记">
          <Icon name="plus" size={12} />
          新建笔记
        </button>
        <span className="spacer" />
        <span className="tag" title="笔记数量">
          {notes.length}
        </span>
        <button type="button" className="icon" onClick={reload} title="刷新笔记" aria-label="刷新笔记">
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

        {creating ? (
          <div className="note-editor">
            <input
              placeholder="知识点标题，例如「审批中断是如何恢复的」"
              value={newTitle}
              aria-label="笔记标题"
              onChange={(event) => setNewTitle(event.target.value)}
              autoFocus
            />
            <textarea
              placeholder="支持 Markdown 的笔记内容"
              aria-label="笔记内容"
              rows={6}
              value={newContent}
              onChange={(event) => setNewContent(event.target.value)}
            />
            <div className="actions">
              <button type="button" className="small ghost" onClick={() => setCreating(false)} disabled={saving}>
                取消
              </button>
              <span className="spacer" />
              <button
                type="button"
                className="small primary"
                disabled={saving || newTitle.trim() === '' || newContent.trim() === ''}
                onClick={() => void saveNew()}
              >
                {saving ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        ) : null}

        {loading && !data ? <Loading /> : null}
        {error ? <ErrorNote message={error} onRetry={reload} /> : null}

        {!loading && !error && notes.length === 0 && !creating ? (
          <EmptyState icon="notes" title="还没有笔记" hint="阅读过程中把结论记下来，之后可以随时回到这里。">
            <button type="button" className="small primary" onClick={startNew}>
              新建笔记
            </button>
          </EmptyState>
        ) : null}

        {notes.map((note) => (
          <div
            className={`note-item${noteView?.id === note.id ? ' selected' : ''}`}
            key={note.id}
            onClick={() => openNoteView(note)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') openNoteView(note)
            }}
            role="button"
            tabIndex={0}
            title="在代码区打开这条笔记"
          >
            <div className="note-head">
              <span className="note-title">{note.title}</span>
              <div className="row-actions">
                <button
                  type="button"
                  className="icon"
                  onClick={(event) => {
                    event.stopPropagation()
                    void retitle(note)
                  }}
                  disabled={busyId === note.id}
                  title="让模型根据内容重新起一个知识点标题"
                  aria-label={`重拟标题 ${note.title}`}
                >
                  <Icon name="sparkles" size={12} />
                </button>
                <button
                  type="button"
                  className="icon"
                  onClick={(event) => {
                    event.stopPropagation()
                    void exportNote(note)
                  }}
                  disabled={busyId === note.id}
                  title="导出为 Markdown 文件"
                  aria-label={`导出笔记 ${note.title}`}
                >
                  <Icon name="download" size={12} />
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
