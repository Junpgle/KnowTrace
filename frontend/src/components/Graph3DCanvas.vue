<script setup lang="ts">
/**
 * Real WebGL scene, no simulated 2D CSS star map.
 * All spatial positions encode *layout* only; edges come from the API.
 * Explicit teardown avoids GPU/RAF leaks on course change and navigation.
 */
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import * as THREE from 'three'
import { orbitMetrics } from '../orbitMetrics'
import { planCenteredCourseOrbits } from '../centeredCourseOrbits'
import { placeOrbitCaptions } from '../orbitCaptionPlacement'
import { tightAtlasDistance } from '../tightAtlasViewport'
import { snapshotOrbitFrame, restoreOrbitFrame } from '../orbitNavigationFrame'
import type { SavedOrbit } from '../orbitNavigationFrame'
import type { OrbitCaptionSlot } from '../orbitCaptionPlacement'
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
  // Real nodes outside the current zoom layer remain in the scene as a dim
  // atlas. This is a visual context, never inferred knowledge relationships.
  contextNodes?: GraphNode[]
  contextEdges?: GraphEdge[]
  contextChapterId?: string | null
  preservePositionsOnSelect?: boolean
  navigation?: { serial: number; mode: 'enter' | 'back' | 'jump'; nodeId?: string }
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
let fogStartDensity = .0015
let fogDesiredDensity = .0015
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
let lastViewportWidth = 0
let lastViewportHeight = 0
let contextCacheNodes: GraphNode[] | undefined
let contextCacheEdges: GraphEdge[] | undefined
let contextCacheCoordinates = new Map<string, THREE.Vector3>()
let contextCacheParents = new Map<string, string>()
// A map view has persistent world coordinates. Saving each drilled layer's
// positions makes back navigation return to the SAME places rather than
// rebuilding a constellation at the world origin.
const sceneFrames = new Map<string, Map<string, THREE.Vector3>>()
// A valid return needs the saved axes, center and phase, not only positions.
const orbitFrames = new Map<string, Map<string, SavedOrbit>>()
// Browse groups live in a neighboring map region. Preserve their world
// addresses when selection, line visibility or a rapid click rebuilds a scene;
// otherwise they snap back to the origin and appear to "expand" once again.
const browseWorldPositions = new Map<string, THREE.Vector3>()
type SceneTransition = {
  mode: 'enter' | 'back' | 'jump'
  anchorId?: string
  restore?: Map<string, THREE.Vector3>
  restoreOrbits?: Map<string, SavedOrbit>
}
const meshes: THREE.Mesh[] = []
const contextMeshes: THREE.Mesh[] = []
type ScreenBox = { left: number; right: number; top: number; bottom: number }
type BackdropPoint = { position: THREE.Vector3; color: THREE.Color; local: boolean }
let backdropCloud: THREE.InstancedMesh | null = null
const backdropPoints: BackdropPoint[] = []
let lastBackdropLayout = 0
const projectedPoint = new THREE.Vector3()
const cameraFacing = new THREE.Vector3()
let hoveredNodeId: string | null = null
const hoveredName = ref('')
const hoveredOrbitInfo = ref('')
const hoveredPoint = ref({ x: 0, y: 0 })
const labelAnchors: LabelAnchor[] = []
// Visual tracks are intentionally browse-only and never become knowledge
// graph edges. Hovering a group subtly illuminates its complete ellipse.
const orbitTracks: { line: THREE.Line; ids: Set<string>; baseOpacity: number }[] = []
// In the browse overview, group planets and their glow are map markers:
// their radius is specified in SCREEN pixels, not permanent world units.
// This prevents a 10-world-unit globe from covering neighboring orbit slots
// when chapter-level camera zoom brings the group atlas into view.
const orbitMarkers: {
  planet: THREE.Mesh; aura: THREE.Sprite; id: string
  count: number; kind: 'chapter' | 'group'
}[] = []
const orbitMarkerRadii = new Map<string, number>()
// Overview captions belong to moving planets and keep a FIXED relative
// position. The ordinary collision solver can jump between many candidate
// offsets every frame, which looks like the name is shaking or teleporting.
const stableOrbitCaptionOpacity = new Map<string, number>()
const stableOrbitCaptionTargets = new Map<string, OrbitCaptionSlot>()
const stableOrbitCaptionOffsets = new Map<string, OrbitCaptionSlot>()
type OrbitingPlanet = {
  id: string
  world: THREE.Vector3
  initialWorld: THREE.Vector3
  center: THREE.Vector3
  axisX: THREE.Vector3
  axisY: THREE.Vector3
  major: number
  minor: number
  phase: number
  radiansPerSecond: number
  elapsedSeconds: number
  objects: {object: THREE.Object3D; start: THREE.Vector3}[]
}
const orbitingPlanets: OrbitingPlanet[] = []
const movingLines: {
  geometry: THREE.BufferGeometry
  edges: {source:string;target:string}[]
}[] = []
let lastOrbitFrame = 0
let chapterOrbitPixelScale = 1
const orbitVisuals = new Map<string, {object:THREE.Object3D;start:THREE.Vector3}[]>()
const screenUp = new THREE.Vector3()
type LabelAnchor = {
  sprite: THREE.Sprite
  center: THREE.Vector3
  distance: number
  nodeId?: string
  priority?: number
  declutter?: boolean
  focused?: boolean
  // Atlas captions maintain a readable size in screen pixels while zooming.
  screen?: { width: number; height: number; radius: number; gap: number }
}
type FadePart = { material: THREE.Material; opacity: number; transparent: boolean; depthWrite: boolean }
type FadeGroup = { group: THREE.Group; parts: FadePart[]; anchors: LabelAnchor[] }
let outgoing: FadeGroup[] = []
let incoming: FadeGroup | null = null
type CameraSnapshot = { sceneKey: string; position: THREE.Vector3; target: THREE.Vector3 }
const cameraHistory: CameraSnapshot[] = []
let transitionMode: 'enter' | 'back' | 'jump' = 'jump'
const CAMERA_DURATION = 1120
const LAYER_REVEAL_START = .24
const LAYER_REVEAL_END = .86
function reducedMotion() {
  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

// Never impose the DESTINATION's zoom bounds on the CURRENT camera before
// the flight begins: OrbitControls.update() would instantly clamp the start
// distance and cause an unmistakable one-frame jump.
function prepareFlightRange(destinationDistance: number) {
  if (!camera || !controls) return
  const startDistance = camera.position.distanceTo(controls.target)
  const inFlightDistance = tween ? desiredPosition.distanceTo(desiredTarget) : 0
  const farthest = Math.max(startDistance, destinationDistance, inFlightDistance)
  controls.maxDistance = Math.max(280, farthest * 1.35)
  camera.far = Math.max(1600, farthest * 3.5)
  camera.updateProjectionMatrix()
}

function prepareFlightFog(destinationDistance: number) {
  if (!(scene?.fog instanceof THREE.FogExp2)) return
  fogStartDensity = scene.fog.density
  fogDesiredDensity = Math.min(.0015, .35 / Math.max(72, destinationDistance))
  if (reducedMotion()) scene.fog.density = fogDesiredDensity
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

// Readable concept captions for the selected one-hop network. Real names
// are kept over two lines where possible, rather than rendered as 3px text
// when the camera fits distant approved neighbors into a single viewport.
function makeFocusLabel(name: string, selected: boolean): THREE.Sprite | null {
  const canvas = document.createElement('canvas')
  canvas.width = 640
  canvas.height = 180
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = selected ? 'rgba(9, 53, 61, .96)' : 'rgba(9, 33, 52, .94)'
  ctx.beginPath()
  ctx.roundRect(4, 4, 632, 172, 22)
  ctx.fill()
  ctx.lineWidth = selected ? 5 : 3
  ctx.strokeStyle = selected ? '#67f4dc' : 'rgba(134, 209, 229, .77)'
  ctx.stroke()
  // Keep up to 11 Chinese characters per row so fillText does not
  // silently squeeze a 15-20-character sentence to tiny visual glyphs.
  const shown = name.length > 22 ? name.slice(0, 21) + '…' : name
  const lines = shown.length > 11
    ? [shown.slice(0, 11), shown.slice(11)] : [shown]
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = selected ? '#f4fffc' : '#e7f6ff'
  ctx.font = '700 58px system-ui, sans-serif'
  if (lines.length === 1) {
    ctx.fillText(lines[0], 320, 90, 584)
  } else {
    ctx.fillText(lines[0], 320, 59, 584)
    ctx.fillText(lines[1], 320, 123, 584)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture, transparent: true, depthWrite: false,
    depthTest: false, fog: false,
  }))
}

// Chapter labels use a larger, high-resolution canvas than knowledge labels.
// Two-line wrapping keeps real chapter names readable without covering planets.
function makeChapterLabel(title: string, subtitle: string) {
  const canvas = document.createElement('canvas')
  canvas.width = 720
  canvas.height = 208
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.fillStyle = 'rgba(7, 29, 44, .85)'
  ctx.beginPath()
  ctx.roundRect(5, 5, 710, 198, 26)
  ctx.fill()
  ctx.strokeStyle = 'rgba(120, 204, 204, .34)'
  ctx.lineWidth = 2
  ctx.stroke()
  const name = title.length > 26 ? title.slice(0, 25) + '…' : title
  const lines = name.length > 13 ? [name.slice(0, 13), name.slice(13)] : [name]
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#f0ffff'
  ctx.font = '650 53px system-ui, sans-serif'
  if (lines.length > 1) {
    ctx.fillText(lines[0], 360, 64, 668)
    ctx.fillText(lines[1], 360, 115, 668)
  } else {
    ctx.fillText(lines[0], 360, 82, 668)
  }
  ctx.font = '550 34px system-ui, sans-serif'
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

// Projectable sunflower disk: sparse points circle their real chapter in
// the XZ atlas plane instead of forming an overlapping miniature 3D ball.
function organicOffsets(count: number, seed: string): THREE.Vector3[] {
  const offsets: THREE.Vector3[] = []
  const twist = ordinalHash(seed + ':twist') * Math.PI * 2
  const step = count < 20 ? 8.1 : 9.4
  for (let i = 0; i < count; i++) {
    const theta = twist + i * G
    const radius = 15 + step * Math.sqrt(i + .6)
    offsets.push(new THREE.Vector3(
      Math.cos(theta) * radius,
      (ordinalHash(seed + ':elev:' + i) - .5) * 10,
      Math.sin(theta) * radius,
    ))
  }
  return offsets
}

// Browse-only groups are distributed over concentric elliptical ORBITS.
// Ring sizes increase with circumference (35 groups -> [6, 8, 10, 11]),
// not over grid rows. Group positioning never represents semantic distance.
function browseOrbitBands(groups: GraphNode[]): GraphNode[][] {
  const sorted = [...groups].sort((a, b) =>
    a.name.localeCompare(b.name, 'zh-Hans') || a.id.localeCompare(b.id))
  const count = sorted.length
  if (!count) return []
  const rings = count <= 8 ? 1 : count <= 18 ? 2 :
    count <= 30 ? 3 : count <= 48 ? 4 :
      Math.min(8, Math.ceil(count / 11))
  const weights = Array.from({length:rings}, (_, i) => 1 + i*.28)
  const sum = weights.reduce((a,b)=>a+b,0)
  const exact = weights.map(w => count*w/sum)
  const sizes = exact.map(Math.floor)
  let pending = count - sizes.reduce((a,b)=>a+b,0)
  const order = exact.map((n,i)=>({i,fraction:n-Math.floor(n)}))
    .sort((a,b)=>b.fraction-a.fraction || b.i-a.i)
  for (let i=0;i<pending;i++) sizes[order[i%order.length].i]++
  const bands: GraphNode[][] = []
  let offset=0
  for (const size of sizes) {
    bands.push(sorted.slice(offset, offset+size))
    offset+=size
  }
  return bands
}

// Equal ANGULAR increments bunch up at the narrow ends of an ellipse.
// Instead sample its perimeter at approximately equal arc-length intervals.
function ellipseArcAngles(a: number, b: number) {
  const segments = 384
  const arc = new Float64Array(segments+1)
  let prevX=a,prevY=0
  for(let i=1;i<=segments;i++) {
    const t=2*Math.PI*i/segments
    const x=a*Math.cos(t),y=b*Math.sin(t)
    arc[i]=arc[i-1]+Math.hypot(x-prevX,y-prevY)
    prevX=x;prevY=y
  }
  const circumference=arc[segments]
  return (fraction:number) => {
    const target=(((fraction%1)+1)%1)*circumference
    let low=0,high=segments
    while(low+1<high) {
      const mid=(low+high)>>1
      if(arc[mid]<target)low=mid
      else high=mid
    }
    const delta=arc[high]-arc[low]
    const u=delta>.00001?(target-arc[low])/delta:0
    return (low+u)*2*Math.PI/segments
  }
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
    // Four (for 31-48 groups) gently offset elliptical orbital belts.
    // They cover the true usable aspect ratio without straight columns,
    // rectangular grids, or six-spoke hubs. They retain small depth parallax.
    const groups = [...groupNodes].sort((a,b) =>
      a.name.localeCompare(b.name,'zh-Hans') || a.id.localeCompare(b.id))
    const bands=browseOrbitBands(groups)
    const viewport=safeViewport()
    const width=Math.max(300, viewport.right-viewport.left)
    const height=Math.max(240, viewport.bottom-viewport.top)
    const aspect=THREE.MathUtils.clamp(width/height*.96,1.20,2.48)
    const outerB=Math.max(245,100+bands.length*55)
    const outerA=outerB*aspect
    const allPoints: THREE.Vector3[]=[]
    const ellipseTilt=-.055
    const sinTilt=Math.sin(ellipseTilt), cosTilt=Math.cos(ellipseTilt)
    // Estimated caption-sized clearance in world coordinates, scaled by
    // the viewport's physical width and height.
    const clearX=Math.max(95,2*outerA*162/Math.max(1,width*.88))
    const clearY=Math.max(52,2*outerB*78/Math.max(1,height*.85))
    bands.forEach((band,ring)=>{
      const fraction=bands.length===1?1:
        .40+.60*ring/(bands.length-1)
      const a=outerA*fraction
      const b=outerB*fraction
      const angleForArc=ellipseArcAngles(a,b)
      let winning: THREE.Vector3[]=[]
      let bestScore=Infinity
      // Rotate the rings independently until adjacent orbits have minimal
      // label/planet projections. Angles are optimized without taking any
      // node OFF its orbit, so the result remains elliptical.
      for(let phase=0;phase<32;phase++){
        const candidate=band.map((node,i)=>{
          const jitter=(ordinalHash(node.id+':orbit-angle')-.5)*.009
          const theta=angleForArc((i+(phase+.19*ring)/32)/band.length+jitter)
          const x=a*Math.cos(theta)
          const y=b*Math.sin(theta)
          return new THREE.Vector3(
            x*cosTilt-y*sinTilt,
            x*sinTilt+y*cosTilt,
            (ordinalHash(node.id+':orbit-depth')-.5)*8+ring*.6,
          )
        })
        let score=0
        for(const p of candidate)for(const q of allPoints){
          const dx=(p.x-q.x)/clearX
          const dy=(p.y-q.y)/clearY
          const distance=Math.hypot(dx,dy)
          const overlap=Math.max(0,1.14-distance)
          score+=overlap*overlap
        }
        if(score<bestScore){
          bestScore=score
          winning=candidate
        }
      }
      band.forEach((node,i)=>pos.set(node.id,winning[i]))
      allPoints.push(...winning)
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

  // A genuine orbital system: every chapter follows a differently sized
  // ellipse around the SAME course-center star. No per-planet local loops.
  const top = chapters.filter(c => !chapterIds.has(parent.get(c.id) ?? ''))
    .sort((a,b)=>a.name.localeCompare(b.name,'zh-Hans')||a.id.localeCompare(b.id))
  const nested = chapters.filter(c => chapterIds.has(parent.get(c.id) ?? ''))
  const viewport=safeViewport()
  const chapterCount=(id:string)=>{
    let total=0
    for(const node of nodes){
      if(node.kind!=='knowledge')continue
      let cursor=parent.get(node.id)
      const seen=new Set<string>()
      while(cursor&&!seen.has(cursor)){
        if(cursor===id){total++;break}
        seen.add(cursor)
        cursor=parent.get(cursor)
      }
    }
    return total
  }
  const orbitPlan=planCenteredCourseOrbits(
    top.map(ch=>({
      id:ch.id,
      knowledgeCount:chapterCount(ch.id),
    })),
    (viewport.right-viewport.left)/Math.max(1,viewport.bottom-viewport.top),
    viewport.right-viewport.left,viewport.bottom-viewport.top)
  const major=orbitPlan.orbits.at(-1)?.major ?? 400
  const minor=orbitPlan.orbits.at(-1)?.minor ?? 260
  for(const ring of orbitPlan.orbits){
    pos.set(ring.id,new THREE.Vector3(
      ring.major*Math.cos(ring.phase),
      0,
      ring.minor*Math.sin(ring.phase),
    ))
  }
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
      (ordinalHash(ch.id + 'x') - .5) * major,
      18, (ordinalHash(ch.id + 'z') - .5) * minor,
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
  for (const layer of outgoing) disposeGraph(layer.group)
  outgoing = []
  if (incoming) {
    for (const part of incoming.parts) {
      part.material.opacity = part.opacity
      part.material.transparent = part.transparent
      part.material.depthWrite = part.depthWrite
    }
    incoming = null
  }
}

function clearGraph(animate = false) {
  const keepVisible = !!group && animate && !reducedMotion()
  if (keepVisible) {
    // If a user clicks DURING an existing flight, the visible picture is a
    // blend of two layers. Keep BOTH at their exact on-screen alpha values.
    // Discarding either layer or forcing the newest one to opacity 1 was
    // causing a sudden jump on the first frame of the next flight.
    const layers = outgoing.map(layer => fadeGroup(layer.group, layer.anchors))
    if (group) layers.push(fadeGroup(group, [...labelAnchors]))
    const removed = layers.splice(0, Math.max(0, layers.length - 6))
    for (const layer of removed) disposeGraph(layer.group)
    outgoing = layers
  } else {
    for (const layer of outgoing) disposeGraph(layer.group)
    outgoing = []
    disposeGraph(group)
  }
  incoming = null
  meshes.splice(0)
  contextMeshes.splice(0)
  backdropCloud = null
  backdropPoints.splice(0)
  labelAnchors.splice(0)
  orbitTracks.splice(0)
  orbitMarkers.splice(0)
  orbitMarkerRadii.clear()
  stableOrbitCaptionOpacity.clear()
  stableOrbitCaptionTargets.clear()
  stableOrbitCaptionOffsets.clear()
  orbitingPlanets.splice(0)
  movingLines.splice(0)
  orbitVisuals.clear()
  lastOrbitFrame = 0
  chapterOrbitPixelScale = 1
  hoveredNodeId = null
  hoveredName.value = ''
  hoveredOrbitInfo.value = ''
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

// Keep the global atlas coordinates independent from the currently opened
// chapter/subgroup. Groups in the fullscreen explorer are synthetic browse
// markers; they orbit their actual chapter without replacing the real graph.
// Screen-space safe area is computed from real DOM overlays, not a fixed
// 365px sidebar guess. It also handles the navigation drawer and narrow
// screens; the 3D camera fits the active constellation inside this rectangle.
function overlayBoxes(): ScreenBox[] {
  const mount = mountPoint.value
  if (!mount) return []
  const host = mount.closest('.gx-body')
  if (!host) return []
  const canvasBounds = mount.getBoundingClientRect()
  const occluders = host.querySelectorAll<HTMLElement>(
    '.gx-detail, .gx-nav, .gx-stage-nav, .gx-view-switch, .gx-counter, .gx-zoom-controls')
  const boxes: ScreenBox[] = []
  for (const el of occluders) {
    const rect = el.getBoundingClientRect()
    if (rect.width < 2 || rect.height < 2) continue
    const left = Math.max(0, rect.left - canvasBounds.left - 9)
    const right = Math.min(canvasBounds.width, rect.right - canvasBounds.left + 9)
    const top = Math.max(0, rect.top - canvasBounds.top - 9)
    const bottom = Math.min(canvasBounds.height, rect.bottom - canvasBounds.top + 9)
    if (right > left && bottom > top) boxes.push({left, right, top, bottom})
  }
  return boxes
}
function overlaps(a: ScreenBox, b: ScreenBox, padding = 0) {
  return a.left < b.right + padding && a.right > b.left - padding &&
    a.top < b.bottom + padding && a.bottom > b.top - padding
}
function safeViewport() {
  const width = Math.max(1, mountPoint.value?.clientWidth ?? 1)
  const height = Math.max(1, mountPoint.value?.clientHeight ?? 1)
  const inset = Math.min(22, Math.max(10, width * .015))
  let safe: ScreenBox = {left:inset, right:width-inset, top:inset, bottom:height-inset}
  for (const block of overlayBoxes()) {
    // Long vertical drawers reserve horizontal space; floating stage
    // overlays reserve top/bottom space but not the whole canvas width.
    if (block.bottom - block.top > height * .48) {
      if (block.left < width * .5) safe.left = Math.max(safe.left, block.right + 12)
      else safe.right = Math.min(safe.right, block.left - 12)
    } else if (block.top < height * .40) {
      safe.top = Math.max(safe.top, block.bottom + 12)
    } else if (block.bottom > height * .66) {
      safe.bottom = Math.min(safe.bottom, block.top - 12)
    }
  }
  // Even with multiple open drawers, preserve a nonempty viewport for a
  // selected concept and its closest neighbors.
  if (safe.right - safe.left < 140) {
    safe.left = inset
    safe.right = Math.max(inset + 80, width - inset)
  }
  if (safe.bottom - safe.top < 160) {
    safe.top = inset
    safe.bottom = Math.max(inset + 80, height - inset)
  }
  return safe
}
function screenPosition(world: THREE.Vector3): {x:number;y:number;visible:boolean} {
  if (!camera || !renderer) return {x:0,y:0,visible:false}
  projectedPoint.copy(world).project(camera)
  const w = renderer.domElement.clientWidth
  const h = renderer.domElement.clientHeight
  return {
    x:(projectedPoint.x+1)*w/2,
    y:(1-projectedPoint.y)*h/2,
    visible: projectedPoint.z > -1 && projectedPoint.z < 1 &&
      Math.abs(projectedPoint.x) < 1.2 && Math.abs(projectedPoint.y) < 1.2,
  }
}

function resolveContextLayout() {
  const nodes = props.contextNodes
  const edges = props.contextEdges ?? props.layoutEdges ?? props.edges
  if (!nodes?.length) return new Map<string, THREE.Vector3>()
  if (nodes !== contextCacheNodes || edges !== contextCacheEdges) {
    const real = nodes.filter(n => n.kind !== 'group')
    contextCacheCoordinates = layout(real, edges)
    contextCacheParents = new Map(edges.filter(e => e.structural && e.type === 'contains')
      .map(e => [e.target, e.source]))
    const groups = nodes.filter(n => n.kind === 'group')
    if (groups.length) {
      const localGroups = layout(groups, [])
      const chapterCenter = contextCacheCoordinates.get(props.contextChapterId ?? '') ??
        new THREE.Vector3()
      for (const node of groups) {
        const p = localGroups.get(node.id)
        const saved = browseWorldPositions.get(node.id)
        if (saved) contextCacheCoordinates.set(node.id, saved.clone())
        else if (p) contextCacheCoordinates.set(node.id, chapterCenter.clone()
          .add(p.clone()))
      }
    }
    contextCacheNodes = nodes
    contextCacheEdges = edges
  }
  return contextCacheCoordinates
}

function belongsToContextChapter(node: GraphNode, chapterId: string | null | undefined) {
  if (!chapterId) return false
  let cursor: string | undefined = node.id
  const visited = new Set<string>()
  while (cursor && !visited.has(cursor)) {
    if (cursor === chapterId) return true
    visited.add(cursor)
    cursor = contextCacheParents.get(cursor)
  }
  return node.chapter_id === chapterId
}

// Orbital paths apply to the two OVERVIEW maps, never to a knowledge
// neighborhood. During a concept selection every planet keeps its position.
function initializeOrbitingPlanets(saved?: ReadonlyMap<string, SavedOrbit>) {
  orbitingPlanets.splice(0)
  if(!camera||props.selectedId||props.nodes.some(n=>n.kind==='knowledge'))
    return
  const isGroup=props.nodes.length>0&&props.nodes.every(n=>n.kind==='group')
  const orbitNodes=isGroup?props.nodes:
    props.nodes.filter(n=>n.kind==='chapter')
  const restored=restoreOrbitFrame(saved,orbitNodes.map(n=>n.id),positions)
  if(restored) {
    for(const pose of restored.values()){
      const position=positions.get(pose.id)!
      const objects=orbitVisuals.get(pose.id)
      if(!objects?.length)continue
      orbitingPlanets.push({
        id:pose.id,world:position,initialWorld:position.clone(),
        center:pose.center.clone(),axisX:pose.axisX.clone(),
        axisY:pose.axisY.clone(),major:pose.major,minor:pose.minor,
        phase:pose.phase,radiansPerSecond:pose.radiansPerSecond,
        elapsedSeconds:0,objects,
      })
    }
    return
  }
  if(isGroup){
    // Busy group views use a few coherent elliptical belts. Each belt
    // rotates as a whole so the 35 labels cannot catch and overtake one
    // another. There is no random wandering or sudden per-planet looping.
    const right=new THREE.Vector3(1,0,0)
      .applyQuaternion(camera.quaternion).normalize()
    const up=new THREE.Vector3(0,1,0)
      .applyQuaternion(camera.quaternion).normalize()
    browseOrbitBands(props.nodes).forEach(band=>{
      const present=band.filter(n=>positions.has(n.id)&&orbitVisuals.has(n.id))
      if(!present.length)return
      const center=new THREE.Vector3()
      present.forEach(node=>center.add(positions.get(node.id)!))
      center.multiplyScalar(1/present.length)
      const vectors=present.map(node=>{
        const p=positions.get(node.id)!.clone().sub(center)
        return {id:node.id,x:p.dot(right),y:p.dot(up)}
      })
      const major=Math.max(1,...vectors.map(v=>Math.abs(v.x)))
      const minor=Math.max(1,...vectors.map(v=>Math.abs(v.y)))
      const average=present.reduce((sum,n)=>sum+n.source_count,0)/present.length
      const seconds=orbitMetrics('group',average,present[0].id).periodSeconds
      for(const node of present){
        const position=positions.get(node.id)!
        const delta=position.clone().sub(center)
        const x=delta.dot(right),y=delta.dot(up)
        const phase=Math.atan2(y/minor,x/major)
        // Retain its exact initial location while turning around the
        // shared ellipse; no jump when orbit motion first starts.
        const residual=delta.clone()
          .addScaledVector(right,-major*Math.cos(phase))
          .addScaledVector(up,-minor*Math.sin(phase))
        orbitingPlanets.push({
          id:node.id,world:position,initialWorld:position.clone(),
          center:center.clone().add(residual),axisX:right.clone(),axisY:up.clone(),
          major,minor,phase,radiansPerSecond:2*Math.PI/seconds,
          elapsedSeconds:0,objects:orbitVisuals.get(node.id)!,
        })
      }
    })
    return
  }

  const chapterParents=new Map<string,string>()
  for(const edge of props.layoutEdges ?? props.edges)
    if(edge.structural&&edge.type==='contains')
      chapterParents.set(edge.target,edge.source)
  const chapterIds=new Set(props.nodes.filter(n=>n.kind==='chapter').map(n=>n.id))
  const top=props.nodes.filter(n=>n.kind==='chapter' &&
    !chapterIds.has(chapterParents.get(n.id)??''))
  if(!top.length)return
  const safe=safeViewport()
  const plan=planCenteredCourseOrbits(top.map(node=>({
    id:node.id,knowledgeCount:cachedChapterTotals.get(node.id)??0,
  })),(safe.right-safe.left)/Math.max(1,safe.bottom-safe.top),
  safe.right-safe.left,safe.bottom-safe.top)
  chapterOrbitPixelScale=plan.pixelScale
  for(const lane of plan.orbits){
    const pos=positions.get(lane.id)
    const objects=orbitVisuals.get(lane.id)
    if(!pos||!objects?.length)continue
    orbitingPlanets.push({
      id:lane.id,world:pos,initialWorld:pos.clone(),
      center:new THREE.Vector3(0,pos.y,0),
      axisX:new THREE.Vector3(1,0,0),
      axisY:new THREE.Vector3(0,0,1),
      major:lane.major,minor:lane.minor,
      // A return flight restores a snapshot at its LAST orbital phase.
      // Resume from that exact pose, not a deterministic initial timestamp.
      phase:Math.atan2(pos.z/lane.minor,pos.x/lane.major),
      radiansPerSecond:2*Math.PI/lane.periodSeconds,
      elapsedSeconds:0,objects,
    })
  }
}

// This is an actual position update: moving planets remain pickable, labels
// track the same shared Vector3, and course-contains edges remain connected.
// Motion stops on hover and respects reduced-motion preference.
function animateOrbits(now:number) {
  if (!orbitingPlanets.length) return
  if (!lastOrbitFrame) lastOrbitFrame=now
  const dt=Math.min(.05,Math.max(0,(now-lastOrbitFrame)/1000))
  lastOrbitFrame=now
  if (reducedMotion() || props.selectedId || outgoing.length || tween)
    return
  for(const planet of orbitingPlanets){
    // Keep the hovered planet stationary for reliable clicking, but let
    // EVERY OTHER planet continue its orbit. Do not catch up in one jump.
    if (hoveredNodeId === planet.id) continue
    planet.elapsedSeconds+=dt
    const angle=planet.phase+planet.radiansPerSecond*planet.elapsedSeconds
    const destination=planet.center.clone()
      .addScaledVector(planet.axisX,planet.major*Math.cos(angle))
      .addScaledVector(planet.axisY,planet.minor*Math.sin(angle))
    // Object transforms are relative to the INITIAL orbit coordinates,
    // not the previous frame; otherwise only the tiny per-frame delta is
    // applied and a moving marker never follows its actual orbit.
    const shift=destination.clone().sub(planet.initialWorld)
    planet.world.copy(destination)
    for(const item of planet.objects)
      item.object.position.copy(item.start).add(shift)
    // Captions are re-positioned by placeLabel() every frame. Update their
    // anchor as well, otherwise the name stays at the old orbital coordinate.
    for(const anchor of labelAnchors)
      if(anchor.nodeId===planet.id)anchor.center.copy(destination)
    if(planet.id.startsWith('view-group:')){
      browseWorldPositions.set(planet.id,destination.clone())
      contextCacheCoordinates.set(planet.id,destination.clone())
    }
  }
  for(const lines of movingLines){
    const attr=lines.geometry.getAttribute('position') as THREE.BufferAttribute
    if(!attr)continue
    lines.edges.forEach((edge,i)=>{
      const a=positions.get(edge.source),b=positions.get(edge.target)
      if(!a||!b)return
      attr.setXYZ(i*2,a.x,a.y,a.z)
      attr.setXYZ(i*2+1,b.x,b.y,b.z)
    })
    attr.needsUpdate=true
    lines.geometry.computeBoundingSphere()
  }
}

function rebuild(animate = false, transition?: SceneTransition) {
  if (!scene) return
  // Clicks on dimmed chapters / concepts are valid navigation too. Their
  // original atlas coordinates must be usable as the next camera anchor.
  const previous = new Map([...resolveContextLayout()]
    .map(([id, p]) => [id, p.clone()] as [string, THREE.Vector3]))
  for (const [id, p] of positions) previous.set(id, p.clone())
  const previousOrbitFrame = snapshotOrbitFrame(orbitingPlanets)
  clearGraph(animate)
  const g = new THREE.Group()
  group = g
  scene.add(g)
  let coords = new Map([...resolveLayout()].map(([id, p]) => [id, p.clone()]))

  if (transition?.restore) {
    // Restore the actual previous coordinates, including the same group
    // atlas address used by the dimmed context layer. Never leave the
    // background cached at another chapter's/phase's position.
    for (const [id, position] of transition.restore) {
      coords.set(id, position.clone())
      if (id.startsWith('view-group:')) {
        browseWorldPositions.set(id, position.clone())
        contextCacheCoordinates.set(id, position.clone())
      }
    }
  } else {
    const anchor = transition?.anchorId ? previous.get(transition.anchorId) : undefined
    const groupNodes = props.nodes.filter(n => n.kind === 'group')
    const groupOverview = groupNodes.length > 0 &&
      props.nodes.every(n => n.kind === 'group')
    if (groupOverview) {
      const previouslyPlaced = groupNodes.every(n => browseWorldPositions.has(n.id))
      if (transition?.mode === 'enter' && anchor && camera && controls) {
        // Google-Maps-style drill-down: the group tiles have fixed world
        // addresses INSIDE the clicked chapter, not in a second district a
        // long horizontal pan away. The camera pans toward the clicked anchor
        // AND zooms in on this local atlas in the same continuous flight.
        // These are persistent positions, not mesh expansion keyframes.
        const originals = groupNodes.map(n => coords.get(n.id)).filter(
          (p): p is THREE.Vector3 => Boolean(p))
        const origin = new THREE.Box3().setFromPoints(originals)
          .getCenter(new THREE.Vector3())
        const look = camera.position.clone().sub(controls.target).normalize()
        const cameraRight = new THREE.Vector3(1, 0, 0)
          .applyQuaternion(camera.quaternion).normalize()
        const cameraUp = new THREE.Vector3(0, 1, 0)
          .applyQuaternion(camera.quaternion).normalize()
        const cameraForward = new THREE.Vector3(0, 0, -1)
          .applyQuaternion(camera.quaternion).normalize()
        const unscaled = originals.map(p => anchor.clone()
          .addScaledVector(cameraRight, p.x - origin.x)
          .addScaledVector(cameraUp, p.y - origin.y)
          .addScaledVector(cameraForward, (p.z - origin.z) * .12))
        const safe = safeViewport()
        const w = Math.max(1, mountPoint.value?.clientWidth ?? 1)
        const h = Math.max(1, mountPoint.value?.clientHeight ?? 1)
        const baseDistance = projectedFitDistance(
          unscaled, anchor, look,
          THREE.MathUtils.clamp((safe.right - safe.left) / w * .85, .25, .85),
          THREE.MathUtils.clamp((safe.bottom - safe.top) / h * .78, .25, .82),
          0, 0, 1,
        )
        const currentDistance = camera.position.distanceTo(controls.target)
        // A new zoom level has a smaller world footprint. Compute the map
        // coordinate scale once, from the zoom that fits this viewport; it
        // never changes during animation. This is *not* an expanding layer.
        const targetDistance = Math.max(85, currentDistance * .48)
        const scale = THREE.MathUtils.clamp(
          targetDistance / Math.max(1, baseDistance) * .93, .075, 1.2)
        for (const node of groupNodes) {
          const local = coords.get(node.id)
          if (!local) continue
          const world = anchor.clone()
            .addScaledVector(cameraRight, (local.x - origin.x) * scale)
            .addScaledVector(cameraUp, (local.y - origin.y) * scale)
            .addScaledVector(cameraForward, (local.z - origin.z) * scale * .12)
          coords.set(node.id, world)
          browseWorldPositions.set(node.id, world.clone())
          contextCacheCoordinates.set(node.id, world.clone())
        }
      } else if (previouslyPlaced) {
        // Non-navigation updates must not reposition the group atlas.
        for (const node of groupNodes)
          coords.set(node.id, browseWorldPositions.get(node.id)!.clone())
      }
    } else if (anchor && groupNodes.length) {
      // Once a browse group is clicked, its actual concepts occupy the
      // existing planet's neighborhood (a separate camera flight).
      const parent = coords.get(transition!.anchorId!) ??
        (groupNodes.length === 1 ? coords.get(groupNodes[0].id) : undefined)
      const origin = parent?.clone() ?? new THREE.Box3().setFromPoints(
        groupNodes.map(n => coords.get(n.id)!).filter(Boolean),
      ).getCenter(new THREE.Vector3())
      const scale = .29
      for (const [id, position] of coords) {
        if (!props.nodes.some(n => n.id === id)) continue
        coords.set(id, position.sub(origin).multiplyScalar(scale).add(anchor))
      }
    }

    // In the regular course atlas, chapter concepts already have an address
    // in the chapter's coordinate system. Display them close to their
    // chapter when zooming in instead of scattering them across the atlas.
    const chapter = props.nodes.find(n => n.kind === 'chapter' && n.id === props.selectedId)
    const chapterAnchor = chapter && (previous.get(chapter.id) ?? coords.get(chapter.id))
    if (chapter && chapterAnchor && props.nodes.some(n => n.kind === 'knowledge')) {
      const center = coords.get(chapter.id) ?? chapterAnchor
      for (const point of props.nodes.filter(n => n.kind === 'knowledge')) {
        const position = coords.get(point.id)
        if (position) coords.set(point.id, chapterAnchor.clone()
          .add(position.sub(center).multiplyScalar(.32)))
      }
      coords.set(chapter.id, chapterAnchor.clone())
    }

    // Related concepts inherit the selected planet's existing map address.
    // Layout stays in the XZ atlas plane so the camera doesn't flip 90
    // degrees when zooming from chapter to a one-hop relation cluster.
    const selected = props.nodes.find(n => n.kind === 'knowledge' && n.id === props.selectedId)
    const local = props.nodes.every(n => n.kind === 'knowledge') && !!selected &&
      props.nodes.length <= 26
    if (local && selected && !props.preservePositionsOnSelect) {
      const center = previous.get(selected.id) ?? coords.get(selected.id) ??
        new THREE.Vector3()
      coords.set(selected.id, center.clone())
      const neighbors = props.nodes.filter(n => n.kind === 'knowledge' && n.id !== selected.id)
        .sort((a, b) => a.id.localeCompare(b.id))
      const inner = neighbors.length <= 8 ? neighbors.length :
        Math.min(7, Math.ceil(neighbors.length * .42))
      neighbors.forEach((node, index) => {
        const outer = index >= inner
        const i = outer ? index - inner : index
        const count = outer ? neighbors.length - inner : inner
        const theta = -Math.PI / 2 + (i + (outer ? .45 : 0)) *
          2 * Math.PI / Math.max(1, count)
        const radius = outer ? Math.max(92, count * 12) : Math.max(46, count * 7.3)
        coords.set(node.id, center.clone().add(new THREE.Vector3(
          Math.cos(theta) * radius * .36,
          (ordinalHash(node.id + ':focus-depth') - .5) * 3,
          Math.sin(theta) * radius * .36,
        )))
      })
    }
  }
  // Reflow the REAL verified one-hop neighborhood in the camera-facing
  // plane. The previous layout could put three related concepts on nearly
  // one screen-space line (or even one over another in depth). Keep the
  // selected planet at its original map position, arrange ONLY its actual
  // semantic neighbors around it, and let the camera fit that constellation.
  const focusState = focusRelations()
  const selectedId = props.selectedId
  const focusCenter = selectedId ? coords.get(selectedId) : undefined
  if (focusCenter && focusState.neighbors.size &&
      !props.preservePositionsOnSelect) {
    const neighbors = [...focusState.neighbors].filter(id => coords.has(id)).sort()
    const count = neighbors.length
    const screenRight = new THREE.Vector3(1,0,0).applyQuaternion(camera?.quaternion ??
      new THREE.Quaternion())
    const screenUp = new THREE.Vector3(0,1,0).applyQuaternion(camera?.quaternion ??
      new THREE.Quaternion())
    const innerCount = count <= 8 ? count : Math.min(8, Math.ceil(count*.46))
    // Deterministic polar sampling is not a rectangular grid. Two links
    // deliberately occupy different vertical bands, not a horizontal row.
    neighbors.forEach((id, i) => {
      const outer = i >= innerCount
      const ordinal = outer ? i - innerCount : i
      const ringCount = outer ? count - innerCount : innerCount
      const angle = count === 1 ? -Math.PI/4
        : count === 2 ? (i === 0 ? -Math.PI/4 : 3*Math.PI/4)
        : -Math.PI/3 + 2*Math.PI*(ordinal + (outer ? .35 : 0)) /
          Math.max(1,ringCount)
      const radius = outer ? Math.max(92,ringCount*15) :
        Math.max(64,Math.min(94,innerCount*13))
      coords.set(id,focusCenter.clone()
        .addScaledVector(screenRight,Math.cos(angle)*radius)
        .addScaledVector(screenUp,Math.sin(angle)*radius))
    })
  }
  if (props.preservePositionsOnSelect && !transition) {
    // Previous *rendered* coordinates are the source of truth, not a fresh
    // orbit/relationship layout from the newly selected node.
    for (const node of props.nodes) {
      const old = previous.get(node.id)
      if (old) coords.set(node.id, old.clone())
    }
  }
  for (const node of props.nodes) {
    const position = coords.get(node.id)
    if (position) positions.set(node.id, position)
  }
  // Course / sibling chapter anchors may be present for structural context
  // in props.nodes, but must be shaded rather than treated as selected.
  const currentChapter = props.contextChapterId
  const brightNodes = props.nodes.filter(n => {
    if (focusState.active)
      return props.preservePositionsOnSelect || n.id === selectedId ||
        focusState.neighbors.has(n.id)
    return !currentChapter || (n.kind !== 'course' && n.kind !== 'chapter') ||
      n.id === currentChapter || n.id === selectedId
  })
  const activeIds = new Set(brightNodes.map(n => n.id))
  // Render the rest of the map below the active layer, keeping its original
  // location. Small spheres are instanced: thousands of dim points remain
  // cheap to draw. Only active nodes get labels, rings and bright halos.
  contextMeshes.splice(0)
  backdropCloud = null
  backdropPoints.splice(0)
  lastBackdropLayout = 0
  const background = (props.contextNodes ?? []).filter(n =>
    !activeIds.has(n.id))
  const backdrop = resolveContextLayout()
  const backKnowledge = background.filter(n => n.kind === 'knowledge' && backdrop.has(n.id))
  if (backKnowledge.length) {
    const material = new THREE.MeshBasicMaterial({
      color: 0xffffff, transparent: true, opacity: .78, depthWrite: false,
    })
    const sphere = new THREE.SphereGeometry(1, 10, 8)
    const cloud = new THREE.InstancedMesh(sphere, material, backKnowledge.length)
    backdropCloud = cloud
    const matrix = new THREE.Matrix4()
    const scale = new THREE.Vector3()
    const rotation = new THREE.Quaternion()
    const ids: string[] = []
    backKnowledge.forEach((node, index) => {
      const onChapter = belongsToContextChapter(node, props.contextChapterId)
      const brightness = onChapter ? .18 : .085
      const size = onChapter ? .55 : .35
      scale.setScalar(size)
      matrix.compose(backdrop.get(node.id)!, rotation, scale)
      cloud.setMatrixAt(index, matrix)
      const tint = new THREE.Color(statusColor(node))
      backdropPoints.push({position: backdrop.get(node.id)!.clone(), color:tint, local:onChapter})
      cloud.setColorAt(index, tint.clone().multiplyScalar(brightness))
      ids.push(node.id)
    })
    cloud.instanceMatrix.needsUpdate = true
    if (cloud.instanceColor) cloud.instanceColor.needsUpdate = true
    cloud.userData.instanceNodeIds = ids
    cloud.computeBoundingSphere()
    g.add(cloud)
    contextMeshes.push(cloud)
  }
  for (const node of background.filter(n => n.kind !== 'knowledge')) {
    const p = backdrop.get(node.id)
    if (!p) continue
    const inChapter = belongsToContextChapter(node, props.contextChapterId)
    const base = node.kind === 'course' ? 0x62ecdf :
      node.kind === 'group' ? 0x88aac5 : 0x80bce7
    const tint = new THREE.Color(base).multiplyScalar(inChapter ? .23 : .12)
    const radius = node.kind === 'chapter' ? 3.5 : node.kind === 'course' ? 3 : 2.8
    const planet = new THREE.Mesh(new THREE.SphereGeometry(radius, 16, 12),
      new THREE.MeshBasicMaterial({
        color: tint, transparent: true, opacity: inChapter ? .42 : .24,
        depthWrite: false,
      }))
    planet.position.copy(p)
    planet.userData.nodeId = node.id
    g.add(planet)
    contextMeshes.push(planet)
  }
  // One InstancedMesh for all knowledge points, including dimmed ones:
  // preserve a single draw call for 1300+ nodes instead of per-node meshes.
  const sphere = new THREE.SphereGeometry(1, 12, 10)
  const planetSphere = new THREE.SphereGeometry(1, 40, 28)
  const palette = chapterColors(props.layoutNodes ?? props.nodes)
  const overview = !props.nodes.some(n => n.kind === 'knowledge')
  const knowledge = brightNodes.filter(n => n.kind === 'knowledge' && positions.has(n.id))
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
      if (center) color.lerp(new THREE.Color(0xe7ffff), .96)
      else if (neighbor) color.lerp(new THREE.Color(0x4cf8eb), .87)
      else if (focusState.active) color.multiplyScalar(0.16)
      cluster.setColorAt(index, color)
      ids.push(node.id)

      if ((smallGroup && (!focusState.active || props.preservePositionsOnSelect)) ||
          (!focusState.active && !smallGroup && index < 32) ||
          center || (neighbor && neighborLabels < 12)) {
        if (neighbor) neighborLabels += 1
        const subdued = props.preservePositionsOnSelect &&
          focusState.active && !center && !neighbor
        const label = focusState.active && !subdued
          ? makeFocusLabel(node.name, center)
          : smallGroup ? makeLeafLabel(node.name) : makeLabel(node.name)
        if (label) {
          if (focusState.active && props.preservePositionsOnSelect &&
              !center && !neighbor) {
            label.material.opacity = .23
            label.material.transparent = true
          }
          label.position.copy(position)
          label.userData.nodeId = node.id
          const width = subdued ? 128 : focusState.active
            ? (center ? 208 : knowledge.length > 9 ? 148 : 178)
            : 154
          labelAnchors.push({
            sprite: label, center: position, nodeId: node.id,
            distance: radius + (smallGroup ? 6.5 : 4.5),
            focused: focusState.active,
            priority: center ? 10000 : neighbor ? 2000 : subdued ? 30 : 1000,
            declutter: !focusState.active && !smallGroup,
            screen: subdued
              ? {width, height: width*150/520, radius, gap: 11}
              : focusState.active
                ? {width, height: width * 180 / 640, radius, gap: 12}
              : smallGroup
                ? {width: 218, height: 218 * 150 / 520, radius, gap: 12}
                : {width: 154, height: 39, radius, gap: 11},
          })
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
  for (const node of brightNodes.filter(n => n.kind !== 'knowledge')) {
    const orbitDecorations: THREE.Object3D[] = []
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
          ? 7.3 + Math.min(17, 2.65 * Math.sqrt(Math.max(1,count)/10))
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
    orbitDecorations.push(planet)

    // Restrained luminous atmosphere: one soft glow and an orbital ring
    // instead of hundreds of fake chapter dots.
    const glowMap = glowTexture()
    const aura = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowMap, color: baseColor,
      transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending,
      opacity: focusState.active ? .10 :
        node.kind === 'group' && overview ? .17 :
        (isCourse ? .65 : .42),
    }))
    aura.position.copy(position)
    aura.scale.setScalar(radius * (isCourse ? 5.6 : 5.1))
    g.add(aura)
    orbitDecorations.push(aura)
    if (overview && (node.kind === 'group' || node.kind === 'chapter')) {
      orbitMarkers.push({planet, aura, id:node.id, count, kind:node.kind})
    }

    if (!focusState.active && !(overview && node.kind === 'group')) {
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
      orbitDecorations.push(ring)
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
        const dust=new THREE.Points(dustGeom, new THREE.PointsMaterial({
          color: baseColor, size: .65, transparent: true,
          opacity: .6, depthWrite: false,
        }))
        g.add(dust)
        orbitDecorations.push(dust)
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
          label.scale.set(isCourse ? 34 : node.kind==='group' ? 37 : 39,
            isCourse ? 9.8 : node.kind==='group' ? 10.7 : 11.3, 1)
        } else {
          label.scale.set(isCourse ? 22 : 24, 5.3, 1)
        }
        label.position.copy(position)
        label.userData.nodeId = node.id
        const canvasWidth = mountPoint.value?.clientWidth ?? 1500
        const widthPx = node.kind === 'group'
          ? Math.min(166, Math.max(125, canvasWidth / 10))
          : isCourse
            ? Math.min(152, Math.max(114, canvasWidth / 11))
            : Math.min(172, Math.max(128, canvasWidth / 9.5))
        labelAnchors.push({
          sprite: label, center: position,
          distance: radius + (overview ? 10.5 : 4),
          nodeId: node.id,
          priority: node.kind === 'group'
            ? count + (groupOrder >= 0 && groupOrder < 6 ? 7 : 0) : 100,
          declutter: overview && (
            node.kind === 'group' && props.nodes.filter(n=>n.kind==='group').length > 7 ||
            node.kind === 'chapter' && props.nodes.filter(n=>n.kind==='chapter').length > 5
          ),
          screen: overview ? {
            width: widthPx, height: widthPx * (208 / 720),
            radius, gap: node.kind === 'group' ? 7 : 9,
          } : undefined,
        })
        g.add(label)
      }
    }
    orbitVisuals.set(node.id,orbitDecorations.map(object=>({
      object,start:object.position.clone(),
    })))
  }
  // Restore the saved 3D orbital basis before constructing orbit lines.
  initializeOrbitingPlanets(transition?.restoreOrbits ?? previousOrbitFrame)
  // Draw ONE visible ellipse per orbit. Course lanes share the same
  // course-center focus; browsing groups share a few coherent belts.
  const isGroupTracks=props.nodes.length>0 &&
    props.nodes.every(n=>n.kind==='group')
  const trackMap=new Map<string,{
    body: OrbitingPlanet; ids:Set<string>
  }>()
  orbitingPlanets.forEach((body,index)=>{
    if(body.major<.001)return
    const key=isGroupTracks
      ? `${body.major.toFixed(4)}:${body.minor.toFixed(4)}`
      : body.id
    const existing=trackMap.get(key)
    if(existing){existing.ids.add(body.id);return}
    trackMap.set(key,{body,ids:new Set([body.id])})
  })
  let trackIndex=0
  for(const {body,ids} of trackMap.values()){
    const points=Array.from({length:160},(_,i)=>{
      const angle=i*Math.PI*2/160
      return body.center.clone()
        .addScaledVector(body.axisX,body.major*Math.cos(angle))
        .addScaledVector(body.axisY,body.minor*Math.sin(angle))
    })
    const track=new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({
        color:[0x6fc6c8,0x729fbf,0x7ea6bf,0x64b6c7][trackIndex++%4],
        transparent:true,opacity:.20,depthWrite:false,depthTest:false,
      }),
    )
    g.add(track)
    orbitTracks.push({line:track,ids,baseOpacity:.20})
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
    if (!points.length) return null
    const geom = new THREE.BufferGeometry()
    geom.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
    const lines = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({
      color, transparent: true, opacity, depthWrite: false,
    }))
    g.add(lines)
    return lines
  }
  if ((!focusState.active || props.preservePositionsOnSelect) &&
      (props.showStructure !== false || props.nodes.some(n=>n.kind==='group'))) {
    const structuralLine = addLines(structural, 0x7b9aad, focusState.active ? .09 : .3)
    if (structuralLine && orbitingPlanets.length) {
      movingLines.push({
        geometry:structuralLine.geometry,
        edges:props.edges.filter(e=>e.structural &&
          positions.has(e.source)&&positions.has(e.target))
          .map(e=>({source:e.source,target:e.target})),
      })
    }
  }
  addLines(ordinary, 0xffd184, focusState.active ? 0.085 : 0.79)
  for (const [type, points] of highlightedByType) {
    addLines(points, RELATION_COLORS[type] ?? 0xffd184, 1)
  }
  if (outgoing.length) {
    // Old levels hold their exact positions and opacities from the last
    // rendered frame. New detail is initially fully transparent.
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
  minimum = 72,
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
  let result = minimum
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
  const courseAtlasOverview = !id && overview && !localGroup &&
    props.nodes.some(n => n.kind === 'course') &&
    orbitingPlanets.some(planet => planet.major > 0)

  // Group detail: fit only the actual neighborhood and its group anchor,
  // not a far-away chapter planet. This is what keeps 3-point views legible.
  let fitPoints: THREE.Vector3[]
  if (localGroup) {
    // The labels in a group overview sit above the planets. Include that
    // screen-space extent when computing the framing center and zoom.
    // Group captions are constant pixel size. Do not add 30 WORLD units
    // above each point: that fake height inflated the camera distance and
    // cancelled the intended zoom into the clicked chapter.
    fitPoints = groupOverview ? groupPoints : [...visibleKnowledge, ...groupPoints]
  } else if (isSubset) {
    fitPoints = visibleKnowledge
  } else {
    fitPoints = [...positions.values()]
  }
  if (!fitPoints.length) fitPoints = [...positions.values()]
  if (!id && orbitingPlanets.length) {
    // Fit the FULL trajectories, not only the planets' current positions.
    // Otherwise half an orbit later the far side leaves the viewport.
    const extrema = orbitingPlanets.flatMap(body => [
      body.center.clone().addScaledVector(body.axisX, body.major),
      body.center.clone().addScaledVector(body.axisX, -body.major),
      body.center.clone().addScaledVector(body.axisY, body.minor),
      body.center.clone().addScaledVector(body.axisY, -body.minor),
    ])
    fitPoints = [...fitPoints, ...extrema]
  }
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
  const chapterFocus = !!coordinate && selectedNode?.kind === 'chapter' && visibleKnowledge.length > 0
  const focusConstellation = !!coordinate && selectedNode?.kind === 'knowledge' &&
    props.nodes.every(n => n.kind === 'knowledge') && props.nodes.length <= 26
  const focusedPoints = relationFocus ? [coordinate, ...relatedPoints]
    : chapterFocus ? [coordinate, ...visibleKnowledge] : fitPoints
  const bounds = new THREE.Box3().setFromPoints(focusedPoints)
  const overviewCenter = bounds.getCenter(new THREE.Vector3())
  // For a real relationship network use its center; for a single point,
  // retain the existing click-to-center behavior.
  desiredTarget.copy(relationFocus || chapterFocus ? overviewCenter : (coordinate ?? overviewCenter))

  const currentDistance = camera.position.distanceTo(controls.target)
  const direction = camera.position.clone().sub(controls.target)
  if (direction.lengthSq() < .01) direction.set(.4, .8, 1)
  if (focusConstellation) {
    // Continue the overhead map trajectory instead of flipping the camera.
    direction.set(.03, 1, .26)
  } else if (chapterFocus) {
    direction.set(0, 1, .30)
  } else if (!id && (resetAtlasAngle || overview)) {
    if (localGroup) {
      if (!groupOverview) {
        // A leaf can have its own small semantic detail plane. On entry to
        // the chapter's GROUP ATLAS, however, keep the current orientation:
        // that is a lateral map pan, never a 90-degree camera turn.
        direction.set(0, .16, 1)
      }
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
        localGroup ? .70 : focusConstellation ? .80 : .60,
        focusConstellation ? .78 : .69,
        focusConstellation ? 26 : 11,
        focusConstellation ? 29 : 16,
      ),
    )
    prepareFlightRange(distance)
  } else if (chapterFocus) {
    distance = Math.max(98, projectedFitDistance(
      focusedPoints, overviewCenter, direction, .77, .76, 20, 23,
    ))
    prepareFlightRange(distance)
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
      // Fit the fixed chapter-local tile footprint into the usable view.
      // The *camera* does the enlarging; nodes do not move during the flight.
      distance = projectedFitDistance(
        fitPoints, overviewCenter, direction,
        .80, .79, 0, 0, 36,
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
    prepareFlightRange(distance)
  } else {
    const selected = props.nodes.find(n => n.id === id)
    if (localGroup && visibleKnowledge.length <= 18)
      distance = selected?.kind === 'knowledge' ? 92 : 112
    else distance = selectionDistance(selected?.kind ?? 'knowledge', currentDistance)
  }
  // Adapt BOTH frame scale and frame center to the actual unobstructed
  // rectangle. This prevents an approved relation from landing under the
  // details drawer while preserving a continuous map-style camera flight.
  const safe = safeViewport()
  const width = Math.max(1, mountPoint.value?.clientWidth ?? 1)
  const height = Math.max(1, mountPoint.value?.clientHeight ?? 1)
  const safeWidth = Math.max(1, safe.right - safe.left)
  const safeHeight = Math.max(1, safe.bottom - safe.top)
  const screenRight = new THREE.Vector3().crossVectors(
    new THREE.Vector3(0,1,0), direction)
  if (screenRight.lengthSq() < 1e-6) screenRight.set(1,0,0)
  screenRight.normalize()
  const viewDirection = direction.clone().normalize()
  const screenTop = new THREE.Vector3()
    .crossVectors(viewDirection,screenRight).normalize()
  const frameCenter = (relationFocus || chapterFocus) ? overviewCenter :
    (coordinate ?? overviewCenter)
  const framePoints = relationFocus || chapterFocus ? focusedPoints :
    coordinate ? [coordinate] : fitPoints
  if (courseAtlasOverview) {
    // The previous generic pass reserved a full-width band for the
    // top-left title and added world-space padding TWICE. That made the
    // seven chapter orbits occupy only ~60% of the available screen.
    // Fit the actual pixels of the COMPLETE future orbital paths once.
    const orbitalSamples = orbitingPlanets.flatMap(body =>
      Array.from({length:32},(_,i)=>{
        const angle=i*Math.PI*2/32
        return body.center.clone()
          .addScaledVector(body.axisX,body.major*Math.cos(angle))
          .addScaledVector(body.axisY,body.minor*Math.sin(angle))
      }),
    )
    distance = tightAtlasDistance({
      points:[...framePoints,...orbitalSamples],
      center:overviewCenter,
      eyeDirection:viewDirection,
      viewportWidth:width,viewportHeight:height,
      fovDegrees:camera.fov,aspect:camera.aspect,
    },distance)
    // Course overview doesn't have a detail drawer. Keep its ellipse
    // centered instead of shifting it away from a title occupying only
    // the upper-left corner.
    desiredTarget.copy(overviewCenter)
  } else {
    distance = Math.max(distance, projectedFitDistance(
      framePoints, frameCenter, viewDirection,
      THREE.MathUtils.clamp(safeWidth/width*.90,.19,.94),
      THREE.MathUtils.clamp(safeHeight/height*.89,.19,.94),
      relationFocus ? 22 : groupOverview ? 0 : 12,
      relationFocus ? 30 : groupOverview ? 0 : 18,
      groupOverview ? 36 : 72,
    ))
    const unitsPerPixel = 2 *
      Math.tan(THREE.MathUtils.degToRad(camera.fov)/2) * distance/height
    // Other views DO need a safe-area shift to avoid the detail drawer.
    desiredTarget.addScaledVector(screenRight,
      (width/2-(safe.left+safe.right)/2)*unitsPerPixel)
    desiredTarget.addScaledVector(screenTop,
      ((safe.top+safe.bottom)/2-height/2)*unitsPerPixel)
  }
  // A visible fog pop before the camera starts moving looks like a jump
  // even if the camera transform itself is continuous.
  prepareFlightRange(distance)
  prepareFlightFog(distance)
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
function restoreCamera(snapshot: CameraSnapshot) {
  if (!camera || !controls) return
  desiredTarget.copy(snapshot.target)
  desiredPosition.copy(snapshot.position)
  const distance = snapshot.position.distanceTo(snapshot.target)
  prepareFlightRange(distance)
  prepareFlightFog(distance)
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

// Pixel-based marker sizing also takes real projected neighbor clearance
// into account. At high zoom the camera sees more detail but planet meshes
// must not grow until they mask the other orbit stops or their captions.
// Both chapter and browse-group planets use count-scaled SCREEN pixel
// radii. Formerly chapter size depended on depth and group sizes were capped
// at 18px; neither reflected the actual knowledge-point count reliably.
function updateOrbitMarkerSizes() {
  if(!camera||!renderer||!orbitMarkers.length)return
  const height=Math.max(1,renderer.domElement.clientHeight)
  const tanHalf=Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)
  const forward=new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion)
  orbitMarkers.forEach(({planet,aura,id,count,kind})=>{
    // Each body has an individual clearance-planned orbit. Collision
    // avoidance belongs to the LANE PLANNER, never in this size function:
    // a 303-point chapter must not suddenly shrink when a neighbor passes.
    // All chapters share ONE viewport fit factor. Their sizes remain
    // proportional and never change just because planets pass nearby.
    const radiusPx=orbitMetrics(kind,count,id).idealRadiusPx *
      (kind==='chapter'&&orbitingPlanets.some(p=>p.id===id)
        ? chapterOrbitPixelScale : 1)
    const position=planet.getWorldPosition(new THREE.Vector3())
    const depth=Math.max(1,position.sub(camera!.position).dot(forward))
    const worldRadius=radiusPx*2*tanHalf*depth/height
    planet.scale.setScalar(worldRadius)
    aura.scale.setScalar(worldRadius*(kind==='chapter'?2.65:2.85))
    orbitMarkerRadii.set(id,worldRadius)
  })
}

function placeLabel(anchor: LabelAnchor) {
  if (!camera || !mountPoint.value) return
  if (!anchor.screen) {
    anchor.sprite.position.copy(anchor.center).addScaledVector(screenUp, anchor.distance)
    return
  }
  // Screen-size labels: world-space sprite scales based on their own depth.
  // This avoids illegible 8px chapter names whenever the 3D camera zooms out.
  const worldCenter = anchor.sprite.parent
    ? anchor.sprite.parent.localToWorld(anchor.center.clone())
    : anchor.center
  const cameraForward = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion)
  const depth = Math.max(1, worldCenter.clone().sub(camera.position).dot(cameraForward))
  const worldPerPx = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)
    * depth / Math.max(1, mountPoint.value.clientHeight)
  // Scene transition groups can be scaled: compensate so the caption stays
  // legible during zooming and crossfades too.
  const parentScale = Math.max(.05, anchor.sprite.parent?.scale.x ?? 1)
  const localPerPx = worldPerPx / parentScale
  const { width, height, radius, gap } = anchor.screen
  anchor.sprite.scale.set(width * localPerPx, height * localPerPx, 1)
  anchor.sprite.position.copy(anchor.center).addScaledVector(
    screenUp, radius + (height / 2 + gap) * localPerPx,
  )
}

// Orbit captions are little map labels, not separate satellites. Keep the
// base offset from placeLabel() FIXED above each moving planet; never feed
// them into the regular solver that jumps between 15 unrelated positions on
// every frame. When two moving captions cross, gently fade the lower-priority
// one instead of teleporting it to the other side of the screen.
function arrangeStableOrbitCaptions() {
  if (!camera || !renderer || !orbitingPlanets.length) return
  if (outgoing.length || incoming) return
  const w=renderer.domElement.clientWidth
  const h=renderer.domElement.clientHeight
  // A hard full-width top inset wastes the free space in the screenshot.
  // Reserve only the REAL DOM overlays and the 12px canvas border.
  const bounds={left:12,top:12,right:w-12,bottom:h-18}
  const overlays=overlayBoxes()
  const nodesById=new Map(props.nodes.map(node=>[node.id,node]))
  const markerById=new Map(orbitMarkers.map(marker=>[marker.id,marker]))
  const cameraForward=new THREE.Vector3(0,0,-1)
    .applyQuaternion(camera.quaternion)
  const tanHalf=Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)
  const candidates=labelAnchors.flatMap(anchor=>{
    if (!anchor.nodeId || !anchor.screen) return []
    const node=nodesById.get(anchor.nodeId)
    if (!node) return []
    const center=screenPosition(anchor.center)
    if (!center.visible) return []
    const marker=markerById.get(anchor.nodeId)
    const depth=Math.max(1,anchor.center.clone()
      .sub(camera!.position).dot(cameraForward))
    const pxPerWorld=h/(2*tanHalf*depth)
    const radius=marker
      ? (orbitMarkerRadii.get(marker.id)??3)*pxPerWorld
      : 8
    const count=node.kind==='chapter'
      ? cachedChapterTotals.get(node.id)??node.source_count
      : node.source_count
    return [{
      id:anchor.nodeId,x:center.x,y:center.y,
      radius:Math.max(5,radius),
      width:anchor.screen.width,height:anchor.screen.height,
      priority:(node.id===hoveredNodeId?100000:0)+
        (node.id===props.selectedId?90000:0)+
        (node.kind==='course'?80000:0)+count,
    }]
  })
  const previous=Object.fromEntries(stableOrbitCaptionTargets)
  const planned=placeOrbitCaptions(candidates,bounds,overlays,previous)
  const slots=new Map(planned.map(p=>[p.id,p]))
  const right=new THREE.Vector3(1,0,0).applyQuaternion(camera.quaternion)
  const up=new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion)
  const anchorsById=new Map(labelAnchors
    .filter(a=>a.nodeId).map(a=>[a.nodeId!,a]))
  for (const [id,anchor] of anchorsById) {
    const slot=slots.get(id)
    if (slot) {
      stableOrbitCaptionTargets.set(id,{dx:slot.dx,dy:slot.dy})
      const old=stableOrbitCaptionOffsets.get(id)??slot
      const dx=old.dx+(slot.dx-old.dx)*.18
      const dy=old.dy+(slot.dy-old.dy)*.18
      stableOrbitCaptionOffsets.set(id,{dx,dy})
      // Set the caption from its planet's CURRENT world center each frame.
      // Previously we added a screen correction to placeLabel()'s
      // translated sprite; stale world matrices/animated parent transforms
      // could make this correction accumulate and leave the card detached.
      const depth=Math.max(1,anchor.center.clone()
        .sub(camera.position).dot(cameraForward))
      const parentScale=Math.max(.05,anchor.sprite.parent?.scale.x??1)
      const localPerPx=2*tanHalf*depth/(h*parentScale)
      anchor.sprite.position.copy(anchor.center)
        .addScaledVector(right,dx*localPerPx)
        .addScaledVector(up,-dy*localPerPx)
    }
    // Existing captions only fade when ALL nearby positions are genuinely
    // blocked. A valid side/below position is chosen before hiding.
    const alphaTarget=slot ? .96 : 0
    const alpha=stableOrbitCaptionOpacity.get(id)??.96
    const next=alpha+(alphaTarget-alpha)*.17
    stableOrbitCaptionOpacity.set(id,next)
    ;(anchor.sprite.material as THREE.SpriteMaterial).opacity=next
    anchor.sprite.visible=next>.018
  }
}

// Adaptive screen-space label solver. It runs AFTER the camera transform and
// label pixel-size scaling, so a user can orbit/zoom without labels covering
// one another, important planets, the detail drawer or the toolbar.
// Unlike the previous two solvers, there is no "best even if overlapping"
// fallback for nonselected captions. Hover always exposes the FULL name.
function arrangeScreenLabels() {
  if (!camera || !renderer || !labelAnchors.length) return
  if (orbitingPlanets.length) {
    arrangeStableOrbitCaptions()
    return
  }
  const safe = safeViewport()
  const reserved = overlayBoxes()
  const width = renderer.domElement.clientWidth
  const height = renderer.domElement.clientHeight
  const selected = props.selectedId
  const planets: {id:string; x:number; y:number; radius:number}[] = []
  for (const node of props.nodes) {
    const point = positions.get(node.id)
    if (!point) continue
    const p = screenPosition(point)
    if (!p.visible) continue
    const depth = Math.max(1, point.distanceTo(camera.position))
    const pxPerWorld = height / (2 *
      Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * depth)
    const worldRadius = node.kind === 'knowledge'
      ? node.id === selected ? 3.5 : 2.7
      : node.kind === 'group' ? (orbitMarkerRadii.get(node.id) ?? 8)
       : node.kind === 'chapter' ? (orbitMarkerRadii.get(node.id) ?? 13) : 10
    planets.push({id:node.id,x:p.x,y:p.y,
      radius:Math.max(11, Math.min(40, pxPerWorld * worldRadius + 6))})
  }
  const labels = [...labelAnchors].sort((a,b) =>
    Number(b.nodeId === selected) - Number(a.nodeId === selected) ||
    Number(b.nodeId === hoveredNodeId) - Number(a.nodeId === hoveredNodeId) ||
    (b.priority ?? 0) - (a.priority ?? 0) ||
    (a.nodeId ?? '').localeCompare(b.nodeId ?? ''))
  const placed: ScreenBox[] = []
  // The cap adapts to available real-estate; crowded layers only label key
  // nodes until hovered, instead of printing 20 unreadable overlapping cards.
  const available = Math.max(1,(safe.right-safe.left)*(safe.bottom-safe.top))
  const groupOverviewLabels = props.nodes.length > 0 &&
    props.nodes.every(n => n.kind === 'group')
  // Browse group captions are the navigation UI, not optional decoration.
  // With the wider collision-safe group layout, show every caption that
  // actually fits instead of limiting 26 groups to an arbitrary 22.
  const maxLabels = groupOverviewLabels ? labels.length :
    Math.max(2,Math.min(22,Math.floor(available / 32000)))
  let visibleLabels = 0
  for (const label of labels) {
    const center = label.sprite.getWorldPosition(new THREE.Vector3())
    const projected = screenPosition(center)
    const depth = Math.max(1,center.distanceTo(camera.position))
    const pxPerWorld = height / (2 *
      Math.tan(THREE.MathUtils.degToRad(camera.fov)/2) * depth)
    const w = label.screen?.width ?? label.sprite.scale.x * pxPerWorld
    const h = label.screen?.height ?? label.sprite.scale.y * pxPerWorld
    const pinned = label.nodeId === selected
    const hovered = label.nodeId === hoveredNodeId
    label.sprite.visible = false
    if (!projected.visible || (visibleLabels >= maxLabels && !pinned && !hovered)) continue
    // In the fullscreen browse atlas, a label belongs directly above its
    // own planet. Never send it hundreds of pixels to an unrelated region
    // merely to force it onto the screen. Hover still shows every full name.
    const offsets: [number,number][] = groupOverviewLabels
      ? [[0,0],[-w*.15,0],[w*.15,0],
        [0,-h*.33],[0,h*.33],
        [-w*.22,-h*.26],[w*.22,-h*.26]]
      : [
        [0,0], [w*.65,0],[-w*.65,0],
        [0,-h-16],[0,h+16],
        [w*.75,-h*.75],[-w*.75,-h*.75],
        [w*.75,h*.95],[-w*.75,h*.95],
        [w+16,0],[-w-16,0],
        [0,-h*2-16],[0,h*2+16],
        [w*1.4,-h*.6],[-w*1.4,-h*.6],
      ]
    let chosen: {dx:number;dy:number;score:number;box:ScreenBox} | null = null
    for (const [dx,dy] of offsets) {
      const x=projected.x+dx,y=projected.y+dy
      const box={left:x-w/2-6,right:x+w/2+6,top:y-h/2-5,bottom:y+h/2+5}
      if (box.left < safe.left || box.right > safe.right ||
          box.top < safe.top || box.bottom > safe.bottom) continue
      if (reserved.some(p => overlaps(box,p,3)) || placed.some(p=>overlaps(box,p,5)))
        continue
      const coversPlanet = planets.some(p => p.id !== label.nodeId &&
        box.left < p.x+p.radius && box.right > p.x-p.radius &&
        box.top < p.y+p.radius && box.bottom > p.y-p.radius)
      if (coversPlanet) continue
      const score=Math.hypot(dx,dy)+
        (dx !== 0 ? 8 : 0)+
        (dy > 0 ? 12 : 0)
      if (!chosen || score < chosen.score) chosen={dx,dy,score,box}
    }
    if (!chosen && pinned) {
      // Search additional nearby bands. Never teleport the selected caption
      // to the far-left corner: that breaks the visual link to its planet.
      for (const radius of [1.8,2.3,2.9,3.6]) {
        for (let j=0;j<12;j++) {
          const angle=-Math.PI/2+2*Math.PI*j/12
          const dx=Math.cos(angle)*w*radius
          const dy=Math.sin(angle)*(h+16)*radius
          const x=projected.x+dx,y=projected.y+dy
          const box={left:x-w/2-6,right:x+w/2+6,
            top:y-h/2-5,bottom:y+h/2+5}
          if (box.left < safe.left || box.right > safe.right ||
              box.top < safe.top || box.bottom > safe.bottom ||
              reserved.some(p=>overlaps(box,p,4)) ||
              placed.some(p=>overlaps(box,p,5)) ||
              planets.some(p=>p.id!==label.nodeId &&
                box.left<p.x+p.radius && box.right>p.x-p.radius &&
                box.top<p.y+p.radius && box.bottom>p.y-p.radius)) continue
          chosen={dx,dy,score:Math.hypot(dx,dy),box}
          break
        }
        if (chosen) break
      }
      // If not even one clean nearby location exists, the detail drawer
      // already displays the selected name, and hover exposes it on demand.
    }
    if (!chosen) continue
    const cameraRight = new THREE.Vector3(1,0,0).applyQuaternion(camera.quaternion)
    const cameraUp = new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion)
    const parentScale = Math.max(.05,label.sprite.parent?.scale.x ?? 1)
    const localPerPx = 1 / Math.max(.00001,pxPerWorld*parentScale)
    label.sprite.position.addScaledVector(cameraRight,chosen.dx*localPerPx)
      .addScaledVector(cameraUp,-chosen.dy*localPerPx)
    label.sprite.visible = true
    placed.push(chosen.box)
    visibleLabels++
  }
  scene?.updateMatrixWorld(true)
}

// Scale background spheres in SCREEN pixels, rather than world units.
// In the previous build a 1.5-unit sphere became a 15–35px grey planet
// on zooming in, and hundreds of them covered the active relation cluster.
// Now this is a sparse, contrast-controlled context layer; the complete
// underlying graph and clickable IDs are unchanged.
function arrangeBackdrop(now: number) {
  if (!camera || !renderer || !backdropCloud || !backdropPoints.length) return
  if (now-lastBackdropLayout < 95) return
  lastBackdropLayout = now
  const width = renderer.domElement.clientWidth
  const height = renderer.domElement.clientHeight
  const safe = safeViewport()
  const overlays = overlayBoxes()
  const cameraForward = new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion)
  const tanHalf = Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)
  const cell = THREE.MathUtils.clamp(Math.sqrt(width*height/140),54,88)
  const maxDots = Math.round(Math.min(145,Math.max(28,
    (safe.right-safe.left)*(safe.bottom-safe.top)/8200)))
  const occupied = new Set<string>()
  const protectedPoints: {x:number;y:number}[] = []
  const focus = focusRelations()
  const ids = new Set([props.selectedId, ...focus.neighbors])
  for (const id of ids) {
    const point = id ? positions.get(id) : undefined
    if (!point) continue
    const projected = screenPosition(point)
    if (projected.visible) protectedPoints.push(projected)
  }
  const labelBoxes: ScreenBox[] = []
  for (const a of labelAnchors) {
    if (!a.sprite.visible) continue
    const projected = screenPosition(a.sprite.getWorldPosition(new THREE.Vector3()))
    if (!projected.visible) continue
    const depth = Math.max(1,a.sprite.getWorldPosition(new THREE.Vector3())
      .sub(camera.position).dot(cameraForward))
    const pixelsPerWorld = height/(2*tanHalf*depth)
    const w = a.screen?.width ?? a.sprite.scale.x*pixelsPerWorld
    const h = a.screen?.height ?? a.sprite.scale.y*pixelsPerWorld
    labelBoxes.push({
      left:projected.x-w/2-12,right:projected.x+w/2+12,
      top:projected.y-h/2-10,bottom:projected.y+h/2+10,
    })
  }
  const matrix = new THREE.Matrix4()
  const unit = new THREE.Quaternion()
  const size = new THREE.Vector3()
  let drawn = 0
  // Focus chapter first, followed by the rest of the atlas. Each screen
  // grid cell admits at most one faint dot, avoiding foreground occlusion.
  for (const local of [true,false]) {
    for (let i=0;i<backdropPoints.length;i++) {
      const node = backdropPoints[i]
      if (node.local !== local) continue
      const p = screenPosition(node.position)
      const depth = node.position.clone().sub(camera.position).dot(cameraForward)
      const key = `${Math.floor(p.x/cell)}:${Math.floor(p.y/cell)}`
      const drawable = p.visible && depth > camera.near &&
        p.x >= safe.left && p.x <= safe.right &&
        p.y >= safe.top && p.y <= safe.bottom &&
        !occupied.has(key) && drawn < maxDots &&
        !overlays.some(b => p.x >= b.left && p.x <= b.right &&
          p.y >= b.top && p.y <= b.bottom) &&
        !protectedPoints.some(t=>Math.hypot(t.x-p.x,t.y-p.y)<72) &&
        !labelBoxes.some(b=>p.x>=b.left&&p.x<=b.right&&p.y>=b.top&&p.y<=b.bottom)
      if (drawable) {
        occupied.add(key)
        drawn++
      }
      // Target diameter: 4.5px in focus chapter, 3px elsewhere.
      // Hidden candidates retain their original map coordinates and IDs.
      const radiusPx = node.local ? 2.2 : 1.5
      const worldRadius = drawable
        ? Math.max(.001,radiusPx*2*tanHalf*depth/height) : .000001
      size.setScalar(worldRadius)
      matrix.compose(node.position,unit,size)
      backdropCloud.setMatrixAt(i,matrix)
      backdropCloud.setColorAt(i,node.color.clone()
        .multiplyScalar(drawable ? node.local ? .21 : .105 : .0001))
    }
  }
  backdropCloud.instanceMatrix.needsUpdate = true
  backdropCloud.computeBoundingSphere()
  if (backdropCloud.instanceColor) backdropCloud.instanceColor.needsUpdate = true
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
    if (scene.fog instanceof THREE.FogExp2)
      scene.fog.density = THREE.MathUtils.lerp(fogStartDensity, fogDesiredDensity, eased)
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
  if (outgoing.length && incoming) {
    // Camera drives level-of-detail reveal (akin to tiled maps).
    const travel = tween ? Math.min(1, (now - cameraAnimationStart) / CAMERA_DURATION) : 1
    const t = THREE.MathUtils.clamp(
      (travel - LAYER_REVEAL_START) / (LAYER_REVEAL_END - LAYER_REVEAL_START), 0, 1)
    const revealed = t * t * (3 - 2 * t)
    for (const layer of outgoing) setFade(layer, 1 - revealed)
    setFade(incoming, revealed)
    if (travel >= 1) finishFade()
  }
  controls.update()
  animateOrbits(now)
  if (!outgoing.length) {
    for (const track of orbitTracks) {
      const hovered = !!hoveredNodeId && track.ids.has(hoveredNodeId)
      ;(track.line.material as THREE.LineBasicMaterial).opacity =
        hovered ? .38 : track.baseOpacity
    }
  }
  // Project the CURRENT camera frame, not last frame's inverse matrix.
  // Screen-space avoidance otherwise lags one frame during fast navigation.
  camera.updateMatrixWorld()
  // Labels and click targets must respect animated parent-group
  // transforms in both forward and reverse transitions.
  scene.updateMatrixWorld(true)
  updateOrbitMarkerSizes()
  for (const anchor of labelAnchors) {
    if (anchor.nodeId && anchor.screen && orbitMarkerRadii.has(anchor.nodeId))
      anchor.screen.radius = orbitMarkerRadii.get(anchor.nodeId)!
  }
  screenUp.set(0, 1, 0).applyQuaternion(camera.quaternion)
  for (const anchor of labelAnchors) placeLabel(anchor)
  for (const layer of outgoing) for (const anchor of layer.anchors) placeLabel(anchor)
  scene.updateMatrixWorld(true)
  arrangeScreenLabels()
  arrangeBackdrop(now)
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
  const wasWidth = lastViewportWidth
  const wasHeight = lastViewportHeight
  lastViewportWidth = width
  lastViewportHeight = height
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height, false)
  // Reflow the group atlas when the window materially changes shape.
  // Layout otherwise stays cached while the user pans, zooms or selects.
  const overviewAtlas = props.nodes.length > 0 &&
    props.nodes.every(n => n.kind === 'group' || n.kind === 'chapter' ||
      n.kind === 'course')
  if (wasWidth && wasHeight && overviewAtlas &&
    (Math.abs(width - wasWidth) > 42 || Math.abs(height - wasHeight) > 42)) {
    cachedNodes = null
    cachedEdges = null
    rebuild()
    focus(null, true)
  }
}
function pickNode(clientX: number, clientY: number): string | null {
  if (!renderer || !camera) return null
  const rect = renderer.domElement.getBoundingClientRect()
  pointer.set(((clientX - rect.left) / rect.width) * 2 - 1,
    -((clientY - rect.top) / rect.height) * 2 + 1)
  raycaster.setFromCamera(pointer, camera)
  const clickableLabels = labelAnchors
    .filter(a => a.sprite.visible && !!a.nodeId).map(a => a.sprite)
  const hits = raycaster.intersectObjects([...meshes, ...clickableLabels, ...contextMeshes])
  const hit = hits[0]
  const instanceIds = hit?.object.userData.instanceNodeIds as string[] | undefined
  return (instanceIds && hit.instanceId !== undefined
    ? instanceIds[hit.instanceId] : hit?.object.userData.nodeId) ?? null
}
function pointerMove(event: PointerEvent) {
  if (!renderer || !mountPoint.value) return
  hoveredNodeId = pickNode(event.clientX, event.clientY)
  const node = [...props.nodes, ...(props.contextNodes ?? [])]
    .find(n => n.id === hoveredNodeId)
  hoveredName.value = node?.name ?? ''
  if (node && (node.kind==='chapter'||node.kind==='group')) {
    const count=node.kind==='group' ? node.source_count :
      (cachedChapterTotals.get(node.id)??node.source_count)
    hoveredOrbitInfo.value=`${count} 个知识点`
  } else hoveredOrbitInfo.value=''
  const bounds = mountPoint.value.getBoundingClientRect()
  const tooltipWidth = Math.min(340, Math.max(0, bounds.width - 24))
  hoveredPoint.value = {
    x: Math.max(10, Math.min(event.clientX - bounds.left + 18,
      bounds.width - tooltipWidth - 12)),
    y: Math.max(10, Math.min(event.clientY - bounds.top + 18,
      bounds.height - 100)),
  }
  renderer.domElement.style.cursor = hoveredNodeId ? 'pointer' : 'grab'
}
function pointerLeave() {
  hoveredNodeId = null
  hoveredName.value = ''
  hoveredOrbitInfo.value = ''
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
  navigation: props.navigation,
  showRelations: props.showRelations,
  showStructure: props.showStructure,
  contextNodes: props.contextNodes,
  contextEdges: props.contextEdges,
  contextChapterId: props.contextChapterId,
  preservePositionsOnSelect: props.preservePositionsOnSelect,
}), (next, previous) => {
  const beforeKey = sceneSignature(previous.nodes)
  const afterKey = sceneSignature(next.nodes)
  const layerChanged = beforeKey !== afterKey
    || next.layoutNodes !== previous.layoutNodes
    || next.layoutEdges !== previous.layoutEdges
  const action = next.navigation?.serial !== previous.navigation?.serial
    ? next.navigation : undefined
  // Only explicit entry/back operations switch map LOD.
  const hierarchyChanged = layerChanged && Boolean(action) &&
    (action?.mode === 'enter' || action?.mode === 'back')
  let returnCamera: CameraSnapshot | null = null
  const nextSceneFrame = hierarchyChanged && action?.mode === 'back'
    ? sceneFrames.get(afterKey) : undefined
  const nextOrbitFrame = hierarchyChanged && action?.mode === 'back'
    ? orbitFrames.get(afterKey) : undefined
  if (layerChanged && positions.size) {
    sceneFrames.set(beforeKey, new Map([...positions].map(([id, p]) => [id, p.clone()])))
    orbitFrames.set(beforeKey, snapshotOrbitFrame(orbitingPlanets))
    if (sceneFrames.size > 16) {
      const oldest=sceneFrames.keys().next().value!
      sceneFrames.delete(oldest)
      orbitFrames.delete(oldest)
    }
  }
  if (hierarchyChanged) {
    transitionMode = action?.mode ?? 'jump'
    if (transitionMode === 'enter' && camera && controls) {
      cameraHistory.push({
        sceneKey: beforeKey,
        // Save the last ACTUALLY RENDERED pose. An in-flight destination
        // has not been shown and would make the return path snap.
        position: camera.position.clone(),
        target: controls.target.clone(),
      })
      if (cameraHistory.length > 12) cameraHistory.shift()
    } else if (transitionMode === 'back') {
      // Match the *destination* scene, not just the last stack entry. This
      // also supports a direct jump from a leaf to the course overview.
      let index = cameraHistory.length - 1
      while (index >= 0 && cameraHistory[index].sceneKey !== afterKey) index--
      if (index >= 0) {
        returnCamera = cameraHistory[index]
        cameraHistory.splice(index)
      }
    } else {
      cameraHistory.length = 0
    }
  }
  const selectedChanged = next.selectedId !== previous.selectedId
  // A local selection can rearrange the neighbor orbit even if the set of
  // nodes stays the same. Blend those frames as well; swapping the meshes
  // immediately would otherwise look like a pre-animation teleport.
  const sceneChanged = layerChanged ||
    (selectedChanged && !props.preservePositionsOnSelect)
  rebuild(sceneChanged, hierarchyChanged ? {
    mode: transitionMode,
    anchorId: action?.nodeId,
    restore: nextSceneFrame,
    restoreOrbits: nextOrbitFrame,
  } : undefined)
  if (hierarchyChanged && transitionMode === 'back') {
    if (returnCamera) restoreCamera(returnCamera)
    else focus(next.selectedId, true)
  } else if (layerChanged || next.selectedId !== previous.selectedId) {
    if (!(props.preservePositionsOnSelect && !layerChanged && selectedChanged))
      focus(next.selectedId, layerChanged)
  }
}, { flush: 'post' })
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
  contextCacheCoordinates.clear()
  contextCacheParents.clear()
  contextCacheNodes = undefined
  contextCacheEdges = undefined
  cameraHistory.length = 0
  sceneFrames.clear()
  orbitFrames.clear()
  browseWorldPositions.clear()
})
</script>

<template>
  <div ref="mountPoint" class="galaxy-canvas">
    <div v-if="!supported" class="galaxy-unsupported" role="alert">
      <strong>无法使用 3D 加速</strong><p>{{ error }}</p>
    </div>
    <div v-if="hoveredName" class="galaxy-hover-name" role="tooltip"
      :style="{left: hoveredPoint.x + 'px', top: hoveredPoint.y + 'px'}">
      <span>{{ hoveredName }}</span>
      <small v-if="hoveredOrbitInfo" class="galaxy-hover-meta">{{ hoveredOrbitInfo }}</small>
    </div>
    <span v-if="supported" class="galaxy-watermark">3D GALAXY · THREE.JS</span>
  </div>
</template>

<style scoped>
.galaxy-canvas { position:relative;width:100%;height:100%;min-height:480px;overflow:hidden;touch-action:pan-y;background:radial-gradient(ellipse at 50% 43%,#133550 0%,#0b2138 43%,#05101f 100%) }
.galaxy-canvas :deep(canvas) { display:block;width:100%;height:100%;outline:none;cursor:grab }
.galaxy-canvas :deep(canvas):active { cursor:grabbing }
.galaxy-hover-name {position:absolute;z-index:9;pointer-events:none;max-width:min(340px,calc(100% - 24px));padding:9px 13px;border:1px solid #83dccbab;border-radius:11px;background:#061c2ef2;color:#f1ffff;box-shadow:0 8px 24px #0008;font-size:14px;font-weight:650;line-height:1.55;overflow-wrap:anywhere}
.galaxy-hover-meta {display:block;margin-top:4px;color:#7ed9dd;font-size:12px;font-weight:500}
.galaxy-watermark { pointer-events:none;position:absolute;left:19px;bottom:17px;font-size:10px;letter-spacing:2px;color:#6687a8 }
.galaxy-unsupported { color:#b4dce8;display:grid;place-content:center;text-align:center;height:100%;padding:26px;font-size:13px }
.galaxy-unsupported p { max-width:270px;line-height:1.8 }
@media(max-width:700px){.galaxy-canvas{min-height:390px}}
</style>
