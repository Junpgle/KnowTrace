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
}>()
const emit = defineEmits<{ select: [id: string] }>()
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
  const n = Math.max(1, chapters.length)
  const orbit = Math.max(19, n * 5.2)
  // Nested chapters are still real chapters; angle encodes groups only.
  chapters.forEach((chapter, i) => {
    const a = (i / n) * Math.PI * 2 + Math.PI / 6
    const r = orbit + (i % 2) * 3
    pos.set(chapter.id, new THREE.Vector3(
      Math.cos(a) * r, Math.sin(i * 2.1) * 7, Math.sin(a) * r,
    ))
  })

  const parent = new Map<string, string>()
  edges.filter(e => e.structural && e.type === 'contains').forEach(e => parent.set(e.target, e.source))
  const byChapter = new Map<string, GraphNode[]>()
  nodes.filter(n => n.kind === 'knowledge').forEach(kp => {
    const id = parent.get(kp.id) || root.id
    if (!byChapter.has(id)) byChapter.set(id, [])
    byChapter.get(id)!.push(kp)
  })
  for (const [id, points] of byChapter) {
    const center = pos.get(id) || new THREE.Vector3()
    points.sort((a, b) => a.id.localeCompare(b.id))
    points.forEach((point, i) => {
      const angle = i * G + ordinalHash(id) * 6.28
      const radius = 4.1 + 1.9 * Math.sqrt(i + 1)
      const elevation = (ordinalHash(point.id) - 0.5) * 8
      pos.set(point.id, new THREE.Vector3(
        center.x + radius * Math.cos(angle),
        center.y + elevation,
        center.z + radius * Math.sin(angle),
      ))
    })
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

function rebuild() {
  if (!scene) return
  clearGraph()
  const g = new THREE.Group()
  group = g
  scene.add(g)
  const coords = layout(props.nodes, props.edges)
  coords.forEach((v, id) => positions.set(id, v))
  // One InstancedMesh for all knowledge points: thousands of planets require
  // one draw call, not a separate draw call/material per planet.
  const sphere = new THREE.SphereGeometry(1, 12, 10)
  const knowledge = props.nodes.filter(n => n.kind === 'knowledge' && positions.has(n.id))
  if (knowledge.length) {
    const material = new THREE.MeshPhongMaterial({
      color: 0xffffff, shininess: 70, specular: 0x526b80,
      emissive: 0x243743, emissiveIntensity: 0.18,
    })
    const cluster = new THREE.InstancedMesh(sphere, material, knowledge.length)
    const matrix = new THREE.Matrix4()
    const size = new THREE.Vector3()
    const rotation = new THREE.Quaternion()
    const ids: string[] = []
    knowledge.forEach((node, index) => {
      const position = positions.get(node.id)!
      // Highlight the selected point without blowing up the planet itself.
      const radius = node.id === props.selectedId ? 1.24 : 1
      size.setScalar(radius)
      matrix.compose(position, rotation, size)
      cluster.setMatrixAt(index, matrix)
      cluster.setColorAt(index, new THREE.Color(statusColor(node)))
      ids.push(node.id)
      if (node.id === props.selectedId) {
        const label = makeLabel(node.name)
        if (label) {
          label.position.copy(position).add(new THREE.Vector3(0, radius + 2.35, 0))
          g.add(label)
        }
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
    const color = statusColor(node)
    const material = new THREE.MeshPhongMaterial({
      color, emissive: color, emissiveIntensity: 0.28,
      shininess: 86, specular: 0x6b8fae,
    })
    const radius = node.kind === 'course' ? 3.8 : 2.15
    const mesh = new THREE.Mesh(sphere, material)
    mesh.scale.setScalar(radius)
    mesh.position.copy(position)
    mesh.userData.nodeId = node.id
    g.add(mesh)
    meshes.push(mesh)
    const label = makeLabel(node.name)
    if (label) {
      label.position.copy(position).add(new THREE.Vector3(0, radius + 2.35, 0))
      g.add(label)
    }
  }
  const activeEdges = props.edges.filter(edge => edge.structural || props.showRelations)
  const positionsStructural: number[] = []
  const positionsSemantic: number[] = []
  activeEdges.forEach(edge => {
    const a = coords.get(edge.source)
    const b = coords.get(edge.target)
    if (!a || !b) return
    ;(edge.structural ? positionsStructural : positionsSemantic).push(
      a.x, a.y, a.z, b.x, b.y, b.z,
    )
  })
  function addLines(points: number[], color: number, opacity: number) {
    if (!points.length) return
    const geom = new THREE.BufferGeometry()
    geom.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
    const lines = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({
      color, transparent: true, opacity, depthWrite: false,
    }))
    g.add(lines)
  }
  addLines(positionsStructural, 0x7b9aad, 0.3)
  addLines(positionsSemantic, 0xffd184, 0.79)
}

// Automatic selection should move the camera to the chosen node, NOT dive
// into its surface. Keep the user's current zoom within a comfortable range,
// so switching between nearby knowledge points doesn't bounce in and out.
export function selectionDistance(kind: GraphNode['kind'], current: number): number {
  const [minimum, maximum] = kind === 'course' ? [105, 145]
    : kind === 'chapter' ? [78, 112] : [58, 94]
  return THREE.MathUtils.clamp(current, minimum, maximum)
}

function focus(id: string | null) {
  if (!camera || !controls) return
  const coordinate = id ? positions.get(id) : undefined
  if (id && !coordinate) return
  desiredTarget.copy(coordinate || new THREE.Vector3())
  const selected = props.nodes.find(n => n.id === id)
  const currentDistance = camera.position.distanceTo(controls.target)
  const distance = !id
    ? Math.max(85, Math.max(18, props.nodes.filter(n => n.kind === 'chapter').length * 5.2) * 2.7)
    : selectionDistance(selected?.kind ?? 'knowledge', currentDistance)
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
  if (id) emit('select', id)
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
watch(() => [props.nodes, props.edges], () => {
  rebuild()
  if (props.selectedId) focus(props.selectedId)
})
watch(() => props.showRelations, rebuild)
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
