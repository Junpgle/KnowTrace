<script setup lang="ts">
/**
 * Real WebGL scene, no simulated 2D CSS star map.
 * All spatial positions encode *layout* only; edges come from the API.
 * Explicit teardown avoids GPU/RAF leaks on course change and navigation.
 */
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { GraphEdge, GraphNode } from '../api'

const props = defineProps<{
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedId: string | null
  showRelations: boolean
  showStructure?: boolean
  // Full set of approved relationships, even when a view hides some lines.
  relationEdges?: GraphEdge[]
  // Use all course nodes for stable positions while filtering displayed nodes.
  layoutNodes?: GraphNode[]
  layoutEdges?: GraphEdge[]
}>()
const emit = defineEmits<{ select: [id: string | null] }>()
const mountPoint = ref<HTMLDivElement | null>(null)
const supported = ref(true)
const error = ref('')
let renderer: THREE.WebGLRenderer | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let scene: THREE.Scene | null = null
let group: THREE.Group | null = null
let observer: ResizeObserver | null = null
let frameId = 0
let visible = true
let disposed = false
let tween = false
const desiredTarget = new THREE.Vector3()
const desiredPosition = new THREE.Vector3()
const positions = new Map<string, THREE.Vector3>()
// Layout must be stable for a full course and should not recompute on each
// node selection, relationship toggle, or chapter filter.
let cachedNodes: GraphNode[] | null = null
let cachedEdges: GraphEdge[] | null = null
let cachedCoords = new Map<string, THREE.Vector3>()
let cachedChapterTotals = new Map<string, number>()
const meshes: THREE.Mesh[] = []
const raycaster = new THREE.Raycaster()
const pointer = new THREE.Vector2()
let pressed: { x: number, y: number } | null = null
const G = Math.PI * (3 - Math.sqrt(5))

function ordinalHash(value: string): number {
  let n = 2166136261
  for (let i = 0; i < value.length; i++) n = Math.imul(n ^ value.charCodeAt(i), 16777619)
  return (n >>> 0) / 0xffffffff
}
function statusColor(node: GraphNode): number {
  if (node.kind === 'course') return 0x62ecdf
  if (node.kind === 'chapter') return 0x6e9fff
  if (node.review_status !== 'approved') return 0x788394
  if (node.mastery === null) return 0x91a6bf // never tested ≠ weak
  if (node.mastery >= 0.8) return 0x54ddb0
  if (node.mastery >= 0.5) return 0x6cbeef
  return 0xf5a86d
}

// A selection highlights real, approved, direct (one-hop) relationships only.
// The set is restricted to rendered nodes so hidden filters never create ghosts.
function focusRelations() {
  const selectedId = props.selectedId
  const focusedNode = props.nodes.find(n => n.id === selectedId)
  const neighbors = new Set<string>()
  const highlighted = new Set<string>()
  if (focusedNode?.kind !== 'knowledge') return { active: false, neighbors, highlighted }
  const visibleKnowledge = new Set(props.nodes.filter(n => n.kind === 'knowledge').map(n => n.id))
  for (const edge of props.relationEdges ?? props.edges) {
    if (edge.structural || !edge.verified) continue
    if (!visibleKnowledge.has(edge.source) || !visibleKnowledge.has(edge.target)) continue
    if (edge.source === selectedId) {
      neighbors.add(edge.target)
      highlighted.add(edge.id)
    } else if (edge.target === selectedId) {
      neighbors.add(edge.source)
      highlighted.add(edge.id)
    }
  }
  return { active: true, neighbors, highlighted }
}

const RELATION_COLORS: Record<string, number> = {
  prerequisite_required: 0xffa76a,
  prerequisite_recommended: 0xffd184,
  related: 0x66e8eb,
  uses: 0x5fd6ab,
  compares: 0xc5adff,
}

function makeLabel(text: string, width = 320) {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = 76
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = 'rgba(6,25,41,.8)'
  ctx.beginPath()
  ctx.roundRect(2, 4, width - 4, 68, 20)
  ctx.fill()
  ctx.strokeStyle = 'rgba(144,218,225,.35)'
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.font = '600 25px system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#eaffff'
  const shown = text.length > 14 ? text.slice(0, 13) + '…' : text
  ctx.fillText(shown, width / 2, 39, width - 28)
  const texture = new THREE.CanvasTexture(canvas)
  const material = new THREE.SpriteMaterial({ map: texture, depthWrite: false, transparent: true })
  const label = new THREE.Sprite(material)
  label.scale.set(11, 2.7, 1)
  return label
}

function layout(nodes: GraphNode[], edges: GraphEdge[]) {
  const pos = new Map<string, THREE.Vector3>()
  const root = nodes.find(n => n.kind === 'course')
  if (!root) return pos
  pos.set(root.id, new THREE.Vector3())

  const chapters = nodes.filter(n => n.kind === 'chapter')
  const chapterIds = new Set(chapters.map(n => n.id))
  const parent = new Map<string, string>()
  for (const e of edges) {
    if (e.structural && e.type === 'contains') parent.set(e.target, e.source)
  }
  const byChapter = new Map<string, GraphNode[]>()
  for (const node of nodes) {
    if (node.kind !== 'knowledge') continue
    const id = parent.get(node.id) ?? root.id
    if (!byChapter.has(id)) byChapter.set(id, [])
    byChapter.get(id)!.push(node)
  }

  // A 3D lattice produces compact, evenly spaced point clouds instead of
  // the previous flattened spiral. Rotation and small deterministic jitter
  // remove its grid appearance without creating node collisions.
  const offsets = (count: number, key: string) => {
    if (!count) return [] as THREE.Vector3[]
    let shell = 1
    while ((2 * shell + 1) ** 3 - 1 < count) shell++
    const cells: { x: number, y: number, z: number, d: number, tie: number }[] = []
    for (let x = -shell; x <= shell; x++) {
      for (let y = -shell; y <= shell; y++) {
        for (let z = -shell; z <= shell; z++) {
          if (x === 0 && y === 0 && z === 0) continue
          const d = x * x + y * y + z * z
          cells.push({ x, y, z, d, tie: ordinalHash(key + ':' + x + ':' + y + ':' + z) })
        }
      }
    }
    cells.sort((a, b) => a.d - b.d || a.tie - b.tie)
    const rotation = new THREE.Euler(
      .16 + ordinalHash(key + ':x') * .35,
      ordinalHash(key + ':y') * Math.PI * 2,
      .12 + ordinalHash(key + ':z') * .25,
    )
    return cells.slice(0, count).map(({ x, y, z }, i) => {
      const jitter = (axis: string) => (ordinalHash(key + ':' + i + ':' + axis) - .5) * .42
      return new THREE.Vector3(
        (x + jitter('x')) * 4.65,
        (y + jitter('y')) * 4.65,
        (z + jitter('z')) * 4.65,
      ).applyEuler(rotation)
    })
  }

  // Pack the chapter systems without overlap. A real nested chapter starts
  // near its parent, whereas top-level chapters orbit the course core.
  // Only real membership edges determine parentage; no learned relation
  // is used to assign a chapter or determine an apparent dependency.
  const footprint = new Map<string, number>()
  for (const chapter of chapters) {
    const size = byChapter.get(chapter.id)?.length ?? 0
    footprint.set(chapter.id, size ? Math.max(9, 5 + 3.2 * Math.cbrt(size)) : 7)
  }
  const placed: { x: number, z: number, radius: number }[] = [
    { x: 0, z: 0, radius: 11 },
  ]
  const remaining = new Map(chapters.map(c => [c.id, c]))
  const ordered: GraphNode[] = []
  // Parent-first walk, even if source chapter records were not ordered.
  while (remaining.size) {
    let advanced = false
    for (const [id, chapter] of remaining) {
      const pid = parent.get(id)
      if (!pid || !chapterIds.has(pid) || !remaining.has(pid)) {
        ordered.push(chapter)
        remaining.delete(id)
        advanced = true
      }
    }
    if (!advanced) { // malformed cycles should not hang the renderer
      ordered.push(...remaining.values())
      break
    }
  }
  for (const chapter of ordered) {
    const pid = parent.get(chapter.id)
    const anchor = (pid ? pos.get(pid) : undefined) ?? pos.get(root.id)!
    const parentRadius = (pid ? footprint.get(pid) : undefined) ?? 11
    const radius = footprint.get(chapter.id) ?? 7
    const initial = parentRadius + radius + 8
    const seed = ordinalHash(chapter.id) * Math.PI * 2
    let center: THREE.Vector3 | null = null
    for (let attempt = 0; attempt < 10000; attempt++) {
      const angle = seed + attempt * G
      const distance = initial + Math.sqrt(attempt) * 5.8
      const x = anchor.x + Math.cos(angle) * distance
      const z = anchor.z + Math.sin(angle) * distance
      if (placed.every(p => Math.hypot(p.x - x, p.z - z) >= p.radius + radius + 6)) {
        center = new THREE.Vector3(x, (ordinalHash(chapter.id + ':h') - .5) * 6, z)
        break
      }
    }
    // This fallback is deterministic and still separates the cluster.
    if (!center) {
      const far = placed.reduce((m, p) => Math.max(m, Math.hypot(p.x, p.z) + p.radius), 0)
      center = new THREE.Vector3(far + radius + 16, 0, 0)
    }
    pos.set(chapter.id, center)
    placed.push({ x: center.x, z: center.z, radius })
  }
  for (const [id, points] of byChapter) {
    const center = pos.get(id) ?? new THREE.Vector3()
    points.sort((a, b) => a.id.localeCompare(b.id))
    const positions = offsets(points.length, id)
    points.forEach((point, i) => pos.set(point.id, center.clone().add(positions[i])))
  }
  return pos
}

function clearGraph() {
  if (!group || !scene) return
  scene.remove(group)
  const geometries = new Set<THREE.BufferGeometry>()
  group.traverse(object => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line ||
        object instanceof THREE.Points || object instanceof THREE.Sprite) {
      if ('geometry' in object && object.geometry instanceof THREE.BufferGeometry)
        geometries.add(object.geometry)
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach(m => {
        if (m instanceof THREE.SpriteMaterial && m.map) m.map.dispose()
        m.dispose()
      })
    }
  })
  geometries.forEach(geometry => geometry.dispose())
  meshes.splice(0)
  positions.clear()
  group = null
}

function resolveLayout() {
  const allNodes = props.layoutNodes ?? props.nodes
  const allEdges = props.layoutEdges ?? props.edges
  if (cachedNodes !== allNodes || cachedEdges !== allEdges) {
    cachedCoords = layout(allNodes, allEdges)
    cachedChapterTotals = new Map()
    const chapterIds = new Set(allNodes.filter(n => n.kind === 'chapter').map(n => n.id))
    const parents = new Map(allEdges.filter(e => e.structural && e.type === 'contains')
      .map(e => [e.target, e.source]))
    for (const node of allNodes) {
      if (node.kind !== 'knowledge') continue
      let cursor = parents.get(node.id)
      const visited = new Set<string>()
      while (cursor && !visited.has(cursor)) {
        visited.add(cursor)
        if (chapterIds.has(cursor)) {
          cachedChapterTotals.set(cursor, (cachedChapterTotals.get(cursor) ?? 0) + 1)
        }
        cursor = parents.get(cursor)
      }
    }
    cachedNodes = allNodes
    cachedEdges = allEdges
  }
  return cachedCoords
}

function rebuild() {
  if (!scene) return
  clearGraph()
  const g = new THREE.Group()
  group = g
  scene.add(g)
  const coords = resolveLayout()
  // Fit the camera to visible nodes while preserving whole-course coordinates.
  for (const node of props.nodes) {
    const position = coords.get(node.id)
    if (position) positions.set(node.id, position)
  }
  const focusState = focusRelations()
  const selectedId = props.selectedId
  // One InstancedMesh for all knowledge points, including dimmed ones:
  // preserve a single draw call for 1300+ nodes instead of per-node meshes.
  const sphere = new THREE.SphereGeometry(1, 12, 10)
  const knowledge = props.nodes.filter(n => n.kind === 'knowledge' && positions.has(n.id))
  if (knowledge.length) {
    const material = new THREE.MeshPhongMaterial({
      color: 0xffffff, shininess: 70, specular: 0x526b80,
      emissive: 0x14212b, emissiveIntensity: 0.12,
    })
    const cluster = new THREE.InstancedMesh(sphere, material, knowledge.length)
    const matrix = new THREE.Matrix4()
    const size = new THREE.Vector3()
    const rotation = new THREE.Quaternion()
    const ids: string[] = []
    let neighborLabels = 0
    knowledge.forEach((node, index) => {
      const position = positions.get(node.id)!
      const center = focusState.active && node.id === selectedId
      const neighbor = focusState.active && focusState.neighbors.has(node.id)
      const radius = center ? 1.36 : neighbor ? 1.19 : 1
      size.setScalar(radius)
      matrix.compose(position, rotation, size)
      cluster.setMatrixAt(index, matrix)

      const color = new THREE.Color(statusColor(node))
      if (center) color.lerp(new THREE.Color(0xffffff), 0.7)
      else if (neighbor) color.lerp(new THREE.Color(0x81f5ea), 0.65)
      else if (focusState.active) color.multiplyScalar(0.16)
      cluster.setColorAt(index, color)
      ids.push(node.id)

      if (center || (neighbor && neighborLabels < 12)) {
        if (neighbor) neighborLabels += 1
        const label = makeLabel(node.name)
        if (label) {
          label.position.copy(position).add(new THREE.Vector3(0, radius + 2.35, 0))
          g.add(label)
        }
      }
      if (center) {
        // One translucent spherical halo (not a pick target), disposed on rebuild.
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(1.92, 16, 12),
          new THREE.MeshBasicMaterial({
            color: 0x72ffe6, transparent: true, opacity: 0.22,
            depthWrite: false, side: THREE.BackSide,
          }),
        )
        halo.position.copy(position)
        g.add(halo)
      }
    })
    cluster.instanceMatrix.needsUpdate = true
    if (cluster.instanceColor) cluster.instanceColor.needsUpdate = true
    cluster.userData.instanceNodeIds = ids
    cluster.computeBoundingSphere()
    g.add(cluster)
    meshes.push(cluster)
  }
  for (const node of props.nodes.filter(n => n.kind !== 'knowledge')) {
    const position = positions.get(node.id)
    if (!position) continue
    const color = new THREE.Color(statusColor(node))
    if (focusState.active) color.multiplyScalar(0.42)
    const material = new THREE.MeshPhongMaterial({
      color, emissive: color, emissiveIntensity: focusState.active ? 0.08 : 0.28,
      shininess: 86, specular: 0x6b8fae,
    })
    // Chapter bubbles encode the number of knowledge points contained in
    // their subtree. Counts include currently collapsed nodes.
    const count = cachedChapterTotals.get(node.id) ?? 0
    const radius = node.kind === 'course' ? 3.8
      : (2.1 + Math.min(1.65, Math.log2(count + 1) * 0.23))
        * (node.id === selectedId ? 1.13 : 1)
    const mesh = new THREE.Mesh(sphere, material)
    mesh.scale.setScalar(radius)
    mesh.position.copy(position)
    mesh.userData.nodeId = node.id
    g.add(mesh)
    meshes.push(mesh)
    // Hide chapter labels in local-focus mode, reducing visual competition.
    if (!focusState.active || node.id === selectedId) {
      const label = makeLabel(node.name)
      if (label) {
        label.position.copy(position).add(new THREE.Vector3(0, radius + 2.35, 0))
        g.add(label)
      }
    }
  }

  const structural: number[] = []
  const ordinary: number[] = []
  const highlightedByType = new Map<string, number[]>()
  for (const edge of props.edges) {
    const a = coords.get(edge.source)
    const b = coords.get(edge.target)
    if (!a || !b) continue
    const bucket = edge.structural ? structural
      : !props.showRelations ? null
      : focusState.active && focusState.highlighted.has(edge.id)
        ? (() => {
            if (!highlightedByType.has(edge.type)) highlightedByType.set(edge.type, [])
            return highlightedByType.get(edge.type)!
          })()
        : ordinary
    if (bucket) bucket.push(a.x, a.y, a.z, b.x, b.y, b.z)
  }
  function addLines(points: number[], color: number, opacity: number) {
    if (!points.length) return
    const geom = new THREE.BufferGeometry()
    geom.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
    const lines = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({
      color, transparent: true, opacity, depthWrite: false,
    }))
    g.add(lines)
  }
  if (props.showStructure !== false) {
    addLines(structural, 0x7b9aad, focusState.active ? 0.055 : 0.3)
  }
  addLines(ordinary, 0xffd184, focusState.active ? 0.085 : 0.79)
  for (const [type, points] of highlightedByType) {
    addLines(points, RELATION_COLORS[type] ?? 0xffd184, 1)
  }
}

// Automatic selection should move the camera to the chosen node, NOT dive
// into its surface. Keep the user's current zoom within a comfortable range,
// so switching between nearby knowledge points doesn't bounce in and out.
function selectionDistance(kind: GraphNode['kind'], current: number): number {
  const [minimum, maximum] = kind === 'course' ? [105, 145]
    : kind === 'chapter' ? [78, 112] : [58, 94]
  return THREE.MathUtils.clamp(current, minimum, maximum)
}

function focus(id: string | null) {
  if (!camera || !controls) return
  const coordinate = id ? positions.get(id) : undefined
  if (id && !coordinate) return
  // With a chapter/search filter active, center on the visible knowledge
  // cloud rather than the original course core. Coordinates stay stable.
  const displayed = props.nodes.filter(n => n.kind === 'knowledge')
    .map(n => positions.get(n.id)).filter((p): p is THREE.Vector3 => !!p)
  const fullKnowledgeCount = (props.layoutNodes ?? props.nodes)
    .filter(n => n.kind === 'knowledge').length
  const subset = !id && displayed.length > 0 && displayed.length < fullKnowledgeCount
  const overviewCenter = new THREE.Vector3()
  if (subset) new THREE.Box3().setFromPoints(displayed).getCenter(overviewCenter)
  desiredTarget.copy(coordinate ?? overviewCenter)
  const selected = props.nodes.find(n => n.id === id)
  const currentDistance = camera.position.distanceTo(controls.target)
  // Fit real visible bounds rather than a fixed chapter-count distance.
  let distance: number
  if (!id) {
    const fitPoints = subset ? displayed : [...positions.values()]
    const extent = Math.max(12, ...fitPoints.map(p => p.distanceTo(overviewCenter) + 8))
    const vfov = THREE.MathUtils.degToRad(camera.fov)
    const hfov = 2 * Math.atan(Math.tan(vfov / 2) * camera.aspect)
    const halfAngle = Math.min(vfov, hfov) / 2
    distance = Math.max(85, extent / Math.sin(halfAngle) * 1.12)
    controls.maxDistance = Math.max(280, distance * 1.4)
    camera.far = Math.max(1600, distance * 3)
    camera.updateProjectionMatrix()
  } else {
    distance = selectionDistance(selected?.kind ?? 'knowledge', currentDistance)
  }
  const direction = camera.position.clone().sub(controls.target)
  if (direction.lengthSq() < 0.01) direction.set(1, .6, 1)
  desiredPosition.copy(desiredTarget).add(direction.normalize().multiplyScalar(distance))
  tween = true
}
function resetCamera() { focus(null) }
defineExpose({ resetCamera })

function draw() {
  if (!renderer || !camera || !controls || !scene || disposed || !visible) return
  if (tween) {
    camera.position.lerp(desiredPosition, 0.075)
    controls.target.lerp(desiredTarget, 0.075)
    if (camera.position.distanceTo(desiredPosition) < 0.075
        && controls.target.distanceTo(desiredTarget) < 0.075) tween = false
  }
  controls.update()
  renderer.render(scene, camera)
  frameId = requestAnimationFrame(draw)
}
function visibilityChanged() {
  visible = !document.hidden
  if (!visible) cancelAnimationFrame(frameId)
  else draw()
}
function resize() {
  if (!renderer || !camera || !mountPoint.value) return
  const width = Math.max(1, mountPoint.value.clientWidth)
  const height = Math.max(1, mountPoint.value.clientHeight)
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height, false)
}
function pointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  pressed = { x: event.clientX, y: event.clientY }
}
function pointerUp(event: PointerEvent) {
  if (!pressed || !renderer || !camera) return
  const movement = Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y)
  pressed = null
  if (movement > 6) return
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1,
              -((event.clientY - rect.top) / rect.height) * 2 + 1)
  raycaster.setFromCamera(pointer, camera)
  const hits = raycaster.intersectObjects(meshes)
  const hit = hits[0]
  const instanceIds = hit?.object.userData.instanceNodeIds as string[] | undefined
  const id = (instanceIds && hit.instanceId !== undefined
    ? instanceIds[hit.instanceId]
    : hit?.object.userData.nodeId) as string | undefined
  emit('select', id ?? null)
}
function initialize() {
  const el = mountPoint.value
  if (!el) return
  try {
    renderer = new THREE.WebGLRenderer({
      alpha: true, antialias: true, powerPreference: 'low-power',
    })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.setAttribute('aria-label', '三维知识星系；可拖动旋转，滚轮缩放，点击星球选择知识点')
    el.appendChild(renderer.domElement)
  } catch (e) {
    supported.value = false
    error.value = '浏览器无法创建 WebGL 场景。你仍可使用左侧知识点列表或切换上方的列表模式查看内容。'
    return
  }
  scene = new THREE.Scene()
  scene.fog = new THREE.FogExp2(0x07192c, 0.0015)
  camera = new THREE.PerspectiveCamera(57, 1, 0.1, 1600)
  camera.position.set(0, 32, 82)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 18
  controls.maxDistance = 280
  controls.minPolarAngle = 0.2
  controls.maxPolarAngle = Math.PI - 0.2
  controls.addEventListener('start', () => { tween = false })
  const ambient = new THREE.AmbientLight(0xa5d8ff, 1.5)
  scene.add(ambient)
  const light = new THREE.PointLight(0x75fff0, 210, 180)
  light.position.set(6, 11, 8)
  scene.add(light)
  const coords: number[] = []
  let seed = 777
  const random = () => {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0
    return seed / 0x100000000
  }
  for (let i = 0; i < 480; i++) {
    const a = random() * Math.PI * 2
    const h = 2 * random() - 1
    const r = 95 + random() * 150
    const plane = Math.sqrt(1 - h * h)
    coords.push(r * plane * Math.cos(a), r * h, r * plane * Math.sin(a))
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(coords, 3))
  const stars = new THREE.Points(geometry, new THREE.PointsMaterial({
    color: 0x9bb7d9, size: 0.32, transparent: true, opacity: 0.72,
  }))
  scene.add(stars)
  rebuild()
  resize()
  observer = new ResizeObserver(resize)
  observer.observe(el)
  renderer.domElement.addEventListener('pointerdown', pointerDown)
  renderer.domElement.addEventListener('pointerup', pointerUp)
  document.addEventListener('visibilitychange', visibilityChanged)
  focus(null)
  draw()
}
onMounted(initialize)
// Changing line visibility must never zoom the camera. Only a change of
// selected node should trigger automatic focus.
watch(() => [props.nodes, props.layoutNodes, props.layoutEdges], () => {
  rebuild()
  focus(props.selectedId)
})
watch(() => [props.edges, props.relationEdges], rebuild)
watch(() => props.showRelations, rebuild)
watch(() => props.showStructure, rebuild)
watch(() => props.selectedId, id => {
  rebuild()
  focus(id)
})
onBeforeUnmount(() => {
  disposed = true
  cancelAnimationFrame(frameId)
  document.removeEventListener('visibilitychange', visibilityChanged)
  observer?.disconnect()
  controls?.dispose()
  if (renderer) {
    renderer.domElement.removeEventListener('pointerdown', pointerDown)
    renderer.domElement.removeEventListener('pointerup', pointerUp)
  }
  clearGraph()
  if (scene) scene.traverse(obj => {
    if (obj instanceof THREE.Points) {
      obj.geometry.dispose()
      const material = obj.material
      if (Array.isArray(material)) material.forEach(m => m.dispose())
      else material.dispose()
    }
  })
  renderer?.dispose()
  renderer?.domElement.remove()
  renderer = null
  scene = null
  camera = null
  controls = null
  cachedNodes = null
  cachedEdges = null
  cachedCoords.clear()
  cachedChapterTotals.clear()
})
</script>

<template>
  <div ref="mountPoint" class="galaxy-canvas">
    <div v-if="!supported" class="galaxy-unsupported" role="alert">
      <strong>无法使用 3D 加速</strong><p>{{ error }}</p>
    </div>
    <span v-else class="galaxy-watermark">3D GALAXY · THREE.JS</span>
  </div>
</template>

<style scoped>
.galaxy-canvas { position:relative;width:100%;height:100%;min-height:480px;overflow:hidden;touch-action:pan-y;background:radial-gradient(ellipse at 50% 43%,#133550 0%,#0b2138 43%,#05101f 100%) }
.galaxy-canvas :deep(canvas) { display:block;width:100%;height:100%;outline:none;cursor:grab }
.galaxy-canvas :deep(canvas):active { cursor:grabbing }
.galaxy-watermark { pointer-events:none;position:absolute;left:19px;bottom:17px;font-size:10px;letter-spacing:2px;color:#6687a8 }
.galaxy-unsupported { color:#b4dce8;display:grid;place-content:center;text-align:center;height:100%;padding:26px;font-size:13px }
.galaxy-unsupported p { max-width:270px;line-height:1.8 }
@media(max-width:700px){.galaxy-canvas{min-height:390px}}
</style>
