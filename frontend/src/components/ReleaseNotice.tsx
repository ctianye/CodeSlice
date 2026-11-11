import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { openExternal } from '../bridge'
import type { AppRelease } from '../lib/releases'
import { Icon } from './Icon'

export function ReleaseDetails({ release }: { release: AppRelease }): ReactNode {
  return <div className="release-notice">
    <strong>发现新版本 v{release.version}</strong>
    <p className="release-notes">{release.notes.slice(0, 2000) || '此版本暂无更新说明。'}</p>
    <p>下载后请退出 CodeSlice，再安装新版本并覆盖原安装位置。正常升级会保留本机笔记、批注、对话和配置；请勿先删除应用数据。</p>
    <div className="release-actions">
      <button className="primary small" type="button" onClick={() => openExternal(release.downloadUrl ?? release.url)}>
        <Icon name="download" size={12} />{release.downloadUrl ? release.downloadLabel : '查看 Release 下载'}
      </button>
      <button className="ghost small" type="button" onClick={() => openExternal(release.url)}>更新详情</button>
    </div>
    <span className="hint">将在默认浏览器中下载；安装包未签名，首次启动请按 Release 页说明处理。</span>
  </div>
}

export function ReleaseNotice({ release, onClose }: { release: AppRelease; onClose: () => void }): ReactNode {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return <div className="modal-backdrop release-backdrop" onMouseDown={onClose}>
    <div className="modal small-modal" role="dialog" aria-modal="true" aria-label="发现新版本" onMouseDown={(event) => event.stopPropagation()}>
      <div className="modal-head"><Icon name="download" size={15} /><h2>CodeSlice 有更新</h2>
        <button autoFocus className="ghost icon" type="button" aria-label="稍后更新" onClick={onClose}><Icon name="x" size={14} /></button>
      </div>
      <div className="modal-body"><ReleaseDetails release={release} /></div>
      <div className="modal-foot"><button className="ghost small" type="button" onClick={onClose}>稍后提醒</button></div>
    </div>
  </div>
}
