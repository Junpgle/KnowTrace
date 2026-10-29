<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  AlertCircle, ArrowRight, BookOpenCheck, CheckCircle2, ChevronLeft, ChevronRight,
  ClipboardCheck, CloudUpload, File, FileCheck2, FileText, Image,
  LoaderCircle, RefreshCw, ScanSearch, Sparkles, Upload, X,
} from '@lucide/vue'
import { api, documentPagePreviewUrl, type AIProviderId, type AIProviderStatus,
  type CourseDocument, type DocumentChunk, type DocumentQuality,
  type ExtractKnowledgeResult } from '../api'

const props = defineProps<{ courseId: string; courseName: string }>()
const emit = defineEmits<{ updated: []; openReview: []; openExternal: [documentId: string] }>()
type LocalFile = { id: number; name: string; status: 'queued' | 'uploading' | 'uploaded' | 'failed'; error?: string }
const maxBytes = 30 * 1024 * 1024
const files = ref<CourseDocument[]>([])
const selectedId = ref('')
const uploading = ref(false)
const fetching = ref(true)
const dropped = ref(false)
const error = ref('')
const success = ref('')
const uploadQueue = ref<LocalFile[]>([])
const selectedQuality = ref<DocumentQuality | null>(null)
const qualityBusy = ref(false)
const chunks = ref<DocumentChunk[]>([])
const chunkOffset = ref(0)
const chunkMore = ref(true)
const chunkBusy = ref(false)
const activePage = ref(1)
const rangeStart = ref(1)
const rangeEnd = ref(1)
const chosenProvider = ref<AIProviderId>('ollama')
const aiProviders = ref<AIProviderStatus | null>(null)
const visual = ref(false)
const extracting = ref(false)
const extractResult = ref<ExtractKnowledgeResult | null>(null)
const input = ref<HTMLInputElement | null>(null)
let loadNumber = 0
let documentNumber = 0
let uploadNumber = 0

const selected = computed(() => files.value.find(f => f.id === selectedId.value) ?? null)
const isPdf = computed(() => selected.value?.original_filename.toLowerCase().endsWith('.pdf') ?? false)
const pageWarnings = computed(() => selectedQuality.value?.pages.filter(p => p.needs_review) ?? [])
const selectedPageQuality = computed(() => selectedQuality.value?.pages[activePage.value - 1] ?? null)
const displayedChunks = computed(() => chunks.value.filter(
  c => c.page_start <= activePage.value && c.page_end >= activePage.value))
const readyToExtract = computed(() => Boolean(selected.value?.status === 'ready' && !extracting.value
  && Number.isInteger(rangeStart.value) && Number.isInteger(rangeEnd.value)
  && rangeStart.value > 0 && rangeEnd.value >= rangeStart.value
  && rangeEnd.value - rangeStart.value <= 3 && rangeEnd.value <= (selected.value?.total_pages ?? 0)
  && (!visual.value || (isPdf.value && chosenProvider.value === 'ollama'))))
const visionCount = computed(() => selectedQuality.value?.pages.filter(
  p => p.needs_review && p.page >= rangeStart.value && p.page <= rangeEnd.value).length ?? 0)
const selectedAI = computed(() => aiProviders.value?.providers.find(p =>
  p.id === (chosenProvider.value === 'default' ? aiProviders.value?.default : chosenProvider.value)))
const preview = computed(() => selected.value && isPdf.value
  ? documentPagePreviewUrl(selected.value.id, activePage.value) : '')

function size(value: number): string {
  return value < 1024 * 1024 ? (value / 1024).toFixed(0) + ' KB'
    : (value / (1024 * 1024)).toFixed(1) + ' MB'
}
function setError(err: unknown) { error.value = err instanceof Error ? err.message : '操作失败，请重试。'; success.value = '' }
function isValid(file: File): string | null {
  if (!/\.(pdf|pptx)$/i.test(file.name)) return file.name + '：仅支持 PDF 或 PPTX'
  if (file.size > maxBytes) return file.name + '：单文件不能超过 30 MiB'
  if (!file.size) return file.name + '：文件为空'
  return null
}
function clearOutcome() { error.value = ''; success.value = ''; extractResult.value = null }
async function reload() {
  const token = ++loadNumber
  fetching.value = true
  try {
    const list = await api.documents(props.courseId)
    if (token !== loadNumber) return
    files.value = list
    if (!list.some(d => d.id === selectedId.value)) selectedId.value = list[0]?.id ?? ''
  } catch (e) { if (token === loadNumber) setError(e) }
  finally { if (token === loadNumber) fetching.value = false }
}
async function selectDocument(id: string) {
  selectedId.value = id
  const token = ++documentNumber
  selectedQuality.value = null
  chunkBusy.value = false
  chunks.value = []
  chunkOffset.value = 0
  chunkMore.value = true
  activePage.value = 1
  rangeStart.value = 1
  rangeEnd.value = 1
  visual.value = false
  extractResult.value = null
  error.value = ''
  if (!id) return
  qualityBusy.value = true
  try {
    const result = await api.documentQuality(id)
    if (token !== documentNumber) return
    selectedQuality.value = result
  } catch (e) { if (token === documentNumber) setError(e) }
  finally { if (token === documentNumber) qualityBusy.value = false }
  await loadMoreChunks(id, token)
}
async function loadMoreChunks(id = selectedId.value, token = documentNumber) {
  if (!id || chunkBusy.value || !chunkMore.value || token !== documentNumber) return
  chunkBusy.value = true
  try {
    const batch = await api.documentChunks(id, 50, chunkOffset.value)
    if (token !== documentNumber) return
    chunks.value = [...chunks.value, ...batch]
    chunkOffset.value += batch.length
    chunkMore.value = batch.length === 50
  } catch (e) { if (token === documentNumber) setError(e) }
  finally { if (token === documentNumber) chunkBusy.value = false }
}
function nextPage(direction: number) {
  if (!selected.value) return
  const next = Math.max(1, Math.min(selected.value.total_pages, activePage.value + direction))
  activePage.value = next
  rangeStart.value = next
  rangeEnd.value = next
  extractResult.value = null
  if (displayedChunks.value.length === 0 && chunkMore.value) void loadMoreChunks()
}
function choosePage(page: number) {
  if (!selected.value) return
  activePage.value = page
  rangeStart.value = page
  rangeEnd.value = page
  extractResult.value = null
  if (displayedChunks.value.length === 0 && chunkMore.value) void loadMoreChunks()
}
async function addFiles(list: File[]) {
  if (uploading.value) { error.value = '当前正在上传，请在本批完成后继续添加。'; return }
  clearOutcome()
  const pending = list.slice(0, 10)
  if (list.length > 10) error.value = '单次最多上传 10 份文件，请分批添加。'
  const valid: { f: File; item: LocalFile }[] = []
  for (const file of pending) {
    const issue = isValid(file)
    const item: LocalFile = { id: ++uploadNumber, name: file.name, status: issue ? 'failed' : 'queued', error: issue ?? undefined }
    uploadQueue.value.push(item)
    if (!issue) valid.push({ f: file, item })
  }
  if (!valid.length) return
  uploading.value = true
  let uploaded = 0
  let latest = ''
  const originalCourse = props.courseId
  try {
    for (const { f, item } of valid) {
      if (originalCourse !== props.courseId) {
        item.status = 'failed'; item.error = '课程已切换，未继续上传'; continue
      }
      item.status = 'uploading'
      try {
        const done = await api.uploadDocument(originalCourse, f)
        item.status = 'uploaded'
        uploaded++
        latest = done.id
      } catch (e) {
        item.status = 'failed'
        item.error = e instanceof Error ? e.message : '上传失败'
      }
    }
    if (originalCourse === props.courseId) {
      await reload()
      if (latest) selectedId.value = latest
      if (uploaded) {
        success.value = '已成功导入 ' + uploaded + ' 份资料，文件已保存在当前课程。'
        emit('updated')
      }
    }
  } finally { uploading.value = false }
}
function fileChosen(e: Event) {
  const target = e.target as HTMLInputElement
  const selectedFiles = Array.from(target.files ?? [])
  target.value = ''
  void addFiles(selectedFiles)
}
function onDrop(e: DragEvent) {
  dropped.value = false
  const list = Array.from(e.dataTransfer?.files ?? [])
  void addFiles(list)
}
async function extract() {
  if (!selected.value || !readyToExtract.value) return
  clearOutcome()
  extracting.value = true
  try {
    const output = await api.extractKnowledge(selected.value.id, {
      page_start: rangeStart.value, page_end: rangeEnd.value,
      include_visual: visual.value, provider: chosenProvider.value,
    })
    extractResult.value = output
    success.value = '本次新增 ' + output.created + ' 个待审核知识点，关联 ' + output.reused + ' 个已有知识点。'
    if (output.created || output.reused) emit('updated')
  } catch (e) { setError(e) }
  finally { extracting.value = false }
}
watch(() => props.courseId, () => {
  ++loadNumber
  ++documentNumber
  uploadQueue.value = []
  files.value = []
  selectedId.value = ''
  chunks.value = []
  chunkMore.value = true
  selectedQuality.value = null
  clearOutcome()
  void reload()
}, { immediate: true })
watch(selectedId, id => { void selectDocument(id) })
watch(rangeStart, start => {
  if (rangeEnd.value < start) rangeEnd.value = start
  if (rangeEnd.value > start + 3) rangeEnd.value = start + 3
  extractResult.value = null
})
watch(rangeEnd, () => { extractResult.value = null })
watch(chosenProvider, () => { extractResult.value = null; if (chosenProvider.value === 'mimo') visual.value = false })
watch(visual, () => { extractResult.value = null })
void api.aiProviders().then(r => aiProviders.value = r).catch(() => {/* listing optional */})
</script>

<template>
  <div class="library">
    <div class="library-heading">
      <div>
        <p class="eyebrow">SOURCE MATERIAL LIBRARY</p>
        <h1>资料库<span class="heading-dot">.</span></h1>
        <p class="section-description">从课件原文出发，构建可追溯的知识点、练习题与知识星系。</p>
      </div>
      <button type="button" class="button button-outline button-small" :disabled="fetching || uploading" @click="reload">
        <RefreshCw :size="16" :class="{spinning:fetching}" /> 刷新列表
      </button>
    </div>
    <div class="library-flow">
      <span class="done"><CheckCircle2 :size="16" /> 01 导入资料</span>
      <ArrowRight :size="15" />
      <span>02 检查解析</span><ArrowRight :size="15" />
      <span>03 AI 提取知识点</span><ArrowRight :size="15" />
      <span>04 人工审核并进入图谱</span>
    </div>
    <div v-if="error" class="library-feedback error" role="alert"><AlertCircle :size="18" />{{ error }}</div>
    <div v-if="success" class="library-feedback success" role="status"><CheckCircle2 :size="18" />{{ success }}</div>

    <section class="library-upload panel">
      <div class="library-section-heading"><div class="library-section-icon"><CloudUpload :size="23" /></div>
        <div><h2>上传学习资料</h2><p>当前课程：<strong>{{ courseName }}</strong> · PDF / PPTX · 每份最多 30 MiB</p></div>
      </div>
      <input ref="input" class="library-hidden-input" type="file" accept=".pdf,.pptx,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation"
        multiple aria-label="选择 PDF 或 PPTX 文件" @change="fileChosen" />
      <div class="library-drop" :class="{active:dropped, disabled:uploading}"
        role="button" tabindex="0" aria-label="选择或拖入 PDF、PPTX 课件"
        @click="!uploading && input?.click()"
        @keydown.enter.prevent="!uploading && input?.click()"
        @keydown.space.prevent="!uploading && input?.click()"
        @dragover.prevent="dropped = !uploading"
        @dragleave.prevent="dropped = false"
        @drop.prevent="onDrop">
        <div class="library-drop-icon"><Upload :size="24" /></div>
        <strong>{{ uploading ? '正在将资料导入本地课程…' : '点击选择文件，或将课件拖放到此处' }}</strong>
        <span>自动解析 PDF 文本或 PPTX 幻灯片文字与备注；图片和扫描页需要进一步视觉分析。</span>
        <span class="library-picker">选择文件 <ArrowRight :size="16" /></span>
      </div>
      <div v-if="uploadQueue.length" class="library-queue">
        <div v-for="item in uploadQueue" :key="item.id" class="library-queue-item">
          <LoaderCircle v-if="item.status==='uploading'" class="spinning" :size="17" />
          <FileCheck2 v-else-if="item.status==='uploaded'" :size="17" class="queue-good" />
          <X v-else-if="item.status==='failed'" :size="17" class="queue-bad" />
          <File v-else :size="17" />
          <span>{{ item.name }}</span>
          <small>{{ item.error || ({queued:'待上传',uploading:'正在上传与解析',uploaded:'已保存',failed:'失败'})[item.status] }}</small>
        </div>
      </div>
      <p class="library-privacy">文件上传至你运行的 KnowTrace 后端并保存在本地。<strong>仅上传</strong>不会自动发送到 MiMo 或其他云端 AI；只有你主动选择云端模型并执行提取时，相关页面文字才可能发送给该服务。</p>
    </section>

    <div class="library-grid">
      <aside class="library-files panel">
        <div class="library-panel-head"><div><h2>已导入资料</h2><p>{{ files.length }} 份课程文档</p></div><FileText :size="20" /></div>
        <div v-if="fetching" class="library-empty"><LoaderCircle :size="22" class="spinning" />读取课程资料…</div>
        <div v-else-if="!files.length" class="library-empty"><FileText :size="25" /><strong>当前课程还没有资料</strong><span>上传第一份课件即可开始。</span></div>
        <div v-else class="library-file-list">
          <button v-for="doc in files" :key="doc.id" type="button" :class="['library-file-item',{active:selectedId===doc.id}]"
            @click="selectedId=doc.id">
            <span class="file-symbol" :class="doc.original_filename.toLowerCase().endsWith('.pdf')?'pdf':'slides'">{{ doc.original_filename.toLowerCase().endsWith('.pdf')?'PDF':'PPT' }}</span>
            <span><strong :title="doc.original_filename">{{ doc.original_filename }}</strong>
              <small>{{ doc.total_pages }} {{ doc.original_filename.toLowerCase().endsWith('.pdf')?'页':'张幻灯片' }} · {{ doc.status==='ready'?'已解析':'处理中' }}</small>
            </span>
            <ChevronRight :size="15" />
          </button>
        </div>
      </aside>

      <div class="library-detail">
        <div v-if="!selected" class="library-placeholder panel"><ScanSearch :size="37" />
          <h2>选择一份资料开始处理</h2><p>导入后可查看页面质量和原文，并选择页码提取知识点。</p>
        </div>
        <template v-else>
          <section class="library-reader panel">
            <div class="library-panel-head">
              <div><p class="eyebrow">DOCUMENT INSPECTOR</p><h2 :title="selected.original_filename">{{ selected.original_filename }}</h2>
                <p>{{ selected.total_pages }} {{ isPdf?'页':'张幻灯片' }} · {{ isPdf?'PDF':'PowerPoint' }} · 已保留原始页码</p></div>
              <span class="library-ready"><CheckCircle2 :size="15" /> {{ selected.status==='ready'?'已解析':'等待解析' }}</span>
            </div>
            <div v-if="qualityBusy" class="library-loading"><LoaderCircle :size="19" class="spinning" />检测页面文字质量…</div>
            <div v-else-if="selectedQuality" :class="['library-quality',{warning:pageWarnings.length>0}]">
              <ScanSearch :size="18" />
              <span>{{ selectedQuality.total_pages-pageWarnings.length }} 页未触发低文本提示，{{ pageWarnings.length }} 页建议检查。
                <small>这是文本量启发式检测，不等于 OCR 准确率；空白封面也可能被提示。</small></span>
            </div>

            <div class="library-page-bar">
              <div class="library-pages"><button type="button" aria-label="上一页" :disabled="activePage<=1" @click="nextPage(-1)"><ChevronLeft :size="19" /></button>
                <span>第 <strong>{{ activePage }}</strong> / {{ selected.total_pages }} 页</span>
                <button type="button" aria-label="下一页" :disabled="activePage>=selected.total_pages" @click="nextPage(1)"><ChevronRight :size="19" /></button></div>
              <span v-if="selectedPageQuality?.needs_review" class="library-page-warning"><AlertCircle :size="14" /> 本页文字较少</span>
              <span v-else class="library-page-ok"><CheckCircle2 :size="14" /> 页面文本检测</span>
            </div>
            <div v-if="isPdf" class="library-page-preview">
              <img :key="preview" :src="preview" :alt="'原始 PDF 第 '+activePage+' 页预览'" loading="lazy" />
              <a :href="preview" target="_blank" rel="noopener noreferrer">在新窗口查看此页 <ArrowRight :size="14" /></a>
            </div>
            <div v-else class="library-no-preview">
              <Image :size="24" /><strong>PPTX 暂无逐页图片预览</strong>
              <p>可以检查下方已提取的幻灯片文字。PPTX 图片尚不支持自动视觉识别，需另行处理。</p>
            </div>
            <div class="library-excerpt">
              <div class="library-excerpt-head"><strong>已提取原文片段</strong><span>共已读取 {{ chunks.length }} 条</span></div>
              <div v-if="displayedChunks.length" class="library-chunk-list">
                <p v-for="chunk in displayedChunks" :key="chunk.id">{{ chunk.content_text.slice(0,1300) }}{{ chunk.content_text.length>1300?'…':'' }}</p>
              </div>
              <div v-else class="library-quiet">当前已读取的片段中没有本页正文；可能是图片页，也可能尚未加载到对应片段。</div>
              <button v-if="chunkMore" type="button" class="library-more" :disabled="chunkBusy" @click="loadMoreChunks()">
                <LoaderCircle v-if="chunkBusy" class="spinning" :size="15" /><RefreshCw v-else :size="15" /> 继续加载原文片段
              </button>
            </div>
          </section>

          <section class="library-extract panel">
            <div class="library-section-heading"><div class="library-section-icon mint"><Sparkles :size="22" /></div>
              <div><h2>AI 提取知识点</h2><p>每次选取 1–4 页，系统会将提取结果保存为待审核知识点。</p></div>
            </div>
            <div class="library-extract-form">
              <label>起始页<input v-model.number="rangeStart" type="number" min="1" :max="selected.total_pages" /></label>
              <label>结束页<input v-model.number="rangeEnd" type="number" :min="rangeStart" :max="Math.min(rangeStart+3,selected.total_pages)" /></label>
              <label class="library-model">选择 AI 模型
                <select v-model="chosenProvider">
                  <option value="ollama">本地 Ollama（默认，不上传云端）</option>
                  <option value="mimo">小米 MiMo API（发送选定页面文字）</option>
                </select>
              </label>
            </div>
            <label v-if="isPdf" class="library-visual-toggle">
              <input v-model="visual" type="checkbox" :disabled="chosenProvider !== 'ollama'" />
              <span><strong>对低文字量 PDF 页面启用本地视觉模型</strong>
                <small>需要配置 Ollama 视觉模型；最多 2 页，提取的图示描述需人工核查。MiMo 文本模型不会自动识图。</small></span>
            </label>
            <div v-if="visual && visionCount>2" class="library-feedback error"><AlertCircle :size="17" />
              所选范围中有 {{ visionCount }} 页低文字量页面；视觉模式每次最多处理 2 页，请缩小范围。</div>
            <p class="library-ai-note">
              <template v-if="chosenProvider==='mimo'">当前选用 MiMo：仅在点击“开始提取”时提交选定的页面文字，请确认资料允许发送给第三方；PDF 视觉提取需切换至本地 Ollama。</template>
              <template v-else>当前使用本地 Ollama。请先确保模型服务运行，并已安装对应文本模型。</template>
              <span v-if="selectedAI"> 检测到模型：{{ selectedAI.model }}。</span>
            </p>
            <button type="button" class="button button-primary library-extract-button"
              :disabled="!readyToExtract || (visual && visionCount>2)" @click="extract">
              <LoaderCircle v-if="extracting" :size="18" class="spinning" />
              <Sparkles v-else :size="18" />
              {{ extracting?'正在提取并核验原文证据…':'开始提取所选页面' }}
              <ArrowRight :size="18" />
            </button>
            <div v-if="extractResult" class="library-extract-result">
              <div><CheckCircle2 :size="22" /><strong>提取任务完成</strong></div>
              <p>新增 <b>{{ extractResult.created }}</b> 个待审核知识点，复用 <b>{{ extractResult.reused }}</b> 个已有知识点。</p>
              <p v-if="extractResult.vision_pages.length">使用视觉描述的页码：{{ extractResult.vision_pages.join('、') }}（需核验原图）</p>
              <p v-if="extractResult.skipped_sparse_pages.length">跳过的低文字量页码：{{ extractResult.skipped_sparse_pages.join('、') }}</p>
              <p v-for="(message,index) in extractResult.warnings" :key="index">{{ message }}</p>
              <button class="button button-primary" type="button" @click="emit('openReview')">
                <ClipboardCheck :size="17" /> 前往知识点审核 <ArrowRight :size="17" />
              </button>
            </div>
            <div class="library-other-options">
              <span>不想调用 API？</span>
              <button type="button" @click="emit('openExternal', selected.id)">复制提示词，交给其他 AI 提取 <ArrowRight :size="16" /></button>
            </div>
          </section>
        </template>
      </div>
    </div>
    <p class="library-footnote"><BookOpenCheck :size="17" />
      课件导入、内容解析和知识点审核是独立步骤。上传成功不意味着模型已经读懂图像或自动生成了题目。
    </p>
  </div>
</template>

<style scoped>
.library{display:grid;gap:20px;min-width:0}
.library-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.library-heading h1{font-size:30px;margin:6px 0 4px}
.library-flow{display:flex;align-items:center;gap:9px;flex-wrap:wrap;color:#91a7af;font-size:11px;padding:4px 0}
.library-flow span{display:flex;align-items:center;gap:7px;font-weight:750}
.library-flow .done{color:#159989}
.library-feedback{display:flex;align-items:flex-start;gap:10px;padding:13px 15px;border-radius:10px;font-size:12px;line-height:1.8}
.library-feedback svg{flex:0 0 18px;margin-top:1px}
.library-feedback.error{background:#fff4ef;border:1px solid #efd1c3;color:#b46f52}
.library-feedback.success{background:#ebf9f4;border:1px solid #c2e9d9;color:#207d69}
.library-upload{padding:25px}
.library-section-heading{display:flex;align-items:center;gap:13px;margin-bottom:18px}
.library-section-heading h2{font-size:17px;margin:0 0 7px;color:#284b58}
.library-section-heading p{font-size:11px;color:#87a0a9;margin:0;line-height:1.8}
.library-section-heading p strong{color:#3c6e78}
.library-section-icon{width:47px;height:47px;display:grid;place-items:center;flex:0 0 47px;border-radius:13px;background:#e8f1ff;color:#558be7}
.library-section-icon.mint{background:#e5f8f2;color:#179b85}
.library-hidden-input{display:none}
.library-drop{min-height:178px;border:2px dashed #c9dfe5;border-radius:14px;background:#f7fbfc;display:grid;justify-items:center;align-content:center;gap:11px;text-align:center;cursor:pointer;padding:22px;color:#66838e;transition:background .15s,border-color .15s}
.library-drop.active,.library-drop:hover{background:#e9f9f4;border-color:#44c2a2}
.library-drop.disabled{cursor:progress;opacity:.7}
.library-drop-icon{color:#159f8c;background:#e3f8f0;width:49px;height:49px;display:grid;place-items:center;border-radius:15px}
.library-drop strong{font-size:13px;color:#355d69}
.library-drop>span:not(.library-picker){font-size:11px;max-width:600px;line-height:1.8}
.library-picker{display:flex;gap:8px;align-items:center;color:#14978a;font-size:11px;font-weight:850}
.library-queue{display:grid;gap:7px;margin-top:14px}
.library-queue-item{border:1px solid #e2ebee;border-radius:9px;padding:10px 13px;display:flex;align-items:center;gap:10px;font-size:11px;color:#708994}
.library-queue-item>span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.library-queue-item small{font-size:10px;color:#95a5ae}
.queue-good{color:#1caa8c}.queue-bad{color:#bd7059}
.library-privacy{font-size:11px;color:#8c9fa9;line-height:1.8;margin:15px 0 0}
.library-grid{display:grid;grid-template-columns:minmax(205px,.75fr) minmax(0,2fr);gap:14px;align-items:start}
.library-files{padding:19px 13px;min-width:0}
.library-panel-head{display:flex;align-items:flex-start;gap:13px;justify-content:space-between;margin-bottom:17px}
.library-panel-head h2{font-size:15px;margin:3px 0 6px;color:#2b4d5b;overflow-wrap:anywhere}
.library-panel-head p{font-size:10px;color:#91a3aa;line-height:1.7;margin:0}
.library-panel-head>svg{color:#7da2ad}
.library-file-list{display:grid;gap:8px;max-height:750px;overflow:auto}
.library-file-item{display:flex;align-items:center;gap:10px;border:1px solid #e4ecef;background:white;padding:12px;border-radius:10px;text-align:left;min-width:0}
.library-file-item.active,.library-file-item:hover{border-color:#94d6ca;background:#eefaf6}
.library-file-item>span:nth-child(2){flex:1;min-width:0;display:grid;gap:7px}
.library-file-item strong{font-size:11px;color:#3c6171;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.library-file-item small{font-size:10px;color:#9baab3}
.library-file-item>svg{color:#90a4ac}
.file-symbol{display:grid;place-items:center;width:39px;height:43px;flex:0 0 39px;font-weight:850;font-size:10px;border-radius:8px}
.file-symbol.pdf{background:#fff1e8;color:#b77754}.file-symbol.slides{background:#e8f0ff;color:#5878b8}
.library-empty{display:flex;flex-direction:column;align-items:center;gap:12px;justify-content:center;min-height:190px;color:#91a6ae;font-size:11px;text-align:center}
.library-empty strong{color:#536f78;font-size:12px}
.library-detail{display:grid;gap:14px;min-width:0}
.library-placeholder{min-height:300px;display:grid;align-content:center;justify-items:center;gap:12px;color:#9aacb4;text-align:center}
.library-placeholder h2{color:#4d727e;font-size:16px;margin:0}
.library-placeholder p{max-width:320px;font-size:11px;line-height:1.8}
.library-reader,.library-extract{padding:23px;min-width:0}
.library-ready{display:flex;align-items:center;gap:6px;padding:7px 9px;flex:0 0 auto;background:#e7f7ef;border-radius:20px;font-size:10px;color:#179a82}
.library-loading{display:flex;gap:8px;align-items:center;font-size:11px;color:#668f98;margin-bottom:13px}
.library-quality{display:flex;gap:10px;align-items:flex-start;background:#edf8f6;border-radius:9px;padding:12px;margin:10px 0 16px;color:#238f7d;font-size:11px}
.library-quality.warning{background:#fff7ea;color:#a17847}
.library-quality svg{flex:0 0 18px}
.library-quality small{display:block;font-size:10px;line-height:1.8;opacity:.8;margin-top:4px}
.library-page-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:16px 0 13px}
.library-pages{display:flex;align-items:center;gap:12px;font-size:11px;color:#668793}
.library-pages button{width:32px;height:32px;border:1px solid #dbe9ea;background:#fff;border-radius:8px;display:grid;place-items:center;color:#388d84}
.library-pages button:disabled{opacity:.4}
.library-page-warning,.library-page-ok{display:flex;align-items:center;gap:7px;font-size:10px;color:#ae8351}
.library-page-ok{color:#379585}
.library-page-preview{background:#eff3f5;border-radius:11px;min-height:210px;display:grid;justify-items:center;padding:17px;gap:9px}
.library-page-preview img{display:block;max-width:100%;max-height:430px;object-fit:contain;box-shadow:0 2px 15px #142f3e23;background:white}
.library-page-preview a{display:flex;align-items:center;gap:6px;font-size:10px;color:#168a80}
.library-no-preview{display:grid;justify-items:center;text-align:center;gap:10px;padding:35px 20px;border:1px dashed #d8e7ec;background:#f8fafb;border-radius:11px;color:#91a8b5}
.library-no-preview strong{font-size:12px;color:#4f7480}
.library-no-preview p{font-size:11px;line-height:1.8;margin:0;max-width:390px}
.library-excerpt{margin-top:20px}
.library-excerpt-head{display:flex;justify-content:space-between;gap:12px;align-items:center;font-size:11px;color:#496b78;margin-bottom:12px}
.library-excerpt-head span{font-size:10px;color:#93a8b0}
.library-chunk-list{display:grid;gap:10px;max-height:265px;overflow:auto}
.library-chunk-list p{font-size:11px;line-height:1.9;color:#65828c;background:#f5f9fa;border-radius:8px;padding:12px;margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.library-quiet{font-size:11px;color:#98aeb4;line-height:1.8;padding:12px;background:#f8fafb}
.library-more{display:flex;align-items:center;gap:8px;font-size:11px;color:#148f80;border:0;background:transparent;margin-top:10px;padding:6px 0}
.library-extract-form{display:grid;grid-template-columns:100px 100px minmax(180px,1fr);gap:12px}
.library-extract-form label{display:grid;gap:7px;font-size:10px;font-weight:800;color:#698693}
.library-extract-form input,.library-extract-form select{min-height:40px;width:100%;min-width:0;padding:8px;border:1px solid #d9e7ea;border-radius:8px;background:white;color:#375967;font-size:11px}
.library-visual-toggle{display:flex;align-items:flex-start;gap:11px;margin:19px 0 10px;color:#456c77}
.library-visual-toggle input{accent-color:#14a88b;width:16px;height:16px;flex:0 0 16px}
.library-visual-toggle span{display:grid;gap:5px}
.library-visual-toggle strong{font-size:11px}
.library-visual-toggle small{font-size:10px;color:#9aa9b2;line-height:1.7}
.library-ai-note{font-size:10px;color:#8fa4ae;line-height:1.85;margin:14px 0}
.library-extract-button{min-height:44px}
.library-extract-result{display:grid;gap:11px;margin-top:17px;padding:17px;background:#eaf9f4;border:1px solid #bde9d7;border-radius:11px;color:#387968}
.library-extract-result>div{display:flex;align-items:center;gap:10px}
.library-extract-result>div strong{font-size:12px}
.library-extract-result p{font-size:11px;line-height:1.8;margin:0}
.library-extract-result .button{justify-self:start}
.library-other-options{border-top:1px solid #e5eef0;margin-top:20px;padding-top:17px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.library-other-options span{font-size:11px;color:#93a5ac}
.library-other-options button{display:flex;align-items:center;gap:6px;border:0;background:none;color:#15998b;font-size:11px;font-weight:800}
.library-footnote{display:flex;align-items:flex-start;gap:10px;color:#8b9fa8;font-size:10px;line-height:1.8}
.library-footnote svg{flex:0 0 17px}
@media(max-width:1000px){.library-grid{grid-template-columns:1fr}.library-file-list{max-height:245px}.library-extract-form{grid-template-columns:repeat(2,minmax(0,1fr))}.library-model{grid-column:1/-1}}
@media(max-width:620px){.library-upload,.library-reader,.library-extract{padding:17px}.library-heading h1{font-size:27px}.library-flow{font-size:10px}.library-page-preview img{max-height:310px}}
</style>
