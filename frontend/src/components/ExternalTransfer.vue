<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, Check, ClipboardCopy, ClipboardPaste, FileText, LoaderCircle, ShieldAlert, Sparkles } from '@lucide/vue'
import { api, type CourseDocument, type KnowledgePoint, type ExternalPrompt } from '../api'

const props = defineProps<{
  courseId: string
  knowledgePoints: KnowledgePoint[]
  preferredKnowledgePointId: string
}>()
const emit = defineEmits<{
  imported: [payload: { kind: 'questions' | 'knowledge'; ids: string[]; created: number; reused?: number }]
}>()

type Kind = 'questions' | 'knowledge'
type PromptSnapshot = {
  kind: Kind
  target: string
  count: number
  types: ('single_choice' | 'true_false')[]
  start: number
  end: number
}

const kind = ref<Kind>('questions')
const kpId = ref('')
const documents = ref<CourseDocument[]>([])
const docId = ref('')
const pageStart = ref(1)
const pageEnd = ref(2)
const count = ref(1)
const includeChoice = ref(true)
const includeBoolean = ref(false)
const prompt = ref<ExternalPrompt | null>(null)
const snapshot = ref<PromptSnapshot | null>(null)
const responseText = ref('')
const loading = ref(false)
const preparing = ref(false)
const importing = ref(false)
const copied = ref(false)
const error = ref('')
const resultMessage = ref('')
const warnings = ref<string[]>([])
const selectedDocument = computed(() => documents.value.find(x => x.id === docId.value))
const readyKnowledge = computed(() => props.knowledgePoints.filter(p => p.review_status === 'approved'))
const types = computed<('single_choice' | 'true_false')[]>(() => {
  const selected: ('single_choice' | 'true_false')[] = []
  if (includeChoice.value) selected.push('single_choice')
  if (includeBoolean.value) selected.push('true_false')
  return selected
})
const canPrepare = computed(() => kind.value === 'questions'
  ? Boolean(kpId.value && readyKnowledge.value.some(p => p.id === kpId.value) && types.value.length)
  : Boolean(selectedDocument.value?.status === 'ready' && pageStart.value >= 1 &&
            pageEnd.value >= pageStart.value && pageEnd.value - pageStart.value < 4 &&
            pageEnd.value <= (selectedDocument.value?.total_pages ?? 0)))
const manualPrivacy = '此功能不会自动调用其他 AI；复制的提示词包含所选课件片段，发送到外部平台前请确认课件允许分享。'
let loadId = 0

function resetTransfer() {
  prompt.value = null
  snapshot.value = null
  responseText.value = ''
  copied.value = false
  error.value = ''
  resultMessage.value = ''
  warnings.value = []
}
watch([kind, kpId, docId, pageStart, pageEnd, count, includeChoice, includeBoolean],
      resetTransfer)
watch(() => props.preferredKnowledgePointId, value => {
  if (value && readyKnowledge.value.some(p => p.id === value)) kpId.value = value
})
watch(() => props.knowledgePoints, value => {
  if (!readyKnowledge.value.some(p => p.id === kpId.value)) {
    kpId.value = readyKnowledge.value.find(p => p.id === props.preferredKnowledgePointId)?.id
      ?? readyKnowledge.value[0]?.id ?? ''
  }
}, { immediate: true })
watch(() => props.courseId, async (course) => {
  const id = ++loadId
  loading.value = true
  documents.value = []
  docId.value = ''
  resetTransfer()
  try {
    const docs = await api.documents(course)
    if (id !== loadId) return
    documents.value = docs
    docId.value = docs.find(d => d.status === 'ready')?.id ?? ''
    pageStart.value = 1
    pageEnd.value = 1
  } catch (e) {
    if (id === loadId) error.value = e instanceof Error ? e.message : '课件列表加载失败'
  } finally {
    if (id === loadId) loading.value = false
  }
}, { immediate: true })
watch(docId, () => {
  pageStart.value = 1
  pageEnd.value = 1
})

async function prepare() {
  if (!canPrepare.value || preparing.value) return
  resetTransfer()
  preparing.value = true
  const ctx: PromptSnapshot = {
    kind: kind.value, target: kind.value === 'questions' ? kpId.value : docId.value,
    count: count.value, types: [...types.value], start: pageStart.value, end: pageEnd.value,
  }
  try {
    const output = ctx.kind === 'questions'
      ? await api.externalQuestionPrompt(ctx.target, { count: ctx.count, question_types: ctx.types })
      : await api.externalKnowledgePrompt(ctx.target, { page_start: ctx.start, page_end: ctx.end })
    // Do not display an out-of-date prompt after the selection has changed.
    if (kind.value !== ctx.kind || (ctx.kind === 'questions' ? kpId.value : docId.value) !== ctx.target
        || count.value !== ctx.count || pageStart.value !== ctx.start || pageEnd.value !== ctx.end
        || types.value.join(',') !== ctx.types.join(',')) return
    prompt.value = output
    snapshot.value = ctx
    resultMessage.value = '提示词已生成。复制到任意 AI 平台，取得回复后粘贴到下方。'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '生成提示词失败'
  } finally {
    preparing.value = false
  }
}

async function copyPrompt() {
  if (!prompt.value) return
  try {
    await navigator.clipboard.writeText(prompt.value.prompt)
    copied.value = true
    resultMessage.value = '提示词已复制到剪贴板。'
  } catch {
    error.value = '浏览器未允许访问剪贴板。请点击提示词框并使用 Command+A / Command+C 手动复制。'
  }
}

async function importReply() {
  const ctx = snapshot.value
  if (!ctx || importing.value) return
  if (!responseText.value.trim()) {
    error.value = '请先粘贴外部 AI 给出的 JSON 答复。'
    return
  }
  error.value = ''
  resultMessage.value = ''
  warnings.value = []
  importing.value = true
  try {
    if (ctx.kind === 'questions') {
      const result = await api.importExternalQuestions(ctx.target, {
        count: ctx.count, question_types: ctx.types, response_text: responseText.value,
      })
      warnings.value = result.warnings
      resultMessage.value = '已导入 ' + result.created + ' 道待审核题目。'
      if (result.created) emit('imported', { kind: 'questions', ids: result.question_version_ids, created: result.created })
    } else {
      const result = await api.importExternalKnowledge(ctx.target, {
        page_start: ctx.start, page_end: ctx.end, response_text: responseText.value,
      })
      warnings.value = result.warnings
      resultMessage.value = '新建 ' + result.created + ' 个待审核知识点，复用 ' + result.reused + ' 个已有知识点。'
      if (result.created || result.reused) emit('imported', {
        kind: 'knowledge', ids: result.created_ids, created: result.created, reused: result.reused,
      })
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解析或导入失败'
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <section class="transfer panel">
    <div class="transfer-hero">
      <div class="transfer-icon"><ClipboardPaste :size="27" /></div>
      <div><span class="transfer-kicker">COPY · PASTE · VERIFY</span><h2>外部 AI 手动接力</h2>
        <p>不用 API Key，也不用绑定平台。你自己选择 ChatGPT、Gemini、DeepSeek 等 AI，KnowTrace 负责生成提示词和核验导入结果。</p>
      </div>
    </div>
    <div class="transfer-note"><ShieldAlert :size="18" /><span>{{ manualPrivacy }}</span></div>

    <div class="transfer-segments" role="tablist" aria-label="外部 AI 任务类型">
      <button type="button" role="tab" :aria-selected="kind === 'questions'" :class="{ active: kind === 'questions' }" @click="kind = 'questions'">
        <Sparkles :size="18" /> 生成练习题
      </button>
      <button type="button" role="tab" :aria-selected="kind === 'knowledge'" :class="{ active: kind === 'knowledge' }" @click="kind = 'knowledge'">
        <FileText :size="18" /> 提取知识点
      </button>
    </div>

    <div class="transfer-steps">
      <div class="transfer-step">
        <div class="transfer-step-heading"><span>01</span><strong>选择任务和材料</strong></div>
        <div v-if="kind === 'questions'" class="transfer-controls">
          <label>已审核知识点<select v-model="kpId" :disabled="loading">
            <option v-if="!readyKnowledge.length" value="">暂无已审核知识点</option>
            <option v-for="kp in readyKnowledge" :key="kp.id" :value="kp.id">{{ kp.canonical_name }}</option>
          </select></label>
          <label>题量<select v-model.number="count"><option v-for="n in 4" :key="n" :value="n">{{ n }} 道</option></select></label>
          <fieldset><legend>题型</legend><label><input v-model="includeChoice" type="checkbox" /> 单选</label>
            <label><input v-model="includeBoolean" type="checkbox" /> 判断</label></fieldset>
        </div>
        <div v-else class="transfer-controls">
          <label>已导入课件<select v-model="docId" :disabled="loading">
            <option v-if="!documents.length" value="">暂无课件</option>
            <option v-for="doc in documents.filter(d => d.status === 'ready')" :key="doc.id" :value="doc.id">
              {{ doc.original_filename }}（{{ doc.total_pages }} 页）
            </option>
          </select></label>
          <label>起始页<input v-model.number="pageStart" type="number" min="1" :max="selectedDocument?.total_pages ?? 1" /></label>
          <label>结束页<input v-model.number="pageEnd" type="number" :min="pageStart" :max="Math.min(pageStart + 3, selectedDocument?.total_pages ?? 1)" /></label>
        </div>
        <p class="transfer-hint" v-if="kind === 'knowledge'">每次最多 4 页；如果是图片页，请先使用本地视觉提取生成可引用的图示描述。</p>
        <p class="transfer-hint" v-else>提示词只包含该知识点关联的少量课件片段，最多 4 个来源；不会包含整份 PDF。</p>
        <button class="button button-primary" type="button" :disabled="!canPrepare || preparing" @click="prepare">
          <LoaderCircle v-if="preparing" class="spinning" :size="17" /><Sparkles v-else :size="17" />
          {{ preparing ? '正在准备…' : '生成可复制提示词' }} <ArrowRight :size="17" />
        </button>
      </div>

      <div class="transfer-step" :class="{ muted: !prompt }">
        <div class="transfer-step-heading"><span>02</span><strong>复制提示词，粘贴到其他 AI</strong></div>
        <div v-if="prompt" class="transfer-box">
          <div class="transfer-box-top"><span>KnowTrace 生成的结构化提示词</span>
            <button type="button" class="transfer-copy" @click="copyPrompt">
              <Check v-if="copied" :size="16" /><ClipboardCopy v-else :size="16" /> {{ copied ? '已复制' : '复制全部' }}
            </button></div>
          <textarea readonly :value="prompt.prompt" aria-label="可复制提示词" rows="11"></textarea>
          <p class="transfer-privacy">{{ prompt.privacy_warning }}</p>
          <p class="transfer-hint" v-if="prompt.skipped_pages?.length">未包含缺乏文本的页码：{{ prompt.skipped_pages.join('、') }}。</p>
        </div>
        <div v-else class="transfer-placeholder">先在上一步选择范围并生成提示词。</div>
      </div>

      <div class="transfer-step" :class="{ muted: !snapshot }">
        <div class="transfer-step-heading"><span>03</span><strong>粘贴 AI 回复并识别</strong></div>
        <p class="transfer-hint">支持纯 JSON、Markdown JSON 代码块，以及前后带少量说明的 JSON；题目、引文与答案不符合要求的候选会被拦截。</p>
        <textarea class="transfer-reply" v-model="responseText" :disabled="!snapshot || importing"
          placeholder="将其他 AI 的完整回复粘贴在这里；推荐让它按提示词输出 JSON…" rows="10" maxlength="100000"
          aria-label="粘贴外部 AI 回复"></textarea>
        <div class="transfer-footer">
          <span>{{ responseText.length }} / 100000 字符</span>
          <button type="button" class="button button-primary" :disabled="!snapshot || !responseText.trim() || importing" @click="importReply">
            <LoaderCircle v-if="importing" class="spinning" :size="17" /><ClipboardPaste v-else :size="17" />
            {{ importing ? '正在验证和导入…' : '识别并导入待审核区' }} <ArrowRight :size="17" />
          </button>
        </div>
      </div>
    </div>
    <div v-if="error" class="transfer-alert error" role="alert">{{ error }}</div>
    <div v-if="resultMessage" class="transfer-alert success" role="status">{{ resultMessage }}</div>
    <div v-if="warnings.length" class="transfer-alert warning">
      <strong>有 {{ warnings.length }} 条候选未通过验证：</strong>
      <p v-for="(warning,i) in warnings" :key="i">{{ warning }}</p>
    </div>
    <p class="transfer-disclaimer">所有导入内容均保留原始来源、标记为待审核；AI 回复的可信度不能替代人工核对。重复提交不会再建立同名知识点或同题干题目。</p>
  </section>
</template>

<style scoped>
.transfer { padding:29px; display:grid;gap:21px; min-width:0 }
.transfer-hero { display:flex;align-items:flex-start;gap:17px }
.transfer-icon { width:59px;height:59px;border-radius:17px;display:grid;place-items:center;flex:0 0 59px;color:#16a18d;background:#e2f7f1 }
.transfer-kicker { font-size:10px;letter-spacing:2px;font-weight:800;color:#159c88 }
.transfer-hero h2 { margin:5px 0 8px;font-size:21px }
.transfer-hero p,.transfer-hint { font-size:11px;line-height:1.85;color:#8399a4;margin:0 }
.transfer-note { display:flex;align-items:flex-start;gap:9px;padding:12px 14px;background:#fff8eb;border:1px solid #f2ddac;border-radius:10px;color:#986d39;font-size:11px;line-height:1.9 }
.transfer-note svg { flex:0 0 18px;margin-top:1px }
.transfer-segments { display:flex;align-items:center;gap:9px;border-bottom:1px solid #e7eff0 }
.transfer-segments button { display:flex;align-items:center;gap:9px;padding:13px 17px;border:none;border-bottom:3px solid transparent;background:transparent;color:#788e9a;font-weight:800;font-size:12px }
.transfer-segments button.active { border-bottom-color:#16aa97;color:#118e80 }
.transfer-steps { display:grid;gap:18px }
.transfer-step { border:1px solid #e6eef0;background:#fff;border-radius:13px;padding:22px }
.transfer-step.muted { background:#fcfdfd }
.transfer-step-heading { display:flex;align-items:center;gap:13px;font-size:13px;margin-bottom:19px;color:#284551 }
.transfer-step-heading > span { width:31px;height:31px;display:grid;place-items:center;border-radius:9px;background:#e4f7f2;color:#159e8c;font-weight:800;font-size:11px }
.transfer-controls { display:flex;align-items:end;gap:14px;flex-wrap:wrap;margin-bottom:16px }
.transfer-controls > label { display:grid;gap:7px;flex:1 1 155px;min-width:0;font-size:11px;font-weight:800;color:#50727d }
.transfer-controls > label:first-child { flex:2 1 260px }
.transfer-controls input[type=number],.transfer-controls select { min-height:43px;border:1px solid #dbe7ea;border-radius:9px;background:#fff;color:#234b59;padding:8px 11px;width:100%;font:inherit;font-size:12px;min-width:0 }
.transfer-controls fieldset { border:0;padding:0;margin:0;display:flex;gap:14px;flex-wrap:wrap;font-size:11px }
.transfer-controls legend { font-weight:800;color:#50727d;margin-bottom:11px }
.transfer-controls fieldset label { display:flex;align-items:center;gap:6px;color:#506c78 }
.transfer-controls input[type=checkbox] { width:16px;height:16px;accent-color:#14a390 }
.transfer-step .button { margin-top:16px }
.transfer-box { border:1px solid #e2ebed;border-radius:11px;overflow:hidden }
.transfer-box-top { display:flex;align-items:center;justify-content:space-between;gap:10px;background:#f3f8f9;padding:10px 15px;font-size:11px;color:#698993;font-weight:750 }
.transfer-copy { display:flex;align-items:center;gap:7px;border:0;background:#e0f4ee;color:#148b7d;padding:8px 11px;border-radius:7px;font-size:11px;font-weight:800 }
.transfer-box textarea,.transfer-reply { border:0;resize:vertical;background:#fff;color:#294854;width:100%;font:12px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace;padding:14px;min-height:170px;display:block }
.transfer-reply { border:1px solid #dbe7eb;border-radius:10px;margin-top:12px }
.transfer-reply:disabled { background:#f7f9fa }
.transfer-box textarea:focus,.transfer-reply:focus { outline:2px solid #46b6aa;outline-offset:-2px }
.transfer-privacy { margin:0;padding:10px 14px;border-top:1px solid #e5edf0;background:#f7fbfa;color:#758f97;font-size:11px }
.transfer-placeholder { border:1px dashed #dae7ea;border-radius:10px;color:#9dadb5;font-size:12px;text-align:center;padding:30px 10px }
.transfer-footer { display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:13px;flex-wrap:wrap }
.transfer-footer span { color:#a0afb7;font-size:11px }
.transfer-footer .button { margin:0 }
.transfer-alert { padding:14px 16px;border-radius:10px;font-size:12px;line-height:1.9 }
.transfer-alert.error { background:#fff4f1;color:#af5a43;border:1px solid #f0ccc1 }
.transfer-alert.success { background:#e9f8f2;color:#177e60;border:1px solid #c7eade }
.transfer-alert.warning { background:#fff7ea;color:#977045;border:1px solid #ead5ae }
.transfer-alert p { margin:5px 0 }
.transfer-disclaimer { color:#9aabb3;line-height:1.85;font-size:11px;margin:0 }
@media(max-width:700px) { .transfer {padding:18px}.transfer-hero {flex-direction:column}.transfer-segments button {font-size:11px;padding:12px 8px}.transfer-step {padding:17px}.transfer-controls > label:first-child{flex:1 1 100%} }
</style>
