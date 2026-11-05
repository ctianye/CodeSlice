import type { ReactNode } from 'react'
import { useState } from 'react'
import * as api from '../api'
import type { Anchor, AnchorStatus } from '../types'
import { useAsync } from '../hooks/useAsync'
import { baseName } from '../lib/citations'
import { formatRelative } from '../lib/format'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'

const STATUS_LABEL: Record<AnchorStatus, string> = {
  unchanged: '未变化',
  modified: '已修改',
  moved: '已移动',
  deleted: '已删除',
}

const STATUS_CLASS: Record<AnchorStatus, string> = {
  unchanged: 'badge ok',
  modified: 'badge warn',
  moved: 'badge info',
  deleted: 'badge err',
}

/** Code anchors recorded by conversations, with a manual refresh trigger. */
export function AnchorsPanel(): ReactNode {
  const { project, reveal, refreshKey, bumpRefresh } = useApp()
  const projectId = project?.id ?? null
  const anchors = useAsync(
    (signal) => (projectId ? api.listAnchors(projectId, signal) : Promise.resolve<Anchor[]>([])),
    [projectId, refreshKey],
    { enabled: projectId !== null },
  )
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  if (projectId === null) return <div className="list-empty">请先选择项目。</div>

  const refresh = async () => {
    setRefreshing(true)
    setActionError(null)
    setMessage(null)
    try {
      const result = await api.refreshAnchors(projectId)
      setMessage(`已更新 ${result.updated} 个，移动 ${result.moved} 个，失效 ${result.deleted} 个。`)
      bumpRefresh()
      anchors.reload()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setRefreshing(false)
    }
  }

  const items = anchors.data ?? []

  return (
    <div>
      <div className="sidebar-toolbar">
        <button className="small" onClick={refresh} disabled={refreshing}>
          {refreshing ? '检查中…' : '重新校验锚点'}
        </button>
        <span className="grow" />
        <button className="small ghost" onClick={anchors.reload}>
          刷新
        </button>
      </div>

      {message ? (
        <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-faint)' }}>{message}</div>
      ) : null}
      {actionError ? <ErrorNote message={actionError} /> : null}
      {anchors.loading && !anchors.data ? <Loading /> : null}
      {anchors.error ? <ErrorNote message={anchors.error} onRetry={anchors.reload} /> : null}
      {anchors.data && items.length === 0 ? (
        <div className="list-empty">暂无代码锚点。对话中引用过的代码会自动记录在这里。</div>
      ) : null}

      {items.map((anchor) => (
        <div
          className="list-row"
          key={anchor.id}
          onClick={() => reveal(anchor.file_path, anchor.start_line, anchor.end_line)}
        >
          <div className="body">
            <div className="title">
              {baseName(anchor.file_path)} · L{anchor.start_line}-{anchor.end_line}
            </div>
            <div className="sub">
              {anchor.file_path}
              {anchor.symbol ? ` · ${anchor.symbol}` : ''}
            </div>
            <div style={{ marginTop: 4 }}>
              <span className={STATUS_CLASS[anchor.status]}>{STATUS_LABEL[anchor.status]}</span>
              {anchor.checked_at ? (
                <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text-faint)' }}>
                  {formatRelative(anchor.checked_at)}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
