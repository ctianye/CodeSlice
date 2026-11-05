import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'
import * as api from '../api'
import type { ModelConfig, ModelProtocol, ModelSettings, ModelTestResult } from '../types'
import { usePersistedState } from '../hooks/useAsync'
import { useApp } from '../state/AppState'
import { CommandRulesPanel } from './CommandRulesPanel'
import { ErrorNote, Loading } from './Feedback'

type Tab = 'model' | 'rules' | 'about'

interface SettingsModalProps {
  onClose: () => void
}

/** Settings dialog: model configuration, command rules, and build info. */
export function SettingsModal({ onClose }: SettingsModalProps): ReactNode {
  const [tab, setTab] = usePersistedState<Tab>('codeslice.settingsTab', 'model')

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h2>设置</h2>
          <button className="ghost small" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="modal-tabs">
          <button className={tab === 'model' ? 'active' : ''} onClick={() => setTab('model')}>
            模型
          </button>
          <button className={tab === 'rules' ? 'active' : ''} onClick={() => setTab('rules')}>
            命令规则
          </button>
          <button className={tab === 'about' ? 'active' : ''} onClick={() => setTab('about')}>
            关于
          </button>
        </div>

        <div className="modal-body">
          {tab === 'model' ? <ModelSettingsForm /> : null}
          {tab === 'rules' ? <CommandRulesPanel /> : null}
          {tab === 'about' ? <AboutPanel /> : null}
        </div>
      </div>
    </div>
  )
}

function ModelSettingsForm(): ReactNode {
  const [settings, setSettings] = useState<ModelSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null)

  const [apiKey, setApiKey] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .getModelSettings()
      .then((next) => {
        if (cancelled) return
        setSettings(next)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading) return <Loading text="正在读取模型配置…" />
  if (loadError) return <ErrorNote message={loadError} />
  if (!settings) return <ErrorNote message="未能读取模型配置。" />

  const chat = settings.chat

  const patchChat = (patch: Partial<ModelConfig>) => {
    setSaveMessage(null)
    setSettings((prev) => (prev ? { ...prev, chat: { ...prev.chat, ...patch } } : prev))
  }

  const save = async () => {
    setSaving(true)
    setActionError(null)
    setSaveMessage(null)
    try {
      const body = {
        chat: {
          protocol: chat.protocol,
          model: chat.model,
          base_url: chat.base_url,
          temperature: chat.temperature,
          max_tokens: chat.max_tokens,
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        },
        embedding: settings.embedding,
      }
      const next = await api.updateModelSettings(body)
      setSettings(next)
      setApiKey('')
      setSaveMessage('已保存。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setActionError(null)
    setTestResult(null)
    try {
      setTestResult(await api.testModel())
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setTesting(false)
    }
  }

  const clearKey = async () => {
    if (!window.confirm('确定要清除本机保存的 API Key 吗？')) return
    setClearing(true)
    setActionError(null)
    setSaveMessage(null)
    try {
      await api.clearModelKey('chat')
      setSettings(await api.getModelSettings())
      setApiKey('')
      setSaveMessage('已清除保存的密钥。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setClearing(false)
    }
  }

  return (
    <div>
      <div className="section-label">对话模型</div>

      <div className="form-grid">
        <div className="field">
          <label htmlFor="protocol">协议</label>
          <select
            id="protocol"
            value={chat.protocol}
            onChange={(event) => patchChat({ protocol: event.target.value as ModelProtocol })}
          >
            <option value="openai-compatible">OpenAI 兼容</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>

        <div className="field">
          <label htmlFor="model">模型名称</label>
          <input
            id="model"
            placeholder="例如 deepseek-chat"
            value={chat.model}
            onChange={(event) => patchChat({ model: event.target.value })}
          />
        </div>

        <div className="field full">
          <label htmlFor="base-url">接口地址（可选）</label>
          <input
            id="base-url"
            placeholder="留空使用服务商默认地址"
            value={chat.base_url ?? ''}
            onChange={(event) => patchChat({ base_url: event.target.value || null })}
          />
          <span className="hint">本地代理或自建网关时填写完整的 API Base URL。</span>
        </div>

        <div className="field">
          <label htmlFor="temperature">温度</label>
          <input
            id="temperature"
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={chat.temperature}
            onChange={(event) => patchChat({ temperature: Number(event.target.value) })}
          />
        </div>

        <div className="field">
          <label htmlFor="max-tokens">最大输出长度</label>
          <input
            id="max-tokens"
            type="number"
            min={256}
            max={32000}
            step={256}
            value={chat.max_tokens}
            onChange={(event) => patchChat({ max_tokens: Number(event.target.value) })}
          />
          <span className="hint">单次回答允许生成的最大 token 数。</span>
        </div>

        <div className="field">
          <label htmlFor="api-key">
            API Key
            {chat.api_key_configured ? <span className="configured">已配置</span> : null}
          </label>
          <input
            id="api-key"
            type="password"
            autoComplete="off"
            placeholder={chat.api_key_configured ? '已保存，留空表示不修改' : '填入你的 API Key'}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <span className="hint">密钥仅写入本地系统钥匙串，接口不会回显明文。</span>
          {chat.api_key_configured ? (
            <button
              className="ghost small"
              onClick={() => void clearKey()}
              disabled={clearing}
              title="删除本机保存的 API Key"
            >
              {clearing ? '清除中…' : '清除已保存的密钥'}
            </button>
          ) : null}
        </div>
      </div>

      {actionError ? <ErrorNote message={actionError} /> : null}
      {saveMessage ? <div className="test-result ok">{saveMessage}</div> : null}
      {testResult ? (
        <div className={`test-result ${testResult.ok ? 'ok' : 'err'}`}>
          {testResult.ok ? '连通性正常' : '连接失败'}
          {testResult.latency_ms !== null ? ` · ${testResult.latency_ms} ms` : ''}
          {'\n'}
          {testResult.detail}
        </div>
      ) : null}

      <div className="form-actions">
        <button className="primary" onClick={() => void save()} disabled={saving || chat.model.trim() === ''}>
          {saving ? '保存中…' : '保存配置'}
        </button>
        <button onClick={() => void test()} disabled={testing}>
          {testing ? '测试中…' : '测试连接'}
        </button>
        <span className="grow">
          {settings.embedding ? `向量模型：${settings.embedding.model}` : '未配置向量模型，检索将退回关键词模式'}
        </span>
      </div>
    </div>
  )
}

function AboutPanel(): ReactNode {
  const [health, setHealth] = useState<{ version: string; data_dir: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { project } = useApp()

  useEffect(() => {
    let cancelled = false
    api
      .getHealth()
      .then((next) => {
        if (!cancelled) setHealth(next)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div>
      <div className="section-label">关于 CodeSlice</div>
      <p style={{ margin: '0 0 8px', fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.8 }}>
        本地优先的代码阅读助手。所有代码与对话数据都保存在本机，模型请求直接由你配置的服务商处理。
      </p>

      {error ? <ErrorNote message={error} /> : null}
      {health ? (
        <div className="test-result">
          <div>后端版本：{health.version}</div>
          <div>数据目录：{health.data_dir}</div>
          <div>当前项目：{project ? `${project.name}（${project.path}）` : '未选择'}</div>
        </div>
      ) : null}

      <div className="section-label">交互提示</div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 11.5, color: 'var(--text-faint)', lineHeight: 1.9 }}>
        <li>在编辑器中拖选代码行，点击「就这段提问」创建片段对话。</li>
        <li>回答中的引用芯片可跳转到对应文件与行号。</li>
        <li>涉及写文件或执行命令时，Agent 会暂停并等待你确认。</li>
      </ul>
    </div>
  )
}
