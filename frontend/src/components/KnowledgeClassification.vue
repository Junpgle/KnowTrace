<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { BookOpen, CheckCircle2, FileText, LoaderCircle, Plus, RefreshCw, Search, ShieldCheck } from '@lucide/vue'
import { api, type GraphNode, type KnowledgeGraph, type PDFSourceChapterMapping, type PDFSourceChapterPreview } from '../api'

const props = defineProps<{ courseId: string }>()
const emit = defineEmits<{ updated: [] }>()
const graph = ref<KnowledgeGraph | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const message = ref('')
const search = ref('')
const chapterFilter = ref('unclassified')
const selectedId = ref('')
const chapterChoice = ref('')
const newChapter = ref('')
const preview = ref<PDFSourceChapterPreview | null>(null)
const pdfOpen = ref(false)
const mappings = ref<Record<string, string>>({})
const titles = ref<Record<string, string>>({})
const pdfConfirmed = ref(false)
let token = 0

const chapters = computed(() => (graph.value?.nodes ?? [])
  .filter(n => n.kind === 'chapter' && !n.virtual && n.chapter_id))
const points = computed(() => (graph.value?.nodes ?? []).filter(n => n.kind === 'knowledge'))
const selected = computed(() => points.value.find(n => n.id === selectedId.value))
const filtered = computed(() => points.value.filter(n => {
  if (chapterFilter.value === 'unclassified' && n.chapter_id) return false
  if (chapterFilter.value !== 'all' && chapterFilter.value !== 'unclassified'
    && n.chapter_id !== chapterFilter.value) return false
  const term = search.value.trim().toLocaleLowerCase()
  return !term || (n.name + ' ' + (n.summary ?? '')).toLocaleLowerCase().includes(term)
}))
const pdfMappings = computed<PDFSourceChapterMapping[]>(() => (preview.value?.documents ?? [])
  .flatMap(doc => {
    const value = mappings.value[doc.document_id] ?? 'skip'
    if (value === 'new') return [{ document_id: doc.document_id, chapter_title: (titles.value[doc.document_id] ?? '').trim() }]
    if (value.startsWith('chapter:')) return [{ document_id: doc.document_id, chapter_id: value.slice(8) }]
    return []
  }))
const canMap = computed(() => pdfMappings.value.length > 0
  && pdfMappings.value.every(m => Boolean(m.chapter_id || m.chapter_title)))

function choosePoint(id: string) {
  selectedId.value = id
  chapterChoice.value = points.value.find(p => p.id === id)?.chapter_id ?? ''
}
async function reload() {
  const current = ++token
  loading.value = true
  error.value = ''
  try {
    const result = await api.knowledgeGraph(props.courseId, { limit: 2000, include_pending: true })
    if (current !== token) return
    graph.value = result
    if (!result.nodes.some(n => n.id === selectedId.value)) selectedId.value = ''
    if (!selectedId.value && filtered.value.length) choosePoint(filtered.value[0].id)
    else if (selectedId.value) choosePoint(selectedId.value)
  } catch (e) {
    if (current === token) error.value = e instanceof Error ? e.message : '加载章节归类失败'
  } finally { if (current === token) loading.value = false }
}
async function createChapter() {
  const title = newChapter.value.trim()
  if (!title || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    await api.createChapter(props.courseId, { title, ordinal: chapters.value.length + 1 })
    newChapter.value = ''
    message.value = '章节已创建，可为知识点设置归属。'
    await reload()
    emit('updated')
  } catch (e) { error.value = e instanceof Error ? e.message : '创建章节失败' }
  finally { busy.value = false }
}
async function assignChapter() {
  const id = selectedId.value
  if (!selected.value || busy.value || chapterChoice.value === (selected.value.chapter_id ?? '')) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    await api.assignKnowledgeChapter(id, chapterChoice.value || null)
    await reload()
    message.value = '归属已保存。该知识点会重新进入待审核；请切换到「知识点校对与审核」核验。'
    emit('updated')
  } catch (e) { error.value = e instanceof Error ? e.message : '保存章节归属失败' }
  finally { busy.value = false }
}
async function loadPDFPreview() {
  busy.value = true; error.value = ''
  try {
    const result = await api.previewSourceChapters(props.courseId)
    preview.value = result
    mappings.value = Object.fromEntries(result.documents.map(d =>
      [d.document_id, d.matching_chapter_id ? 'chapter:' + d.matching_chapter_id : 'new']))
    titles.value = Object.fromEntries(result.documents.map(d => [d.document_id, d.suggested_title]))
    pdfConfirmed.value = false
  } catch (e) { error.value = e instanceof Error ? e.message : 'PDF 归类预览失败' }
  finally { busy.value = false }
}
async function togglePDF() {
  pdfOpen.value = !pdfOpen.value
  if (pdfOpen.value && !preview.value) await loadPDFPreview()
}
async function applyPDF() {
  if (!pdfConfirmed.value || !canMap.value || busy.value) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const result = await api.applySourceChapters(props.courseId, pdfMappings.value)
    message.value = '已归类 ' + result.assigned_points + ' 个知识点，新增 ' + result.created_chapters.length
      + ' 个章节，仍有 ' + result.remaining_unclassified + ' 个未归类。'
    await reload()
    await loadPDFPreview()
    emit('updated')
  } catch (e) { error.value = e instanceof Error ? e.message : 'PDF 批量归类失败' }
  finally { busy.value = false }
}
watch(() => props.courseId, () => {
  ++token; graph.value = null; selectedId.value = ''; chapterChoice.value = ''
  chapterFilter.value = 'unclassified'; preview.value = null; pdfOpen.value = false
  pdfConfirmed.value = false; message.value = ''; error.value = ''
  void reload()
}, { immediate: true })
</script>

<template>
  <div class="kc">
    <div class="kc-heading">
      <div><h2>章节归类工作台</h2>
        <p>按真实章节整理知识点，支持手动归属、已审核关系建议和依据 PDF 来源批量归类。</p></div>
      <button type="button" class="button button-outline button-small" :disabled="loading || busy" @click="reload">
        <RefreshCw :size="15" /> 刷新
      </button>
    </div>
    <div v-if="error" class="kc-alert" role="alert">{{ error }}</div>
    <div v-if="message" class="kc-success" role="status"><CheckCircle2 :size="16" /> {{ message }}</div>
    <div v-if="loading && !graph" class="kc-loading"><LoaderCircle class="spinning" :size="20" /> 正在读取章节与知识点…</div>
    <template v-else-if="graph">
      <div class="kc-stats">
        <span>知识点 <strong>{{ graph.stats.total_knowledge_points }}</strong></span>
        <span>未归类 <strong>{{ graph.stats.unclassified_points }}</strong></span>
        <span>有归类建议 <strong>{{ graph.stats.suggested_chapter_points }}</strong></span>
        <span>待审核 <strong>{{ graph.stats.pending_points }}</strong></span>
      </div>
      <p v-if="graph.stats.truncated" class="kc-alert" role="alert">
        仅加载 {{ graph.stats.shown_knowledge_points }} / {{ graph.stats.total_knowledge_points }} 个知识点。
        请不要把未展示的知识点视为不存在。
      </p>
      <section class="kc-actions panel">
        <div class="kc-create">
          <label>新建课程章节<input v-model="newChapter" maxlength="160" placeholder="例如：第2章 数据通信" /></label>
          <button type="button" class="button button-outline button-small" :disabled="busy || !newChapter.trim()" @click="createChapter">
            <Plus :size="16" /> 创建章节
          </button>
        </div>
        <button type="button" class="button button-outline button-small" :disabled="busy" @click="togglePDF">
          <FileText :size="16" /> {{ pdfOpen ? '收起 PDF 批量归类' : '按来源 PDF 批量归类' }}
        </button>
      </section>
      <section v-if="pdfOpen" class="kc-pdf panel">
        <div class="kc-pdf-head">
          <div><h3>依据 PDF 来源批量归类</h3><p>每份课件映射到一个章节，只处理当前未归类的知识点。</p></div>
          <button type="button" class="button button-outline button-small" :disabled="busy" @click="loadPDFPreview">
            <RefreshCw :size="15" /> 更新预览
          </button>
        </div>
        <template v-if="preview">
          <p class="kc-muted">未归类 {{ preview.unclassified_points }} 个 · 单一 PDF 来源
            {{ preview.single_pdf_points }} 个 · 多来源 {{ preview.multi_document_points }} 个 · 无来源
            {{ preview.no_source_points }} 个</p>
          <p v-if="!preview.documents.length" class="kc-muted">没有可用于归类的 PDF 来源。</p>
          <div v-for="doc in preview.documents" :key="doc.document_id" class="kc-pdf-row">
            <span><strong>{{ doc.filename }}</strong><small>关联未归类知识点 {{ doc.linked_unclassified }} 个</small></span>
            <div>
              <select v-model="mappings[doc.document_id]" :aria-label="'映射 ' + doc.filename">
                <option value="skip">暂不处理</option><option value="new">新建章节</option>
                <option v-for="chapter in chapters" :key="chapter.id"
                  :value="'chapter:' + chapter.chapter_id">{{ chapter.name }}</option>
              </select>
              <input v-if="mappings[doc.document_id] === 'new'" v-model="titles[doc.document_id]"
                maxlength="160" :aria-label="'章节名 ' + doc.filename" placeholder="章节名" />
            </div>
          </div>
          <p class="kc-muted">只有所有来源 PDF 均指向同一章节的知识点才会被归类。操作不会覆盖已有章节归属。</p>
          <label class="kc-confirm"><input v-model="pdfConfirmed" type="checkbox" />
            我已核对 PDF 与章节对应关系，同意仅归类未分章知识点。</label>
          <button type="button" class="button button-primary button-small"
            :disabled="busy || !pdfConfirmed || !canMap" @click="applyPDF">
            <ShieldCheck :size="16" /> 确认映射 {{ pdfMappings.length }} 份 PDF
          </button>
        </template>
      </section>
      <div class="kc-layout">
        <section class="kc-list panel">
          <div class="kc-search"><Search :size="16" /><input v-model="search" type="search" placeholder="搜索知识点…" /></div>
          <select v-model="chapterFilter" aria-label="筛选章节">
            <option value="unclassified">未归类知识点</option><option value="all">全部知识点</option>
            <option v-for="ch in chapters" :key="ch.id" :value="ch.chapter_id">{{ ch.name }}</option>
          </select>
          <p class="kc-muted">匹配 {{ filtered.length }} 个知识点</p>
          <div class="kc-results">
            <p v-if="!filtered.length" class="kc-muted">暂无匹配项。可切换章节或搜索条件。</p>
            <button v-for="p in filtered" :key="p.id" type="button"
              :class="{active:selectedId === p.id}" @click="choosePoint(p.id)">
              <span>{{ p.name }}</span>
              <small>{{ p.review_status === 'approved' ? '已通过' : p.review_status === 'rejected' ? '已驳回' : '待审核' }}</small>
            </button>
          </div>
        </section>
        <section class="kc-detail panel">
          <template v-if="selected">
            <p class="eyebrow">KNOWLEDGE CLASSIFICATION</p>
            <h3>{{ selected.name }}</h3>
            <p class="kc-muted">{{ selected.summary || '暂无知识点摘要' }}</p>
            <p class="kc-muted">当前归属：{{ chapters.find(c => c.chapter_id === selected?.chapter_id)?.name || '未归类' }}
              · 审核状态：{{ selected.review_status === 'approved' ? '已通过' : '待处理' }}</p>
            <div v-if="!selected.chapter_id && selected.suggested_chapter_id" class="kc-suggestion">
              已审核关联节点推荐归入「{{ chapters.find(c => c.chapter_id === selected?.suggested_chapter_id)?.name || '目标章节' }}」
              （支持 {{ selected.suggestion_support }} / {{ selected.suggestion_total }}）。
              <button type="button" class="button button-outline button-small"
                @click="chapterChoice = selected.suggested_chapter_id ?? ''">采用建议</button>
              <small>仅为人工整理建议，不代表已审核的先修依赖。</small>
            </div>
            <label>调整归属章节
              <select v-model="chapterChoice">
                <option value="">未归类</option>
                <option v-for="ch in chapters" :key="ch.id" :value="ch.chapter_id">{{ ch.name }}</option>
              </select>
            </label>
            <button type="button" class="button button-primary button-small"
              :disabled="busy || chapterChoice === (selected.chapter_id ?? '')" @click="assignChapter">
              <BookOpen :size="16" /> 保存章节归属
            </button>
            <p class="kc-muted">手动变更章节后知识点会重新进入待审核，需返回「知识点校对与审核」复核；关联题库和学习记录不会被清空。</p>
          </template>
          <div v-else class="kc-empty">从左侧选择知识点以编辑章节归属。</div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.kc{display:grid;gap:15px;min-width:0}
.kc-heading,.kc-pdf-head{display:flex;justify-content:space-between;align-items:flex-start;gap:15px;flex-wrap:wrap}
.kc-heading h2{font-size:21px;color:#294e59;margin:0 0 7px}
.kc-heading p,.kc-pdf-head p,.kc-muted{font-size:12px;color:#78939c;line-height:1.75;margin:0}
.kc-stats{display:flex;gap:10px;flex-wrap:wrap}
.kc-stats span{background:#eff8f5;border-radius:9px;padding:11px 16px;font-size:12px;color:#518378}
.kc-stats strong{font-size:16px;color:#176f62}
.kc-alert,.kc-success{padding:12px;border-radius:9px;font-size:12px}
.kc-alert{background:#fff3ed;color:#aa5947}.kc-success{background:#edf8f3;color:#16816b;display:flex;align-items:center;gap:8px}
.kc-loading{display:flex;align-items:center;gap:9px;padding:30px}
.kc-actions{padding:16px;display:flex;align-items:end;justify-content:space-between;flex-wrap:wrap;gap:12px}
.kc-create{display:flex;align-items:end;gap:9px;flex-wrap:wrap}
.kc label,.kc-create label{display:grid;gap:8px;font-size:12px;color:#526f7a}
.kc input,.kc select{min-height:38px;min-width:0;padding:8px 10px;border:1px solid #dce8ea;border-radius:8px;background:white;color:#385562;font-size:12px}
.kc-pdf{padding:18px;display:grid;gap:13px}
.kc-pdf-head h3{margin:0 0 6px;font-size:17px}
.kc-pdf-row{display:grid;grid-template-columns:minmax(170px,1fr) minmax(230px,1fr);gap:14px;padding:11px 0;border-top:1px solid #e7eff0}
.kc-pdf-row>span,.kc-pdf-row>div{display:grid;gap:6px;min-width:0}
.kc-pdf-row strong{font-size:12px;overflow-wrap:anywhere}
.kc-pdf-row small{font-size:11px;color:#78939c}
.kc-confirm{display:flex!important;flex-direction:row;align-items:center}
.kc-confirm input{min-height:0;accent-color:#159c8c}
.kc-layout{display:grid;grid-template-columns:minmax(250px,.9fr) minmax(0,1.6fr);gap:13px}
.kc-list,.kc-detail{padding:18px;min-width:0;display:grid;align-content:start;gap:12px}
.kc-search{display:flex;align-items:center;gap:8px;border:1px solid #dce8ea;border-radius:8px;padding:0 10px}
.kc-search input{border:0;flex:1;width:100%}
.kc-results{display:grid;gap:5px;max-height:540px;overflow:auto}
.kc-results button{display:flex;justify-content:space-between;gap:10px;align-items:center;text-align:left;padding:12px;border:1px solid transparent;background:#f8fafb;border-radius:8px;cursor:pointer}
.kc-results button.active{background:#e7f7f0;border-color:#8cd4bd}
.kc-results button span{color:#305c68;font-size:12px}
.kc-results button small{white-space:nowrap;font-size:10px;color:#709b91}
.kc-detail h3{font-size:19px;color:#315565;margin:0}
.kc-detail select{width:100%}
.kc-suggestion{display:grid;gap:10px;background:#edf8f4;border-radius:9px;padding:14px;color:#276d60;font-size:12px}
.kc-suggestion button{justify-self:start}
.kc-suggestion small{color:#689084}
.kc-detail>.button{justify-self:start}
.kc-empty{padding:50px 10px;text-align:center;font-size:13px;color:#839ca5}
@media(max-width:760px){.kc-layout,.kc-pdf-row{grid-template-columns:1fr}}
</style>
