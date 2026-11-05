import type { ReactNode } from 'react'
import { useCallback, useMemo, useState } from 'react'
import type { TreeNode } from '../types'
import { useApp } from '../state/AppState'

interface FileTreeProps {
  nodes: TreeNode[]
  dirtyPaths: Set<string>
}

/** Recursive read-only file tree; directory state is kept in a flat path set. */
export function FileTree({ nodes, dirtyPaths }: FileTreeProps): ReactNode {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const { openFile, reveal } = useApp()

  const toggle = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  return (
    <div className="tree" role="tree">
      {nodes.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          depth={0}
          expanded={expanded}
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
  onToggle: (path: string) => void
  selectedPath: string | null
  onOpenFile: (path: string) => void
  dirtyPaths: Set<string>
}

function TreeRow({
  node,
  depth,
  expanded,
  onToggle,
  selectedPath,
  onOpenFile,
  dirtyPaths,
}: TreeRowProps): ReactNode {
  const isOpen = expanded.has(node.path)
  const isSelected = !node.is_dir && selectedPath === node.path
  const dirty = useMemo(() => dirtyPaths.has(node.path), [dirtyPaths, node.path])

  const className = ['tree-node', node.is_dir ? 'dir' : 'file', isSelected ? 'selected' : '', dirty ? 'dirty' : '']
    .filter(Boolean)
    .join(' ')

  const onClick = () => {
    if (node.is_dir) onToggle(node.path)
    else onOpenFile(node.path)
  }

  return (
    <>
      <div
        className={className}
        style={{ paddingLeft: 6 + depth * 12 }}
        role="treeitem"
        aria-expanded={node.is_dir ? isOpen : undefined}
        title={node.path}
        onClick={onClick}
      >
        <span className="twist">{node.is_dir ? (isOpen ? '▼' : '▶') : ''}</span>
        <span className="icon">{node.is_dir ? (isOpen ? '▾' : '▸') : '·'}</span>
        <span className="label">{node.name}</span>
      </div>
      {node.is_dir && isOpen && node.children
        ? node.children.map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              selectedPath={selectedPath}
              onOpenFile={onOpenFile}
              dirtyPaths={dirtyPaths}
            />
          ))
        : null}
    </>
  )
}
