import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import type { Note, NoteFolder } from '../types'
import { useAsync } from '../hooks/useAsync'
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

type Scope = { kind: 'all' } | { kind: 'folder'; id: number }

const pinyinCollator = new Intl.Collator('zh-CN-u-co-pinyin', { numeric: true, sensitivity: 'base' })

function compareTitle(a: string, b: string): number {
  const aLatin = /^[a-z]/i.test(a)
  const bLatin = /^[a-z]/i.test(b)
  if (aLatin !== bLatin) return aLatin ? -1 : 1
  return pinyinCollator.compare(a, b)
}

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
  const [query, setQuery] = useState('')

  const folders = useAsync((signal) => api.listNoteFolders(signal), [refreshKey])
  const notes = useAsync((signal) => api.listNotes({}, signal), [refreshKey])

  const [creating, setCreating] = useState<'note' | 'folder' | null>(null)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [movingNoteId, setMovingNoteId] = useState<number | null>(null)
  const [newTitle, setNewTitle] = useState('')
  const [newFileName, setNewFileName] = useState('')
  const [newContent, setNewContent] = useState('')
  const [newFolder, setNewFolder] = useState('')

  const tree = useMemo(() => buildTree(folders.data ?? []), [folders.data])
  const folderOptions = useMemo(() => flattenFolders(tree), [tree])

  // Reveal the note even when it belongs to a collapsed folder or another project.
  useEffect(() => {
    if (focusNoteId === null || !notes.data || !folders.data) return
    const target = notes.data.find((note) => note.id === focusNoteId)
    if (target) {
      openNoteView(target)
      setQuery('')
      const parents: Record<number, boolean> = {}
      let id = target.folder_id
      while (id != null && !parents[id]) {
        parents[id] = true
        id = folders.data.find((folder) => folder.id === id)?.parent_id
      }
      setOpenFolders((prev) => ({ ...prev, ...parents }))
    }
    focusNote(null)
  }, [focusNoteId, notes.data, folders.data, focusNote, openNoteView])

  const reloadAll = () => {
    folders.reload()
    notes.reload()
  }

  const startNew = () => {
    setCreating('note')
    setActionError(null)
    setNotice(null)
    setNewTitle('')
    setNewFileName('')
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
        file_name: newFileName.trim() || newTitle.trim(),
        content_md: newContent,
        conversation_id: activeConversationId,
        folder_id: scope.kind === 'folder' ? scope.id : null,
      })
      setCreating(null)
      bumpRefresh()
      reloadAll()
      openNoteView(note)
      if (scope.kind === 'folder') setOpenFolders((prev) => ({ ...prev, [scope.id]: true }))
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
      if (scope.kind === 'folder') setOpenFolders((prev) => ({ ...prev, [scope.id]: true }))
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
      if (scope.kind === 'folder' && scope.id === folder.id) setScope({ kind: 'all' })
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
      setMovingNoteId(null)
      if (folderId !== null) setOpenFolders((prev) => ({ ...prev, [folderId]: true }))
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  const allNotes = notes.data ?? []
  const list = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    const filtered = allNotes.filter((note) => {
      if (!needle) return true
      return [note.title, preview(note.content_md), note.project_name ?? '']
        .some((value) => value.toLocaleLowerCase().includes(needle))
    })
    return filtered.sort((a, b) => compareTitle(a.title, b.title) || a.id - b.id)
  }, [allNotes, query])
  const notesByFolder = useMemo(() => {
    const knownFolders = new Set((folders.data ?? []).map((folder) => folder.id))
    const grouped = new Map<number | null, Note[]>()
    for (const note of list) {
      const key = note.folder_id != null && knownFolders.has(note.folder_id) ? note.folder_id : null
      const group = grouped.get(key) ?? []
      group.push(note)
      grouped.set(key, group)
    }
    return grouped
  }, [list, folders.data])
  const visibleTree = useMemo(() => {
    if (!query.trim()) return tree
    const needle = query.trim().toLocaleLowerCase()
    const filter = (nodes: FolderNode[]): FolderNode[] => nodes.flatMap((node) => {
      const children = filter(node.children)
      return children.length || notesByFolder.has(node.folder.id) || node.folder.name.toLocaleLowerCase().includes(needle)
        ? [{ ...node, children }]
        : []
    })
    return filter(tree)
  }, [tree, query, notesByFolder])

  const renderNote = (note: Note, depth: number): ReactNode => (
    <div
      className={`note-item${noteView?.id === note.id ? ' selected' : ''}${movingNoteId === note.id ? ' moving' : ''}`}
      key={note.id}
      style={{ marginLeft: `${depth * 16}px` }}
      onClick={() => openNoteView(note)}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && event.key === 'Enter') openNoteView(note)
      }}
      role="button"
      tabIndex={0}
      title={`${note.title}\n${note.project_name ?? ''}\n${preview(note.content_md)}`}
    >
      <div className="note-head">
        <Icon name="notes" size={13} />
        <span className="note-title">{note.title}</span>
        <div className="row-actions">
          <button
            type="button"
            className="icon"
            onClick={(event) => {
              event.stopPropagation()
              setMovingNoteId((current) => (current === note.id ? null : note.id))
            }}
            disabled={busyId === note.id}
            title="移动到文件夹"
            aria-label={`移动笔记 ${note.title} 到文件夹`}
            aria-expanded={movingNoteId === note.id}
          >
            <Icon name="folder" size={12} />
          </button>
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

      {movingNoteId === note.id ? (
        <div
          className="note-move-menu"
          role="menu"
          aria-label={`移动笔记 ${note.title}`}
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setMovingNoteId(null)
            event.stopPropagation()
          }}
        >
          <button
            type="button"
            className="note-move-option"
            role="menuitem"
            disabled={busyId === note.id || note.folder_id == null}
            onClick={() => void fileInto(note, null)}
          >
            <Icon name="file" size={12} />
            未分类
            {note.folder_id == null ? <span className="current-folder">当前</span> : null}
          </button>
          {folderOptions.map(({ folder, depth }) => (
            <button
              key={folder.id}
              type="button"
              className="note-move-option"
              role="menuitem"
              style={{ paddingLeft: `${8 + depth * 14}px` }}
              disabled={busyId === note.id || note.folder_id === folder.id}
              onClick={() => void fileInto(note, folder.id)}
              title={folder.name}
            >
              <Icon name="folder" size={12} />
              <span className="note-move-label">{folder.name}</span>
              {note.folder_id === folder.id ? <span className="current-folder">当前</span> : null}
            </button>
          ))}
          {folderOptions.length === 0 ? (
            <div className="note-move-empty">
              <span>还没有文件夹</span>
              <button
                type="button"
                className="note-move-create"
                onClick={() => {
                  setMovingNoteId(null)
                  setCreating('folder')
                  setNewFolder('')
                }}
              >
                新建文件夹
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

    </div>
  )

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
          {allNotes.length}
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

        <button
          type="button"
          className={`note-library-root${scope.kind === 'all' ? ' active' : ''}`}
          onClick={() => setScope({ kind: 'all' })}
          title="知识库根目录；文件夹在前，笔记在后，按拼音排序"
        >
          <Icon name="notes" size={13} />
          知识库
          <span className="muted">{allNotes.length}</span>
        </button>
        {scope.kind === 'folder' ? (
          <div className="note-create-target">
            新建位置：{folderOptions.find(({ folder }) => folder.id === scope.id)?.folder.name ?? '文件夹'}
          </div>
        ) : null}

        <div className="note-tools">
          <label className="note-search">
            <Icon name="search" size={12} />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索标题、内容或项目"
              aria-label="搜索知识库笔记"
            />
          </label>
        </div>

        {creating === 'note' ? (
          <div className="note-editor">
            <input
              placeholder="知识点标题，例如「审批中断是如何恢复的」"
              value={newTitle}
              aria-label="笔记标题"
              onChange={(event) => setNewTitle(event.target.value)}
              autoFocus
            />
            <input
              placeholder="导出文件名（可自定义）"
              value={newFileName}
              aria-label="导出文件名"
              onChange={(event) => setNewFileName(event.target.value)}
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
        {folders.error ? <ErrorNote message={folders.error} onRetry={folders.reload} /> : null}

        {!notes.loading && !notes.error && list.length === 0 && visibleTree.length === 0 && creating !== 'note' ? (
          <EmptyState
            icon={query ? 'search' : 'notes'}
            title={query ? '没有匹配的笔记' : '这里还没有笔记'}
            hint={query ? '试试缩短关键词，或搜索知识点中的技术名词。' : '阅读过程中把结论记下来，之后可以随时回到这里。'}
          >
            {query ? (
              <button type="button" className="small ghost" onClick={() => setQuery('')}>
                清除搜索
              </button>
            ) : (
              <button type="button" className="small primary" onClick={startNew} disabled={projectId === null}>
                新建笔记
              </button>
            )}
          </EmptyState>
        ) : null}

        <div className="note-library" aria-label="知识库文件夹与笔记">
          {visibleTree.map((node) => (
            <FolderRows
              key={node.folder.id}
              node={node}
              depth={0}
              scope={scope}
              openFolders={openFolders}
              searching={query.trim() !== ''}
              notesByFolder={notesByFolder}
              renderNote={renderNote}
              onToggle={(id) => setOpenFolders((prev) => ({ ...prev, [id]: !(prev[id] ?? false) }))}
              onPick={(id) => {
                setScope({ kind: 'folder', id })
                setOpenFolders((prev) => ({ ...prev, [id]: !(prev[id] ?? false) }))
              }}
              onRename={renameFolder}
              onRemove={removeFolder}
            />
          ))}
          {(notesByFolder.get(null) ?? []).map((note) => renderNote(note, 0))}
        </div>
      </div>
    </div>
  )
}

interface FolderNode {
  folder: NoteFolder
  children: FolderNode[]
}

function flattenFolders(
  nodes: FolderNode[],
  depth = 0,
): Array<{ folder: NoteFolder; depth: number }> {
  return nodes.flatMap((node) => [
    { folder: node.folder, depth },
    ...flattenFolders(node.children, depth + 1),
  ])
}

/** Nests the flat folder list by `parent_id`; the API returns them all. */
function buildTree(folders: NoteFolder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>()
  for (const folder of folders) nodes.set(folder.id, { folder, children: [] })
  const roots: FolderNode[] = []
  for (const node of [...nodes.values()].sort((a, b) => compareTitle(a.folder.name, b.folder.name) || a.folder.id - b.folder.id)) {
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
  notesByFolder,
  renderNote,
  searching,
}: {
  node: FolderNode
  depth: number
  scope: Scope
  openFolders: Record<number, boolean>
  onToggle: (id: number) => void
  onPick: (id: number) => void
  onRename: (folder: NoteFolder) => void
  onRemove: (folder: NoteFolder) => void
  notesByFolder: Map<number | null, Note[]>
  renderNote: (note: Note, depth: number) => ReactNode
  searching: boolean
}): ReactNode {
  const folder = node.folder
  const folderNotes = notesByFolder.get(folder.id) ?? []
  const hasChildren = node.children.length > 0 || folderNotes.length > 0
  const open = searching || (openFolders[folder.id] ?? false)
  const active = scope.kind === 'folder' && scope.id === folder.id

  return (
    <>
      <div
        className={`note-folder${active ? ' active' : ''}`}
        style={{ paddingLeft: `${6 + depth * 16}px` }}
        onClick={() => onPick(folder.id)}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            onPick(folder.id)
          }
        }}
        role="button"
        tabIndex={0}
        title={folder.name}
        aria-expanded={open}
      >
        <button
          type="button"
          className={`twist${hasChildren && open ? ' open' : ''}`}
          onClick={(event) => {
            event.stopPropagation()
            onToggle(folder.id)
          }}
          aria-label={`${open ? '收起' : '展开'}文件夹 ${folder.name}`}
          aria-expanded={open}
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
              notesByFolder={notesByFolder}
              renderNote={renderNote}
              searching={searching}
            />
          ))
        : null}
      {open ? folderNotes.map((note) => renderNote(note, depth + 1)) : null}
      {open && !hasChildren ? <div className="note-folder-empty" style={{ paddingLeft: `${28 + depth * 16}px` }}>空文件夹</div> : null}
    </>
  )
}
