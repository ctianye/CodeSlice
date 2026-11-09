import { useEffect, useRef, useState } from 'react'
import * as api from '../../api'
import type { GraphSummary } from '../../types'
import { Markdown } from '../Markdown'
import { Icon } from '../Icon'

export function RelationshipSummary({ scope, projectId, nodeId, neighborCount }: {
  scope: 'code' | 'notes'; projectId: number; nodeId: string; neighborCount: number
}) {
  const [result, setResult] = useState<GraphSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  const summarize = async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const answer = await api.summarizeGraph(scope, projectId, nodeId, controller.signal)
      if (!controller.signal.aborted) setResult(answer)
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <section className="graph-summary" aria-label="AI 关系总结">
    <div className="graph-summary-head">
      <button className="small primary" disabled={busy} onClick={() => void summarize()}>
        <Icon name="sparkles" size={12} />{busy ? '正在总结…' : error ? '重试 AI 总结关系' : 'AI 总结关系'}
      </button>
      <span>以所选节点为核心 · 全部 {result?.neighbor_count ?? neighborCount} 个直接邻点</span>
      {busy && <button className="small ghost" onClick={() => { request.current?.abort(); setBusy(false) }}>取消</button>}
    </div>
    <div className="graph-summary-output" aria-live="polite" aria-busy={busy}>
      {error ? <span role="alert">{error}</span> : result ? <Markdown text={result.summary} />
        : <span className="graph-dock-hint">{busy ? '正在梳理核心节点与所有直接邻点的关联逻辑…'
          : '点击按钮生成关系说明；社区收起和显示筛选不会缩减总结范围。'}</span>}
    </div>
  </section>
}
