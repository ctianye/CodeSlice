import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'
import { GraphApiError, updateAgentNode } from '../apiGraph'
import type { EditableField, GraphNode, GraphNodeConfig } from '../apiGraph'
import { Icon } from './Icon'

/** Form values are kept as strings so a half-typed number does not snap back. */
type Draft = Record<string, string | boolean>

/** Chinese label and hint for a known field; unknown keys fall back to the raw key. */
const FIELD_TEXT: Record<string, { label: string; hint?: string }> = {
  'prepare.retrieval_top_k': {
    label: '检索条数',
    hint: '每次检索取回多少段代码（越多上下文越全，也越费 token）',
  },
  'prepare.include_git_branch': { label: '带上 Git 分支', hint: '关闭后 system prompt 不再提及当前分支' },
  'agent.model': { label: '模型', hint: '留空则沿用设置里的模型；填写则覆盖本节点使用的模型名' },
  'agent.temperature': { label: 'temperature', hint: '0 最稳定，1 以上更发散' },
  'agent.max_tokens': { label: '最大输出 tokens', hint: '单次回答的上限' },
  'agent.extra_prompt': { label: '追加指令', hint: '追加在 system prompt 末尾，用来约束回答风格或关注点' },
  'agent.max_tool_rounds': { label: '工具轮上限', hint: '一轮对话里最多允许的推理/工具循环次数' },
  'run_one.max_tool_rounds': { label: '工具轮上限', hint: '与「模型推理」节点保持一致即可' },
  'run_one.auto_approve_readonly_tools': {
    label: '只读工具免确认',
    hint: 'list_files / read_file / search_code / git_diff 视为已批准',
  },
  'finalize.auto_retitle': { label: '自动重拟标题', hint: '回答保存后，把占位标题改成知识点标题' },
}

function fieldText(nodeId: string, key: string): { label: string; hint?: string } {
  return FIELD_TEXT[`${nodeId}.${key}`] ?? { label: key }
}

function draftFrom(node: GraphNode): Draft {
  const draft: Draft = {}
  for (const [key, spec] of Object.entries(node.editable)) {
    const value = node.config[key]
    draft[key] = spec.type === 'bool' ? Boolean(value) : String(value ?? '')
  }
  return draft
}

function defaultsDraft(node: GraphNode): Draft {
  const draft: Draft = {}
  for (const [key, spec] of Object.entries(node.editable)) {
    const value = node.defaults[key]
    draft[key] = spec.type === 'bool' ? Boolean(value) : String(value ?? '')
  }
  return draft
}

function sameDraft(a: Draft, b: Draft): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  for (const key of keys) {
    if (a[key] !== b[key]) return false
  }
  return true
}

/** Coerce the draft back into typed config; an unusable number keeps the old value. */
function payloadFrom(node: GraphNode, draft: Draft): GraphNodeConfig {
  const config: GraphNodeConfig = {}
  for (const [key, spec] of Object.entries(node.editable)) {
    const raw = draft[key]
    if (spec.type === 'bool') {
      config[key] = Boolean(raw)
      continue
    }
    const text = typeof raw === 'string' ? raw : ''
    if (spec.type === 'int' || spec.type === 'float') {
      const parsed = spec.type === 'int' ? Number.parseInt(text, 10) : Number.parseFloat(text)
      config[key] = Number.isFinite(parsed) ? parsed : (node.config[key] ?? 0)
      continue
    }
    config[key] = text
  }
  return config
}

interface FieldProps {
  fieldKey: string
  spec: EditableField
  nodeId: string
  value: string | boolean
  onChange: (next: string | boolean) => void
}

function Field({ fieldKey, spec, nodeId, value, onChange }: FieldProps): ReactNode {
  const text = fieldText(nodeId, fieldKey)
  const id = `graph-field-${nodeId}-${fieldKey}`
  if (spec.type === 'bool') {
    return (
      <div className="graph-field graph-field-bool">
        <label htmlFor={id}>
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(event) => onChange(event.target.checked)}
          />
          {text.label}
        </label>
        {text.hint ? <span className="hint">{text.hint}</span> : null}
      </div>
    )
  }
  if (spec.type === 'text') {
    return (
      <div className="graph-field graph-field-full">
        <label htmlFor={id}>{text.label}</label>
        <textarea
          id={id}
          rows={4}
          value={String(value)}
          placeholder="例如：回答尽量给出具体行号和调用链"
          onChange={(event) => onChange(event.target.value)}
        />
        {text.hint ? <span className="hint">{text.hint}</span> : null}
      </div>
    )
  }
  const numeric = spec.type === 'int' || spec.type === 'float'
  return (
    <div className="graph-field">
      <label htmlFor={id}>{text.label}</label>
      <input
        id={id}
        type={numeric ? 'number' : 'text'}
        value={String(value)}
        step={spec.type === 'float' ? 0.1 : undefined}
        min={spec.min}
        max={spec.max}
        onChange={(event) => onChange(event.target.value)}
      />
      {text.hint ? <span className="hint">{text.hint}</span> : null}
    </div>
  )
}

interface GraphNodeFormProps {
  node: GraphNode
  onSaved: (node: GraphNode) => void
}

/**
 * Inline editor for one workflow node.
 *
 * Saving is explicit and applies to the next turn only — nothing here touches the
 * running turn, so a half-finished edit can never corrupt an answer in flight.
 */
export function GraphNodeForm({ node, onSaved }: GraphNodeFormProps): ReactNode {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(node))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const baseline = useMemo(() => draftFrom(node), [node])
  const dirty = !sameDraft(draft, baseline)

  const set = (key: string, next: string | boolean) => {
    setDraft((prev) => ({ ...prev, [key]: next }))
    setSaved(false)
    setError(null)
  }

  const submit = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await updateAgentNode(node.id, payloadFrom(node, draft))
      setDraft(draftFrom(updated))
      setSaved(true)
      onSaved(updated)
    } catch (err) {
      setError(err instanceof GraphApiError ? err.detail : String(err))
    } finally {
      setSaving(false)
    }
  }

  const fields = Object.entries(node.editable)

  return (
    <div className="graph-form">
      {fields.length === 0 ? <p className="muted">该节点没有可配置项。</p> : null}
      <div className="graph-form-grid">
        {fields.map(([key, spec]) => (
          <Field
            key={key}
            fieldKey={key}
            spec={spec}
            nodeId={node.id}
            value={draft[key] ?? (spec.type === 'bool' ? false : '')}
            onChange={(next) => set(key, next)}
          />
        ))}
      </div>

      <div className="graph-form-actions">
        <button type="button" className="primary small" onClick={submit} disabled={saving || !dirty}>
          {saving ? '保存中…' : '保存'}
        </button>
        <button
          type="button"
          className="ghost small"
          onClick={() => {
            setDraft(defaultsDraft(node))
            setSaved(false)
            setError(null)
          }}
          disabled={saving}
        >
          恢复默认
        </button>
        <span className="spacer" />
        {dirty && !saving ? <span className="graph-dirty">未保存</span> : null}
      </div>

      {saved ? (
        <div className="graph-saved">
          <Icon name="check" size={12} /> 已保存，下一轮生效
        </div>
      ) : null}
      {error ? (
        <div className="graph-error" role="alert">
          <Icon name="alert" size={12} /> {error}
        </div>
      ) : null}
    </div>
  )
}
