<script setup lang="ts">
/**
 * Dedicated full-viewport knowledge galaxy explorer.
 * Read-only: semantic and membership edges always come from the graph API.
 * Filtering changes visibility, never knowledge chapter assignment or approval.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowRight, BookOpen, CircleHelp, Eye, EyeOff, FileText,
  Maximize2, RotateCcw, Search, X,
} from '@lucide/vue'
import Graph3DCanvas from './Graph3DCanvas.vue'
import { api, sourcePreviewUrl, type GraphNode,
  type KnowledgeGraph, type KnowledgePointDetail } from '../api'

const props = defineProps<{ graph: KnowledgeGraph }>()
const emit = defineEmits<{ close: [] }>()
const canvas = ref<InstanceType<typeof Graph3DCanvas> | null>(null)
const term = ref('')
const chapterChoice = ref('all')
const includePending = ref(true)
const selectedId = ref<string | null>(null)
const relationView = ref<'none' | 'selected' | 'all'>('selected')
const showStructure = ref(false)
const detail = ref<KnowledgePointDetail | null>(null)
const loadingDetail = ref(false)
const error = ref('')
const explorerList = ref(true)

const root = computed(() => props.graph.nodes.find(n => n.kind === 'course'))
const chapters = computed(() => props.graph.nodes.filter(n => n.kind === 'chapter'))
const knowledge = computed(() => props.graph.nodes.filter(n => n.kind === 'knowledge'))
const selected = computed(() => props.graph.nodes.find(n => n.id === selectedId.value))
const byId = computed(() => new Map(props.graph.nodes.map(n => [n.id, n])))
const parent = computed(() => new Map(props.graph.edges.filter(e => e.structural)
  .map(e => [e.target, e.source])))
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
  matching.value.forEach(n => addAncestors(n.id))
  return ids
})
const nodes = computed(() => props.graph.nodes.filter(n => visibleIds.value.has(n.id)))
const edges = computed(() => props.graph.edges.filter(e => {
  if (!visibleIds.value.has(e.source) || !visibleIds.value.has(e.target)) return false
  if (e.structural) return true // needed for 3D layout even when lines are hidden
  if (relationView.value === 'none') return false
  if (relationView.value === 'all') return true
  return selectedId.value !== null &&
    (e.source === selectedId.value || e.target === selectedId.value)
}))
const semanticEdges = computed(() => edges.value.filter(e => !e.structural))
const selectedRelations = computed(() => props.graph.edges.filter(e =>
  !e.structural && selectedId.value !== null &&
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
  term.value = ''
  void nextTick(() => canvas.value?.resetCamera())
}
function focusRelated(id: string) {
  // Cross-chapter edges may point outside the current filter.
  // Make the target visible before selecting it.
  if (!visibleIds.value.has(id)) {
    chapterChoice.value = 'all'
    term.value = ''
  }
  selectedId.value = id
}
function chooseChapter(chapter: GraphNode) {
  chapterChoice.value = chapter.id
  selectedId.value = null
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
        <span class="gx-brand-mark"><Maximize2 :size="18" /></span>
        <div><strong>知识星系 · 全屏探索</strong><small>{{ graph.course_name }} · {{ matching.length }} 个匹配知识点</small></div>
      </div>
      <div class="gx-toolbar-actions">
        <button type="button" title="回到全部章节和初始视角" @click="reset"><RotateCcw :size="16" /> <span>重置视角</span></button>
        <button type="button" title="关闭全屏探索" aria-label="关闭全屏探索" @click="emit('close')"><X :size="19" /> <span>退出全屏</span></button>
      </div>
    </header>

    <div class="gx-body">
      <aside class="gx-panel gx-nav" aria-label="知识图谱筛选">
        <label class="gx-field"><span><Search :size="14" /> 搜索知识点</span>
          <input v-model="term" type="search" placeholder="输入知识点名称或摘要" />
        </label>
        <label class="gx-field"><span><BookOpen :size="14" /> 按章节探索</span>
          <select v-model="chapterChoice">
            <option value="all">全部章节</option>
            <option v-for="chapter in chapters" :key="chapter.id" :value="chapter.id">{{ chapter.name }}</option>
          </select>
        </label>
        <label class="gx-checkbox">
          <input v-model="includePending" type="checkbox" /> 显示尚未审核的知识点
        </label>

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
            :class="{active:selectedId===node.id}" @click="selectedId=node.id">
            <span class="gx-point"></span><span>{{ node.name }}</span>
          </button>
          <p v-if="matching.length>150" class="gx-note">列表显示前 150 个，请搜索或选择章节缩小范围。画布中仍展示所有匹配点。</p>
        </div>
      </aside>

      <main class="gx-main">
        <Graph3DCanvas ref="canvas" :nodes="nodes" :edges="edges" :selected-id="selectedId"
          :show-relations="relationView!=='none'" :show-structure="showStructure"
          @select="selectedId=$event" />
        <div class="gx-counter">
          <span><i class="gx-symbol"></i> {{ nodes.filter(n=>n.kind==='knowledge').length }} 知识点</span>
          <span>{{ semanticEdges.length }} 条可见关系</span>
        </div>
        <p class="gx-hint">拖拽旋转 · 滚轮缩放 · 点击星球聚焦 · Esc 取消选中 / 退出</p>
      </main>

      <aside v-if="selected" class="gx-panel gx-detail" aria-label="选中节点详情">
        <div class="gx-detail-head"><span>{{ selected.kind === 'knowledge' ? '知识点详情' : '星系结构' }}</span>
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
      <span><CircleHelp :size="13" /> 节点距离只是排版效果；前置关系仅来自经过审核的记录。</span>
      <span>KNOWTRACE · GALAXY EXPLORER</span>
    </footer>
  </div>
</template>

<style scoped>
.gx-page{position:fixed;inset:0;z-index:1000;background:#07182a;color:#d6e8f3;display:flex;flex-direction:column;width:100vw;height:100vh;height:100dvh;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.gx-toolbar{height:70px;flex:none;display:flex;align-items:center;justify-content:space-between;gap:18px;padding:10px 22px;border-bottom:1px solid #25435b;background:#0c2539}
.gx-brand{display:flex;align-items:center;gap:13px;min-width:0}
.gx-brand-mark{display:grid;place-items:center;width:39px;height:39px;border-radius:12px;background:#155e63;color:#8befe4}
.gx-brand>div{display:grid;gap:4px;min-width:0}.gx-brand strong{font-size:17px;white-space:nowrap}.gx-brand small{font-size:11px;color:#86a9bc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gx-toolbar-actions{display:flex;gap:9px}
.gx-toolbar button,.gx-toggle,.gx-list-header button{border:1px solid #355a70;border-radius:9px;background:#16364b;color:#b9dce5;display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:9px 13px;font-size:12px}
.gx-toolbar button:hover,.gx-toggle:hover{background:#24506a}
.gx-body{display:flex;min-height:0;flex:1;position:relative}
.gx-panel{background:#0d2639;flex:none;width:270px;overflow:auto;padding:19px 15px;z-index:2}
.gx-nav{border-right:1px solid #2b4d61}
.gx-detail{width:310px;border-left:1px solid #2b4d61}
.gx-field{display:grid;gap:9px;margin-bottom:17px}.gx-field>span{display:flex;align-items:center;gap:7px;color:#9ebbc9;font-size:11px;font-weight:700}
.gx-field input,.gx-field select{min-width:0;width:100%;height:41px;background:#0a1b2c;border:1px solid #35556a;border-radius:9px;color:#e6f7fa;padding:9px 10px;font-size:12px}
.gx-checkbox{display:flex;align-items:center;gap:9px;color:#9db7c5;font-size:11px;line-height:1.5}.gx-checkbox input{accent-color:#3bccb8}
.gx-divider{border-top:1px solid #294458;margin:20px 0}.gx-panel h3{font-size:12px;color:#d3e8ee;margin:0 0 10px;display:flex;align-items:center;gap:8px}
.gx-note{color:#789aab;font-size:11px;line-height:1.8;margin:8px 0}
.gx-segment{display:flex;border:1px solid #315266;border-radius:9px;overflow:hidden;margin:13px 0;max-width:100%}
.gx-segment button{flex:1;min-width:0;border:0;border-right:1px solid #315266;background:#102d41;color:#93b5c5;padding:10px 5px;font-size:11px}
.gx-segment button:last-child{border:0}.gx-segment button.active{background:#166c6c;color:#d3fff3;font-weight:700}
.gx-toggle{width:100%;justify-content:flex-start}
.gx-list-header{display:flex;align-items:center;justify-content:space-between}.gx-list-header button{padding:3px 8px;font-size:10px}
.gx-results{display:grid;gap:3px;overflow:auto;max-height:min(40vh,390px)}
.gx-results>button{display:flex;align-items:center;gap:9px;width:100%;background:transparent;border:1px solid transparent;border-radius:7px;color:#b2cdd9;text-align:left;padding:9px 6px;font-size:11px}
.gx-results>button span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gx-results>button:hover,.gx-results>button.active{background:#1c4b53;border-color:#246c70}
.gx-point{flex:0 0 7px;width:7px;height:7px;border-radius:50%;background:#40c7b6}
.gx-main{min-width:0;flex:1;position:relative;overflow:hidden;background:#07182a}
.gx-main :deep(.galaxy-canvas){position:absolute;inset:0;min-height:0;height:100%}
.gx-counter{position:absolute;top:17px;left:18px;display:flex;flex-wrap:wrap;gap:8px;pointer-events:none}
.gx-counter span{background:#061b2bca;border:1px solid #2a5866;border-radius:100px;padding:8px 13px;color:#b8e7e4;font-size:11px}
.gx-symbol{width:8px;height:8px;border-radius:50%;background:#59d8be;display:inline-block;margin-right:5px}
.gx-hint{position:absolute;bottom:8px;left:18px;right:18px;text-align:center;font-size:11px;color:#89b2c3;pointer-events:none}
.gx-detail-head{display:flex;justify-content:space-between;align-items:center;color:#70d6c6;font-size:11px}
.gx-detail-head button{border:0;background:none;color:#a7c8d5;padding:6px}
.gx-detail h2{font-size:20px;color:#eaffff;line-height:1.45;margin:14px 0;overflow-wrap:anywhere}
.gx-section{color:#67d7c8;font-size:12px}.gx-summary{font-size:12px;line-height:1.9;color:#a8c5d4}
.gx-metrics{display:grid;grid-template-columns:1fr auto;gap:9px;margin:20px 0;border-top:1px solid #335164;border-bottom:1px solid #335164;padding:17px 0;font-size:11px}
.gx-metrics span{color:#84a3b7}.gx-metrics strong{color:#ddf6f0}
.gx-related{background:#102f44;border:1px solid #2a5263;border-radius:8px;padding:10px;display:flex;align-items:center;gap:8px;margin:5px 0;width:100%;text-align:left;color:#d2e6eb;font-size:11px}
.gx-related small{color:#7edacb;white-space:nowrap}.gx-related span{flex:1}
.gx-source{border-bottom:1px solid #2c495d;padding:10px 0;display:grid;gap:8px;font-size:11px;color:#c0d8e0;overflow-wrap:anywhere}
.gx-source a{color:#75dece;display:inline-flex;align-items:center;gap:5px}
.gx-primary{width:100%;display:flex;align-items:center;justify-content:center;gap:9px;background:#116a67;color:#e9fffb;border:0;border-radius:9px;padding:12px}
.gx-footer{height:32px;flex:none;padding:0 19px;display:flex;align-items:center;justify-content:space-between;gap:16px;background:#092234;border-top:1px solid #264357;color:#7194a8;font-size:10px}
.gx-footer span:first-child{display:flex;align-items:center;gap:6px}
@media(max-width:900px){.gx-panel{width:214px}.gx-detail{width:252px}.gx-toolbar{padding:10px 14px}.gx-hint{display:none}}
@media(max-width:660px){.gx-toolbar{height:62px}.gx-toolbar-actions button span{display:none}.gx-toolbar-actions button{padding:9px}.gx-brand strong{font-size:13px}.gx-nav{position:absolute;left:8px;top:9px;width:min(240px,56vw);max-height:58%;border:1px solid #30576b;border-radius:12px;background:#092439e9}.gx-detail{position:absolute;right:7px;bottom:10px;width:min(290px,70vw);max-height:58%;border:1px solid #30576b;border-radius:12px;background:#092439ef}.gx-main{width:100%}.gx-counter{top:10px;right:8px;left:auto;justify-content:flex-end}.gx-footer span:last-child{display:none}}
</style>
