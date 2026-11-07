import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Job, JobStatus, RetrievedChunk, TreeNode } from '../types'
import { useAsync } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { useIndexJob } from '../hooks/useIndexJob'
import type { IndexTotals } from '../hooks/useIndexJob'
import { EmptyState, ErrorNote, Loading } from './Feedback'
import { FileTree } from './FileTree'
import { Icon } from './Icon'

/** File tree + git badge + index progress + project search for one project. */
export function FilePanel(): ReactNode {
  const { project, reveal, refreshKey, focusNote, bumpRefresh } = useApp()
  const projectId = project?.id ?? null
  const index = useIndexJob(projectId, () => bumpRefresh())

  const tree = useAsync(
    (signal) =>
      projectId ? api.getTree(projectId, '', 2, signal) : Promise.resolve<TreeNode[]>([]),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const git = useAsync(
    (signal) => (projectId ? fetchGit(projectId, signal) : Promise.resolve(null)),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<RetrievedChunk[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)

  const runSearch = async () => {
    if (projectId === null || query.trim() === '') return
    setSearching(true)
    setSearchError(null)
    try {
      setResults(await api.searchProject(projectId, query.trim(), 8))
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : String(err))
      setResults(null)
    } finally {
      setSearching(false)
    }
  }

  const clearSearch = () => {
    setResults(null)
    setSearchError(null)
    setQuery('')
  }

  /** A note hit opens the notes panel; a code hit jumps the editor. */
  const openChunk = (chunk: RetrievedChunk) => {
    if (chunk.kind === 'note') {
      const id = Number(chunk.path.replace(/^note:\/\//, ''))
      if (Number.isInteger(id) && id > 0) {
        focusNote(id)
        return
      }
    }
    reveal(chunk.path, chunk.start_line, chunk.end_line)
  }

  if (projectId === null) {
    return (
      <EmptyState icon="folder" title="还没有打开项目" hint="先用顶部的「切换项目」添加一个本地代码目录。" />
    )
  }

  const dirtyPaths = new Set(git.data ? [...git.data.modified, ...git.data.untracked] : [])
  const job = index.job
  const totals = index.totals

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        {totals && totals.files > 0 ? (
          <span className="badge mute" title="当前索引里已有的文件 / 片段 / 向量数">
            {totals.files} 文件 · {totals.chunks} 片段
            {totals.vectors > 0 ? ` · ${totals.vectors} 向量` : ''}
          </span>
        ) : null}
        {git.data ? (
          <span
            className={`badge ${git.data.dirty ? 'warn' : 'ok'}`}
            title={git.data.head ?? 'Git 状态'}
          >
            <Icon name="git-branch" size={10} />
            {git.data.is_repo ? (git.data.branch ?? '未知分支') : '非 Git 仓库'}
            {git.data.dirty ? ' · 有改动' : ''}
          </span>
        ) : null}
        <span className="spacer" />
        <button
          type="button"
          className="icon"
          onClick={tree.reload}
          title="刷新文件树"
          aria-label="刷新文件树"
        >
          <Icon name="refresh" size={13} />
        </button>
      </div>

      {index.error ? <ErrorNote message={index.error} /> : null}
      {job ? <IndexCard job={job} totals={totals} /> : null}

      <div className="sidebar-toolbar">
        <div className="search-field">
          <span className="search-icon">
            <Icon name="search" size={13} />
          </span>
          <input
            placeholder="搜索代码，回车检索"
            value={query}
            aria-label="搜索项目内的代码"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void runSearch()
              if (event.key === 'Escape' && results !== null) clearSearch()
            }}
          />
          {query ? (
            <button
              type="button"
              className="icon clear-btn"
              onClick={clearSearch}
              title="清除搜索"
              aria-label="清除搜索"
            >
              <Icon name="x" size={12} />
            </button>
          ) : null}
        </div>
        <button
          type="button"
          className="small"
          onClick={() => void runSearch()}
          disabled={searching || query.trim() === ''}
          title="在项目中检索相关代码"
        >
          {searching ? '检索中…' : '搜索'}
        </button>
      </div>

      <div className="sidebar-scroll">
        {searchError ? <ErrorNote message={searchError} /> : null}
        {searching ? <Loading text="检索中…" /> : null}

        {results !== null ? (
          <SearchResults
            results={results}
            onOpen={openChunk}
            onBack={clearSearch}
          />
        ) : (
          <>
            {tree.loading && !tree.data ? <Loading /> : null}
            {tree.error ? <ErrorNote message={tree.error} onRetry={tree.reload} /> : null}
            {tree.data ? <FileTree nodes={tree.data} dirtyPaths={dirtyPaths} /> : null}
            {tree.data && tree.data.length === 0 ? (
              <div className="list-empty">该目录下没有可见文件。</div>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}

async function fetchGit(projectId: number, signal: AbortSignal) {
  // getGitStatus has no signal parameter in the client; guard with a race so an
  // aborted request does not write state after unmount.
  const result = await api.getGitStatus(projectId)
  if (signal.aborted) throw new DOMException('aborted', 'AbortError')
  return result
}

const JOB_LABEL: Record<JobStatus, string> = {
  pending: '排队中',
  running: '进行中',
  done: '已完成',
  error: '失败',
}

const JOB_BADGE: Record<JobStatus, string> = {
  pending: 'badge mute',
  running: 'badge info',
  done: 'badge ok',
  error: 'badge err',
}

interface IndexCardProps {
  job: Job
  totals: IndexTotals | null
}

/**
 * Compact progress card for the running/last index job.
 *
 * While a job runs its own counters are the interesting ones; once it settled
 * the card falls back to the cumulative index size, because "文件 0 片段 0" after
 * a no-op incremental run is true-but-useless.
 */
function IndexCard({ job, totals }: IndexCardProps): ReactNode {
  const percent = Math.max(0, Math.min(100, job.progress))
  const busy = job.status === 'running' || job.status === 'pending'
  const shown = busy || !totals ? job.stats : totals
  return (
    <div className="index-card">
      <div className="index-head">
        <span className={JOB_BADGE[job.status]}>{JOB_LABEL[job.status]}</span>
        <span className="truncate" style={{ flex: 1 }} title={job.message}>
          {job.message || `索引任务 #${job.id}`}
        </span>
      </div>

      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>

      {shown ? (
        <div className="index-stats">
          <span>文件 {shown.files}</span>
          <span>片段 {shown.chunks}</span>
          <span>向量 {shown.vectors}</span>
        </div>
      ) : null}

      {job.error ? <ErrorNote message={job.error} /> : null}
    </div>
  )
}

interface SearchResultsProps {
  results: RetrievedChunk[]
  onOpen: (chunk: RetrievedChunk) => void
  onBack: () => void
}

function SearchResults({ results, onOpen, onBack }: SearchResultsProps): ReactNode {
  if (results.length === 0) {
    return (
      <>
        <div className="sidebar-toolbar">
          <span className="muted" style={{ fontSize: 10.5 }}>
            没有命中
          </span>
          <span className="spacer" />
          <button type="button" className="small ghost" onClick={onBack} title="回到文件树">
            <Icon name="chevron-left" size={12} />
            文件树
          </button>
        </div>
        <div className="list-empty">没有检索到相关代码，请尝试调整关键词。</div>
      </>
    )
  }

  return (
    <div className="search-results">
      <div className="row" style={{ padding: '0 2px 2px' }}>
        <span className="muted" style={{ fontSize: 10.5 }}>
          命中 {results.length} 段
        </span>
        <span className="spacer" />
        <button type="button" className="small ghost" onClick={onBack} title="回到文件树">
          <Icon name="chevron-left" size={12} />
          文件树
        </button>
      </div>

      {results.map((chunk, i) => (
        <button
          type="button"
          className="search-result"
          key={`${chunk.path}-${chunk.start_line}-${i}`}
          onClick={() => onOpen(chunk)}
          title={
            chunk.kind === 'note'
              ? `笔记：${chunk.symbol ?? ''}（点击打开）`
              : `${chunk.path} 第 ${chunk.start_line}-${chunk.end_line} 行`
          }
        >
          <div className="sr-path">
            <Icon name={chunk.kind === 'note' ? 'notes' : 'file-code'} size={11} />
            {chunk.kind === 'note' ? '笔记' : chunk.path}
          </div>
          <div className="sr-meta">
            {chunk.kind === 'note' ? (
              <span className="tag accent">知识库</span>
            ) : (
              <span>
                L{chunk.start_line}-{chunk.end_line}
              </span>
            )}
            {chunk.symbol ? <span>{chunk.symbol}</span> : null}
            <span>{chunk.retriever}</span>
            <span>{chunk.score.toFixed(2)}</span>
          </div>
          <pre>{chunk.content}</pre>
        </button>
      ))}
    </div>
  )
}

