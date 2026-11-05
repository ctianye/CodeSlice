import type { ReactNode } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api'
import type { EmbeddingConfig, IndexStatus, ModelSettings, OllamaPreset, OllamaStatus } from '../types'
import { openExternal } from '../bridge'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

/** Where the "install Ollama" button sends people. */
const OLLAMA_DOWNLOAD_URL = 'https://ollama.com/download'

const EMPTY_PROGRESS = { percent: 0, detail: '' }

interface VectorModelsPanelProps {
  /** Current stored embedding config; null when nothing is configured yet. */
  embedding: EmbeddingConfig | null
  /** Called with the server's answer after any successful write. */
  onChanged: (next: ModelSettings) => void
}

/**
 * Local embedding models: two curated Ollama presets, one-click pull with live
 * progress, and a collapsed manual form for anyone using a hosted API instead.
 *
 * The backend never writes this config on its own (`POST /ollama/pull` only
 * downloads), so "使用此模型" is the single place a switch happens.
 */
export function VectorModelsPanel({ embedding, onChanged }: VectorModelsPanelProps): ReactNode {
  const { project, bumpRefresh } = useApp()
  const [status, setStatus] = useState<OllamaStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [statusError, setStatusError] = useState<string | null>(null)

  const [pulling, setPulling] = useState<string | null>(null)
  const [progress, setProgress] = useState(EMPTY_PROGRESS)
  const [pullError, setPullError] = useState<string | null>(null)

  const [applying, setApplying] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [rebuildNeeded, setRebuildNeeded] = useState(false)
  const [rebuilding, setRebuilding] = useState(false)

  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [advModel, setAdvModel] = useState(embedding?.model ?? '')
  const [advBaseUrl, setAdvBaseUrl] = useState(embedding?.base_url ?? '')
  const [advApiKey, setAdvApiKey] = useState('')
  const [savingAdvanced, setSavingAdvanced] = useState(false)

  const pullController = useRef<AbortController | null>(null)

  const loadStatus = useCallback(() => {
    setLoading(true)
    setStatusError(null)
    api
      .getOllamaStatus()
      .then((next) => {
        setStatus(next)
        setLoading(false)
      })
      .catch((err: unknown) => {
        setStatusError(err instanceof Error ? err.message : String(err))
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  useEffect(
    () => () => {
      pullController.current?.abort()
    },
    [],
  )

  const applyPreset = async (preset: OllamaPreset) => {
    const baseUrl = status?.openai_base_url ?? 'http://127.0.0.1:11434/v1'

    // Switching models makes the stored vectors incomparable with new query
    // vectors, so a rebuild is required — warn before the user loses nothing
    // silently and gets keyword-only retrieval back.
    //
    // The authority here is the *index*, not `project.indexed_at`: that field is
    // a snapshot taken when the project was loaded, so it is still null right
    // after the user indexed the project in this same session. Ask the server
    // which models the chunks actually carry.
    if (project) {
      try {
        const info: IndexStatus = await api.getIndexStatus(project.id)
        const stale = info.embed_models.filter((name) => name !== preset.model)
        if (stale.length > 0) {
          const proceed = window.confirm('切换向量模型后需重建索引才能生效，是否继续？')
          if (!proceed) return
          setRebuildNeeded(true)
        }
      } catch {
        /* the confirmation is advisory: never block a switch on a probe failure */
      }
    }

    setApplying(preset.model)
    setActionError(null)
    setMessage(null)
    try {
      const next = await api.updateModelSettings({
        embedding: { model: preset.model, base_url: baseUrl, api_key: 'ollama' },
      })
      onChanged(next)
      setMessage(`已切换到 ${preset.label}，新索引将使用该模型生成向量。`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(null)
    }
  }

  const startPull = async (preset: OllamaPreset) => {
    if (pulling) return
    pullController.current?.abort()
    const controller = new AbortController()
    pullController.current = controller

    setPulling(preset.model)
    setPullError(null)
    setMessage(null)
    setProgress(EMPTY_PROGRESS)

    try {
      for await (const event of api.pullOllamaModel(preset.model, controller.signal)) {
        if (controller.signal.aborted) return
        if (event.type === 'progress') {
          setProgress({
            percent: typeof event.percent === 'number' ? event.percent : 0,
            detail: event.detail ?? event.status ?? '',
          })
        } else if (event.type === 'error') {
          setPullError(event.detail ?? '下载失败')
        } else {
          break
        }
      }
      setMessage(`${preset.label} 已下载完成，点击「使用此模型」启用。`)
    } catch (err) {
      if (!controller.signal.aborted) {
        setPullError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      if (!controller.signal.aborted) {
        setPulling(null)
        setProgress(EMPTY_PROGRESS)
        loadStatus()
      }
    }
  }

  const rebuild = async () => {
    if (!project) return
    setRebuilding(true)
    setActionError(null)
    try {
      await api.startIndex(project.id)
      bumpRefresh()
      setRebuildNeeded(false)
      setMessage('已开始重建索引，可在左侧文件面板查看进度。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setRebuilding(false)
    }
  }

  const saveAdvanced = async () => {
    setSavingAdvanced(true)
    setActionError(null)
    setMessage(null)
    try {
      const body: { model: string; base_url: string | null; api_key?: string } = {
        model: advModel.trim(),
        base_url: advBaseUrl.trim() || null,
      }
      if (advApiKey.trim()) body.api_key = advApiKey.trim()
      const next = await api.updateModelSettings({ embedding: body })
      onChanged(next)
      setAdvApiKey('')
      setMessage('已保存手动配置的向量模型。')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingAdvanced(false)
    }
  }

  if (loading) return <Loading text="正在检测本地 Ollama…" />
  if (statusError) return <ErrorNote message={statusError} onRetry={loadStatus} />
  if (!status) return <ErrorNote message="未能读取 Ollama 状态。" />

  const installedModels = status.models
  const activeModel = embedding?.model ?? ''
  const activePreset = status.presets.find((preset) => preset.model === activeModel)

  const isInstalled = (preset: OllamaPreset): boolean =>
    installedModels.includes(preset.model) || installedModels.includes(`${preset.model}:latest`)

  return (
    <div className="vector-models">
      <div className="section-label">
        向量模型
        <button
          className="ghost icon tiny"
          onClick={loadStatus}
          title="重新检测 Ollama"
          aria-label="重新检测 Ollama"
          type="button"
        >
          <Icon name="refresh" size={11} />
        </button>
      </div>

      <p className="vector-models-lede">
        使用本地 Ollama 生成代码向量，检索可走语义模式。模型只下载到本机，不上传任何代码。
      </p>

      {status.installed ? (
        <div className="ollama-status ok">
          <Icon name="check" size={12} />
          <span>
            已检测到 Ollama{status.version ? ` ${status.version}` : ''} · {status.base_url}
          </span>
        </div>
      ) : (
        <div className="ollama-status warn">
          <Icon name="alert" size={12} />
          <span className="grow">未检测到本地 Ollama，无法下载或运行本地向量模型。</span>
          <button className="small" onClick={() => openExternal(OLLAMA_DOWNLOAD_URL)} type="button">
            <Icon name="external" size={11} />
            安装 Ollama
          </button>
        </div>
      )}

      <div className="preset-grid">
        {status.presets.map((preset) => {
          const installed = isInstalled(preset)
          const inUse = activeModel === preset.model
          const isPulling = pulling === preset.model
          const disabled = !status.installed || Boolean(pulling)

          return (
            <div
              key={preset.id}
              className={`preset-card${inUse ? ' active' : ''}${status.installed ? '' : ' disabled'}`}
            >
              <div className="preset-head">
                <strong>{preset.label}</strong>
                {inUse ? (
                  <span className="badge ok">
                    <Icon name="check" size={9} />
                    使用中
                  </span>
                ) : installed ? (
                  <span className="badge info">已安装</span>
                ) : (
                  <span className="badge mute">未安装</span>
                )}
              </div>

              <div className="preset-meta">
                <span>{preset.size}</span>
                <span>·</span>
                <span>{preset.context} 上下文</span>
                <span>·</span>
                <span>{preset.params} 参数</span>
              </div>

              <p className="preset-desc">{preset.description}</p>

              <div className="preset-foot">
                <span className="preset-req">
                  <Icon name="cpu" size={10} />
                  {preset.requirement}
                </span>

                {isPulling ? (
                  <span className="preset-progress">
                    <span className="progress-track">
                      <span className="progress-fill" style={{ width: `${progress.percent}%` }} />
                    </span>
                    <span className="preset-percent">{Math.round(progress.percent)}%</span>
                  </span>
                ) : inUse ? null : installed ? (
                  <button
                    className="primary small"
                    onClick={() => void applyPreset(preset)}
                    disabled={Boolean(applying) || !status.installed}
                    type="button"
                  >
                    <Icon name="check" size={11} />
                    {applying === preset.model ? '切换中…' : '使用此模型'}
                  </button>
                ) : (
                  <button
                    className="small"
                    onClick={() => void startPull(preset)}
                    disabled={disabled}
                    title={status.installed ? '从 Ollama 下载该模型' : '需要先安装 Ollama'}
                    type="button"
                  >
                    <Icon name="download" size={11} />
                    下载
                  </button>
                )}
              </div>

              {isPulling && progress.detail ? (
                <div className="preset-detail">{progress.detail}</div>
              ) : null}
            </div>
          )
        })}
      </div>

      {pullError ? <ErrorNote message={pullError} /> : null}
      {actionError ? <ErrorNote message={actionError} /> : null}

      {rebuildNeeded && project ? (
        <div className="test-result err">
          <Icon name="alert" size={12} />
          <span className="grow">当前项目的向量还是用旧模型生成的，检索前需要重建索引。</span>
          <button className="small" onClick={() => void rebuild()} disabled={rebuilding} type="button">
            <Icon name="refresh" size={11} />
            {rebuilding ? '正在启动…' : '重建索引'}
          </button>
        </div>
      ) : null}

      {message ? (
        <div className="test-result ok">
          <Icon name="check" size={12} />
          {message}
        </div>
      ) : null}

      <div className="preset-summary">
        {activePreset
          ? `当前向量模型：${activePreset.label}（${status.openai_base_url}）`
          : embedding?.model
            ? `当前向量模型：${embedding.model}${embedding.base_url ? `（${embedding.base_url}）` : ''}`
            : '未配置向量模型，检索将退回关键词模式'}
      </div>

      <button
        className="advanced-toggle"
        onClick={() => setAdvancedOpen((prev) => !prev)}
        aria-expanded={advancedOpen}
        type="button"
      >
        <Icon name={advancedOpen ? 'chevron-down' : 'chevron-right'} size={11} />
        高级：手动配置
      </button>

      {advancedOpen ? (
        <div className="advanced-form">
          <div className="form-grid">
            <div className="field">
              <label htmlFor="embedding-model">模型名称</label>
              <input
                id="embedding-model"
                placeholder="例如 text-embedding-3-small"
                value={advModel}
                onChange={(event) => setAdvModel(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="embedding-base-url">Base URL</label>
              <input
                id="embedding-base-url"
                placeholder="留空使用服务商默认地址"
                value={advBaseUrl}
                onChange={(event) => setAdvBaseUrl(event.target.value)}
              />
            </div>
            <div className="field full">
              <label htmlFor="embedding-api-key">
                API Key
                {embedding?.api_key_configured ? (
                  <span className="configured">
                    <Icon name="check" size={10} />
                    已配置
                  </span>
                ) : null}
              </label>
              <input
                id="embedding-api-key"
                type="password"
                autoComplete="off"
                placeholder={embedding?.api_key_configured ? '已保存，留空表示不修改' : '云端服务商需要，本地 Ollama 填 ollama'}
                value={advApiKey}
                onChange={(event) => setAdvApiKey(event.target.value)}
              />
              <span className="hint">密钥写入系统钥匙串；本地 Ollama 不校验，但字段不能为空。</span>
            </div>
          </div>
          <div className="form-actions">
            <button
              className="primary"
              onClick={() => void saveAdvanced()}
              disabled={savingAdvanced || advModel.trim() === ''}
              type="button"
            >
              <Icon name="check" size={12} />
              {savingAdvanced ? '保存中…' : '保存手动配置'}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
