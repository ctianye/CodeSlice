import { useEffect, useRef, useState } from 'react'
import * as api from '../../api'
import type { GraphSummary } from '../../types'
import { Markdown } from '../Markdown'
import { Icon } from '../Icon'

/**
 * "Explain this node's relations" panel.
 *
 * Owned by the rail and keyed by the selection: switching nodes remounts it, so
 * a summary can never be shown next to a node it does not describe. The abort
 * controller is also the stale-response guard — a cancelled request resolves
 * after the node changed, and the signal check keeps it out of the panel.
 */
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
      <button className="graph-act primary graph-summary-run" disabled={busy} onClick={() => void summarize()}>
        <Icon name="sparkles" size={12} />{busy ? '正在解读…' : error ? '重试 AI 解读' : 'AI 解读关系'}
      </button>
      {busy
        ? <button className="graph-act" onClick={() => { request.current?.abort(); setBusy(false) }}>取消</button>
        : <span className="graph-summary-scope">覆盖全部 {result?.neighbor_count ?? neighborCount} 个直接邻点</span>}
    </div>
    <div className="graph-summary-output" aria-live="polite" aria-busy={busy}>
      {error
        ? <p className="graph-summary-error" role="alert">{error}</p>
        : result
          ? <Markdown text={result.summary} />
          : busy
            ? <p className="graph-hint">正在梳理这个节点与全部直接邻点的关联…</p>
            : <p className="graph-hint">只依据图谱里的直接关系解释，不会扩展到二跳节点。</p>}
    </div>
  </section>
}
