import { useEffect, useRef, useState } from 'react'
import * as api from '../../api'
import type { GraphNode } from '../../types'
import { useApp } from '../../state/AppState'
import { Icon } from '../Icon'

/** Explicit navigation: opening a conversation alone never moves its graph. */
export function ConversationGraphLink({ conversationId }: { conversationId: string }) {
  const { openConversationGraph } = useApp()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [choices, setChoices] = useState<{ projectId: number; nodes: GraphNode[] } | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  const navigate = (projectId: number, nodeId: string) => {
    if (!openConversationGraph(projectId, conversationId, nodeId)) {
      setError('关联项目尚未加载，请重新打开该项目后再定位。')
    }
  }
  const locate = async () => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError(null)
    setChoices(null)
    try {
      const result = await api.getConversationGraphTargets(conversationId, controller.signal)
      if (controller.signal.aborted) return
      if (!result.targets.length) setError('这段对话暂无可定位的代码节点。请先建立项目索引，或在对话中引用具体代码。')
      else if (result.targets.length === 1) navigate(result.project_id, result.targets[0].id)
      else setChoices({ projectId: result.project_id, nodes: result.targets })
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err))
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <div className="conversation-graph-link">
    <button className="small ghost" disabled={busy} onClick={() => void locate()} title="在代码图谱中展开并放大到这段对话关联的节点">
      <Icon name="network" size={13} />{busy ? '正在定位…' : '定位到代码图谱'}
    </button>
    {error && <span role="alert">{error}</span>}
    {choices && <div className="conversation-graph-choices">
      <label>选择要定位的代码
        <select aria-label="选择对话关联节点" defaultValue="" onChange={event => {
          if (event.target.value) { navigate(choices.projectId, event.target.value); setChoices(null) }
        }}>
          <option value="" disabled>这段对话涉及 {choices.nodes.length} 个位置</option>
          {choices.nodes.map(node => <option key={node.id} value={node.id}>{node.label} · {node.path}:{node.start_line}</option>)}
        </select>
      </label>
      <button className="small ghost" onClick={() => setChoices(null)}>取消</button>
    </div>}
  </div>
}
