import type { ReactNode } from 'react'
import { useCallback, useMemo, useState } from 'react'
import * as api from '../api'
import type { TreeNode } from '../types'
import { useApp } from '../state/AppState'
import { ErrorNote, Loading } from './Feedback'
import { Icon } from './Icon'

interface FileTreeProps {
  nodes: TreeNode[]
  dirtyPaths: Set<string>
}

/**
 * Recursive read-only file tree.
 *
 * The API only returns the first two levels eagerly (deeper nodes carry
 * `children: null`), so expanding a directory whose children were not sent
 * fetches that one directory on demand. Without this a nested folder such as
 * `backend/app` looked clickable but never revealed anything.
 */
export function FileTree({ nodes, dirtyPaths }: FileTreeProps): ReactNode {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [loaded, setLoaded] = useState<Record<string, TreeNode[]>>({})
  const [loading, setLoading] = useState<Set<string>>(() => new Set())
  const [error, setError] = useState<string | null>(null)
  const { openFile, project, reveal } = useApp()
  const projectId = project?.id ?? null

  const toggle = useCallback(
    (node: TreeNode) => {
      const path = node.path
      const willOpen = !expanded.has(path)
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(path)) next.delete(path)
        else next.add(path)
        return next
      })
      if (!willOpen || node.children !== null || loaded[path] || projectId === null) return

      setLoading((prev) => new Set(prev).add(path))
      setError(null)
      api
        .getTree(projectId, path, 2)
        .then((children) => setLoaded((prev) => ({ ...prev, [path]: children })))
        .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
        .finally(() =>
          setLoading((prev) => {
            const next = new Set(prev)
            next.delete(path)
            return next
          }),
        )
    },
    [expanded, loaded, projectId],
  )

  return (
    <div className="tree" role="tree" aria-label="项目文件树">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      {nodes.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          depth={0}
          expanded={expanded}
          loading={loading}
          loaded={loaded}
          onToggle={toggle}
          selectedPath={openFile?.path ?? null}
          onOpenFile={reveal}
          dirtyPaths={dirtyPaths}
        />
      ))}
    </div>
  )
}

interface TreeRowProps {
  node: TreeNode
  depth: number
  expanded: Set<string>
  loading: Set<string>
  loaded: Record<string, TreeNode[]>
  onToggle: (node: TreeNode) => void
  selectedPath: string | null
  onOpenFile: (path: string) => void
  dirtyPaths: Set<string>
}

function TreeRow({
  node,
  depth,
  expanded,
  loading,
  loaded,
  onToggle,
  selectedPath,
  onOpenFile,
  dirtyPaths,
}: TreeRowProps): ReactNode {
  const isOpen = expanded.has(node.path)
  const isSelected = !node.is_dir && selectedPath === node.path
  const dirty = useMemo(() => dirtyPaths.has(node.path), [dirtyPaths, node.path])
  const children = node.children ?? loaded[node.path] ?? null
  const isLoading = loading.has(node.path)

  const className = [
    'tree-node',
    node.is_dir ? 'dir' : 'file',
    isSelected ? 'selected' : '',
    dirty ? 'dirty' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <>
      <button
        type="button"
        className={className}
        style={{ paddingLeft: 6 + depth * 14 }}
        role="treeitem"
        aria-expanded={node.is_dir ? isOpen : undefined}
        aria-selected={isSelected}
        aria-busy={isLoading || undefined}
        title={node.path}
        onClick={() => (node.is_dir ? onToggle(node) : onOpenFile(node.path))}
      >
        {node.is_dir ? (
          <span className={`twist${isOpen ? ' open' : ''}`}>
            <Icon name="chevron-right" size={12} />
          </span>
        ) : (
          <span className="twist" aria-hidden="true" />
        )}
        <span className="icon">
          <Icon
            name={node.is_dir ? (isOpen ? 'folder-open' : 'folder') : isCodeLike(node.name) ? 'file-code' : 'file'}
            size={13}
          />
        </span>
        <span className="label">{node.name}</span>
        {isLoading ? <span className="tree-spinner" aria-hidden="true" /> : null}
      </button>

      {node.is_dir && isOpen ? (
        isLoading && children === null ? (
          <div className="tree-loading" style={{ paddingLeft: 18 + (depth + 1) * 14 }}>
            <Loading text="加载中…" />
          </div>
        ) : (
          (children ?? []).map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              loading={loading}
              loaded={loaded}
              onToggle={onToggle}
              selectedPath={selectedPath}
              onOpenFile={onOpenFile}
              dirtyPaths={dirtyPaths}
            />
          ))
        )
      ) : null}
    </>
  )
}

/** Files that can be opened in the editor get the code glyph. */
const CODE_EXTENSIONS = new Set([
  '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.py', '.go', '.rs', '.java', '.kt',
  '.c', '.h', '.cpp', '.hpp', '.cs', '.rb', '.php', '.swift', '.lua', '.sh', '.ps1',
  '.sql', '.vue', '.svelte', '.css', '.scss', '.less', '.html', '.json', '.toml',
  '.yaml', '.yml', '.ini', '.md',
])

function isCodeLike(name: string): boolean {
  const dot = name.lastIndexOf('.')
  if (dot < 0) return false
  return CODE_EXTENSIONS.has(name.slice(dot).toLowerCase())
}
