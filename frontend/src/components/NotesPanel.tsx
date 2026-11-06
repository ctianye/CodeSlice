import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import type { Note, NoteFolder } from '../types'
import { useAsync } from '../hooks/useAsync'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

/** The note body starts with its own ``# 标题``; the list already shows it. */
function preview(content: string): string {
  const body = content
    .replace(/^#\s+.*(\r?\n)+/, '')
    // The card is plain text, so markdown markers would read as literal noise
    // ("## 知识点"). Strip the ones that survive a line-oriented body.
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}[-*+]\s+/gm, '')
    .trim()
  // Notes are written as "知识点 / 要点 / 何时使用" sections. The first section
  // title is redundant in a card that already shows the knowledge point, so the
  // preview starts at its content.
  const withoutLead = body.replace(/^(?:知识点|要点|何时使用)\s*\n+/, '').trim()
  return withoutLead || body || content.trim()
}

type Scope = { kind: 'all' } | { kind: 'folder'; id: number } | { kind: 'unfiled' }

/**
 * The knowledge base.
 *
 * Unlike the file tree this is not per project: everything learned in any
 * project lands in the same library, and the user files notes into folders of
 * their own making. Opening a note hands it to the main pane (see `NoteReader`)
 * rather than growing an editor inside this narrow column.
 */
export function NotesPanel(): ReactNode {
  const { project, activeConversationId, refreshKey, bumpRefresh, focusNoteId, focusNote, noteView, openNoteView } =
    useApp()
  const projectId = project?.id ?? null

  const [scope, setScope] = useState<Scope>({ kind: 'all' })
  const [openFolders, setOpenFolders] = useState<Record<number, boolean>>({})

  const folders = useAsync((signal) => api.listNoteFolders(signal), [refreshKey])
  const notes = useAsync(
    (signal) =>
      api.listNotes(
        scope.kind === 'folder'
          ? { folderId: scope.id }
          : scope.kind === 'unfiled'
            ? { unfiled: true }
            : {},
        signal,
      ),
    [scope, refreshKey],
  )

  const [creating, setCreating] = useState<'note' | 'folder' | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')
  const [newFolder, setNewFolder] = useState('')

  // A code card or a note citation can ask for one specific note: open it in the
  // reader once the list has loaded, then clear the request.
  useEffect(() => {
    if (focusNoteId === null || !notes.data) return
    const target = notes.data.find((note) => note.id === focusNoteId)
    if (target) openNoteView(target)
    focusNote(null)
  }, [focusNoteId, notes.data, focusNote, openNoteView])

  const tree = useMemo(() => buildTree(folders.data ?? []), [folders.data])

  const reloadAll = () => {
    folders.reload()
    notes.reload()
  }

  const startNew = () => {
    setCreating('note')
    setActionError(null)
    setNotice(null)
    setNewTitle('')
    setNewContent('')
  }

  const saveNew = async () => {
    if (projectId === null || newTitle.trim() === '' || newContent.trim() === '') return
    setSaving(true)
    setActionError(null)
    try {
      const note = await api.createNote({
        project_id: projectId,
        title: newTitle.trim(),
        content_md: newContent,
        conversation_id: activeConversationId,
        folder_id: scope.kind === 'folder' ? scope.id : null,
      })
      setCreating(null)
      bumpRefresh()
      reloadAll()
      openNoteView(note)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const addFolder = async () => {
    const name = newFolder.trim()
    if (name === '') return
    setSaving(true)
    setActionError(null)
    try {
      await api.createNoteFolder({ name, parent_id: scope.kind === 'folder' ? scope.id : null })
      setCreating(null)
      setNewFolder('')
      folders.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const renameFolder = async (folder: NoteFolder) => {
    const name = window.prompt('文件夹名称', folder.name)
    if (name === null || name.trim() === '' || name.trim() === folder.name) return
    try {
      await api.renameNoteFolder(folder.id, { name: name.trim() })
      folders.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const removeFolder = async (folder: NoteFolder) => {
    if (!window.confirm(`删除文件夹「${folder.name}」？里面的笔记会回到未分类，不会被删除。`)) return
    try {
      await api.deleteNoteFolder(folder.id)
      if (scope.kind === 'folder' && scope.id === folder.id) setScope({ kind: 'unfiled' })
      reloadAll()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    }
  }

  const remove = async (note: Note) => {
    if (!window.confirm(`删除笔记「${note.title}」？`)) return
    setActionError(null)
    try {
      await api.deleteNote(note.id)
      if (noteView?.id === note.id) openNoteView(null)
      bumpRefresh()
      reloadAll()
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
      notes.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const fileInto = async (note: Note, folderId: number | null) => {
    setBusyId(note.id)
    setActionError(null)
    try {
      const updated = await api.updateNote(note.id, { folder_id: folderId, move_folder: true })
      if (noteView?.id === note.id) openNoteView(updated)
      reloadAll()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const list = notes.data ?? []

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button type="button" className="small" onClick={startNew} disabled={projectId === null || saving} title="新建一条阅读笔记">
          <Icon name="plus" size={12} />
          新建笔记
        </button>
        <button
          type="button"
          className="icon"
          onClick={() => {
            setCreating(creating === 'folder' ? null : 'folder')
            setNewFolder('')
          }}
          title="新建文件夹，把笔记归类"
          aria-label="新建文件夹"
        >
          <Icon name="folder" size={13} />
        </button>
        <span className="spacer" />
        <span className="tag" title="知识库里的笔记总数">
          {list.length}
        </span>
        <button type="button" className="icon" onClick={reloadAll} title="刷新知识库" aria-label="刷新知识库">
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

        {creating === 'folder' ? (
          <div className="folder-form">
            <input
              placeholder="文件夹名称，例如「并发」"
              value={newFolder}
              aria-label="文件夹名称"
              onChange={(event) => setNewFolder(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void addFolder()
              }}
              autoFocus
            />
            <div className="actions">
              <button type="button" className="small ghost" onClick={() => setCreating(null)} disabled={saving}>
                取消
              </button>
              <span className="spacer" />
              <button
                type="button"
                className="small primary"
                onClick={() => void addFolder()}
                disabled={saving || newFolder.trim() === ''}
              >
                创建
              </button>
            </div>
          </div>
        ) : null}

        {/* Scope switch: the shared library, a folder of the user's tree, or
            notes that have not been filed yet. */}
        <div className="note-scopes">
          <button
            type="button"
            className={`note-scope${scope.kind === 'all' ? ' active' : ''}`}
            onClick={() => setScope({ kind: 'all' })}
            title="所有项目里的笔记"
          >
            <Icon name="notes" size={11} />
            全部项目
            <span className="muted">{list.length}</span>
          </button>
          <button
            type="button"
            className={`note-scope${scope.kind === 'unfiled' ? ' active' : ''}`}
            onClick={() => setScope({ kind: 'unfiled' })}
            title="还没有放进文件夹的笔记"
          >
            <Icon name="file" size={11} />
            未分类
          </button>
        </div>

        {tree.length > 0 ? (
          <div className="note-folders">
            {tree.map((node) => (
              <FolderRows
                key={node.folder.id}
                node={node}
                depth={0}
                scope={scope}
                openFolders={openFolders}
                onToggle={(id) => setOpenFolders((prev) => ({ ...prev, [id]: !prev[id] }))}
                onPick={(id) => setScope({ kind: 'folder', id })}
                onRename={renameFolder}
                onRemove={removeFolder}
              />
            ))}
          </div>
        ) : null}

        {creating === 'note' ? (
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
              <button type="button" className="small ghost" onClick={() => setCreating(null)} disabled={saving}>
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

        {notes.loading && !notes.data ? <Loading /> : null}
        {notes.error ? <ErrorNote message={notes.error} onRetry={notes.reload} /> : null}

        {!notes.loading && !notes.error && list.length === 0 && creating !== 'note' ? (
          <EmptyState icon="notes" title="这里还没有笔记" hint="阅读过程中把结论记下来，之后可以随时回到这里。">
            <button type="button" className="small primary" onClick={startNew} disabled={projectId === null}>
              新建笔记
            </button>
          </EmptyState>
        ) : null}

        {list.map((note) => (
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
              {note.project_name ? <span className="note-source">{note.project_name}</span> : null}
              {formatRelative(note.updated_at)}
              {note.conversation_id ? ' · 来自对话' : ''}
              {note.indexed ? ' · 已入知识库' : ' · 未入知识库'}
            </div>

            {scope.kind === 'folder' ? (
              <button
                type="button"
                className="note-unfile"
                onClick={(event) => {
                  event.stopPropagation()
                  void fileInto(note, null)
                }}
                disabled={busyId === note.id}
                title="移出这个文件夹"
              >
                移出文件夹
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}

interface FolderNode {
  folder: NoteFolder
  children: FolderNode[]
}

/** Nests the flat folder list by `parent_id`; the API returns them all. */
function buildTree(folders: NoteFolder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>()
  for (const folder of folders) nodes.set(folder.id, { folder, children: [] })
  const roots: FolderNode[] = []
  for (const node of nodes.values()) {
    const parent = node.folder.parent_id != null ? nodes.get(node.folder.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  return roots
}

function FolderRows({
  node,
  depth,
  scope,
  openFolders,
  onToggle,
  onPick,
  onRename,
  onRemove,
}: {
  node: FolderNode
  depth: number
  scope: Scope
  openFolders: Record<number, boolean>
  onToggle: (id: number) => void
  onPick: (id: number) => void
  onRename: (folder: NoteFolder) => void
  onRemove: (folder: NoteFolder) => void
}): ReactNode {
  const folder = node.folder
  const hasChildren = node.children.length > 0
  const open = openFolders[folder.id] ?? true
  const active = scope.kind === 'folder' && scope.id === folder.id

  return (
    <>
      <div
        className={`note-folder${active ? ' active' : ''}`}
        style={{ paddingLeft: `${6 + depth * 12}px` }}
        onClick={() => onPick(folder.id)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onPick(folder.id)
        }}
        role="button"
        tabIndex={0}
        title={folder.name}
      >
        <button
          type="button"
          className={`twist${hasChildren && open ? ' open' : ''}`}
          onClick={(event) => {
            event.stopPropagation()
            onToggle(folder.id)
          }}
          aria-label={open ? '收起子文件夹' : '展开子文件夹'}
          disabled={!hasChildren}
        >
          <Icon name="chevron-right" size={10} />
        </button>
        <Icon name={hasChildren && open ? 'folder-open' : 'folder'} size={12} />
        <span className="label">{folder.name}</span>
        <span className="count">{folder.note_count}</span>
        <div className="row-actions">
          <button
            type="button"
            className="icon"
            onClick={(event) => {
              event.stopPropagation()
              onRename(folder)
            }}
            title="重命名文件夹"
            aria-label={`重命名 ${folder.name}`}
          >
            <Icon name="pencil" size={11} />
          </button>
          <button
            type="button"
            className="icon danger"
            onClick={(event) => {
              event.stopPropagation()
              onRemove(folder)
            }}
            title="删除文件夹"
            aria-label={`删除文件夹 ${folder.name}`}
          >
            <Icon name="trash" size={11} />
          </button>
        </div>
      </div>
      {hasChildren && open
        ? node.children.map((child) => (
            <FolderRows
              key={child.folder.id}
              node={child}
              depth={depth + 1}
              scope={scope}
              openFolders={openFolders}
              onToggle={onToggle}
              onPick={onPick}
              onRename={onRename}
              onRemove={onRemove}
            />
          ))
        : null}
    </>
  )
}
