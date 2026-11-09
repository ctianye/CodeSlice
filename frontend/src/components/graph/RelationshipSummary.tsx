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
 *
 * The answer streams token by token; a hub explanation can take a while and the
 * reader should see the model working rather than a blank box.
 */
export function RelationshipSummary({ scope, projectId, nodeId, neighborCount }: {
  scope: 'code' | 'notes'; projectId: number; nodeId: string; neighborCount: number
}) {
  const [text, setText] = useState('')
  const [result, setResult] = useState<GraphSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])

  useEffect(() => {
    if (!busy) {
      setElapsed(0)
      return
    }
    const timer = window.setInterval(() => setElapsed(value => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [busy])

  const summarize = async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    setResult(null)
    setText('')
    let gotDone = false
    let failed = false
    try {
      for await (const event of api.streamGraphSummary(scope, projectId, nodeId, controller.signal)) {
        if (controller.signal.aborted) break
        if (event.type === 'delta') {
          setText(current => current + event.text)
        } else if (event.type === 'done') {
          gotDone = true
          setResult(event)
          setText(current => current || event.summary)
        } else if (event.type === 'error') {
          failed = true
          setError(event.detail)
        }
      }
      if (!controller.signal.aborted && !gotDone && !failed) {
        setError('AI 解读流意外结束，请重试。')
      }
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  const cancel = () => {
    request.current?.abort()
    setBusy(false)
  }

  return <section className="graph-summary" aria-label="AI 关系总结">
    <div className="graph-summary-head">
      <button className="graph-act primary graph-summary-run" disabled={busy} onClick={() => void summarize()}>
        <Icon name="sparkles" size={12} />
        {busy ? '正在解读…' : error ? '重试 AI 解读' : text ? '重新解读' : 'AI 解读关系'}
      </button>
      {busy
        ? <button className="graph-act" onClick={cancel}>取消</button>
        : <span className="graph-summary-scope">覆盖全部 {result?.neighbor_count ?? neighborCount} 个直接邻点</span>}
    </div>
    <div className="graph-summary-output" aria-live={busy ? 'off' : 'polite'} aria-busy={busy}>
      {text ? <Markdown text={text} /> : null}
      {error ? <p className="graph-summary-error" role="alert">{error}</p> : null}
      {!text && !error
        ? <p className="graph-hint">{busy
          ? `正在梳理这个节点与全部直接邻点的关联… ${elapsed}s；首个片段到达后会立即显示。`
          : '只依据图谱里的直接关系解释，不会扩展到二跳节点。'}</p>
        : null}
    </div>
  </section>
}
