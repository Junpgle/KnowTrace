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
let cameraAnimationStart = 0
const cameraStartPosition = new THREE.Vector3()
const cameraStartTarget = new THREE.Vector3()
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
let hoveredNodeId: string | null = null
const labelAnchors: LabelAnchor[] = []
const screenUp = new THREE.Vector3()
type LabelAnchor = {
  sprite: THREE.Sprite
  center: THREE.Vector3
  distance: number
  nodeId?: string
  priority?: number
  declutter?: boolean
  // Atlas captions maintain a readable size in screen pixels while zooming.
  screen?: { width: number; height: number; radius: number; gap: number }
}
type FadePart = { material: THREE.Material; opacity: number; transparent: boolean; depthWrite: boolean }
type FadeGroup = { group: THREE.Group; parts: FadePart[]; anchors: LabelAnchor[] }
let outgoing: FadeGroup | null = null
let incoming: FadeGroup | null = null
let layerTransitionStart = 0
const incomingStartOffset = new THREE.Vector3()
let pendingTransitionOrigin: THREE.Vector3 | null = null
const LAYER_DURATION = 680
const CAMERA_DURATION = 920
function reducedMotion() {
  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
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

// A label belongs to the celestial object, never a tiny badge drawn over points.
function makeLabel(text: string, width = 320, subtitle?: string) {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = subtitle ? 106 : 76
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = 'rgba(6,25,41,.8)'
  ctx.beginPath()
  ctx.roundRect(2, 4, width - 4, subtitle ? 97 : 68, 20)
  ctx.fill()
  ctx.strokeStyle = 'rgba(144,218,225,.35)'
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.font = subtitle ? '600 30px system-ui, sans-serif' : '600 25px system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#eaffff'
  const maxChars = subtitle ? 23 : 14
  const shown = text.length > maxChars ? text.slice(0, maxChars - 1) + '…' : text
  ctx.fillText(shown, width / 2, subtitle ? 36 : 39, width - 28)
  if (subtitle) {
    ctx.fillStyle = '#79cfc5'
    ctx.font = '500 19px system-ui, sans-serif'
    ctx.fillText(subtitle, width / 2, 75, width - 28)
  }
  const texture = new THREE.CanvasTexture(canvas)
  const material = new THREE.SpriteMaterial({ map: texture, depthWrite: false, transparent: true })
  const label = new THREE.Sprite(material)
  label.scale.set(11, subtitle ? 3.6 : 2.7, 1)
  return label
}

// Leaf-neighborhood labels stay crisp at the larger camera distance.
// Use up to two compact lines to avoid extra-wide cards on 14-node rings.
function makeLeafLabel(title: string): THREE.Sprite | null {
  const canvas = document.createElement('canvas')
  canvas.width = 520
  canvas.height = 150
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = 'rgba(9, 32, 48, .91)'
  ctx.beginPath()
  ctx.roundRect(3, 3, 514, 144, 19)
  ctx.fill()
  ctx.strokeStyle = 'rgba(113, 207, 207, .39)'
  ctx.lineWidth = 2
  ctx.stroke()
  const name = title.length > 24 ? title.slice(0, 23) + '…' : title
  const lines = name.length > 12 ? [name.slice(0, 12), name.slice(12)] : [name]
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#e9fbfc'
  ctx.font = '600 61px system-ui, sans-serif'
  if (lines.length === 1) ctx.fillText(lines[0], 260, 75, 476)
  else {
    ctx.fillText(lines[0], 260, 51, 476)
    ctx.fillText(lines[1], 260, 107, 476)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const label = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture, transparent: true, depthWrite: false, depthTest: false,
  }))
  label.scale.set(44, 12.7, 1)
  return label
}

// Chapter labels use a larger, high-resolution canvas than knowledge labels.
// Two-line wrapping keeps real chapter names readable without covering planets.
function makeChapterLabel(title: string, subtitle: string) {
  const canvas = document.createElement('canvas')
  canvas.width = 720
  canvas.height = 208
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = 'rgba(7, 29, 44, .90)'
  ctx.beginPath()
  ctx.roundRect(5, 5, 710, 198, 26)
  ctx.fill()
  ctx.strokeStyle = 'rgba(120, 204, 204, .44)'
  ctx.lineWidth = 2
  ctx.stroke()
  const name = title.length > 26 ? title.slice(0, 25) + '…' : title
  const lines = name.length > 13 ? [name.slice(0, 13), name.slice(13)] : [name]
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#f0ffff'
  ctx.font = '700 58px system-ui, sans-serif'
  if (lines.length > 1) {
    ctx.fillText(lines[0], 360, 64, 668)
    ctx.fillText(lines[1], 360, 115, 668)
  } else {
    ctx.fillText(lines[0], 360, 82, 668)
  }
  ctx.font = '600 38px system-ui, sans-serif'
  ctx.fillStyle = '#8edacf'
  ctx.fillText(subtitle, 360, lines.length > 1 ? 166 : 155, 668)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const label = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture, transparent: true, depthWrite: false,
    depthTest: false, opacity: .98,
  }))
  return label
}

// Deterministic, quasi-uniform spherical samples. Unlike a cubic lattice,
// these have no parallel rows or visible square silhouettes at any camera angle.
// Muted chapter accents: an identifier only affects presentation and
// conveys no scientific relationship or mastery estimate.
const CHAPTER_COLORS = [
  0x49c7c0, 0x8c95ee, 0x6bbbe6, 0x79cab1,
  0x9aa5e8, 0x7fcbde, 0xe0b787, 0x87b5d6,
]
function chapterColors(nodes: GraphNode[]) {
  const chapters = nodes.filter(n => n.kind === 'chapter')
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans') || a.id.localeCompare(b.id))
  return new Map(chapters.map((n, i) => [n.id, n.virtual ? 0x91a1b1 : CHAPTER_COLORS[i % CHAPTER_COLORS.length]]))
}
function glowTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 128
  const context = canvas.getContext('2d')!
  const grad = context.createRadialGradient(64,64,0,64,64,64)
  grad.addColorStop(0, 'rgba(255,255,255,.72)')
  grad.addColorStop(.22, 'rgba(255,255,255,.25)')
  grad.addColorStop(.58, 'rgba(255,255,255,.05)')
  grad.addColorStop(1, 'rgba(255,255,255,0)')
  context.fillStyle = grad
  context.fillRect(0,0,128,128)
  return new THREE.CanvasTexture(canvas)
}

function organicOffsets(count: number, seed: string): THREE.Vector3[] {
  const points: THREE.Vector3[] = []
  if (!count) return points
  const angle = Math.PI * (3 - Math.sqrt(5))
  const twist = ordinalHash(seed + ':twist') * Math.PI * 2
  // Volume, rather than just the surface: sample by cubed radius.
  for (let i = 0; i < count; i++) {
    const u = (i + .5) / count
    const jitter = (ordinalHash(seed + ':j:' + i) - .5) * .22
    const y = 1 - 2 * ((i * .6180339887498949 + .5) % 1)
    const radial = Math.sqrt(Math.max(0, 1 - y * y))
    const theta = i * angle + twist + jitter
    const radius = 5 + 2.3 * Math.cbrt(count) * Math.cbrt(.1 + .9 * u)
    points.push(new THREE.Vector3(
      Math.cos(theta) * radial * radius,
      y * radius * .82,
      Math.sin(theta) * radial * radius,
    ))
  }
  return points
}

function layout(nodes: GraphNode[], edges: GraphEdge[]) {
  const pos = new Map<string, THREE.Vector3>()
  const groupNodes=nodes.filter(n=>n.kind==='group')
  if (groupNodes.length) {
    const chapter = nodes.find(n => n.kind === 'chapter')
    const detailed = groupNodes.length === 1 &&
      nodes.some(n => n.kind === 'knowledge')
    // In a leaf neighborhood, use the entire available screen plane.
    // A true 3D ball around the hub projected many labels onto each other.
    // Use concentric, staggered constellations and leave space for captions.
    if (detailed) {
      const group = groupNodes[0]
      pos.set(group.id, new THREE.Vector3())
      const points = nodes.filter(n => n.kind === 'knowledge')
        .sort((a, b) => a.id.localeCompare(b.id))
      const count = points.length
      const innerCount = count <= 8 ? count : Math.min(6, Math.round(count * .42))
      const outerCount = count - innerCount
      const ring1 = Math.max(49, innerCount * 8.5)
      const ring2 = 112
      // XY lies parallel to the preferred detail camera plane. Keeping the
      // Z depth small avoids accidental label overlaps on entry.
      points.forEach((node, i) => {
        const outer = i >= innerCount
        const j = outer ? i - innerCount : i
        const n = outer ? outerCount : innerCount
        const theta = -Math.PI / 2 + 2 * Math.PI * j / Math.max(1, n)
          + (outer ? Math.PI / Math.max(1, outerCount) : .12)
        const radius = outer ? ring2 : ring1
        const wobble = (ordinalHash(node.id + ':leaf') - .5) * 7
        pos.set(node.id, new THREE.Vector3(
          Math.cos(theta) * (radius + wobble) * 1.30,
          Math.sin(theta) * (radius + wobble) * .80,
          (ordinalHash(node.id + ':depth') - .5) * 3,
        ))
      })
      // The actual parent chapter is a breadcrumb in the UI, not another
      // selectable planet fighting for the center of this local diagram.
      if (chapter) pos.set(chapter.id, new THREE.Vector3(-150, 0, -8))
      return pos
    }
    // Diffuse annular star field, NOT spokes and NOT rows of perfect rings.
    // Golden-angle azimuth and an independent low-discrepancy radial sequence
    // create an irregular but balanced cloud around a softly empty center.
    // No semantic/learning relationship is encoded by this visual position.
    const groups = [...groupNodes].sort((a, b) =>
      a.name.localeCompare(b.name, 'zh-Hans') || a.id.localeCompare(b.id))
    const count = groups.length
    const width = mountPoint.value?.clientWidth ?? 1440
    const height = mountPoint.value?.clientHeight ?? 820
    const aspect = width / Math.max(1, height)
    // Gently use a wider canvas, never flatten the galaxy into a rectangle.
    const spreadX = THREE.MathUtils.clamp(1 + (aspect - 1) * .68, 1, 1.60)
    const innerRadius = count < 4 ? 0 : count <= 12 ? 78 : 102
    const outerRadius = count <= 4 ? 125 : count <= 12 ? 200
      : count <= 22 ? 255 : 345 + Math.max(0, count - 34) * 1.8
    const rotation = ordinalHash('annulus:' + count) * Math.PI * 2
    const goldenRatioFraction = .7548776662466927
    const points: THREE.Vector3[] = []
    for (let i = 0; i < count; i++) {
      const node = groups[i]
      const radialUnit = (i * goldenRatioFraction + .37) % 1
      const theta = i * G + rotation +
        (ordinalHash(node.id + ':theta') - .5) * .17
      const radius = count === 1 ? 0
        : innerRadius + (outerRadius - innerRadius)
          * Math.sqrt(.08 + radialUnit * .92)
      const x = Math.cos(theta) * radius * spreadX
      const y = Math.sin(theta) * radius
      const z = ((ordinalHash(node.id + ':depth') - .5) * 36)
        + 9 * Math.sin(theta * 2.3)
      points.push(new THREE.Vector3(x, y, z))
    }
    // Mild collision relaxation in the projection plane for small/medium
    // group counts. Keep a soft annulus instead of pushing points into
    // either a six-armed structure or a rigid lattice.
    const clearance = count <= 12 ? 73 : count <= 24 ? 67 : 54
    for (let pass = 0; pass < 16; pass++) {
      for (let i = 0; i < count; i++) {
        for (let j = i + 1; j < count; j++) {
          const dx = points[j].x - points[i].x
          const dy = points[j].y - points[i].y
          const distance = Math.hypot(dx, dy)
          if (distance >= clearance) continue
          const fallbackAngle = (i + j * 2.399) * G
          const ux = distance > .0001 ? dx / distance : Math.cos(fallbackAngle)
          const uy = distance > .0001 ? dy / distance : Math.sin(fallbackAngle)
          const shift = (clearance - distance) * .29
          points[i].x -= ux * shift
          points[i].y -= uy * shift
          points[j].x += ux * shift
          points[j].y += uy * shift
        }
      }
      for (const point of points) {
        const radius = Math.hypot(point.x / spreadX, point.y)
        if (radius < innerRadius * .75 && radius > .0001) {
          const boost = innerRadius * .75 / radius
          point.x *= boost
          point.y *= boost
        }
      }
    }
    groups.forEach((node, i) => pos.set(node.id, points[i]))
    return pos
  }
  const root = nodes.find(n => n.kind === 'course')
  if (!root) return pos
  pos.set(root.id, new THREE.Vector3())

  const chapters = nodes.filter(n => n.kind === 'chapter')
  const chapterIds = new Set(chapters.map(ch => ch.id))
  const parent = new Map<string, string>()
  const byChapter = new Map<string, GraphNode[]>()
  for (const edge of edges) {
    if (edge.structural && edge.type === 'contains') parent.set(edge.target, edge.source)
  }
  for (const node of nodes) {
    if (node.kind !== 'knowledge') continue
    const owner = parent.get(node.id) ?? root.id
    if (!byChapter.has(owner)) byChapter.set(owner, [])
    byChapter.get(owner)!.push(node)
  }

  // Use a balanced course-centered orbit, not sequential collision packing.
  // Ordering and placement are stable across selection and filtering.
  const top = chapters.filter(c => !chapterIds.has(parent.get(c.id) ?? ''))
  const nested = chapters.filter(c => chapterIds.has(parent.get(c.id) ?? ''))
  top.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans') || a.id.localeCompare(b.id))
  const total = top.length
  const sizes = top.map(c => byChapter.get(c.id)?.length ?? 0)
  const large = Math.max(1, ...sizes)
  const orbit = Math.max(52, 34 + total * 4.8 + Math.cbrt(large) * 3.2)
  // Radial constellation instead of box/rectangle columns. Small angular
  // jitter and alternating orbit distance break the grid-like alignment while
  // preserving a clear course-centered star structure.
  top.forEach((ch, i) => {
    const angle = -Math.PI / 2 + 2 * Math.PI * i / Math.max(1, total)
      + (ordinalHash(ch.id + ':angle') - .5) * .20
    const radius = orbit * (.94 + ordinalHash(ch.id + ':orbit') * .20)
    pos.set(ch.id, new THREE.Vector3(
      Math.cos(angle) * radius,
      (ordinalHash(ch.id + ':elevation') - .5) * 9,
      Math.sin(angle) * radius,
    ))
  })
  // Nested chapters stay near their real parent but get their own system.
  const pending = new Map(nested.map(c => [c.id, c]))
  for (let pass = 0; pending.size && pass <= nested.length; pass++) {
    for (const [id, ch] of pending) {
      const p = pos.get(parent.get(id) ?? '')
      if (!p) continue
      const siblings = chapters.filter(c => parent.get(c.id) === parent.get(id))
        .sort((a, b) => a.id.localeCompare(b.id))
      const index = siblings.findIndex(c => c.id === id)
      const a = index * G + ordinalHash(parent.get(id) ?? '') * Math.PI * 2
      const dist = 24 + Math.sqrt(siblings.length) * 7
      pos.set(id, p.clone().add(new THREE.Vector3(
        Math.cos(a) * dist, ((index % 3) - 1) * 9, Math.sin(a) * dist,
      )))
      pending.delete(id)
    }
  }
  for (const [id, ch] of pending) {
    pos.set(id, new THREE.Vector3(
      (ordinalHash(ch.id + 'x') - .5) * orbit,
      18, (ordinalHash(ch.id + 'z') - .5) * orbit,
    ))
  }

  for (const [owner, points] of byChapter) {
    const center = pos.get(owner) ?? new THREE.Vector3()
    points.sort((a, b) => a.id.localeCompare(b.id))
    const offsets = organicOffsets(points.length, owner)
    points.forEach((p, i) => pos.set(p.id, center.clone().add(offsets[i])))
  }
  // Slightly adapt the star to a wide viewport without flattening it into
  // a rectangle. Never stretch a course constellation more than 25%.
  if (chapters.length >= 4) {
    const width = mountPoint.value?.clientWidth ?? window.innerWidth
    const height = mountPoint.value?.clientHeight ?? window.innerHeight
    const xStretch = THREE.MathUtils.clamp(
      1 + (width / Math.max(1, height) - 1) * .17, 1, 1.25,
    )
    for (const point of pos.values()) point.x *= xStretch
  }
  return pos
}

function disposeGraph(target: THREE.Group | null) {
  if (!target) return
  scene?.remove(target)
  const geometries = new Set<THREE.BufferGeometry>()
  const materials = new Set<THREE.Material>()
  target.traverse(object => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line ||
        object instanceof THREE.Points || object instanceof THREE.Sprite) {
      if ('geometry' in object && object.geometry instanceof THREE.BufferGeometry)
        geometries.add(object.geometry)
      const list = Array.isArray(object.material) ? object.material : [object.material]
      list.forEach(m => materials.add(m))
    }
  })
  for (const material of materials) {
    if (material instanceof THREE.SpriteMaterial && material.map)
      material.map.dispose()
    material.dispose()
  }
  geometries.forEach(geometry => geometry.dispose())
}
function fadeGroup(target: THREE.Group, anchors: LabelAnchor[]): FadeGroup {
  const materials = new Set<THREE.Material>()
  target.traverse(object => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line ||
        object instanceof THREE.Points || object instanceof THREE.Sprite) {
      const list = Array.isArray(object.material) ? object.material : [object.material]
      list.forEach(m => materials.add(m))
    }
  })
  return { group: target, anchors, parts: [...materials].map(material => ({
    material, opacity: material.opacity,
    transparent: material.transparent, depthWrite: material.depthWrite,
  })) }
}
function setFade(target: FadeGroup, alpha: number) {
  for (const p of target.parts) {
    p.material.opacity = p.opacity * alpha
    p.material.transparent = alpha < 1 || p.transparent
    p.material.depthWrite = alpha >= 1 && p.depthWrite
  }
}
function finishFade() {
  if (outgoing) {
    disposeGraph(outgoing.group)
    outgoing = null
  }
  if (incoming) {
    for (const p of incoming.parts) {
      p.material.opacity = p.opacity
      p.material.transparent = p.transparent
      p.material.depthWrite = p.depthWrite
    }
    incoming = null
  }
}
function clearGraph(animate = false) {
  if (outgoing) {
    disposeGraph(outgoing.group)
    outgoing = null
  }
  incoming = null
  if (group && animate && !reducedMotion()) {
    outgoing = fadeGroup(group, [...labelAnchors])
    layerTransitionStart = performance.now()
  } else {
    disposeGraph(group)
  }
  meshes.splice(0)
  labelAnchors.splice(0)
  hoveredNodeId = null
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

function rebuild(animate = false) {
  if (!scene) return
  clearGraph(animate)
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
  const planetSphere = new THREE.SphereGeometry(1, 40, 28)
  const palette = chapterColors(props.layoutNodes ?? props.nodes)
  const overview = !props.nodes.some(n => n.kind === 'knowledge')
  const knowledge = props.nodes.filter(n => n.kind === 'knowledge' && positions.has(n.id))
  const smallGroup=props.nodes.some(n=>n.kind==='group') && knowledge.length>0 && knowledge.length<=18
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
      const radius = smallGroup
        ? (center ? 3.5 : neighbor ? 3.15 : 2.75)
        : (center ? 1.36 : neighbor ? 1.19 : 1)
      size.setScalar(radius)
      matrix.compose(position, rotation, size)
      cluster.setMatrixAt(index, matrix)

      const color = new THREE.Color(statusColor(node))
      if (center) color.lerp(new THREE.Color(0xffffff), 0.7)
      else if (neighbor) color.lerp(new THREE.Color(0x81f5ea), 0.65)
      else if (focusState.active) color.multiplyScalar(0.16)
      cluster.setColorAt(index, color)
      ids.push(node.id)

      if ((smallGroup && !focusState.active) || center || (neighbor && neighborLabels < 12)) {
        if (neighbor) neighborLabels += 1
        const label = smallGroup ? makeLeafLabel(node.name) : makeLabel(node.name)
        if (label) {
          label.position.copy(position)
          labelAnchors.push({ sprite: label, center: position, distance: radius + (smallGroup ? 6.5 : 4.5) })
          g.add(label)
        }
      }
      if (center) {
        // One translucent spherical halo (not a pick target), disposed on rebuild.
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(radius * 1.5, 16, 12),
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
    const isCourse = node.kind === 'course'
    const count = node.kind==='group' ? node.source_count : (cachedChapterTotals.get(node.id) ?? 0)
    const groupPalette = [0x54c9cf, 0x8ea0ef, 0x69cba8, 0xe0b981, 0x86c4ed, 0xb39be8]
    const groupOrder = node.kind === 'group'
      ? props.nodes.filter(n=>n.kind==='group')
        .sort((a,b)=>a.name.localeCompare(b.name,'zh-Hans')||a.id.localeCompare(b.id))
        .findIndex(n=>n.id===node.id) : -1
    const baseColor = isCourse ? 0x4be6d6
      : node.kind==='group' ? groupPalette[(groupOrder + groupPalette.length) % groupPalette.length]
        : palette.get(node.id) ?? 0x83a4d9
    const chapterSelected = node.id === selectedId
    // Overview planets must be readable on a large desktop canvas.
    // The previous 4-6 world-unit spheres appeared only ~30px across.
    const radius = node.kind==='group'
      ? (smallGroup ? 3.8 : overview
        ? 7.7 + Math.min(3.3, Math.sqrt(Math.max(1, count)) * 1.0)
        : 6.8)
      : isCourse ? (overview ? 9.8 : 6.0)
      : (overview
          ? 11.7 + Math.min(4.1, Math.log2(count + 1) * .53)
          : 3.6 + Math.min(2.5, Math.log2(count + 1) * .35))
        * (chapterSelected ? 1.12 : 1)
    // Front and back stars get different brightness and actual projected
    // sizes, rather than uniformly sized flat dots.
    const depthFactor = node.kind === 'group' && overview
      ? THREE.MathUtils.clamp((position.z + 125) / 250, 0, 1) : .5
    const material = new THREE.MeshPhongMaterial({
      color: baseColor,
      emissive: baseColor,
      emissiveIntensity: focusState.active ? .09 : (.14 + depthFactor * .22),
      shininess: 100, specular: 0xd9ffff,
      transparent: true,
      opacity: focusState.active && !chapterSelected ? .55
        : node.kind === 'group' && overview ? .72 + depthFactor * .27 : .96,
    })
    const planet = new THREE.Mesh(planetSphere, material)
    planet.scale.setScalar(radius)
    planet.position.copy(position)
    planet.userData.nodeId = node.id
    g.add(planet)
    meshes.push(planet)

    // Restrained luminous atmosphere: one soft glow and an orbital ring
    // instead of hundreds of fake chapter dots.
    const glowMap = glowTexture()
    const aura = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowMap, color: baseColor,
      transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending,
      opacity: focusState.active ? .10 : (isCourse ? .65 : .42),
    }))
    aura.position.copy(position)
    aura.scale.setScalar(radius * (isCourse ? 5.6 : 5.1))
    g.add(aura)

    if (!focusState.active) {
      const ringR = radius * (isCourse ? 2.2 : 1.9)
      const curve = new THREE.EllipseCurve(0, 0, ringR, ringR, 0, Math.PI * 2)
      const ringGeometry = new THREE.BufferGeometry().setFromPoints(
        curve.getPoints(90).map(p => new THREE.Vector3(p.x, 0, p.y)),
      )
      const ring = new THREE.LineLoop(ringGeometry, new THREE.LineBasicMaterial({
        color: baseColor, transparent: true, opacity: isCourse ? .28 : .20,
        depthWrite: false,
      }))
      if (node.kind === 'group' && overview) {
        // Ring lives in an almost camera-facing plane; its slight tilt
        // changes with each constellation arm and reveals true parallax.
        ring.rotation.set(Math.PI / 2 - .35,
          (ordinalHash(node.id + ':tilt') - .5) * .48,
          (ordinalHash(node.id + ':spin') - .5) * .52)
      } else {
        ring.rotation.set(.32, ordinalHash(node.id) * Math.PI, .15)
      }
      ring.position.copy(position)
      g.add(ring)
      // Sparse decorative halo is intentionally NOT part of the knowledge
      // graph: its dots cannot be selected or mistaken for knowledge nodes.
      if (overview && !isCourse && count > 0) {
        const particles: number[] = []
        const dustCount = Math.min(32, Math.max(12, Math.round(8 + Math.sqrt(count))))
        for (let i = 0; i < dustCount; i++) {
          const a = i * G + ordinalHash(node.id) * Math.PI * 2
          const z = 1 - 2 * (i + .5) / dustCount
          const radial = Math.sqrt(1 - z * z)
          const rr = radius * (2.15 + ordinalHash(node.id + i) * .8)
          particles.push(
            position.x + Math.cos(a) * radial * rr,
            position.y + z * rr * .58,
            position.z + Math.sin(a) * radial * rr,
          )
        }
        const dustGeom = new THREE.BufferGeometry()
        dustGeom.setAttribute('position', new THREE.Float32BufferAttribute(particles, 3))
        g.add(new THREE.Points(dustGeom, new THREE.PointsMaterial({
          color: baseColor, size: .65, transparent: true,
          opacity: .6, depthWrite: false,
        })))
      }
    }

    // A readable chapter name / count is much more useful than a tiny label
    // submerged in the cloud of points.
    if ((!focusState.active || chapterSelected) &&
        !(smallGroup && node.kind === 'group')) {
      const label = overview
        ? makeChapterLabel(node.name, isCourse ? '课程中心' : `${count} 个知识点`)
        : makeLabel(node.name, isCourse ? 430 : 520,
            isCourse ? '课程中心' : `${count} 个知识点`)
      if (label) {
        if (overview) {
          // Twice the previous screen-space label size at the overview camera.
          label.scale.set(isCourse ? 43 : node.kind==='group' ? 49 : 50,
            isCourse ? 12.4 : node.kind==='group' ? 14.2 : 14.5, 1)
        } else {
          label.scale.set(isCourse ? 22 : 24, 5.3, 1)
        }
        label.position.copy(position)
        label.userData.nodeId = node.id
        const canvasWidth = mountPoint.value?.clientWidth ?? 1500
        const widthPx = node.kind === 'group'
          ? Math.min(186, Math.max(135, canvasWidth / 7.6))
          : Math.min(242, Math.max(150, canvasWidth / 6.5))
        labelAnchors.push({
          sprite: label, center: position,
          distance: radius + (overview ? 10.5 : 4),
          nodeId: node.id,
          priority: node.kind === 'group'
            ? count + (groupOrder >= 0 && groupOrder < 6 ? 7 : 0) : 100,
          declutter: overview && node.kind === 'group' && props.nodes.filter(n=>n.kind==='group').length > 7,
          screen: overview ? {
            width: widthPx, height: widthPx * (208 / 720),
            radius, gap: node.kind === 'group' ? 8 : 12,
          } : undefined,
        })
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
  if (!focusState.active &&
      (props.showStructure !== false || props.nodes.some(n=>n.kind==='group'))) {
    addLines(structural, 0x7b9aad, 0.3)
  }
  addLines(ordinary, 0xffd184, focusState.active ? 0.085 : 0.79)
  for (const [type, points] of highlightedByType) {
    addLines(points, RELATION_COLORS[type] ?? 0xffd184, 1)
  }
  if (outgoing) {
    incoming = fadeGroup(g, [...labelAnchors])
    // Grow the next level out of the clicked planet (or previous camera
    // target for breadcrumb navigation) instead of teleporting a fully
    // populated star field into the scene at unrelated world coordinates.
    const anchors = [...positions.values()]
    const nextCenter = anchors.length
      ? new THREE.Box3().setFromPoints(anchors).getCenter(new THREE.Vector3())
      : new THREE.Vector3()
    const origin = pendingTransitionOrigin ?? controls?.target ?? new THREE.Vector3()
    incomingStartOffset.copy(origin).addScaledVector(nextCenter, -.76)
    incomingStartOffset.clampLength(0, 90)
    incoming.group.position.copy(incomingStartOffset)
    incoming.group.scale.setScalar(.76)
    setFade(incoming, 0)
  }
  pendingTransitionOrigin = null
}

// Automatic selection should move the camera to the chosen node, NOT dive
// into its surface. Keep the user's current zoom within a comfortable range,
// so switching between nearby knowledge points doesn't bounce in and out.
function selectionDistance(kind: GraphNode['kind'], current: number): number {
  const [minimum, maximum] = kind === 'course' ? [105, 145]
    : kind === 'chapter' ? [78, 112] : [58, 94]
  return THREE.MathUtils.clamp(current, minimum, maximum)
}

// Frame what the camera actually projects, not the radius of a 3D sphere.
// Bounding-sphere fit substantially overestimates the needed distance when
// the galaxy is spread horizontally across a wide monitor.
function projectedFitDistance(
  points: THREE.Vector3[],
  center: THREE.Vector3,
  viewDirection: THREE.Vector3,
  fillX: number,
  fillY: number,
  paddingX = 11,
  paddingY = 16,
): number {
  if (!camera || points.length === 0) return 100
  const forward = viewDirection.clone().normalize()
  const right = new THREE.Vector3().crossVectors(
    new THREE.Vector3(0, 1, 0), forward,
  )
  // At a pole, choose another orthogonal basis.
  if (right.lengthSq() < 1e-6) right.set(1, 0, 0)
  right.normalize()
  const screenUp = new THREE.Vector3().crossVectors(forward, right).normalize()
  const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)
  const tanH = tanV * camera.aspect
  let result = 72
  const relative = new THREE.Vector3()
  for (const point of points) {
    relative.subVectors(point, center)
    // Positive depth is closer to the camera; account for perspective
    // rather than fitting all nodes at the center's depth.
    const depth = relative.dot(forward)
    result = Math.max(result,
      depth + (Math.abs(relative.dot(right)) + paddingX) / (tanH * fillX),
      depth + (Math.abs(relative.dot(screenUp)) + paddingY) / (tanV * fillY),
    )
  }
  return result
}

function focus(id: string | null, resetAtlasAngle = false) {
  if (!camera || !controls || !positions.size) return
  const coordinate = id ? positions.get(id) : undefined
  if (id && !coordinate) return
  const localGroup = props.nodes.some(n => n.kind === 'group')
  const visibleKnowledge = props.nodes.filter(n => n.kind === 'knowledge')
    .map(n => positions.get(n.id)).filter((p): p is THREE.Vector3 => Boolean(p))
  const groupPoints = props.nodes.filter(n => n.kind === 'group')
    .map(n => positions.get(n.id)).filter((p): p is THREE.Vector3 => Boolean(p))
  const allKnowledgeCount = (props.layoutNodes ?? props.nodes)
    .filter(n => n.kind === 'knowledge').length
  const isSubset = visibleKnowledge.length > 0 && visibleKnowledge.length < allKnowledgeCount
  const overview = props.nodes.every(n => n.kind !== 'knowledge')
  const groupOverview = localGroup && visibleKnowledge.length === 0

  // Group detail: fit only the actual neighborhood and its group anchor,
  // not a far-away chapter planet. This is what keeps 3-point views legible.
  let fitPoints: THREE.Vector3[]
  if (localGroup) {
    // The labels in a group overview sit above the planets. Include that
    // screen-space extent when computing the framing center and zoom.
    fitPoints = groupOverview
      ? groupPoints.flatMap(p => [p, p.clone().add(new THREE.Vector3(0, 30, 0))])
      : [...visibleKnowledge, ...groupPoints]
  } else if (isSubset) {
    fitPoints = visibleKnowledge
  } else {
    fitPoints = [...positions.values()]
  }
  if (!fitPoints.length) fitPoints = [...positions.values()]
  // Selecting a concept is selecting its one-hop network, not a single
  // isolated planet. Fit the selected concept plus all visible, approved
  // semantic neighbors into the viewport (including the details sidebar).
  const selectedNode = id ? props.nodes.find(n => n.id === id) : undefined
  const visibleIds = new Set(props.nodes.filter(n => n.kind === 'knowledge').map(n => n.id))
  const relatedIds = new Set<string>()
  if (id && selectedNode?.kind === 'knowledge') {
    for (const edge of props.relationEdges ?? props.edges) {
      if (edge.structural || !edge.verified) continue
      if (edge.source === id && visibleIds.has(edge.target)) relatedIds.add(edge.target)
      else if (edge.target === id && visibleIds.has(edge.source)) relatedIds.add(edge.source)
    }
  }
  const relatedPoints = [...relatedIds].map(n => positions.get(n))
    .filter((p): p is THREE.Vector3 => Boolean(p))
  const relationFocus = !!coordinate && relatedPoints.length > 0
  const focusedPoints = relationFocus ? [coordinate, ...relatedPoints] : fitPoints
  const bounds = new THREE.Box3().setFromPoints(focusedPoints)
  const overviewCenter = bounds.getCenter(new THREE.Vector3())
  // For a real relationship network use its center; for a single point,
  // retain the existing click-to-center behavior.
  desiredTarget.copy(relationFocus ? overviewCenter : (coordinate ?? overviewCenter))

  const currentDistance = camera.position.distanceTo(controls.target)
  const direction = camera.position.clone().sub(controls.target)
  if (direction.lengthSq() < .01) direction.set(.4, .8, 1)
  if (!id && (resetAtlasAngle || overview)) {
    if (localGroup) {
      // Browsing groups and leaf networks are laid out in the XY plane.
      // Show them face-on instead of projecting their rows onto each other.
      direction.set(groupOverview ? .22 : 0, groupOverview ? .34 : .16, 1)
    } else {
      // The chapter/course systems occupy the XZ plane: use a high
      // overhead perspective for both chapter AND "all concepts" views.
      direction.set(0, 1, .24)
    }
  }
  let distance: number
  if (relationFocus) {
    // Reserve the right-side detail drawer and the large readable labels.
    // The focus must be based on the actual neighbor positions rather than
    // the old fixed 92-unit zoom that cropped most related nodes.
    distance = Math.max(
      localGroup ? 86 : 110,
      projectedFitDistance(
        focusedPoints, overviewCenter, direction,
        localGroup ? .49 : .54,
        .64,
      ),
    )
    controls.maxDistance = Math.max(280, distance * 1.7)
    camera.far = Math.max(1600, distance * 3)
    camera.updateProjectionMatrix()
  } else if (!id) {
    const extent = Math.max(1, ...fitPoints.map(p=>p.distanceTo(overviewCenter)))
    const vFov = THREE.MathUtils.degToRad(camera.fov)
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect)
    const halfAngle = Math.min(vFov, hFov) / 2
    if (localGroup && visibleKnowledge.length && visibleKnowledge.length <= 18) {
      // Fit the FULL radial graph plus labels, not an arbitrary 94-unit cap.
      // With 14 nodes that cap made every label overlap on screen.
      // Fit the wide, almost planar network against BOTH viewport axes.
      // A spherical radius fit wastes horizontal space on desktop.
      const tanHalf = Math.tan(vFov / 2)
      const maxX = Math.max(0, ...fitPoints.map(p => Math.abs(p.x - overviewCenter.x)))
      const maxY = Math.max(0, ...fitPoints.map(p => Math.abs(p.y - overviewCenter.y)))
      distance = THREE.MathUtils.clamp(Math.max(
        (maxY + 27) / tanHalf,
        (maxX + 38) / (tanHalf * camera.aspect),
      ) * 1.08, 74, 390)
    } else if (groupOverview) {
      // The previous 3D bounding-sphere fit made 12 groups tiny, while a
      // ring plus chapter hub still overlapped. Fit the planar grid and
      // reserve space for each two-line label.
      distance = projectedFitDistance(
        fitPoints, overviewCenter, direction,
        .80, .86, 25, 14,
      )
    } else if (!localGroup) {
      // Course/chapters are arranged on the XZ atlas plane. Projected
      // framing fills most of the available viewport, without the
      // over-conservative spherical radius and minimum zoom clamps.
      distance = projectedFitDistance(
        fitPoints, overviewCenter, direction,
        overview ? .86 : (isSubset ? .76 : .87),
        overview ? .82 : (isSubset ? .73 : .85),
        overview ? 29 : 13,
        overview ? 36 : 18,
      )
    } else {
      distance = Math.max(68, (extent + 12) / Math.sin(halfAngle) * .97)
    }
    controls.maxDistance = Math.max(280, distance * 1.8)
    camera.far = Math.max(1600, distance * 3)
    camera.updateProjectionMatrix()
  } else {
    const selected = props.nodes.find(n => n.id === id)
    if (localGroup && visibleKnowledge.length <= 18)
      distance = selected?.kind === 'knowledge' ? 92 : 112
    else distance = selectionDistance(selected?.kind ?? 'knowledge', currentDistance)
  }
  if (scene?.fog instanceof THREE.FogExp2)
    scene.fog.density = Math.min(0.0015, .35 / Math.max(72,distance))
  if (direction.lengthSq() < 0.01) direction.set(.4, .8, 1)
  desiredPosition.copy(desiredTarget).add(direction.normalize().multiplyScalar(distance))
  if (reducedMotion()) {
    camera.position.copy(desiredPosition)
    controls.target.copy(desiredTarget)
    tween = false
  } else {
    cameraStartPosition.copy(camera.position)
    cameraStartTarget.copy(controls.target)
    cameraAnimationStart = performance.now()
    tween = true
  }
}
function resetCamera() { focus(null, true) }
// User-controlled zoom is useful for dense whole-course views; do not reset
// the scene or change the selected concept when adjusting the lens.
function zoomBy(factor: number) {
  if (!camera || !controls || factor <= 0) return
  const target = tween ? desiredTarget : controls.target
  const origin = tween ? desiredPosition : camera.position
  const offset = origin.clone().sub(target)
  if (offset.lengthSq() < .01) offset.set(.4, .8, 1)
  const distance = THREE.MathUtils.clamp(
    offset.length() * factor, controls.minDistance, controls.maxDistance,
  )
  desiredTarget.copy(target)
  desiredPosition.copy(target).add(offset.normalize().multiplyScalar(distance))
  if (reducedMotion()) {
    camera.position.copy(desiredPosition)
    controls.target.copy(desiredTarget)
    tween = false
  } else {
    cameraStartPosition.copy(camera.position)
    cameraStartTarget.copy(controls.target)
    cameraAnimationStart = performance.now()
    tween = true
  }
}
function zoomIn() { zoomBy(.78) }
function zoomOut() { zoomBy(1.28) }
defineExpose({ resetCamera, zoomIn, zoomOut })

function placeLabel(anchor: LabelAnchor) {
  if (!camera || !mountPoint.value) return
  if (!anchor.screen) {
    anchor.sprite.position.copy(anchor.center).addScaledVector(screenUp, anchor.distance)
    return
  }
  // Screen-size labels: world-space sprite scales based on their own depth.
  // This avoids illegible 8px chapter names whenever the 3D camera zooms out.
  const depth = Math.max(1, camera.position.distanceTo(anchor.center))
  const worldPerPx = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)
    * depth / Math.max(1, mountPoint.value.clientHeight)
  const { width, height, radius, gap } = anchor.screen
  anchor.sprite.scale.set(width * worldPerPx, height * worldPerPx, 1)
  anchor.sprite.position.copy(anchor.center).addScaledVector(
    screenUp, radius + (height / 2 + gap) * worldPerPx,
  )
}

// When 30+ browsing groups are shown, 30 large labels cannot all fit in the
// same viewport. Keep the planets visible, but show only captions that do not
// overlap; a hovered planet gets first priority and always reveals its name.
function declutterGroupLabels() {
  if (!camera || !renderer) return
  const items = labelAnchors.filter(a => a.declutter && a.screen)
  if (!items.length) return
  const width = renderer.domElement.clientWidth
  const height = renderer.domElement.clientHeight
  const boxes: { left: number; right: number; top: number; bottom: number }[] = []
  const sorted = [...items].sort((a,b) =>
    Number(b.nodeId === hoveredNodeId) - Number(a.nodeId === hoveredNodeId) ||
    (b.priority ?? 0) - (a.priority ?? 0) ||
    (a.nodeId ?? '').localeCompare(b.nodeId ?? ''))
  const cap = Math.min(16, Math.max(8, Math.round(width * height / 78000)))
  let visibleCount = 0
  for (const anchor of sorted) {
    const screen = anchor.sprite.position.clone().project(camera)
    const px = (screen.x + 1) * width / 2
    const py = (1 - screen.y) * height / 2
    const w = anchor.screen!.width
    const h = anchor.screen!.height
    const box = {left:px-w/2-7, right:px+w/2+7,
      top:py-h/2-6, bottom:py+h/2+6}
    const forced = anchor.nodeId === hoveredNodeId
    const fits = screen.z > -1 && screen.z < 1 &&
      box.left >= 10 && box.right <= width - 10 &&
      box.top >= 100 && box.bottom <= height - 25
    const overlaps = boxes.some(other =>
      box.left < other.right && box.right > other.left &&
      box.top < other.bottom && box.bottom > other.top)
    // Do not hide the hovered caption just because it overlaps a label
    // elsewhere; the sort above allows it to displace that label.
    const show = fits && !overlaps && (visibleCount < cap || forced)
    anchor.sprite.visible = show
    if (show) {
      visibleCount++
      boxes.push(box)
    }
  }
}

function draw() {
  if (!renderer || !camera || !controls || !scene || disposed || !visible) return
  const now = performance.now()
  if (tween) {
    const raw = Math.min(1, (now - cameraAnimationStart) / CAMERA_DURATION)
    // Interpolate pan, orientation and zoom independently. Straight-line
    // camera interpolation used to pass through the graph and made
    // chapter-to-group transitions look like a sudden jump.
    const eased = raw * raw * (3 - 2 * raw)
    controls.target.lerpVectors(cameraStartTarget, desiredTarget, eased)
    const fromOffset = cameraStartPosition.clone().sub(cameraStartTarget)
    const toOffset = desiredPosition.clone().sub(desiredTarget)
    const fromRadius = Math.max(1, fromOffset.length())
    const toRadius = Math.max(1, toOffset.length())
    const arc = new THREE.Quaternion().setFromUnitVectors(
      fromOffset.normalize(), toOffset.normalize(),
    )
    const rotation = new THREE.Quaternion().identity().slerp(arc, eased)
    const radius = Math.exp(Math.log(fromRadius) * (1 - eased) +
      Math.log(toRadius) * eased)
    camera.position.copy(controls.target).addScaledVector(
      fromOffset.applyQuaternion(rotation).normalize(), radius,
    )
    if (raw >= 1) tween = false
  }
  if (outgoing && incoming) {
    const raw = Math.min(1, (now - layerTransitionStart) / LAYER_DURATION)
    const eased = raw * raw * (3 - 2 * raw)
    setFade(outgoing, 1 - eased)
    setFade(incoming, eased)
    incoming.group.scale.setScalar(.76 + .24 * eased)
    incoming.group.position.copy(incomingStartOffset).multiplyScalar(1 - eased)
    if (raw >= 1) finishFade()
  }
  controls.update()
  // Keep all visible labels above the corresponding planet on screen,
  // including labels in the outgoing scene during a level transition.
  screenUp.set(0, 1, 0).applyQuaternion(camera.quaternion)
  for (const anchor of labelAnchors) placeLabel(anchor)
  if (outgoing) for (const anchor of outgoing.anchors) placeLabel(anchor)
  declutterGroupLabels()
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
function pickNode(clientX: number, clientY: number): string | null {
  if (!renderer || !camera) return null
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.set(((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1)
  raycaster.setFromCamera(pointer, camera)
  const clickableLabels = labelAnchors
    .filter(a => a.sprite.visible && !!a.nodeId).map(a => a.sprite)
  const hits = raycaster.intersectObjects([...meshes, ...clickableLabels])
  const hit = hits[0]
  const instanceIds = hit?.object.userData.instanceNodeIds as string[] | undefined
  return (instanceIds && hit.instanceId !== undefined
    ? instanceIds[hit.instanceId] : hit?.object.userData.nodeId) ?? null
}
function pointerMove(event: PointerEvent) {
  if (!renderer) return
  hoveredNodeId = pickNode(event.clientX, event.clientY)
  renderer.domElement.style.cursor = hoveredNodeId ? 'pointer' : 'grab'
}
function pointerLeave() {
  hoveredNodeId = null
  if (renderer) renderer.domElement.style.cursor = 'grab'
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
  const id = pickNode(event.clientX, event.clientY)
  // Remember the exact clicked position for the next hierarchy transition.
  pendingTransitionOrigin = id ? positions.get(id)?.clone() ?? null : null
  emit('select', id)
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
  // A slightly overhead view gives chapter orbits a circular silhouette,
  // rather than flattening the galaxy into an edge-on ribbon.
  camera.position.set(34, 112, 96)
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
  renderer.domElement.addEventListener('pointermove', pointerMove)
  renderer.domElement.addEventListener('pointerleave', pointerLeave)
  document.addEventListener('visibilitychange', visibilityChanged)
  focus(null, true)
  draw()
}
onMounted(initialize)
// Changing line visibility must never zoom the camera. Only a change of
// selected node should trigger automatic focus.
// Batch all reactive scene inputs into one render pass. Separate watchers for
// nodes, edges and selection used to overwrite the outgoing animation within
// the same Vue flush, making level switches appear instantaneous.
// Use actual node identity rather than the array object reference.
// Computed arrays can be recreated by selection/filter changes, previously
// restarting a layer fade and camera fit even when the scene was unchanged.
const sceneSignature = (nodes: GraphNode[]) =>
  nodes.map(n => n.kind + ':' + n.id).sort().join('|')
watch(() => ({
  nodes: props.nodes,
  edges: props.edges,
  relations: props.relationEdges,
  layoutNodes: props.layoutNodes,
  layoutEdges: props.layoutEdges,
  selectedId: props.selectedId,
  showRelations: props.showRelations,
  showStructure: props.showStructure,
}), (next, previous) => {
  const layerChanged = sceneSignature(next.nodes) !== sceneSignature(previous.nodes)
    || next.layoutNodes !== previous.layoutNodes
    || next.layoutEdges !== previous.layoutEdges
  if (!layerChanged) pendingTransitionOrigin = null
  rebuild(layerChanged)
  if (layerChanged || next.selectedId !== previous.selectedId)
    focus(next.selectedId, layerChanged)
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
    renderer.domElement.removeEventListener('pointermove', pointerMove)
    renderer.domElement.removeEventListener('pointerleave', pointerLeave)
  }
  clearGraph()
  finishFade()
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
