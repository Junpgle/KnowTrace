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
const labelAnchors: { sprite: THREE.Sprite; center: THREE.Vector3; distance: number }[] = []
const screenUp = new THREE.Vector3()
type LabelAnchor = { sprite: THREE.Sprite; center: THREE.Vector3; distance: number }
type FadePart = { material: THREE.Material; opacity: number; transparent: boolean; depthWrite: boolean }
type FadeGroup = { group: THREE.Group; parts: FadePart[]; anchors: LabelAnchor[] }
let outgoing: FadeGroup | null = null
let incoming: FadeGroup | null = null
let layerTransitionStart = 0
const LAYER_DURATION = 520
const CAMERA_DURATION = 850
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
  ctx.font = '600 48px system-ui, sans-serif'
  if (lines.length > 1) {
    ctx.fillText(lines[0], 360, 64, 668)
    ctx.fillText(lines[1], 360, 115, 668)
  } else {
    ctx.fillText(lines[0], 360, 82, 668)
  }
  ctx.font = '500 29px system-ui, sans-serif'
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
  if(groupNodes.length){
    const chapter=nodes.find(n=>n.kind==='chapter')
    if(chapter)pos.set(chapter.id,groupNodes.length===1?
      new THREE.Vector3(-27,-5,-7):new THREE.Vector3())
    groupNodes.forEach((g,i)=>{
      const angle=2*Math.PI*i/groupNodes.length-Math.PI/2
      const center=groupNodes.length===1?new THREE.Vector3():
       new THREE.Vector3(Math.cos(angle)*(32+Math.sqrt(groupNodes.length)*4),
         Math.sin(i*2.4)*5,Math.sin(angle)*(32+Math.sqrt(groupNodes.length)*4))
      pos.set(g.id,center)
      const points=nodes.filter(n=>n.kind==='knowledge'&&
        edges.some(e=>e.structural&&e.source===g.id&&e.target===n.id))
        .sort((a,b)=>a.id.localeCompare(b.id))
      points.forEach((p,j)=>{
        const theta=j*Math.PI*(3-Math.sqrt(5))
        const y=1-2*(j+.5)/points.length
        const radial=Math.sqrt(Math.max(0,1-y*y))
        // Small relationship neighborhoods must occupy a tight, readable
        // field rather than distributing 3 points over a 60-unit diameter.
        const radius = 11 + Math.min(8, Math.sqrt(points.length)*2.4)
        pos.set(p.id,center.clone().add(new THREE.Vector3(
          Math.cos(theta)*radial*radius,y*radius*.63,Math.sin(theta)*radial*radius)))
      })
    })
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
  top.forEach((ch, i) => {
    const a = Math.PI * 2 * (i / Math.max(1, total)) - Math.PI / 2
    const tilt = 0.95 + .08 * Math.sin(i * 3.1)
    pos.set(ch.id, new THREE.Vector3(
      Math.cos(a) * orbit * tilt,
      Math.sin(i * 2.399) * 11,
      Math.sin(a) * orbit * tilt,
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
  const smallGroup=props.nodes.some(n=>n.kind==='group')&&knowledge.length<=18
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

      if (smallGroup || center || (neighbor && neighborLabels < 12)) {
        if (neighbor) neighborLabels += 1
        const label = makeLabel(node.name, smallGroup ? 480 : 320)
        if (label) {
          if (smallGroup) label.scale.set(19, 4, 1)
          label.position.copy(position)
          labelAnchors.push({ sprite: label, center: position, distance: radius + (smallGroup ? 4.3 : 4.5) })
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
    const isCourse = node.kind === 'course'
    const count = node.kind==='group' ? node.source_count : (cachedChapterTotals.get(node.id) ?? 0)
    const baseColor = isCourse ? 0x4be6d6 : node.kind==='group' ? 0x5bcabb : palette.get(node.id) ?? 0x83a4d9
    const chapterSelected = node.id === selectedId
    // Overview planets must be readable on a large desktop canvas.
    // The previous 4-6 world-unit spheres appeared only ~30px across.
    const radius = node.kind==='group'
      ? (smallGroup ? 5.7 : overview ? 9.3 : 6.8)
      : isCourse ? (overview ? 9.8 : 6.0)
      : (overview
          ? 8.1 + Math.min(3.0, Math.log2(count + 1) * .47)
          : 3.6 + Math.min(2.5, Math.log2(count + 1) * .35))
        * (chapterSelected ? 1.12 : 1)
    const material = new THREE.MeshPhongMaterial({
      color: baseColor,
      emissive: baseColor,
      emissiveIntensity: focusState.active ? .09 : .23,
      shininess: 100, specular: 0xd9ffff,
      transparent: true,
      opacity: focusState.active && !chapterSelected ? .55 : .96,
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
      ring.rotation.set(.32, ordinalHash(node.id) * Math.PI, .15)
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
    if (!focusState.active || chapterSelected) {
      const label = overview
        ? makeChapterLabel(node.name, isCourse ? '课程中心' : `${count} 个知识点`)
        : makeLabel(node.name, isCourse ? 430 : 520,
            isCourse ? '课程中心' : `${count} 个知识点`)
      if (label) {
        if (overview) {
          // Twice the previous screen-space label size at the overview camera.
          label.scale.set(isCourse ? 43 : node.kind==='group' ? 27 : 50,
            isCourse ? 12.4 : node.kind==='group' ? 8.2 : 14.5, 1)
        } else {
          label.scale.set(isCourse ? 22 : 24, 5.3, 1)
        }
        label.position.copy(position)
        labelAnchors.push({
          sprite: label, center: position,
          distance: radius + (overview ? 10.5 : 4),
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
  if (props.showStructure !== false || props.nodes.some(n=>n.kind==='group')) {
    addLines(structural, 0x7b9aad, focusState.active ? 0.055 : 0.3)
  }
  addLines(ordinary, 0xffd184, focusState.active ? 0.085 : 0.79)
  for (const [type, points] of highlightedByType) {
    addLines(points, RELATION_COLORS[type] ?? 0xffd184, 1)
  }
  if (outgoing) {
    incoming = fadeGroup(g, [...labelAnchors])
    setFade(incoming, 0)
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

  // Group detail: fit only the actual neighborhood and its group anchor,
  // not a far-away chapter planet. This is what keeps 3-point views legible.
  let fitPoints: THREE.Vector3[]
  if (localGroup) {
    fitPoints = visibleKnowledge.length
      ? [...visibleKnowledge, ...groupPoints]
      : groupPoints
  } else if (isSubset) {
    fitPoints = visibleKnowledge
  } else {
    fitPoints = [...positions.values()]
  }
  if (!fitPoints.length) fitPoints = [...positions.values()]
  const bounds = new THREE.Box3().setFromPoints(fitPoints)
  const overviewCenter = bounds.getCenter(new THREE.Vector3())
  desiredTarget.copy(coordinate ?? overviewCenter)

  const currentDistance = camera.position.distanceTo(controls.target)
  let distance: number
  if (!id) {
    const extent = Math.max(1, ...fitPoints.map(p=>p.distanceTo(overviewCenter)))
    const vFov = THREE.MathUtils.degToRad(camera.fov)
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * camera.aspect)
    const halfAngle = Math.min(vFov, hFov) / 2
    if (localGroup && visibleKnowledge.length && visibleKnowledge.length <= 18) {
      // Node radius and labels are also enlarged in this detail mode.
      distance = THREE.MathUtils.clamp(
        (extent + 8) / Math.sin(halfAngle) * 1.06, 46, 94)
    } else {
      distance = Math.max(localGroup ? 58 : overview ? 72 : 85,
        (extent + (overview ? 15 : 9)) / Math.sin(halfAngle)
        * (overview ? .93 : 1.10))
    }
    controls.maxDistance = Math.max(280, distance * 1.8)
    camera.far = Math.max(1600, distance * 3)
    camera.updateProjectionMatrix()
  } else {
    const selected = props.nodes.find(n => n.id === id)
    if (localGroup && visibleKnowledge.length <= 18)
      distance = selected?.kind === 'knowledge' ? 46 : 59
    else distance = selectionDistance(selected?.kind ?? 'knowledge', currentDistance)
  }
  if (scene?.fog instanceof THREE.FogExp2)
    scene.fog.density = Math.min(0.0015, .35 / Math.max(72,distance))
  const direction = camera.position.clone().sub(controls.target)
  if (direction.lengthSq() < 0.01) direction.set(.4,.8,1)
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
function resetCamera() { focus(null) }
defineExpose({ resetCamera })

function draw() {
  if (!renderer || !camera || !controls || !scene || disposed || !visible) return
  const now = performance.now()
  if (tween) {
    const raw = Math.min(1, (now - cameraAnimationStart) / CAMERA_DURATION)
    const eased = raw * raw * (3 - 2 * raw)
    camera.position.lerpVectors(cameraStartPosition, desiredPosition, eased)
    controls.target.lerpVectors(cameraStartTarget, desiredTarget, eased)
    if (raw >= 1) tween = false
  }
  if (outgoing && incoming) {
    const raw = Math.min(1, (now - layerTransitionStart) / LAYER_DURATION)
    const eased = raw * raw * (3 - 2 * raw)
    setFade(outgoing, 1 - eased)
    setFade(incoming, eased)
    if (raw >= 1) finishFade()
  }
  controls.update()
  // Keep all visible labels above the corresponding planet on screen,
  // including labels in the outgoing scene during a level transition.
  screenUp.set(0, 1, 0).applyQuaternion(camera.quaternion)
  for (const anchor of labelAnchors) {
    anchor.sprite.position.copy(anchor.center).addScaledVector(screenUp, anchor.distance)
  }
  if (outgoing) {
    for (const anchor of outgoing.anchors) {
      anchor.sprite.position.copy(anchor.center).addScaledVector(screenUp, anchor.distance)
    }
  }
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
  document.addEventListener('visibilitychange', visibilityChanged)
  focus(null)
  draw()
}
onMounted(initialize)
// Changing line visibility must never zoom the camera. Only a change of
// selected node should trigger automatic focus.
watch(() => [props.nodes, props.layoutNodes, props.layoutEdges], () => {
  rebuild(true)
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
