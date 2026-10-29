<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  Activity, ArrowRight, BookOpen, CircleHelp, FileText, List,
  LoaderCircle, Network, Orbit, RefreshCw, RotateCcw, Search,
  ShieldCheck, Sparkles, X,
} from '@lucide/vue'
import Graph3DCanvas from './Graph3DCanvas.vue'
import RelationStudio from './RelationStudio.vue'
import {
  api, sourcePreviewUrl,
  type GraphEdge, type GraphNode, type KnowledgeGraph, type KnowledgePointDetail,
} from '../api'

const props = defineProps<{ courseId: string; courseName: string }>()
const emit = defineEmits<{ openBank: []; openPractice: [] }>()
const graph = ref<KnowledgeGraph | null>(null)
const detail = ref<KnowledgePointDetail | null>(null)
const loading = ref(true)
const detailLoading = ref(false)
const error = ref('')
const selectedId = ref<string | null>(null)
const filter = ref<'all' | 'weak' | 'unlearned' | 'mastered'>('all')
const query = ref('')
const selectedChapter = ref('all')
const includePending = ref(true)
const showRelations = ref(true)
const display = ref<'3d' | 'list'>('3d')
const showStudio = ref(false)
const studioAnchor = ref<HTMLDivElement | null>(null)
async function toggleStudio() {
  showStudio.value = !showStudio.value
  if (showStudio.value) {
    await nextTick()
    studioAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
const newChapter = ref('')
const chapterChoice = ref('')
const chapterBusy = ref(false)
const chapterMessage = ref('')
const canvas = ref<InstanceType<typeof Graph3DCanvas> | null>(null)
let requestToken = 0
let detailToken = 0

const chapters = computed(() => graph.value?.nodes.filter(n => n.kind === 'chapter') ?? [])
const allKnowledge = computed(() => graph.value?.nodes.filter(n => n.kind === 'knowledge') ?? [])
const chapterNames = computed(() => new Map(chapters.value.filter(c => !c.virtual).map(c => [c.chapter_id, c.name])))
const selectedNode = computed(() => graph.value?.nodes.find(n => n.id === selectedId.value) ?? null)
const relationship = computed(() => graph.value?.edges.filter(
  e => !e.structural && (e.source === selectedId.value || e.target === selectedId.value),
) ?? [])
const scored = computed(() => allKnowledge.value.filter(n => n.mastery !== null))
const meanMastery = computed<number | null>(() => scored.value.length
  ? scored.value.reduce((sum, n) => sum + (n.mastery ?? 0), 0) / scored.value.length
  : null)

const structuralParents = computed(() => {
  const parents = new Map<string, string>()
  graph.value?.edges.filter(e => e.structural).forEach(e => parents.set(e.target, e.source))
  return parents
})
function inChapter(node: GraphNode, id: string) {
  if (id === 'all') return true
  let current = node.id
  const seen = new Set<string>()
  while (current && !seen.has(current)) {
    if (current === id) return true
    seen.add(current)
    current = structuralParents.value.get(current) ?? ''
  }
  return false
}
const matching = computed(() => {
  const term = query.value.trim().toLocaleLowerCase()
  return allKnowledge.value.filter(node => {
    if (!includePending.value && node.review_status !== 'approved') return false
    if (!inChapter(node, selectedChapter.value)) return false
    if (term && !(node.name + ' ' + (node.summary ?? '')).toLocaleLowerCase().includes(term)) return false
    if (filter.value === 'weak' && !(node.mastery !== null && node.mastery < 0.5)) return false
    if (filter.value === 'unlearned' && node.mastery !== null) return false
    if (filter.value === 'mastered' && !(node.mastery !== null && node.mastery >= 0.8)) return false
    return true
  })
})
const visibleIds = computed(() => {
  const ids = new Set<string>()
  const root = graph.value?.nodes.find(n => n.kind === 'course')
  if (root) ids.add(root.id)
  const addParents = (id: string) => {
    let current = id
    const seen = new Set<string>()
    while (current && !seen.has(current)) {
      ids.add(current)
      seen.add(current)
      current = structuralParents.value.get(current) ?? ''
    }
  }
  if (selectedChapter.value === 'all' && filter.value === 'all' && !query.value.trim())
    chapters.value.forEach(n => addParents(n.id))
  matching.value.forEach(node => addParents(node.id))
  if (selectedChapter.value !== 'all') addParents(selectedChapter.value)
  return ids
})
const visibleNodes = computed(() => (graph.value?.nodes ?? []).filter(n => visibleIds.value.has(n.id)))
const visibleEdges = computed(() => (graph.value?.edges ?? [])
  .filter(e => visibleIds.value.has(e.source) && visibleIds.value.has(e.target)))
const visibleRelations = computed(() => visibleEdges.value.filter(e => !e.structural).length)
const related = computed(() => relationship.value.map(edge => ({
  id: edge.source === selectedId.value ? edge.target : edge.source,
  edge,
  label: graph.value?.nodes.find(n => n.id ===
    (edge.source === selectedId.value ? edge.target : edge.source))?.name ?? '未知知识点',
})))

function statusClass(node: GraphNode) {
  if (node.kind !== 'knowledge') return node.kind
  if (node.review_status !== 'approved') return 'pending'
  if (node.mastery === null) return 'untested'
  return node.mastery >= 0.8 ? 'mastered' : node.mastery >= 0.5 ? 'growing' : 'weak'
}
function statusLabel(node: GraphNode) {
  if (node.kind !== 'knowledge') return '课程结构'
  if (node.review_status !== 'approved') return '未审核'
  if (node.mastery === null) return '尚未测评'
  return node.mastery >= 0.8 ? '已掌握' : node.mastery >= 0.5 ? '学习中' : '待巩固'
}
function percent(value: number | null) {
  return value === null ? '尚未测评' : Math.round(value * 100) + '%'
}
function relationName(type: GraphEdge['type']) {
  const names: Record<GraphEdge['type'], string> = {
    contains: '属于', prerequisite_required: '必要前置',
    prerequisite_recommended: '建议前置', related: '相关',
    uses: '使用', compares: '对比',
  }
  return names[type]
}
async function refresh() {
  const token = ++requestToken
  loading.value = true
  error.value = ''
  try {
    const response = await api.knowledgeGraph(props.courseId, { limit: 2000, include_pending: true })
    if (token !== requestToken) return
    graph.value = response
    if (selectedId.value && !response.nodes.some(n => n.id === selectedId.value))
      selectedId.value = null
  } catch (e) {
    if (token === requestToken) error.value = e instanceof Error ? e.message : '图谱加载失败'
  } finally { if (token === requestToken) loading.value = false }
}
async function select(id: string) {
  const token = ++detailToken
  selectedId.value = id
  detail.value = null
  const node = graph.value?.nodes.find(n => n.id === id)
  if (node?.kind !== 'knowledge') { detailLoading.value = false; return }
  detailLoading.value = true
  try {
    const response = await api.knowledgePoint(id)
    if (token === detailToken && id === selectedId.value) detail.value = response
  } catch { /* still show point metadata and actions */ }
  finally { if (token === detailToken) detailLoading.value = false }
}
async function createNewChapter() {
  const title = newChapter.value.trim()
  if (!title || chapterBusy.value) return
  chapterBusy.value = true
  chapterMessage.value = ''
  try {
    await api.createChapter(props.courseId, { title, ordinal: chapters.value.length + 1 })
    newChapter.value = ''
    await refresh()
    chapterMessage.value = '章节已创建。可选择知识点并调整归属。'
  } catch (e) { chapterMessage.value = e instanceof Error ? e.message : '创建章节失败' }
  finally { chapterBusy.value = false }
}
async function moveToChapter() {
  const point = selectedNode.value
  if (!point || point.kind !== 'knowledge' || chapterBusy.value) return
  const id = point.id
  chapterBusy.value = true
  chapterMessage.value = ''
  try {
    await api.assignKnowledgeChapter(id, chapterChoice.value || null)
    await refresh()
    await select(id)
    chapterMessage.value = '章节归属已更新。根据现有审核规则，知识点需在“题库管理”中重新审核。'
  } catch (e) { chapterMessage.value = e instanceof Error ? e.message : '归属调整失败' }
  finally { chapterBusy.value = false }
}
watch(selectedNode, point => {
  if (point?.kind === 'knowledge') chapterChoice.value = point.chapter_id || ''
})
async function resetView() {
  selectedId.value = null
  selectedChapter.value = 'all'
  filter.value = 'all'
  query.value = ''
  await nextTick()
  canvas.value?.resetCamera()
}
watch(() => props.courseId, () => {
  ++detailToken
  selectedId.value = null
  selectedChapter.value = 'all'
  query.value = ''
  filter.value = 'all'
  detail.value = null
  void refresh()
}, { immediate: true })
</script>

<template>
  <div class="kg-page">
    <header class="kg-heading">
      <div><p class="eyebrow">KNOWLEDGE UNIVERSE</p>
        <h1>知识星系<span class="heading-dot">.</span></h1>
        <p class="section-description">以真实知识为星球，以经过审核的关联为星际航路。</p>
      </div>
      <div class="kg-top-actions">
        <button type="button" class="button button-primary button-small" @click="toggleStudio"><Network :size="16" /> {{ showStudio ? "收起关系工作台" : "提取 / 审核关系" }}</button>
        <button type="button" class="button button-outline button-small" :disabled="loading" @click="refresh"><RefreshCw :size="16" /> 刷新</button>
        <button type="button" class="button button-outline button-small" @click="resetView"><RotateCcw :size="16" /> 返回总览</button>
      </div>
    </header>
    <div v-if="error" class="kg-error" role="alert">{{ error }}</div>
    <div v-if="loading" class="kg-loading panel"><LoaderCircle class="spinning" :size="30" /> 正在从本地数据库构建知识星系…</div>
    <template v-else-if="graph">
      <section class="kg-stats" aria-label="图谱统计">
        <div><span>课程星核</span><strong>{{ graph.course_name }}</strong></div>
        <div><span>知识星球</span><strong>{{ graph.stats.shown_knowledge_points }} 个</strong></div>
        <div><span>已审核关系</span><strong>{{ graph.stats.approved_semantic_edges }} 条</strong></div>
        <div><span>已有 BKT 观测</span><strong>{{ graph.stats.mastery_estimated_points }} 个</strong></div>
      </section>
      <p v-if="graph.stats.unclassified_points" class="kg-muted">
        未归类 {{ graph.stats.unclassified_points }} 个；其中 {{ graph.stats.suggested_chapter_points }} 个具有多个已审核关联节点支持的章节建议。
        关系提取不会自动填写章节，选择知识点后可人工确认建议。
      </p>
      <p v-if="graph.stats.truncated" class="kg-error">
        本次仅加载 {{ graph.stats.shown_knowledge_points }} / {{ graph.stats.total_knowledge_points }} 个知识点；
        后续将增加分页和分章节按需加载，不应把未加载知识点视为不存在。
      </p>
      <div class="kg-layout">
        <aside class="kg-filter panel">
          <h2><Search :size="17" /> 星系导航</h2>
          <label class="kg-field"><span>知识点搜索</span>
            <input v-model="query" type="search" placeholder="搜索知识点名称…" />
          </label>
          <label class="kg-field"><span>章节</span>
            <select v-model="selectedChapter">
              <option value="all">全部章节</option>
              <option v-for="chapter in chapters" :key="chapter.id" :value="chapter.id">{{ chapter.name }}</option>
            </select>
          </label>
          <h2 class="kg-subtitle"><BookOpen :size="17" /> 章节整理</h2>
          <label class="kg-field"><span>新增真实课程章节</span>
            <input v-model="newChapter" maxlength="160" placeholder="例如：第2章 数据通信" />
          </label>
          <button type="button" class="button button-outline button-small" :disabled="chapterBusy || !newChapter.trim()" @click="createNewChapter">创建章节</button>
          <p v-if="chapterMessage" class="kg-muted" role="status">{{ chapterMessage }}</p>
          <h2 class="kg-subtitle"><Activity :size="17" /> 学习状态</h2>
          <div class="kg-filters">
            <button type="button" :class="{active:filter==='all'}" @click="filter='all'">全部</button>
            <button type="button" :class="{active:filter==='weak'}" @click="filter='weak'">待巩固</button>
            <button type="button" :class="{active:filter==='unlearned'}" @click="filter='unlearned'">未测评</button>
            <button type="button" :class="{active:filter==='mastered'}" @click="filter='mastered'">已掌握</button>
          </div>
          <label class="kg-check"><input v-model="includePending" type="checkbox" />显示未审核知识点</label>
          <label class="kg-check"><input v-model="showRelations" type="checkbox" />显示已审核关系线</label>
          <p class="kg-count">已筛选 {{ matching.length }} 个知识点 · {{ visibleRelations }} 条已审核关系</p>
          <h2 class="kg-subtitle"><Network :size="17" /> 定位知识星球</h2>
          <div v-if="!matching.length" class="kg-muted">没有匹配的知识点，可调整筛选条件。</div>
          <div v-else class="kg-navigation">
            <button v-for="node in matching.slice(0,100)" :key="node.id" type="button"
              :class="{active:selectedId===node.id}" @click="select(node.id)">
              <i class="kg-dot" :class="statusClass(node)"></i>
              <span :title="node.name">{{ node.name }}</span><small>{{ node.mastery===null?'—':Math.round(node.mastery*100)+'%' }}</small>
            </button>
            <div v-if="matching.length>100" class="kg-muted">仅列出前 100 个结果，请通过搜索定位。</div>
          </div>
        </aside>

        <section class="kg-stage">
          <div class="kg-stage-head"><span><Orbit :size="16" /> {{ display==='3d' ? '3D 宇宙视图' : '列表视图' }}</span>
            <div>
              <button type="button" :class="{active:display==='3d'}" @click="display='3d'"><Orbit :size="14" /> 3D</button>
              <button type="button" :class="{active:display==='list'}" @click="display='list'"><List :size="14" /> 列表</button>
            </div>
          </div>
          <Graph3DCanvas v-if="display==='3d'" ref="canvas" :nodes="visibleNodes" :edges="visibleEdges"
            :selected-id="selectedId" :show-relations="showRelations" @select="select" />
          <div v-else class="kg-list-mode">
            <p v-if="!matching.length" class="kg-muted">暂无匹配知识点。</p>
            <button v-for="node in matching" :key="node.id" type="button"
              :class="{active:selectedId===node.id}" @click="select(node.id)">
              <i class="kg-dot" :class="statusClass(node)"></i>
              <span><strong>{{ node.name }}</strong><small>{{ node.summary || '暂无摘要' }}</small></span>
              <em>{{ statusLabel(node) }}</em>
            </button>
          </div>
          <div class="kg-stage-foot"><span>拖动旋转 · 滚轮缩放 · 点击选中</span><span>真实数据库驱动</span></div>
        </section>

        <aside class="kg-detail panel">
          <div v-if="!selectedNode" class="kg-idle">
            <div class="kg-idle-icon"><Sparkles :size="30" /></div>
            <h2>探索知识宇宙</h2>
            <p>点击星球查看知识点详情、原始课件及经过审核的前置关系。</p>
            <p v-if="meanMastery!==null" class="kg-tip">已测评知识点的平均 BKT 估计值：{{ percent(meanMastery) }}</p>
          </div>
          <template v-else>
            <div class="kg-detail-head">
              <small>{{ selectedNode.kind==='knowledge'?'KNOWLEDGE PLANET':selectedNode.kind==='chapter'?'CHAPTER SYSTEM':'COURSE CORE' }}</small>
              <button type="button" aria-label="关闭详情" @click="selectedId=null;detail=null"><X :size="18" /></button>
            </div>
            <span class="kg-status"><i class="kg-dot" :class="statusClass(selectedNode)"></i>{{ statusLabel(selectedNode) }}</span>
            <h2>{{ selectedNode.name }}</h2>
            <p class="kg-description">{{ selectedNode.summary || (selectedNode.kind==='knowledge'?'该知识点暂无摘要。':'课程与章节结构来自现有数据库。') }}</p>
            <template v-if="selectedNode.kind==='knowledge'">
              <div class="kg-metrics">
                <div><span>BKT 估计</span><strong>{{ percent(selectedNode.mastery) }}</strong></div>
                <div><span>作答观测</span><strong>{{ selectedNode.observations }} 次</strong></div>
                <div><span>已审核题目</span><strong>{{ selectedNode.question_count }} 道</strong></div>
                <div><span>证据来源</span><strong>{{ selectedNode.source_count }} 条</strong></div>
              </div>
              <p class="kg-muted">未测评不代表掌握度为 0%。</p>
              <h3><BookOpen :size="17" /> 调整章节归属</h3>
              <div v-if="!selectedNode.chapter_id && selectedNode.suggested_chapter_id" class="kg-tip">
                建议归入「{{ chapterNames.get(selectedNode.suggested_chapter_id) }}」：
                {{ selectedNode.suggestion_support }} / {{ selectedNode.suggestion_total }} 个已审核关联的已归类知识点支持。
                <button type="button" class="button button-outline button-small"
                  @click="chapterChoice = selectedNode.suggested_chapter_id!">采纳建议（需保存）</button>
                <p>建议不等于正式归属；保存后仍需重新审核该知识点。</p>
              </div>
              <label class="kg-field"><span>选择归属章节（仅调整结构，不自动生成知识依赖）</span>
                <select v-model="chapterChoice">
                  <option value="">未归类</option>
                  <option v-for="chapter in chapters.filter(x => !x.virtual)" :key="chapter.id" :value="chapter.chapter_id">{{ chapter.name }}</option>
                </select>
              </label>
              <button type="button" class="button button-outline button-small"
                :disabled="chapterBusy || chapterChoice === (selectedNode.chapter_id || '')"
                @click="moveToChapter">保存章节归属</button>
              <p class="kg-muted">调整章节后知识点重新进入待审核，不会自动保留原审核结果。</p>
              <h3><ShieldCheck :size="17" /> 已审核的知识关系</h3>
              <p v-if="!related.length" class="kg-muted">暂无已审核的前置或相关关系。系统不会凭空间邻近推测它们。</p>
              <button v-for="item in related" :key="item.edge.id" class="kg-related" @click="select(item.id)">
                <span><small>{{ relationName(item.edge.type) }}</small><strong>{{ item.label }}</strong>
                  <small v-if="item.edge.rationale">{{ item.edge.rationale }}</small></span>
                <ArrowRight :size="15" />
              </button>
              <h3><FileText :size="17" /> 课件来源</h3>
              <p v-if="detailLoading" class="kg-muted">正在读取证据…</p>
              <p v-else-if="!detail?.sources?.length" class="kg-muted">暂无可展示的课件片段。</p>
              <div v-for="source in detail?.sources ?? []" :key="source.chunk_id" class="kg-source">
                <strong>{{ source.original_filename }} · 第 {{ source.page_start }} 页</strong>
                <small v-if="source.content_kind==='figure_description'">AI 图示描述 · 原图需复核</small>
                <p>{{ source.evidence_note || '暂无来源注释' }}</p>
                <a v-if="source.original_filename.toLowerCase().endsWith('.pdf')"
                  :href="sourcePreviewUrl(source)" target="_blank" rel="noopener noreferrer">查看 PDF 原页 <ArrowRight :size="14" /></a>
              </div>
            </template>
            <template v-else-if="selectedNode.kind==='chapter'">
              <p class="kg-muted">该章节包含 {{ allKnowledge.filter(n=>inChapter(n,selectedNode!.id)).length }} 个知识点。</p>
              <button type="button" class="button button-primary" @click="selectedChapter=selectedNode.id">仅显示本章节</button>
            </template>
            <div class="kg-actions">
              <button type="button" class="button button-primary" @click="emit('openBank')"><BookOpen :size="16" /> 题库管理</button>
              <button type="button" class="button button-outline" @click="emit('openPractice')">课程练习 <ArrowRight :size="16" /></button>
            </div>
          </template>
        </aside>
      </div>
      <div v-if="showStudio" ref="studioAnchor" class="kg-studio-anchor">
        <RelationStudio :course-id="courseId" :knowledge="allKnowledge"
          :focus-id="selectedId" @updated="refresh" @select="select" />
      </div>
      <div class="kg-legend">
        <span><i class="kg-dot mastered"></i>已掌握 ≥80%</span>
        <span><i class="kg-dot growing"></i>学习中 50–79%</span>
        <span><i class="kg-dot weak"></i>待巩固 &lt;50%</span>
        <span><i class="kg-dot untested"></i>未测评</span>
        <span><i class="kg-dot pending"></i>待审核</span>
      </div>
      <p class="kg-disclaimer"><CircleHelp :size="16" />
        章节连线仅表示真实归属；金色连线仅表示已审核的知识关系。星球的距离不代表知识相似度。
      </p>
    </template>
  </div>
</template>

<style scoped>
.kg-page{display:grid;gap:20px;min-width:0}
.kg-studio-anchor{scroll-margin-top:22px;min-width:0}
.kg-heading{display:flex;justify-content:space-between;align-items:center;gap:17px;flex-wrap:wrap}
.kg-heading h1{font-size:30px;margin:5px 0}
.kg-top-actions{display:flex;gap:8px;flex-wrap:wrap}
.kg-error{padding:13px 16px;border:1px solid #f0d1b8;background:#fff7ef;border-radius:11px;color:#9c6545;font-size:12px}
.kg-loading{min-height:360px;display:flex;justify-content:center;align-items:center;gap:14px;color:#208e83;font-size:13px}
.kg-stats{display:grid;grid-template-columns:1.5fr repeat(3,1fr);gap:11px}
.kg-stats>div{background:#fff;border:1px solid #e6eef1;border-radius:12px;padding:14px 18px;display:grid;gap:8px;min-width:0}
.kg-stats span{font-size:10px;color:#90a3ad}
.kg-stats strong{font-size:17px;color:#274353;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kg-layout{display:grid;grid-template-columns:minmax(176px,.75fr) minmax(335px,2fr) minmax(211px,.9fr);gap:12px;align-items:stretch;min-height:585px}
.kg-filter,.kg-detail{padding:19px 15px;min-width:0}
.kg-filter h2,.kg-detail h3{display:flex;align-items:center;gap:8px;font-size:12px;color:#315465;margin:4px 0 18px}
.kg-filter h2 svg,.kg-detail h3 svg{color:#149e8d}
.kg-filter h2.kg-subtitle{margin:24px 0 14px}
.kg-field{display:grid;gap:8px;margin-bottom:15px}
.kg-field span{font-size:10px;font-weight:750;color:#839ba5}
.kg-field input,.kg-field select{width:100%;height:43px;min-width:0;border:1px solid #dce8ea;border-radius:9px;padding:9px;color:#2d5261;background:#fff;font-size:11px}
.kg-filters{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}
.kg-filters button{background:#f7fafb;border:1px solid #e0eaec;border-radius:8px;padding:9px 4px;color:#7793a1;font-size:10px}
.kg-filters button.active{background:#e4f8f1;border-color:#a3e3d3;color:#118c7e;font-weight:800}
.kg-check{display:flex;align-items:center;gap:8px;font-size:10px;color:#77939b;margin-top:14px}
.kg-check input{width:15px;height:15px;accent-color:#17a28f}
.kg-count{background:#eef8f5;padding:12px;color:#287f74;border-radius:9px;font-size:10px;line-height:1.7;margin:20px 0}
.kg-navigation{max-height:285px;overflow:auto;display:grid;align-content:start;gap:3px}
.kg-navigation button{display:flex;align-items:center;gap:8px;border:1px solid transparent;background:none;border-radius:7px;padding:9px 5px;text-align:left;min-width:0}
.kg-navigation button.active,.kg-navigation button:hover{background:#edfaf6;border-color:#d5ebe5}
.kg-navigation button span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;font-size:11px;color:#3f6673}
.kg-navigation button small{font-size:10px;color:#9ab0b9}
.kg-muted{font-size:11px;color:#90a2ad;line-height:1.85}
.kg-dot{display:inline-block;width:10px;height:10px;flex:0 0 10px;border-radius:50%;background:#9cb2c6}
.kg-dot.course{background:#2ac8b0}.kg-dot.chapter{background:#5e9ee7}
.kg-dot.mastered{background:#27c199}.kg-dot.growing{background:#66b6e9}
.kg-dot.weak{background:#f0a267}.kg-dot.untested{background:#9cb2c6}.kg-dot.pending{background:#858b98}
.kg-stage{border:1px solid #244053;border-radius:16px;overflow:hidden;background:#071b30;display:flex;flex-direction:column;min-width:0}
.kg-stage-head{display:flex;justify-content:space-between;align-items:center;gap:9px;padding:11px 15px;color:#c3e3e9;background:#102c42;font-size:11px}
.kg-stage-head>span,.kg-stage-head>div{display:flex;align-items:center;gap:6px}
.kg-stage-head button{border:1px solid #2c5061;background:#16384b;color:#a4c2cd;border-radius:7px;padding:7px 10px;font-size:10px;display:flex;gap:5px;align-items:center}
.kg-stage-head button.active{border-color:#26998e;background:#1d5156;color:#baf5e8}
.kg-stage :deep(.galaxy-canvas){flex:1}
.kg-stage-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:12px 15px;background:#102c42;color:#8cacbd;font-size:9px}
.kg-list-mode{padding:12px;max-height:540px;overflow:auto;flex:1}
.kg-list-mode button{display:flex;align-items:center;gap:11px;width:100%;margin-bottom:8px;text-align:left;border:1px solid #2d5062;background:#123249;color:#c9e2ed;border-radius:9px;padding:13px}
.kg-list-mode button.active,.kg-list-mode button:hover{border-color:#55bfae;background:#1c4351}
.kg-list-mode button>span{flex:1;min-width:0;display:grid;gap:5px}
.kg-list-mode strong{font-size:12px}
.kg-list-mode small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:10px;color:#8eafbb}
.kg-list-mode em{font-size:10px;font-style:normal}
.kg-detail-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.kg-detail-head small{font-size:9px;color:#179b8e;letter-spacing:1.4px;font-weight:800}
.kg-detail-head button{border:0;background:none;color:#7d9ca8}
.kg-status{display:flex;align-items:center;gap:8px;color:#809da6;font-size:11px}
.kg-detail h2{font-size:19px;line-height:1.6;margin:13px 0;overflow-wrap:anywhere}
.kg-description{font-size:11px;color:#7893a1;line-height:1.9}
.kg-metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:15px}
.kg-metrics>div{padding:12px 9px;background:#f1f8f7;border-radius:9px;display:grid;gap:9px}
.kg-metrics span{font-size:9px;color:#88a0a9}.kg-metrics strong{font-size:13px;color:#24596a}
.kg-detail h3{margin:24px 0 14px;border-top:1px solid #e7eef0;padding-top:20px}
.kg-related{border:1px solid #e0efeb;border-radius:9px;background:#f6fbf9;padding:10px;width:100%;display:flex;justify-content:space-between;gap:10px;text-align:left;color:#2b8579;margin-bottom:8px}
.kg-related>span{display:grid;gap:5px}.kg-related strong{font-size:11px}.kg-related small{font-size:10px;color:#799e99;line-height:1.7}
.kg-source{border:1px solid #e2ecef;background:#f7fafb;padding:12px;border-radius:9px;display:grid;gap:9px;margin:8px 0}
.kg-source strong{font-size:10px;color:#436b7a;overflow-wrap:anywhere}
.kg-source p,.kg-source small{font-size:10px;line-height:1.8;color:#829ba8;overflow-wrap:anywhere;margin:0}
.kg-source a{display:flex;align-items:center;gap:7px;color:#128f82;font-size:10px;font-weight:800}
.kg-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:23px}
.kg-actions .button{padding:0 11px}
.kg-idle{text-align:center;display:grid;justify-items:center;align-content:center;gap:12px;min-height:330px}
.kg-idle-icon{background:#e3f6f1;color:#16a08e;width:66px;height:66px;display:grid;place-items:center;border-radius:19px}
.kg-idle h2{font-size:15px;margin:0}.kg-idle p{font-size:11px;line-height:1.9;color:#819ba5}
.kg-idle p.kg-tip{color:#1a9c89;background:#edf8f5;border-radius:8px;padding:11px}
.kg-legend{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.kg-legend span{display:flex;align-items:center;gap:7px;color:#8198a4;font-size:10px}
.kg-disclaimer{display:flex;gap:9px;align-items:flex-start;font-size:10px;color:#96a8b0;line-height:1.8;margin:0}
.kg-disclaimer svg{flex:0 0 16px}
@media(max-width:1300px){.kg-layout{grid-template-columns:minmax(185px,.7fr) minmax(360px,2fr)}.kg-detail{grid-column:1/-1}}
@media(max-width:950px){.kg-layout{grid-template-columns:1fr}.kg-detail{grid-column:auto}.kg-navigation{max-height:150px}}
@media(max-width:650px){.kg-stats{grid-template-columns:repeat(2,1fr)}.kg-heading h1{font-size:26px}.kg-stage-foot span:last-child{display:none}.kg-stage :deep(.galaxy-canvas){min-height:390px}}
</style>
