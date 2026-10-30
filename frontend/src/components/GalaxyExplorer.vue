<script setup lang="ts">
/**
 * Dedicated full-viewport knowledge galaxy explorer.
 * Read-only: semantic and membership edges always come from the graph API.
 * Filtering changes visibility, never knowledge chapter assignment or approval.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowRight, BookOpen, CircleHelp, Eye, EyeOff, FileText,
  Maximize2, Menu, RotateCcw, Search, Layers3, X, ChevronLeft, Compass,
  ZoomIn, ZoomOut, Scan,
} from '@lucide/vue'
import Graph3DCanvas from './Graph3DCanvas.vue'
import { api, sourcePreviewUrl, type GraphNode,
  type KnowledgeGraph, type KnowledgePointDetail } from '../api'

const props = defineProps<{ graph: KnowledgeGraph }>()
const emit = defineEmits<{ close: [] }>()
const canvas = ref<InstanceType<typeof Graph3DCanvas> | null>(null)
const term = ref('')
const chapterChoice = ref('all')
const level = ref<'clusters' | 'groups' | 'knowledge'>('clusters')
const activeGroup = ref<string | null>(null)
const includePending = ref(true)
const selectedId = ref<string | null>(null)
const relationView = ref<'none' | 'selected' | 'all'>('selected')
const showStructure = ref(false)
const detail = ref<KnowledgePointDetail | null>(null)
const loadingDetail = ref(false)
const error = ref('')
const explorerList = ref(true)
const navOpen = ref(false)

const root = computed(() => props.graph.nodes.find(n => n.kind === 'course'))
const chapters = computed(() => props.graph.nodes.filter(n => n.kind === 'chapter'))
const knowledge = computed(() => props.graph.nodes.filter(n => n.kind === 'knowledge'))
const chapterCounts = computed(() => new Map(chapters.value.map(chapter =>
  [chapter.id, knowledge.value.filter(n => underChapter(n, chapter.id)).length],
)))
const selected = computed(() => props.graph.nodes.find(n => n.id === selectedId.value))
const byId = computed(() => new Map(props.graph.nodes.map(n => [n.id, n])))
const parent = computed(() => new Map(props.graph.edges.filter(e => e.structural)
  .map(e => [e.target, e.source])))
const expanded = computed(() => level.value === 'knowledge' ||
  Boolean(term.value.trim()) ||
  (selected.value?.kind === 'knowledge'))
const displayMode = computed(() => chapterChoice.value !== 'all' && level.value === 'groups' && !term.value.trim() ? 'groups' : expanded.value ? 'knowledge' : 'clusters')
const visibleKnowledgeCount = computed(() => nodes.value.filter(n => n.kind === 'knowledge').length)
const hiddenKnowledgeCount = computed(() => Math.max(0, knowledge.value.length - visibleKnowledgeCount.value))
const currentChapter = computed(() => chapters.value.find(ch => ch.id === chapterChoice.value))
const chapterName = computed(() => {
  const chapterId = selected.value?.chapter_id
  if (!chapterId) return '未归类'
  return chapters.value.find(c => c.chapter_id === chapterId)?.name ?? '未知章节'
})

function underChapter(node: GraphNode, target: string): boolean {
  if (target === 'all') return true
  let id: string | undefined = node.id
  const seen = new Set<string>()
  while (id && !seen.has(id)) {
    if (id === target) return true
    seen.add(id)
    id = parent.value.get(id)
  }
  return false
}
const matching = computed(() => {
  const q = term.value.trim().toLocaleLowerCase()
  return knowledge.value.filter(n =>
    (includePending.value || n.review_status === 'approved') &&
    underChapter(n, chapterChoice.value) &&
    (!q || (n.name + ' ' + (n.summary ?? '')).toLocaleLowerCase().includes(q)),
  )
})

/** Browse-only grouping; no new stored semantic relationships are inferred. */
type BrowseGroup = {id:string; name:string; members:string[]}
const browseGroups = computed<BrowseGroup[]>(()=>{
 if(chapterChoice.value==='all')return []
 const points=[...matching.value].sort((a,b)=>a.name.localeCompare(b.name,'zh-Hans')||a.id.localeCompare(b.id))
 const ids=new Set(points.map(p=>p.id))
 const adj=new Map(points.map(p=>[p.id,new Set<string>()]))
 for(const e of props.graph.edges){
  if(e.structural||!e.verified||!ids.has(e.source)||!ids.has(e.target))continue
  adj.get(e.source)?.add(e.target);adj.get(e.target)?.add(e.source)
 }
 const remain=new Set(ids),out:BrowseGroup[]=[],loose:string[]=[]
 while(remain.size){
  const degree=(id:string)=>[...(adj.get(id)??[])].filter(x=>remain.has(x)).length
  const seed=[...remain].sort((x,y)=>degree(y)-degree(x)||x.localeCompare(y))[0]
  const group=[seed];remain.delete(seed)
  while(remain.size&&group.length<14){
   const ranked=[...remain].map(id=>({id,links:group.filter(x=>adj.get(id)?.has(x)).length,degree:degree(id)}))
    .sort((x,y)=>y.links-x.links||y.degree-x.degree||x.id.localeCompare(y.id))
   if(!ranked[0].links)break
   const next=ranked[0].id;group.push(next);remain.delete(next)
  }
  if(group.length<3){loose.push(...group);continue}
  const name=points.find(p=>p.id===seed)?.name??''
  const i=out.length+1
  out.push({id:`view-group:${chapterChoice.value}:${i}`,
    name:`关系邻域 ${String(i).padStart(2,'0')} · ${name.slice(0,10)}`,members:group})
 }
 const remainder=[...loose,...remain].sort()
 for(let k=0;k<remainder.length;k+=14){
  const i=out.length+1
  out.push({id:`view-group:${chapterChoice.value}:${i}`,
    name:`待关联浏览组 ${String(i).padStart(2,'0')}`,
    members:remainder.slice(k,k+14)})
 }
 return out
})
const activeBrowseGroup=computed(()=>browseGroups.value.find(g=>g.id===activeGroup.value))
function groupNode(g:BrowseGroup):GraphNode{
 return {id:g.id,kind:'group',name:g.name,chapter_id:null,
  summary:'仅用于浏览的关系优先分组，并非新建章节或先修关系',
  difficulty:null,review_status:'approved',mastery:null,observations:0,
  source_count:g.members.length,question_count:0,virtual:true}
}
function syntheticEdge(source:string,target:string):typeof props.graph.edges[number]{
 return {id:`browse:${source}:${target}`,source,target,type:'contains',structural:true,verified:false,rationale:null}
}
const visibleIds = computed(() => {
  const ids = new Set<string>()
  if (root.value) ids.add(root.value.id)
  const addAncestors = (id: string) => {
    const seen = new Set<string>()
    let cursor: string | undefined = id
    while (cursor && !seen.has(cursor)) {
      ids.add(cursor)
      seen.add(cursor)
      cursor = parent.value.get(cursor)
    }
  }
  if (chapterChoice.value === 'all') chapters.value.forEach(c => addAncestors(c.id))
  if (chapterChoice.value !== 'all') addAncestors(chapterChoice.value)
  // Cluster overview displays chapter anchors only: do not materialize
  // 1300+ knowledge meshes until a chapter is opened or search is used.
  if (expanded.value) matching.value.forEach(n => addAncestors(n.id))
  // A selected point can connect across chapters. Reveal those direct
  // verified neighbors and their real structural ancestors without guessing.
  if (selectedId.value && byId.value.get(selectedId.value)?.kind === 'knowledge') {
    addAncestors(selectedId.value)
    for (const edge of props.graph.edges) {
      if (edge.structural || !edge.verified) continue
      if (edge.source === selectedId.value) addAncestors(edge.target)
      else if (edge.target === selectedId.value) addAncestors(edge.source)
    }
  }
  return ids
})
const nodes = computed<GraphNode[]>(()=>{
 const chapter=currentChapter.value
 if(chapter&&displayMode.value==='groups')return [chapter,...browseGroups.value.map(groupNode)]
 if(chapter&&activeBrowseGroup.value&&level.value==='knowledge'&&!term.value.trim()){
  const ids=new Set(activeBrowseGroup.value.members)
  if(selectedId.value){
   ids.add(selectedId.value)
   for(const edge of props.graph.edges){
    if(edge.structural||!edge.verified)continue
    if(edge.source===selectedId.value)ids.add(edge.target)
    if(edge.target===selectedId.value)ids.add(edge.source)
   }
  }
  // The chapter remains in the breadcrumb and drawer. Rendering another
  // chapter planet inside a leaf neighborhood wastes space and crowds labels.
  return [groupNode(activeBrowseGroup.value),
   ...props.graph.nodes.filter(n=>n.kind==='knowledge'&&ids.has(n.id))]
 }
 return props.graph.nodes.filter(n=>visibleIds.value.has(n.id))
})
const edges = computed(()=>{
 const chapter=currentChapter.value
 if(chapter&&displayMode.value==='groups')return browseGroups.value.map(g=>syntheticEdge(chapter.id,g.id))
 if(chapter&&activeBrowseGroup.value&&level.value==='knowledge'&&!term.value.trim()){
  const g=activeBrowseGroup.value,ids=new Set(nodes.value.map(n=>n.id))
  return [
   ...nodes.value.filter(n=>n.kind==='knowledge').map(n=>syntheticEdge(g.id,n.id)),
   ...props.graph.edges.filter(e=>!e.structural&&ids.has(e.source)&&ids.has(e.target)&&
    (relationView.value==='all'||relationView.value==='selected'&&!!selectedId.value&&
     (e.source===selectedId.value||e.target===selectedId.value)))]
 }
 return props.graph.edges.filter(e=>{
  if(!visibleIds.value.has(e.source)||!visibleIds.value.has(e.target))return false
  if(e.structural)return true
  if(relationView.value==='none')return false
  if(relationView.value==='all')return true
  return selectedId.value!==null&&(e.source===selectedId.value||e.target===selectedId.value)
 })
})
const semanticEdges = computed(() => edges.value.filter(e => !e.structural))
const selectedRelations = computed(() => props.graph.edges.filter(e =>
  !e.structural && e.verified && selectedId.value !== null &&
  (e.source === selectedId.value || e.target === selectedId.value))
  .map(e => {
    const other = e.source === selectedId.value ? e.target : e.source
    return { edge: e, id: other, name: byId.value.get(other)?.name ?? '未知知识点' }
  }))
const relationLabels: Record<string, string> = {
  prerequisite_required: '必要前置', prerequisite_recommended: '建议前置',
  related: '相关', uses: '使用', compares: '对比',
}
let detailRequest = 0
watch(selectedId, async id => {
  const token = ++detailRequest
  detail.value = null
  error.value = ''
  if (!id || byId.value.get(id)?.kind !== 'knowledge') {
    loadingDetail.value = false
    return
  }
  loadingDetail.value = true
  try {
    const result = await api.knowledgePoint(id)
    if (token === detailRequest) detail.value = result
  } catch {
    if (token === detailRequest) error.value = '读取知识点来源失败，可稍后重试'
  } finally {
    if (token === detailRequest) loadingDetail.value = false
  }
})
function reset() {
  selectedId.value = null
  chapterChoice.value = 'all'
  level.value = 'clusters'
  activeGroup.value = null
  term.value = ''
  // The WebGL canvas watches the level's nodes and performs one fitted
  // camera transition. A second nextTick reset used to override it.
}
function switchLevel(value: 'clusters' | 'groups' | 'knowledge') {
  activeGroup.value = null
  selectedId.value = null
  level.value = value
  if (value === 'clusters' || (value === 'groups' && chapterChoice.value === 'all')) {
    chapterChoice.value = 'all'
    selectedId.value = null
    term.value = ''
  }
  // The WebGL canvas watches the level's nodes and performs one fitted
  // camera transition. A second nextTick reset used to override it.
}
function selectCanvasNode(id: string | null) {
  if (!id) { selectedId.value = null; return }
  if(id.startsWith('view-group:')){
    if(browseGroups.value.some(g=>g.id===id)){
      activeGroup.value=id;level.value='knowledge';selectedId.value=null
    }
    return
  }
  const node = byId.value.get(id)
  if (node?.kind === 'chapter') {
    selectChapter(id)
    return
  } else if (node?.kind === 'course') {
    chapterChoice.value = 'all'
    level.value = 'clusters'
  } else if (node?.kind === 'knowledge') {
    level.value = 'knowledge'
  }
  selectedId.value = id
}
function focusKnowledge(id: string) {
  // Navigate into the concept's actual chapter instead of unexpectedly
  // expanding the entire course from a collapsed overview.
  const chapter = parent.value.get(id)
  chapterChoice.value = chapter && byId.value.get(chapter)?.kind === 'chapter'
    ? chapter : 'all'
  term.value = ''
  level.value = 'knowledge'
  activeGroup.value = browseGroups.value.find(g=>g.members.includes(id))?.id??null
  selectedId.value = id
}
function focusRelated(id: string) {
  focusKnowledge(id)
}
function selectChapter(id: string) {
  chapterChoice.value = id
  level.value = id === 'all' ? 'clusters' : 'groups'
  activeGroup.value = null
  selectedId.value = null
  // The WebGL canvas watches the level's nodes and performs one fitted
  // camera transition. A second nextTick reset used to override it.
}
function chooseChapter(chapter: GraphNode) {
  selectChapter(chapter.id)
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (selectedId.value) selectedId.value = null
    else emit('close')
  }
}
let originalOverflow = ''
onMounted(() => {
  originalOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.body.style.overflow = originalOverflow
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="gx-page" role="dialog" aria-modal="true" aria-label="3D 知识星系全屏探索">
    <header class="gx-toolbar">
      <div class="gx-brand">
        <button type="button" class="gx-nav-trigger" :aria-expanded="navOpen"
          aria-label="打开或收起知识图谱导航" @click="navOpen=!navOpen">
          <X v-if="navOpen" :size="18" /><Menu v-else :size="18" />
        </button>
        <div class="gx-brand-text">
          <strong>知迹 <span>· 知识星系</span></strong>
          <small>{{ graph.course_name }} / {{ currentChapter?.name ?? (displayMode==='clusters' ? '章节总览' : '知识全景') }}</small>
        </div>
      </div>
      <div class="gx-header-mode">
        <span class="gx-live-dot"></span>
        {{ displayMode==='clusters' ? 'COURSE ATLAS' : displayMode==='groups' ? 'CHAPTER GROUPS' : 'KNOWLEDGE EXPLORER' }}
      </div>
      <div class="gx-toolbar-actions">
        <button type="button" title="探索与搜索" aria-label="探索与搜索" @click="navOpen=!navOpen"><Search :size="17" /></button>
        <button type="button" title="返回章节总览" aria-label="返回章节总览" @click="reset"><RotateCcw :size="17" /></button>
        <button type="button" class="gx-exit" title="退出全屏探索" aria-label="退出全屏探索" @click="emit('close')"><X :size="19" /></button>
      </div>
    </header>

    <div class="gx-body" :class="{ 'gx-nav-open': navOpen, 'gx-has-detail': !!selected }">
      <aside v-if="navOpen" class="gx-panel gx-nav" aria-label="知识图谱筛选">
        <div class="gx-panel-title">
          <strong>探索知识</strong>
          <button type="button" aria-label="收起探索设置" @click="navOpen=false"><X :size="17" /></button>
        </div>
        <label class="gx-field"><span><Search :size="14" /> 搜索知识点</span>
          <input v-model="term" type="search" placeholder="输入知识点名称或摘要" />
        </label>
        <h3><BookOpen :size="14" /> 章节导航</h3>
        <div class="gx-chapters">
          <button type="button" :class="{active:chapterChoice==='all'}"
            @click="selectChapter('all');navOpen=false">
            <span class="gx-chapter-index"><Layers3 :size="14"/></span>
            <span class="gx-chapter-name">课程总览</span>
            <small>{{ chapters.length }} 章</small>
          </button>
          <button v-for="(chapter,i) in chapters" :key="chapter.id" type="button"
            :class="{active:chapterChoice===chapter.id}"
            @click="selectChapter(chapter.id);navOpen=false">
            <span class="gx-chapter-index">{{ String(i+1).padStart(2,'0') }}</span>
            <span class="gx-chapter-name">{{ chapter.name }}</span>
            <small>{{ chapterCounts.get(chapter.id) ?? 0 }}</small>
          </button>
        </div>
        <label class="gx-checkbox">
          <input v-model="includePending" type="checkbox" /> 显示尚未审核的知识点
        </label>

        <div class="gx-divider"></div>
        <h3>显示层级</h3>
        <div class="gx-segment" role="group" aria-label="节点展开模式">
          <button type="button" :class="{active:displayMode==='clusters'}"
            @click="switchLevel('clusters')">章节星团</button>
          <button type="button" :class="{active:displayMode==='knowledge'}"
            @click="switchLevel('knowledge')">全部知识点</button>
          <button v-if="currentChapter" type="button" :class="{active:displayMode==='groups'}"
            @click="switchLevel('groups')">章节分组</button>
        </div>
        <p class="gx-note">进入章节先查看每组最多14个点的浏览分组；仅真实已审核边代表知识关系。</p>

        <div class="gx-divider"></div>
        <h3>连线显示</h3>
        <p class="gx-note">默认只显示选中知识点的关联，避免千级图谱出现线团。</p>
        <div class="gx-segment" role="group" aria-label="语义关系显示模式">
          <button type="button" :class="{active:relationView==='none'}" @click="relationView='none'">隐藏</button>
          <button type="button" :class="{active:relationView==='selected'}" @click="relationView='selected'">仅选中</button>
          <button type="button" :class="{active:relationView==='all'}" @click="relationView='all'">全部</button>
        </div>
        <button type="button" class="gx-toggle" @click="showStructure=!showStructure">
          <Eye v-if="showStructure" :size="16" /><EyeOff v-else :size="16" />
          {{ showStructure ? '隐藏课程/章节隶属线' : '显示课程/章节隶属线' }}
        </button>

        <div class="gx-divider"></div>
        <div class="gx-list-header">
          <h3>知识点导航</h3>
          <button type="button" @click="explorerList=!explorerList">{{ explorerList ? '收起' : '展开' }}</button>
        </div>
        <div v-if="explorerList" class="gx-results">
          <p v-if="!matching.length" class="gx-note">没有匹配的知识点。</p>
          <button v-for="node in matching.slice(0, 150)" :key="node.id" type="button"
             :class="{active:selectedId===node.id}" @click="focusKnowledge(node.id)">
            <span class="gx-point"></span><span>{{ node.name }}</span>
          </button>
          <p v-if="matching.length>150" class="gx-note">列表显示前 150 个，请搜索或选择章节缩小范围。画布中仍展示所有匹配点。</p>
        </div>
      </aside>

      <main class="gx-main">
        <Graph3DCanvas ref="canvas" :nodes="nodes" :edges="edges" :selected-id="selectedId"
          :show-relations="relationView!=='none'" :show-structure="showStructure"
          :relation-edges="graph.edges" :layout-nodes="chapterChoice==='all' ? graph.nodes : nodes"
          :layout-edges="chapterChoice==='all' ? graph.edges : edges"
          @select="selectCanvasNode" />
        <div class="gx-stage-nav">
          <button v-if="activeBrowseGroup" type="button" @click="switchLevel('groups')"
            class="gx-back"><ChevronLeft :size="17" /> 返回章节分组</button>
          <button v-else-if="currentChapter" type="button" @click="selectChapter('all')"
            class="gx-back"><ChevronLeft :size="17" /> 返回总览</button>
          <span class="gx-stage-kicker">{{ displayMode==='clusters' ? 'CHAPTER CONSTELLATIONS' : displayMode==='groups' ? 'CHAPTER BROWSE GROUPS' : 'EXPLORE CONCEPTS' }}</span>
          <h1>{{ activeBrowseGroup?.name ?? currentChapter?.name ?? (displayMode==='clusters' ? '从章节开始，探索知识宇宙' : '知识点全景') }}</h1>
          <p v-if="selected?.kind === 'knowledge' && selectedRelations.length">
            已点亮 {{ new Set(selectedRelations.map(r => r.id)).size }} 个直接关联知识点 · 点击关联星球继续探索
          </p>
          <p v-else>{{ currentChapter ? (displayMode==='groups' ? `${chapterCounts.get(currentChapter.id) ?? 0} 个知识点 · ${browseGroups.length} 个浏览分组 · 点击进入小规模网络` : activeBrowseGroup ? `${activeBrowseGroup.members.length} 个知识点 · 点击查看真实关联` : `${chapterCounts.get(currentChapter.id) ?? 0} 个知识点 · 点击星球查看关系`) :
            (displayMode==='clusters' ? '每颗星球代表一个真实章节。选择章节，逐层深入。' : '点击知识点，点亮它的一阶关联。') }}</p>
        </div>
        <div class="gx-view-switch" role="group" aria-label="图谱显示层级">
          <button type="button" :class="{active:displayMode==='clusters'}"
            @click="switchLevel('clusters')"><Layers3 :size="15" /> 章节</button>
          <button v-if="currentChapter" type="button" :class="{active:displayMode==='groups'}"
            @click="switchLevel('groups')"><Layers3 :size="15" /> 分组</button>
          <button type="button" :class="{active:displayMode==='knowledge' && !activeBrowseGroup}"
            @click="switchLevel('knowledge')"><BookOpen :size="15" /> 全部</button>
        </div>
        <div class="gx-counter">
          <strong>{{ chapters.length }}<small>章节</small></strong>
          <strong>{{ knowledge.length }}<small>知识点</small></strong>
          <strong>{{ graph.stats.approved_semantic_edges }}<small>已审核关系</small></strong>
        </div>
        <div class="gx-zoom-controls" role="group" aria-label="知识图谱缩放">
          <button type="button" title="放大星系" aria-label="放大星系"
            @click="canvas?.zoomIn()"><ZoomIn :size="18" /></button>
          <button type="button" title="自动适配当前可见知识点" aria-label="适应画布"
            @click="canvas?.resetCamera()"><Scan :size="17" /></button>
          <button type="button" title="缩小星系" aria-label="缩小星系"
            @click="canvas?.zoomOut()"><ZoomOut :size="18" /></button>
        </div>
        <div class="gx-hint"><Compass :size="13" /> 拖动旋转 · 滚轮缩放 · 点击星球进入章节</div>
      </main>

      <aside v-if="selected" class="gx-panel gx-detail" aria-label="选中节点详情">
        <div class="gx-detail-head"><span>{{ selected.kind === 'knowledge' ? '节点详情 · 直接关联' : '章节 / 课程概览' }}</span>
          <button type="button" aria-label="关闭知识点详情" @click="selectedId=null"><X :size="18" /></button>
        </div>
        <h2>{{ selected.name }}</h2>
        <p class="gx-section">{{ selected.kind === 'knowledge' ? chapterName : '课程结构节点' }}</p>
        <p class="gx-summary">{{ selected.summary || '暂无知识点摘要。' }}</p>
        <template v-if="selected.kind==='knowledge'">
          <div class="gx-metrics">
            <span>掌握状态</span><strong>{{ selected.mastery===null?'尚未测评':Math.round(selected.mastery*100)+'%' }}</strong>
            <span>来源片段</span><strong>{{ selected.source_count }} 条</strong>
            <span>已审核关系</span><strong>{{ selectedRelations.length }} 条</strong>
          </div>
          <h3><BookOpen :size="15" /> 已审核关系</h3>
          <p v-if="!selectedRelations.length" class="gx-note">暂无已审核的直接关联。</p>
          <button v-for="item in selectedRelations.slice(0,35)" :key="item.edge.id"
            type="button" class="gx-related" @click="focusRelated(item.id)">
            <small>{{ relationLabels[item.edge.type] || item.edge.type }}</small>
            <span>{{ item.name }}</span><ArrowRight :size="14" />
          </button>
          <h3><FileText :size="15" /> PDF / 课件来源</h3>
          <p v-if="loadingDetail" class="gx-note">正在读取来源…</p>
          <p v-if="error" class="gx-note" role="alert">{{ error }}</p>
          <p v-else-if="!loadingDetail && !detail?.sources?.length" class="gx-note">没有可展示的引用。</p>
          <div v-for="source in detail?.sources?.slice(0,20) ?? []" :key="source.chunk_id" class="gx-source">
            <span>{{ source.original_filename }} · 第 {{ source.page_start }} 页</span>
            <a v-if="source.original_filename.toLowerCase().endsWith('.pdf')"
              :href="sourcePreviewUrl(source)" target="_blank" rel="noopener noreferrer">查看原页 <ArrowRight :size="13" /></a>
          </div>
        </template>
        <template v-else-if="selected.kind==='chapter'">
          <button type="button" class="gx-primary" @click="chooseChapter(selected)">仅查看本章节 <ArrowRight :size="15" /></button>
        </template>
      </aside>
    </div>
    <footer class="gx-footer">
      <span><CircleHelp :size="13" /> 浏览分组只是展示辅助；真实关联仅来自已审核关系，节点位置不代表先修。</span>
      <span>KNOWTRACE</span>
    </footer>
  </div>
</template>

<style scoped>
.gx-page{position:fixed;inset:0;z-index:1000;display:flex;flex-direction:column;width:100vw;height:100dvh;overflow:hidden;background:#061522;color:#deebf5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.gx-page button,.gx-page input,.gx-page select{font:inherit}
.gx-page button:focus-visible,.gx-page a:focus-visible,.gx-page input:focus-visible{outline:2px solid #83e4d4;outline-offset:2px}
.gx-toolbar{height:68px;flex:none;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:10px 24px;background:#081c2bdc;border-bottom:1px solid #274455;z-index:10}
.gx-brand{display:flex;align-items:center;gap:15px;min-width:0}
.gx-brand-text{display:grid;gap:5px;min-width:0}
.gx-brand strong{font-size:16px;letter-spacing:.8px;color:#e5f6f8;white-space:nowrap}
.gx-brand strong span{font-weight:450;color:#a3c2cb}
.gx-brand small{font-size:10px;color:#78a1b4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gx-nav-trigger,.gx-toolbar-actions button{width:39px;height:39px;display:grid;place-items:center;padding:0;background:transparent;border:1px solid #29465a;border-radius:11px;color:#a9cbd5}
.gx-nav-trigger:hover,.gx-toolbar-actions button:hover{background:#173a4c;color:#e7fcfa;border-color:#3d7a83}
.gx-toolbar-actions{display:flex;align-items:center;gap:7px}
.gx-toolbar-actions .gx-exit{background:#123145}
.gx-header-mode{font-size:10px;font-weight:700;letter-spacing:1.8px;color:#80aebb;display:flex;align-items:center;gap:9px}
.gx-live-dot{width:7px;height:7px;border-radius:50%;background:#64d4bd;box-shadow:0 0 10px #45bca688}
.gx-body{flex:1;min-height:0;position:relative;isolation:isolate}
.gx-main{position:absolute;inset:0;z-index:0;overflow:hidden}
.gx-main :deep(.galaxy-canvas){position:absolute;inset:0;height:100%;min-height:0;background:radial-gradient(ellipse at 50% 50%,#11283e 0%,#081928 53%,#04101d 100%)}
.gx-stage-nav{position:absolute;z-index:2;top:23px;left:26px;max-width:min(560px,calc(100% - 52px));pointer-events:none}
.gx-stage-nav .gx-back{pointer-events:auto;border:0;display:flex;align-items:center;gap:5px;color:#7bd8c5;background:transparent;font-size:12px;padding:3px 0 10px;cursor:pointer}
.gx-stage-kicker{display:block;font-size:10px;letter-spacing:2.4px;font-weight:700;color:#72baae;margin-bottom:12px}
.gx-stage-nav h1{font-size:clamp(19px,2.1vw,30px);line-height:1.35;font-weight:620;letter-spacing:-.7px;color:#e9f9fb;margin:0 0 10px;text-shadow:0 2px 15px #02101ba9}
.gx-stage-nav p{font-size:12px;line-height:1.7;color:#87adbd;margin:0}
.gx-view-switch{position:absolute;top:28px;right:28px;z-index:3;display:flex;gap:2px;padding:4px;background:#0a2738d6;border:1px solid #28495a;border-radius:12px;box-shadow:0 10px 25px #020c1470}
.gx-view-switch button{display:flex;align-items:center;gap:7px;border:0;border-radius:9px;padding:9px 14px;background:transparent;color:#91b2c2;font-size:11px;white-space:nowrap}
.gx-view-switch button.active{background:#1a545a;color:#defaf4;font-weight:700}
.gx-view-switch button:hover{color:#defaf4}
.gx-counter{position:absolute;left:26px;bottom:34px;z-index:2;display:flex;gap:22px;padding:14px 19px;background:#081c2bc9;border:1px solid #264659;border-radius:13px;pointer-events:none;backdrop-filter:blur(10px)}
.gx-counter strong{display:flex;flex-direction:column;gap:5px;font-size:21px;font-weight:650;line-height:1;color:#d6f6ef}
.gx-counter small{font-size:10px;font-weight:400;color:#7eabba}
.gx-zoom-controls{position:absolute;bottom:84px;right:26px;z-index:3;display:flex;flex-direction:column;gap:3px;padding:5px;background:#0a2738df;border:1px solid #28495a;border-radius:13px;box-shadow:0 10px 25px #020c1470}
.gx-zoom-controls button{display:grid;place-items:center;width:36px;height:36px;color:#9cc9d2;border:0;border-radius:9px;background:transparent;cursor:pointer}
.gx-zoom-controls button:hover{background:#1b5259;color:#e8fff9}
.gx-body.gx-has-detail .gx-zoom-controls{right:370px}
.gx-hint{position:absolute;right:25px;bottom:28px;display:flex;align-items:center;gap:9px;color:#719aad;font-size:10px;pointer-events:none}
.gx-footer{height:27px;flex:none;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:0 22px;background:#071725;border-top:1px solid #193747;color:#5c879c;font-size:10px}
.gx-footer span:first-child{display:flex;align-items:center;gap:7px}
.gx-panel{position:absolute;z-index:5;top:14px;bottom:14px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#305b6c transparent;background:#0b2335f2;border:1px solid #365668;box-shadow:0 25px 70px #0009;backdrop-filter:blur(16px);border-radius:16px;padding:20px}
.gx-nav{left:18px;width:308px}
.gx-detail{right:18px;width:330px}
.gx-body.gx-nav-open .gx-stage-nav,.gx-body.gx-nav-open .gx-counter{left:350px}
.gx-body.gx-has-detail .gx-view-switch{right:370px}
.gx-panel-title{display:flex;align-items:center;justify-content:space-between;gap:8px;padding-bottom:17px;margin-bottom:21px;border-bottom:1px solid #28495b}
.gx-panel-title strong{font-size:14px;color:#e4f8fa}
.gx-panel-title button,.gx-detail-head button{width:28px;height:28px;display:grid;place-items:center;border:0;background:transparent;color:#a7cbd6}
.gx-panel h3{display:flex;align-items:center;gap:9px;margin:16px 0 12px;font-size:12px;color:#b3d6dc}
.gx-field{display:grid;gap:9px;margin-bottom:20px}
.gx-field span{display:flex;align-items:center;gap:8px;font-size:11px;color:#94b5c2}
.gx-field input{height:42px;width:100%;min-width:0;background:#071b2d;color:#e4f9fa;border:1px solid #31566a;border-radius:10px;padding:11px 13px;font-size:12px}
.gx-chapters{display:grid;gap:4px;max-height:35vh;overflow-y:auto;margin:0 -5px 16px}
.gx-chapters button{width:100%;display:flex;align-items:center;gap:11px;border:1px solid transparent;background:transparent;text-align:left;color:#a5c4d0;border-radius:10px;padding:10px 8px;font-size:11px}
.gx-chapters button:hover,.gx-chapters button.active{background:#16394b;color:#e1f9f8;border-color:#285966}
.gx-chapter-index{flex:0 0 32px;font-size:10px;color:#81bbbc;letter-spacing:.6px;text-align:center}
.gx-chapter-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gx-chapters small{flex:none;color:#729dac;font-size:10px}
.gx-checkbox{display:flex;align-items:center;gap:9px;font-size:11px;color:#94b6c4}
.gx-checkbox input{accent-color:#3dccbc}
.gx-note{font-size:11px;color:#7498aa;line-height:1.7;margin:9px 0 15px}
.gx-divider{border-top:1px solid #2a4759;margin:22px 0}
.gx-segment{display:flex;padding:3px;gap:3px;border:1px solid #2c4c5e;background:#0a2235;border-radius:10px;margin:12px 0}
.gx-segment button{flex:1;border:0;background:transparent;color:#8cabbc;padding:9px 5px;font-size:11px;border-radius:7px}
.gx-segment button.active{background:#1c5d64;color:#e0fbf6}
.gx-toggle{width:100%;display:flex;justify-content:flex-start;align-items:center;gap:9px;padding:10px;border:1px solid #315367;background:#12374b;border-radius:9px;color:#b8d5df;font-size:11px}
.gx-list-header{display:flex;align-items:center;justify-content:space-between;gap:10px}
.gx-list-header button{background:transparent;border:0;color:#78d5c5;font-size:11px}
.gx-results{display:grid;gap:3px;max-height:270px;overflow:auto}
.gx-results>button{display:flex;gap:9px;align-items:center;text-align:left;background:transparent;border:0;border-radius:7px;color:#accbd3;padding:9px;font-size:11px}
.gx-results>button:hover,.gx-results>button.active{background:#1c4855}
.gx-results>button span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gx-point{width:7px;height:7px;flex:0 0 7px;background:#55c9bc;border-radius:50%}
.gx-detail-head{display:flex;align-items:center;justify-content:space-between;font-size:11px;color:#72d4c4}
.gx-detail h2{font-size:20px;line-height:1.5;font-weight:630;color:#f0ffff;margin:15px 0;overflow-wrap:anywhere}
.gx-section{font-size:12px;color:#70d6c6}
.gx-summary{font-size:12px;line-height:1.85;color:#a8c5d1}
.gx-metrics{display:grid;grid-template-columns:1fr auto;gap:10px;margin:20px 0;padding:17px 0;border-top:1px solid #305064;border-bottom:1px solid #305064;font-size:11px}
.gx-metrics span{color:#8ba7b9}.gx-metrics strong{color:#d5f5ee}
.gx-related{width:100%;display:flex;align-items:center;gap:9px;margin:5px 0;padding:11px;background:#112f43;border:1px solid #295269;border-radius:9px;text-align:left;color:#d0e5eb;font-size:11px}
.gx-related:hover{background:#204a55;border-color:#4f9c92}
.gx-related small{color:#7bd9c8;white-space:nowrap}
.gx-related span{flex:1}
.gx-source{border-bottom:1px solid #2b4c60;padding:10px 0;display:grid;gap:8px;font-size:11px;color:#b9d5e0;overflow-wrap:anywhere}
.gx-source a{color:#78dfcb;display:inline-flex;align-items:center;gap:5px}
.gx-primary{width:100%;display:flex;align-items:center;justify-content:center;gap:9px;background:#176d6b;color:#efffff;border:0;border-radius:9px;padding:12px}
@media(max-width:1150px){.gx-header-mode{display:none}.gx-body.gx-nav-open .gx-stage-nav,.gx-body.gx-nav-open .gx-counter{left:345px}.gx-body.gx-has-detail .gx-view-switch{right:367px}}
@media(max-width:830px){.gx-body.gx-has-detail .gx-zoom-controls{right:26px}.gx-toolbar{padding:10px 13px}.gx-nav{left:10px}.gx-detail{right:10px}.gx-body.gx-nav-open .gx-stage-nav{display:none}.gx-body.gx-nav-open .gx-counter{left:26px}.gx-body.gx-has-detail .gx-view-switch{right:28px;top:10px}.gx-stage-nav{top:18px;left:18px}.gx-view-switch{right:13px;top:15px}.gx-hint{display:none}}
@media(max-width:600px){.gx-zoom-controls{right:12px;bottom:88px}.gx-brand strong{font-size:13px}.gx-brand strong span{display:none}.gx-brand small{max-width:160px}.gx-toolbar{height:58px}.gx-toolbar-actions{gap:4px}.gx-nav-trigger,.gx-toolbar-actions button{width:34px;height:34px}.gx-nav,.gx-detail{width:min(335px,calc(100vw - 20px));top:10px;bottom:10px}.gx-detail{max-height:65%;top:auto}.gx-body.gx-has-detail .gx-view-switch{display:none}.gx-stage-nav{top:18px;left:15px;right:15px}.gx-stage-nav h1{font-size:20px}.gx-stage-nav p{max-width:230px}.gx-view-switch{top:122px;left:14px;right:auto}.gx-counter{left:13px;bottom:15px;gap:15px;padding:11px}.gx-counter strong{font-size:16px}.gx-footer span:last-child{display:none}}
</style>
