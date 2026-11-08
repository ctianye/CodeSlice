import type { ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, forceZ } from 'd3-force-3d'
import type { Note, NoteFolder } from '../types'
import { Icon } from './Icon'
import { buildNoteConnections, noteCategory as categoryFor } from './notesGraph'

const PALETTE = ['#44c2b1', '#e2a64f', '#e7798e', '#75a9ee', '#9c86dc', '#83bd73', '#dc83c2', '#58b8d6']

interface GraphCategory {
  id: string
  label: string
  color: string
  count: number
}

interface NotesGraphModalProps {
  notes: Note[]
  folders: NoteFolder[]
  onClose: () => void
  onOpen: (note: Note) => void
}

export function NotesGraphModal({ notes, folders, onClose, onOpen }: NotesGraphModalProps): ReactNode {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const resetRef = useRef<() => void>(() => undefined)
  const focusRef = useRef<(id: number) => void>(() => undefined)
  const filterRef = useRef<(ids: Set<number>) => void>(() => undefined)
  const selectedIdRef = useRef<number | null>(null)
  const rotationRef = useRef(true)
  const nodeMeshesRef = useRef<THREE.Mesh[]>([])
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const [graphicsError, setGraphicsError] = useState(false)
  const [hoverTitle, setHoverTitle] = useState('')
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(() => new Set())
  const folderNames = useMemo(() => new Map(folders.map((folder) => [folder.id, folder.name])), [folders])
  const categories = useMemo<GraphCategory[]>(() => {
    const grouped = new Map<string, { label: string; count: number }>()
    for (const note of notes) {
      const id = categoryFor(note)
      const label = note.folder_id != null
        ? folderNames.get(note.folder_id) ?? '未分类'
        : note.project_name ?? '未分类'
      const value = grouped.get(id) ?? { label, count: 0 }
      value.count += 1
      grouped.set(id, value)
    }
    return [...grouped].map(([id, item], index) => ({ ...item, id, color: PALETTE[index % PALETTE.length] }))
  }, [notes, folderNames])
  const visibleNotes = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    return notes.filter((note) => {
      if (hiddenCategories.has(categoryFor(note))) return false
      return !needle || `${note.title}\n${note.content_md}\n${note.project_name ?? ''}`.toLocaleLowerCase().includes(needle)
    })
  }, [notes, hiddenCategories, query])
  const connections = useMemo(() => buildNoteConnections(notes), [notes])
  const visibleIds = useMemo(() => new Set(visibleNotes.map((note) => note.id)), [visibleNotes])
  const visibleConnectionCount = useMemo(() => connections.reduce((count, [a, b]) => count + Number(visibleIds.has(a) && visibleIds.has(b)), 0), [connections, visibleIds])
  const selectedNote = notes.find((note) => note.id === selectedId) ?? null
  const visibleIdsRef = useRef(visibleIds)
  const onPickRef = useRef<(id: number) => void>(() => undefined)
  visibleIdsRef.current = visibleIds
  rotationRef.current = autoRotate
  onPickRef.current = (id) => {
    selectedIdRef.current = id
    setSelectedId(id)
  }

  useEffect(() => {
    filterRef.current(visibleIds)
    setHoverTitle('')
    if (selectedId !== null && !visibleIds.has(selectedId)) {
      setSelectedId(null)
      selectedIdRef.current = null
    }
  }, [selectedId, visibleIds])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const scene = new THREE.Scene()
    scene.background = new THREE.Color('#0b1118')
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 200)
    camera.position.set(0, 0, 14)
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false })
      setGraphicsError(false)
    } catch {
      setGraphicsError(true)
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.8))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 1.15
    const controls = new OrbitControls(camera, canvas)
    controlsRef.current = controls
    controls.enableDamping = true
    controls.dampingFactor = 0.07
    controls.minDistance = 1.5
    controls.maxDistance = 120
    controls.autoRotate = true
    controls.autoRotateSpeed = 0.22
    scene.add(new THREE.AmbientLight(0xc6d7ed, 1.2))
    const keyLight = new THREE.PointLight(0x76cabb, 55, 80)
    keyLight.position.set(2, 8, 10)
    scene.add(keyLight)
    const rimLight = new THREE.PointLight(0xe1a55d, 34, 70)
    rimLight.position.set(-9, -5, -8)
    scene.add(rimLight)

    const categoriesNow = categories
    const notesNow = notes
    const categoryPositions = new Map<string, THREE.Vector3>()
    categoriesNow.forEach((category, index) => {
      const angle = (index / Math.max(1, categoriesNow.length)) * Math.PI * 2 - Math.PI / 2
      const ring = categoriesNow.length > 1 ? 3.4 : 0
      const elevation = [0.48, -0.4, 0.7, -0.62, 0.3, -0.7][index % 6]
      categoryPositions.set(category.id, new THREE.Vector3(
        Math.cos(angle) * ring * Math.cos(elevation),
        Math.sin(angle) * ring * Math.cos(elevation),
        Math.sin(elevation) * ring,
      ))
    })
    const nodeMeshes: THREE.Mesh[] = []
    const pointById = new Map<number, THREE.Vector3>()
    const noteGroups = new Map<string, Note[]>()
    for (const note of notesNow) {
      const key = categoryFor(note)
      const group = noteGroups.get(key) ?? []
      group.push(note)
      noteGroups.set(key, group)
    }
    const layoutNodes = notesNow.map((note, index) => {
      const center = categoryPositions.get(categoryFor(note)) ?? new THREE.Vector3()
      const angle = index * 2.399963
      const radius = 0.4 + Math.sqrt(index % 9) * 0.2
      return {
        id: note.id,
        categoryId: categoryFor(note),
        x: center.x + Math.cos(angle) * radius,
        y: center.y + Math.sin(angle * 1.27) * radius,
        z: center.z + Math.cos(angle * 0.93) * radius,
      }
    })
    const simulationLinks = connections.map(([source, target]) => ({ source, target }))
    const layout = forceSimulation(layoutNodes, 3)
      .force('link', forceLink<(typeof layoutNodes)[number]>(simulationLinks).id((node) => node.id).distance(0.9).strength(0.04))
      .force('charge', forceManyBody<(typeof layoutNodes)[number]>().strength(-0.3))
      .force('collide', forceCollide(0.3))
      .force('center', forceCenter(0, 0, 0))
      .force('clusterX', forceX<(typeof layoutNodes)[number]>((node) => categoryPositions.get(node.categoryId)?.x ?? 0).strength(0.65))
      .force('clusterY', forceY<(typeof layoutNodes)[number]>((node) => categoryPositions.get(node.categoryId)?.y ?? 0).strength(0.65))
      .force('clusterZ', forceZ<(typeof layoutNodes)[number]>((node) => categoryPositions.get(node.categoryId)?.z ?? 0).strength(0.65))
      .stop()
    // Search/filtering keeps this layout and WebGL context intact. Cap work for larger libraries.
    layout.tick(Math.max(35, Math.min(150, Math.floor(60000 / Math.max(1, notesNow.length)))))
    let layoutRadius = 1
    for (const node of layoutNodes) layoutRadius = Math.max(layoutRadius, Math.hypot(node.x, node.y, node.z))
    const layoutScale = Math.min(1, 5.5 / layoutRadius)
    for (const node of layoutNodes) { node.x *= layoutScale; node.y *= layoutScale; node.z *= layoutScale }
    for (const center of categoryPositions.values()) center.multiplyScalar(layoutScale)
    const layoutById = new Map(layoutNodes.map((node) => [node.id, node]))
    const nodeGeometry = new THREE.SphereGeometry(0.095, 12, 8)
    const hubs: THREE.Mesh[] = []
    for (const [categoryId, group] of noteGroups) {
      const categoryIndexNow = categoriesNow.findIndex((category) => category.id === categoryId)
      const color = new THREE.Color(categoriesNow[categoryIndexNow]?.color ?? PALETTE[0])
      const center = categoryPositions.get(categoryId) ?? new THREE.Vector3()
      group.forEach((note) => {
        const layoutNode = layoutById.get(note.id)
        const point = new THREE.Vector3(layoutNode?.x ?? center.x, layoutNode?.y ?? center.y, layoutNode?.z ?? center.z)
        pointById.set(note.id, point)
        const material = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.18, roughness: 0.36, metalness: 0.22 })
        const mesh = new THREE.Mesh(nodeGeometry, material)
        mesh.position.copy(point)
        mesh.userData.noteId = note.id
        mesh.userData.categoryIndex = categoryIndexNow
        mesh.userData.title = note.title
        scene.add(mesh)
        nodeMeshes.push(mesh)
      })
      const hubMaterial = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.34, roughness: 0.3, metalness: 0.28 })
      const hub = new THREE.Mesh(new THREE.IcosahedronGeometry(0.24, 1), hubMaterial)
      hub.position.copy(center)
      hub.userData.categoryId = categoryId
      hubs.push(hub)
      scene.add(hub)
    }
    nodeMeshesRef.current = nodeMeshes

    const edgeGeometry = new THREE.BufferGeometry()
    const edgeBuffer = new THREE.Float32BufferAttribute(new Float32Array(connections.length * 6), 3)
    edgeBuffer.setUsage(THREE.DynamicDrawUsage)
    edgeGeometry.setAttribute('position', edgeBuffer)
    const lines = new THREE.LineSegments(edgeGeometry, new THREE.LineBasicMaterial({ color: '#8ea0b6', transparent: true, opacity: 0.28 }))
    lines.frustumCulled = false
    scene.add(lines)
    const filter = (ids: Set<number>) => {
      const visibleCategories = new Set(notesNow.filter((note) => ids.has(note.id)).map(categoryFor))
      for (const mesh of nodeMeshes) mesh.visible = ids.has(Number(mesh.userData.noteId))
      for (const hub of hubs) hub.visible = visibleCategories.has(String(hub.userData.categoryId))
      let vertex = 0
      for (const [from, to] of connections) {
        if (!ids.has(from) || !ids.has(to)) continue
        const a = pointById.get(from)
        const b = pointById.get(to)
        if (!a || !b) continue
        edgeBuffer.setXYZ(vertex++, a.x, a.y, a.z)
        edgeBuffer.setXYZ(vertex++, b.x, b.y, b.z)
      }
      edgeGeometry.setDrawRange(0, vertex)
      edgeBuffer.needsUpdate = true
    }
    filterRef.current = filter
    filter(visibleIdsRef.current)

    const fit = () => {
      const bounds = new THREE.Box3()
      for (const mesh of nodeMeshes) if (mesh.visible) bounds.expandByPoint(mesh.position)
      const sphere = bounds.isEmpty() ? new THREE.Sphere(new THREE.Vector3(), 2) : bounds.getBoundingSphere(new THREE.Sphere())
      const vertical = THREE.MathUtils.degToRad(camera.fov / 2)
      const horizontal = Math.atan(Math.tan(vertical) * camera.aspect)
      const distance = Math.max(3, (sphere.radius + 0.4) / Math.sin(Math.min(vertical, horizontal)) * 1.15)
      controls.maxDistance = Math.max(36, distance * 2.5)
      camera.far = Math.max(200, distance * 4)
      camera.updateProjectionMatrix()
      controls.target.copy(sphere.center)
      camera.position.copy(sphere.center).add(new THREE.Vector3(0, 0, distance))
      controls.update()
    }
    resetRef.current = fit
    focusRef.current = (id) => {
      const point = pointById.get(id)
      if (!point) return
      const offset = camera.position.clone().sub(controls.target).normalize().multiplyScalar(4)
      controls.target.copy(point)
      camera.position.copy(point).add(offset)
      controls.update()
    }

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    let down: { x: number; y: number } | null = null
    const onPointerDown = (event: PointerEvent) => { down = { x: event.clientX, y: event.clientY } }
    const onPointerUp = (event: PointerEvent) => {
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5) return
      const rect = canvas.getBoundingClientRect()
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(pointer, camera)
      const hit = raycaster.intersectObjects(nodeMeshes.filter((mesh) => mesh.visible), false)[0]
      if (hit) onPickRef.current(Number(hit.object.userData.noteId))
      down = null
    }
    const onPointerMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(pointer, camera)
      const hit = raycaster.intersectObjects(nodeMeshes.filter((mesh) => mesh.visible), false)[0]
      const title = hit ? String(hit.object.userData.title) : ''
      setHoverTitle(title)
      canvas.style.cursor = title ? 'pointer' : 'grab'
    }
    const onPointerLeave = () => { down = null; setHoverTitle('') }
    const onContextLost = (event: Event) => { event.preventDefault(); setGraphicsError(true) }
    canvas.addEventListener('pointerdown', onPointerDown)
    canvas.addEventListener('pointerup', onPointerUp)
    canvas.addEventListener('pointermove', onPointerMove)
    canvas.addEventListener('pointerleave', onPointerLeave)
    canvas.addEventListener('webglcontextlost', onContextLost)

    const resize = () => {
      const rect = canvas.getBoundingClientRect()
      const width = Math.max(1, rect.width)
      const height = Math.max(1, rect.height)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      fit()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()
    let frame = 0
    const render = () => {
      controls.autoRotate = rotationRef.current
      controls.update()
      renderer.render(scene, camera)
      frame = window.requestAnimationFrame(render)
    }
    render()

    return () => {
      window.cancelAnimationFrame(frame)
      observer.disconnect()
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerleave', onPointerLeave)
      canvas.removeEventListener('webglcontextlost', onContextLost)
      controls.dispose()
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.LineSegments) {
          if (object.geometry !== nodeGeometry) object.geometry.dispose()
          const material = object.material
          if (Array.isArray(material)) material.forEach((entry) => entry.dispose())
          else material.dispose()
        }
      })
      nodeGeometry.dispose()
      renderer.dispose()
      controlsRef.current = null
      nodeMeshesRef.current = []
      resetRef.current = () => undefined
      focusRef.current = () => undefined
      filterRef.current = () => undefined
    }
  }, [notes, categories, connections])

  useEffect(() => {
    for (const mesh of nodeMeshesRef.current) {
      const selected = mesh.userData.noteId === selectedId
      mesh.scale.setScalar(selected ? 1.9 : 1)
      ;(mesh.material as THREE.MeshStandardMaterial).emissiveIntensity = selected ? 0.75 : 0.18
    }
    selectedIdRef.current = selectedId
    if (selectedId !== null) focusRef.current(selectedId)
  }, [selectedId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const resetView = () => {
    resetRef.current()
  }

  const toggleRotation = () => setAutoRotate((value) => !value)

  const toggleCategory = (id: string) => setHiddenCategories((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  return <div className="graph-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section className="graph-modal" role="dialog" aria-modal="true" aria-label="知识图谱">
      <header className="graph-modal-head">
        <div className="graph-title"><span className="graph-title-icon"><Icon name="network" size={15} /></span><div><strong>知识图谱</strong><span>{visibleNotes.length} 篇笔记 · {categories.length} 个分类</span></div></div>
        <div className="graph-head-actions">
          <button type="button" className="ghost icon" title={autoRotate ? '暂停自动旋转' : '恢复自动旋转'} aria-label={autoRotate ? '暂停自动旋转' : '恢复自动旋转'} aria-pressed={!autoRotate} onClick={toggleRotation}><Icon name={autoRotate ? 'pause' : 'play'} size={14} /></button>
          <button type="button" className="ghost icon" title="重置视角" aria-label="重置视角" onClick={resetView}><Icon name="refresh" size={14} /></button>
          <button type="button" className="ghost icon" title="关闭" aria-label="关闭知识图谱" onClick={onClose}><Icon name="x" size={14} /></button>
        </div>
      </header>
      <div className="graph-modal-body">
        <div className="graph-stage">
          <canvas ref={canvasRef} aria-label="可旋转的 3D 笔记知识图谱" />
          {hoverTitle ? <div className="graph-hover-title" aria-hidden="true">{hoverTitle}</div> : null}
          <div className="graph-controls-hint">拖动旋转 · 滚轮缩放{autoRotate ? ' · 自动旋转中' : ''}</div>
          <div className="graph-stage-stats"><strong>{visibleNotes.length}</strong><span>笔记节点</span><i /> <strong>{visibleConnectionCount}</strong><span>关系边</span></div>
          {visibleNotes.length === 0 ? <div className="graph-empty">没有匹配的笔记</div> : null}
          {graphicsError ? <div className="graph-fallback"><span>3D 图谱暂不可用</span><small>仍可通过搜索和列表打开笔记</small></div> : null}
        </div>
        <aside className="graph-sidebar">
          <label className="graph-search-field"><Icon name="search" size={13} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索笔记" aria-label="搜索知识图谱" /></label>
          {query.trim() ? <div className="graph-search-results" role="listbox" aria-label="匹配的笔记">{visibleNotes.slice(0, 8).map((note) => <button type="button" role="option" aria-selected={selectedId === note.id} className="graph-search-result" key={note.id} onClick={() => { onPickRef.current(note.id); focusRef.current(note.id) }}><span>{note.title}</span><small>{note.project_name ?? '本地知识库'}</small></button>)}{visibleNotes.length > 8 ? <small className="graph-search-more">还有 {visibleNotes.length - 8} 篇匹配笔记</small> : null}</div> : null}
          <div className="graph-section-label">分类</div>
          <label className="graph-category graph-select-all"><input type="checkbox" checked={hiddenCategories.size === 0} onChange={() => setHiddenCategories(hiddenCategories.size === 0 ? new Set(categories.map((category) => category.id)) : new Set())} /><span>全部分类</span><small>{notes.length}</small></label>
          <div className="graph-category-list">{categories.map((category) => <label className="graph-category" key={category.id}>
            <input type="checkbox" checked={!hiddenCategories.has(category.id)} onChange={() => toggleCategory(category.id)} />
            <span className="graph-color" style={{ background: category.color }} />
            <span className="graph-category-name" title={category.label}>{category.label}</span><small>{category.count}</small>
          </label>)}</div>
          <div className="graph-selection">
            <div className="graph-section-label">笔记详情</div>
            {selectedNote ? <>
              <h3>{selectedNote.title}</h3>
              <div className="graph-note-meta">{selectedNote.project_name ?? '本地知识库'}{selectedNote.folder_id != null ? ` · ${folderNames.get(selectedNote.folder_id) ?? ''}` : ''}</div>
              <p>{selectedNote.content_md.replace(/^#.*\n/, '').replace(/[#*`]/g, '').trim().slice(0, 420)}</p>
              <button type="button" className="small primary" onClick={() => onOpen(selectedNote)}><Icon name="book" size={12} />打开笔记</button>
            </> : <div className="graph-no-selection">选择一个节点查看笔记</div>}
          </div>
        </aside>
      </div>
    </section>
  </div>
}
