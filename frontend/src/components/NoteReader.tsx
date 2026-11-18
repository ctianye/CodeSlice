import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import type { NoteFolder, NoteRef } from '../types'
import { formatRelative } from '../lib/format'
import { usePersistedState } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { ErrorNote } from './Feedback'
import { Icon } from './Icon'
import { Markdown } from './Markdown'

/**
 * A saved knowledge point, read as Markdown in the editor pane.
 *
 * Notes are the durable output of a reading session, so they get the full pane
 * rather than a cramped textarea in the sidebar: the body renders through the
 * same Markdown pipeline as an assistant answer, which means the `## 关联代码`
 * section turns its `path:line-line` headings into clickable citations that jump
 * back into the editor.
 */
export function NoteReader(): ReactNode {
  const {
    noteView,
    openNoteView,
    bumpRefresh,
    setActiveConversationId,
    refreshKey,
    revision,
    openRefCode,
  } = useApp()
  const [draft, setDraft] = useState<{ title: string; fileName: string; content: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState<'retitle' | 'export' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [folders, setFolders] = useState<NoteFolder[]>([])
  /** The reference list collapses to its one-line header; the choice persists. */
  const [refsOpen, setRefsOpen] = usePersistedState('codeslice.noteRefsOpen', true)
  /** The same note, re-fetched with this commit's reference resolution. */
  const [scoped, setScoped] = useState<typeof noteView>(null)

  const rev = revision.kind === 'rev' ? revision.sha : null
  const note = scoped && scoped.id === noteView?.id ? scoped : noteView

  useEffect(() => {
    if (!noteView) {
      setScoped(null)
      return
    }
    const controller = new AbortController()
    // Fetch even in the work-tree scope: the list payload does not carry refs,
    // and the reader needs their current/historical resolution to render the
    // clickable reference list.
    api
      .getNote(noteView.id, controller.signal, rev ?? undefined)
      .then((fresh) => {
        if (!controller.signal.aborted) setScoped(fresh)
      })
      .catch(() => {
        /* the tab still holds the unscoped note; the badges simply stay off */
      })
    return () => controller.abort()
  }, [noteView?.id, rev, refreshKey])

  // Leaving one note and opening another must not carry the old draft over.
  useEffect(() => {
    setDraft(null)
    setError(null)
    setNotice(null)
  }, [note?.id, refreshKey])

  // The folder picker needs the whole tree, not just this note's folder.
  useEffect(() => {
    const controller = new AbortController()
    api
      .listNoteFolders(controller.signal)
      .then(setFolders)
      .catch(() => {
        /* the picker simply stays empty */
      })
    return () => controller.abort()
  }, [refreshKey])

  const body = useMemo(() => stripTitleLine(note?.content_md ?? ''), [note?.content_md])

  if (!note) return null

  const editing = draft !== null
  const canSave =
    editing && draft.title.trim() !== '' && draft.fileName.trim() !== '' && draft.content.trim() !== '' && !saving

  const startEdit = () => {
    setError(null)
    setNotice(null)
    setDraft({ title: note.title, fileName: note.file_name || note.title, content: note.content_md })
  }

  const save = async () => {
    if (!draft) return
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateNote(note.id, {
        title: draft.title.trim(),
        file_name: draft.fileName.trim(),
        content_md: draft.content,
      })
      openNoteView(updated)
      setDraft(null)
      setNotice('已保存')
      bumpRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const retitle = async () => {
    setBusy('retitle')
    setError(null)
    setNotice(null)
    try {
      const updated = await api.retitleNote(note.id)
      openNoteView(updated)
      if (updated.retitle_failed) {
        setNotice('模型没能给出更好的标题，已保留原标题')
      } else {
        setNotice(`标题已更新为「${updated.title}」`)
      }
      bumpRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const exportNote = async () => {
    setBusy('export')
    setError(null)
    setNotice(null)
    try {
      const saved = await api.exportNote(note.id)
      setNotice(`已导出到 ${saved.path}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    if (!window.confirm(`删除笔记「${note.title}」？`)) return
    setError(null)
    try {
      await api.deleteNote(note.id)
      openNoteView(null)
      bumpRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const openSource = () => {
    if (!note.conversation_id) return
    setActiveConversationId(note.conversation_id)
  }

  const moveToFolder = async (value: string) => {
    const folderId = value === '' ? null : Number(value)
    setError(null)
    try {
      const updated = await api.updateNote(note.id, { folder_id: folderId, move_folder: true })
      openNoteView(updated)
      bumpRefresh()
      setNotice(folderId === null ? '已移出文件夹' : '已移到所选文件夹')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  /**
   * One click policy for both the reference list and inline `path:line` links:
   * exact current match opens the live file at its re-resolved lines; anything
   * else opens the commit the note was written from.
   */
  const openRef = (ref: NoteRef) => {
    const current = ref.status === 'unchanged' || ref.status === 'moved'
    const targetRev = current ? rev : (ref.resolved_commit || ref.commit_sha || rev)
    const start = ref.resolved_start_line ?? ref.start_line
    const end = ref.resolved_end_line ?? ref.end_line
    openRefCode(targetRev, ref.path, { start, end }, {
      label: note.title,
      noteId: note.id,
      conversationId: note.conversation_id,
    })
  }

  const openCitation = (path: string, start: number, end: number) => {
    const match = note.refs?.find(
      (ref) => ref.path === path && ref.start_line <= end && ref.end_line >= start,
    )
    if (match) {
      openRef(match)
      return
    }
    // A citation with no saved ref (for example an older note) still gets the
    // best-effort work-tree jump.
    openRefCode(null, path, { start, end }, {
      label: note.title,
      noteId: note.id,
      conversationId: note.conversation_id,
    })
  }

  return (
    <div className="note-reader">
      <div className="note-reader-bar">
        <span className="note-reader-mark" aria-hidden="true">
          <Icon name="notes" size={14} />
        </span>
        <div className="note-reader-title">
          <span className="label">知识点</span>
          <h2 title={note.title}>{note.title}</h2>
        </div>

        <div className="note-reader-meta">
          {note.project_name ? (
            <span className="badge mute" title="笔记写于哪个项目">
              {note.project_name}
            </span>
          ) : null}
          {note.indexed ? (
            <span className="badge ok" title="这条笔记已进入检索索引，提问时可能被引用">
              已入知识库
            </span>
          ) : (
            <span className="badge mute" title="这条笔记还没进入检索索引">
              未入知识库
            </span>
          )}
          <span className="muted">{formatRelative(note.updated_at)}</span>
          {note.base_commit ? (
            <span className="badge mute" title={`写于提交 ${note.base_commit}`}>
              @{note.base_commit.slice(0, 7)}
              {note.base_dirty ? '（当时有未提交改动）' : ''}
            </span>
          ) : (
            <span className="badge mute" title="这条笔记没有记录生成时的提交">未记录版本</span>
          )}
          {rev && note.note_state === 'later' ? (
            <span className="badge warn" title={`这条笔记写于 ${rev.slice(0, 7)} 之后`}>
              此后新增
            </span>
          ) : null}
          {rev && note.note_state === 'existing' ? (
            <span className="badge ok" title="这条笔记在查看的版本里已经存在">当时已存在</span>
          ) : null}
        </div>

        <div className="note-reader-actions">
          {folders.length > 0 ? (
            <select
              className="note-reader-folder"
              value={note.folder_id == null ? '' : String(note.folder_id)}
              onChange={(event) => void moveToFolder(event.target.value)}
              title="把这条笔记放进文件夹"
              aria-label="笔记所在文件夹"
            >
              <option value="">未分类</option>
              {folders.map((folder) => (
                <option key={folder.id} value={String(folder.id)}>
                  {folder.name}
                </option>
              ))}
            </select>
          ) : null}
          {note.conversation_id ? (
            <button type="button" className="ghost small" onClick={openSource} title="在右侧打开生成它的对话">
              <Icon name="message" size={12} />
              <span>来源对话</span>
            </button>
          ) : null}
          <button type="button" className="ghost small" onClick={startEdit} disabled={editing || saving} title="编辑标题与正文">
            <Icon name="pencil" size={12} />
            <span>编辑</span>
          </button>
          <button
            type="button"
            className="ghost small"
            onClick={retitle}
            disabled={busy !== null || saving}
            title="让模型根据内容重新起一个知识点标题"
          >
            <Icon name="sparkles" size={12} />
            <span>{busy === 'retitle' ? '重拟中…' : '重拟标题'}</span>
          </button>
          <button type="button" className="ghost small" onClick={exportNote} disabled={busy !== null} title="导出为 Markdown 文件">
            <Icon name="download" size={12} />
            <span>导出</span>
          </button>
          <button type="button" className="icon danger" onClick={remove} title="删除这条笔记" aria-label="删除这条笔记">
            <Icon name="trash" size={13} />
          </button>
          <button
            type="button"
            className="icon"
            onClick={() => openNoteView(null)}
            title="关闭笔记，回到代码"
            aria-label="关闭笔记"
          >
            <Icon name="x" size={13} />
          </button>
        </div>
      </div>

      {error ? <ErrorNote message={error} /> : null}
      {notice ? (
        <div className="notice">
          <Icon name="check" size={12} />
          <span className="notice-text">{notice}</span>
        </div>
      ) : null}

      {note.refs && note.refs.length > 0 ? (
        <div className={`note-refs${refsOpen ? '' : ' collapsed'}`}>
          <button
            type="button"
            className="note-refs-head"
            aria-expanded={refsOpen}
            onClick={() => setRefsOpen((open) => !open)}
            title={refsOpen ? '收起代码引用列表' : '展开代码引用列表'}
          >
            <Icon name={refsOpen ? 'chevron-down' : 'chevron-right'} size={11} />
            <Icon name="git-branch" size={12} />
            <span>{note.refs.length} 处代码引用</span>
            <span className="muted">
              {rev ? `按 ${rev.slice(0, 7)} 解析` : '仍存在看最新代码，已删除回写作提交'}
            </span>
          </button>
          {refsOpen ? (
            <>
              <div className="note-ref-list">
                {note.refs.map((ref) => {
                  const current = ref.status === 'unchanged' || ref.status === 'moved'
                  // Current means the exact block is still in the file; line numbers
                  // are the re-resolved ones. Historical/missing falls back to the
                  // commit the note was written from and shows that whole commit.
                  const targetRev = current ? rev : (ref.resolved_commit || ref.commit_sha || rev)
                  const sha = targetRev
                  const start = ref.resolved_start_line ?? ref.start_line
                  const end = ref.resolved_end_line ?? ref.end_line
                  return (
                    <button
                      key={ref.id}
                      type="button"
                      className={`note-ref-row${ref.status === 'modified' ? ' changed' : ''}`}
                      title={`${ref.path}:${start}-${end}${sha ? ` @ ${sha.slice(0, 7)}` : ' · 最新工作区'}`}
                      onClick={() => openRef(ref)}
                    >
                      <span className={`ref-status ${ref.status}`}>{REF_STATUS_LABEL[ref.status] ?? ref.status}</span>
                      <span className="note-ref-path">
                        {ref.path}
                        <span className="muted">:{start}–{end}</span>
                        {ref.symbol ? <span className="note-ref-symbol">{ref.symbol}</span> : null}
                      </span>
                      {current ? (
                        <span className="badge ok" title={rev ? '这段代码在当前查看的版本里' : '这段代码在当前工作区里'}>
                          {rev ? '当前版本' : '最新'}
                        </span>
                      ) : sha ? (
                        <span className="scm-sha">{sha.slice(0, 7)}</span>
                      ) : (
                        <span className="muted">工作区</span>
                      )}
                    </button>
                  )
                })}
              </div>
              {refSummary(note.refs) ? <div className="note-refs-summary muted">{refSummary(note.refs)}</div> : null}
            </>
          ) : null}
        </div>
      ) : null}

      {editing ? (
        <div className="note-reader-edit">
          <input
            className="note-reader-input"
            value={draft.fileName}
            aria-label="导出文件名"
            placeholder="导出文件名"
            onChange={(event) => setDraft({ ...draft, fileName: event.target.value })}
          />
          <input
            className="note-reader-input"
            value={draft.title}
            aria-label="笔记标题"
            placeholder="知识点标题（知识库显示名）"
            onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            autoFocus
          />
          <textarea
            className="note-reader-textarea"
            value={draft.content}
            aria-label="笔记内容（Markdown）"
            spellCheck={false}
            onChange={(event) => setDraft({ ...draft, content: event.target.value })}
          />
          <div className="note-reader-edit-actions">
            <span className="muted">支持 Markdown；首行会自动作为标题</span>
            <span className="spacer" />
            <button type="button" className="small ghost" onClick={() => setDraft(null)} disabled={saving}>
              取消
            </button>
            <button type="button" className="small primary" onClick={save} disabled={!canSave}>
              <Icon name="check" size={12} />
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      ) : (
        <div className="note-reader-scroll">
          <article className="note-doc">
            <Markdown text={body} onCite={openCitation} />
          </article>
        </div>
      )}
    </div>
  )
}

/** The saved body repeats the title as an `# H1`; the reader shows it in its bar. */
function stripTitleLine(content: string): string {
  return content.replace(/^#\s+.*(\r?\n)+/, '').trim() || content.trim()
}

/** One sentence for the revision banner: how many quoted ranges still match. */
const REF_STATUS_LABEL: Record<NoteRef['status'], string> = {
  unchanged: '一致',
  moved: '移动',
  modified: '改动',
  deleted: '删除',
  historical: '历史',
}

function refSummary(refs: NoteRef[]): string {
  const counts = refs.reduce<Record<string, number>>((acc, ref) => {
    acc[ref.status] = (acc[ref.status] ?? 0) + 1
    return acc
  }, {})
  const parts = [
    counts.unchanged ? `${counts.unchanged} 处一致` : '',
    counts.moved ? `${counts.moved} 处已搬家` : '',
    counts.historical ? `${counts.historical} 处可在旧提交回溯` : '',
    counts.modified ? `${counts.modified} 处内容已改` : '',
    counts.deleted ? `${counts.deleted} 处文件已不存在` : '',
  ].filter(Boolean)
  return `此版本中的引用：${parts.join(' · ') || '无记录'}`
}
