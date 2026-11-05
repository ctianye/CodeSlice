import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { RetrievedChunk, TreeNode } from '../types'
import { useAsync } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { useIndexJob } from '../hooks/useIndexJob'
import { ErrorNote, Loading } from './Feedback'
import { FileTree } from './FileTree'

/** File tree + git badge + index progress + semantic search for one project. */
export function FilePanel(): ReactNode {
  const { project, reveal, refreshKey } = useApp()
  const projectId = project?.id ?? null
  const index = useIndexJob(projectId)

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

  if (projectId === null) return <div className="list-empty">请先选择项目。</div>

  const dirtyPaths = new Set(git.data ? [...git.data.modified, ...git.data.untracked] : [])
  const job = index.job

  return (
    <div className="sidebar-section">
      <div className="sidebar-toolbar">
        <button
          className="small"
          onClick={index.start}
          disabled={index.starting || (job !== null && (job.status === 'running' || job.status === 'pending'))}
          title="建立/更新代码索引"
        >
          {index.starting ? '提交中…' : '建立索引'}
        </button>
        {git.data ? (
          <span className={`badge ${git.data.dirty ? 'warn' : 'ok'}`} title={git.data.head ?? ''}>
            {git.data.is_repo ? (git.data.branch ?? '未知分支') : '非 Git 仓库'}
            {git.data.dirty ? ' · 有改动' : ''}
          </span>
        ) : null}
        <span className="grow" />
        <button className="small ghost" onClick={tree.reload} title="刷新文件树">
          刷新
        </button>
      </div>

      {index.error ? <ErrorNote message={index.error} /> : null}
      {job ? (
        <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-faint)' }}>
          <div>
            索引任务 #{job.id} · {jobLabel(job.status)}
            {job.message ? ` · ${job.message}` : ''}
          </div>
          <div className="progress-track" style={{ marginTop: 5 }}>
            <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
          </div>
          {job.stats ? (
            <div style={{ marginTop: 4 }}>
              文件 {job.stats.files} · 片段 {job.stats.chunks} · 向量 {job.stats.vectors}
            </div>
          ) : null}
          {job.error ? <div style={{ color: 'var(--danger)', marginTop: 4 }}>{job.error}</div> : null}
          <button className="small ghost" style={{ marginTop: 6 }} onClick={index.dismiss}>
            收起
          </button>
        </div>
      ) : null}

      <div className="sidebar-toolbar">
        <input
          placeholder="在项目中搜索（语义/关键词）"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void runSearch()
          }}
        />
        <button className="small" onClick={() => void runSearch()} disabled={searching || query.trim() === ''}>
          {searching ? '…' : '搜索'}
        </button>
      </div>

      <div className="sidebar-scroll">
        {searchError ? <ErrorNote message={searchError} /> : null}
        {results !== null ? (
          <SearchResults
            results={results}
            query={query}
            onClear={() => {
              setResults(null)
              setQuery('')
            }}
            onOpen={(chunk) => reveal(chunk.path, chunk.start_line, chunk.end_line)}
          />
        ) : null}

        {searching ? <Loading text="检索中…" /> : null}
        {results === null ? (
          <>
            {tree.loading && !tree.data ? <Loading /> : null}
            {tree.error ? <ErrorNote message={tree.error} onRetry={tree.reload} /> : null}
            {tree.data ? <FileTree nodes={tree.data} dirtyPaths={dirtyPaths} /> : null}
            {tree.data && tree.data.length === 0 ? <div className="list-empty">该目录下没有可见文件。</div> : null}
          </>
        ) : null}
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

function jobLabel(status: string): string {
  switch (status) {
    case 'pending':
      return '排队中'
    case 'running':
      return '进行中'
    case 'done':
      return '已完成'
    case 'error':
      return '失败'
    default:
      return status
  }
}

interface SearchResultsProps {
  results: RetrievedChunk[]
  query: string
  onOpen: (chunk: RetrievedChunk) => void
  onClear: () => void
}

function SearchResults({ results, query, onOpen, onClear }: SearchResultsProps): ReactNode {
  return (
    <div>
      <div className="sidebar-toolbar">
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          「{query}」命中 {results.length} 段
        </span>
        <span className="grow" />
        <button className="small ghost" onClick={onClear}>
          返回文件树
        </button>
      </div>
      {results.length === 0 ? <div className="list-empty">没有检索到相关代码，请先建立索引。</div> : null}
      {results.map((chunk, index) => (
        <div className="search-result" key={`${chunk.path}-${chunk.start_line}-${index}`} onClick={() => onOpen(chunk)}>
          <div className="sr-path" title={chunk.path}>
            {chunk.path}
          </div>
          <div className="sr-meta">
            L{chunk.start_line}-{chunk.end_line}
            {chunk.symbol ? ` · ${chunk.symbol}` : ''} · {chunk.retriever} · {chunk.score.toFixed(2)}
          </div>
          <pre>{chunk.content}</pre>
        </div>
      ))}
    </div>
  )
}
