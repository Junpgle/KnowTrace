<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ExternalTransfer from './ExternalTransfer.vue'
import BulkQuestionStudio from './BulkQuestionStudio.vue'
import KnowledgeAIReviewPanel from './KnowledgeAIReviewPanel.vue'
import DirectBulkApproval from './DirectBulkApproval.vue'
import QuestionBulkApproval from './QuestionBulkApproval.vue'
import {
  AlertCircle, ArrowRight, BookOpen, CheckCircle2, ChevronRight,
  ClipboardCheck, FileQuestion, FileText, Image, LayoutDashboard, LoaderCircle,
  Pencil, RefreshCw, ShieldCheck, Sparkles, Settings2, XCircle,
} from '@lucide/vue'
import {
  api, sourcePreviewUrl,
  type GenerationResult, type KnowledgePoint, type KnowledgePointDetail,
  type QuestionRevision, type QuestionSummary, type QuestionVersion,
  type SourceEvidence, type AIProviderId, type AIProviderStatus,
} from '../api'

const props = defineProps<{ courseId: string; initialTab?: 'overview' | 'knowledge' | 'questions' | 'external' | 'batch'; initialDocumentId?: string; initialKnowledgePointId?: string; embedded?: boolean }>()
const emit = defineEmits<{ updated: []; 'blind-trial': []; 'navigate-tab': [tab: Tab] }>()

type Tab = 'overview' | 'knowledge' | 'questions' | 'external' | 'batch'
type Filter = 'all' | 'pending' | 'approved' | 'rejected'
const tab = ref<Tab>(props.initialTab ?? 'overview')
const filter = ref<Filter>('pending')
const filters: Filter[] = ['pending', 'approved', 'rejected', 'all']
type KnowledgeTool = 'none' | 'batch-review' | 'ai-review'
const knowledgeTool = ref<KnowledgeTool>('none')
const showBatchReview = ref(false)
const showSingleGenerate = ref(false)
const showTools = ref(false)
const questionSearch = ref('')
const knowledgeSearch = ref('')
const knowledgeStatus = ref<'all' | 'pending' | 'approved'>('all')
const busy = ref(false)
const loading = ref(true)
const editingKnowledge = ref(false)
const editingQuestion = ref(false)
const banner = ref('')
const success = ref('')
const knowledge = ref<KnowledgePoint[]>([])
const questions = ref<QuestionSummary[]>([])
const kpDetail = ref<KnowledgePointDetail | null>(null)
const questionDetail = ref<QuestionVersion | null>(null)
const selectedKpId = ref('')
const selectedQuestionId = ref('')
const reviewConfirmed = ref(false)
const answerRevealed = ref(false)
const kpName = ref('')
const kpSummary = ref('')
const kpDifficulty = ref(2)
const formStem = ref('')
const formExplanation = ref('')
const formDifficulty = ref(2)
const formOptions = ref<Record<string, string>>({ A: '', B: '', C: '', D: '' })
const formAnswer = ref<string>('A')
const formMultiple = ref<string[]>(['A','C'])
const formShortAnswer = ref('')
const useMulti = ref(true)
const useShort = ref(true)
const formBoolean = ref<boolean>(true)
const questionCount = ref(1)
const useSingle = ref(true)
const useBoolean = ref(false)
const generated = ref<GenerationResult | null>(null)
const aiStatus = ref<AIProviderStatus | null>(null)
const selectedProvider = ref<AIProviderId>('default')
const selectedModel = computed(() => {
  const id = selectedProvider.value === 'default' ? aiStatus.value?.default : selectedProvider.value
  return aiStatus.value?.providers.find(item => item.id === id)
})
void api.aiProviders().then(result => { aiStatus.value = result }).catch(() => { /* model listing optional */ })
let loadToken = 0
let kpToken = 0
let questionToken = 0

const pendingQuestions = computed(() => questions.value.filter(q => q.review_status === 'pending').length)
const pendingKnowledge = computed(() => knowledge.value.filter(k => k.review_status === 'pending').length)
const approvedQuestions = computed(() => questions.value.filter(q => q.review_status === 'approved').length)
const visibleQuestions = computed(() => questions.value.filter(q =>
  (filter.value === 'all' || q.review_status === filter.value) &&
  (!questionSearch.value.trim() || q.stem.toLocaleLowerCase().includes(questionSearch.value.trim().toLocaleLowerCase()))
))
const visibleKnowledge = computed(() => knowledge.value.filter(k =>
  (knowledgeStatus.value === 'all' || k.review_status === knowledgeStatus.value) &&
  (!knowledgeSearch.value.trim() || k.canonical_name.toLocaleLowerCase().includes(knowledgeSearch.value.trim().toLocaleLowerCase()))
))
const canGenerate = computed(() => Boolean(kpDetail.value &&
  kpDetail.value.review_status === 'approved' && kpDetail.value.sources.length > 0))
const qSourcesAreVisual = computed(() => Boolean(questionDetail.value?.sources.some(s => s.content_kind === 'figure_description')))

function statusName(status: string): string {
  return ({ pending: '待审核', approved: '已通过', rejected: '已驳回', draft: '草稿' } as Record<string, string>)[status] ?? status
}
function statusClass(status: string): string {
  return status === 'approved' ? 'approved' : status === 'rejected' ? 'rejected' : 'pending'
}
function feedback(error: unknown) {
  banner.value = error instanceof Error ? error.message : '操作失败，请检查本地服务状态。'
  success.value = ''
}
function resetFeedback() { banner.value = ''; success.value = '' }
function shortName(value: string, max = 80): string { return value.length > max ? value.slice(0, max) + '…' : value }
function asAnswer(value: string | boolean | string[]): string {
  return Array.isArray(value) ? value.join('、') : value === true ? '正确' : value === false ? '错误' : value
}

async function reload(preferKp = (props.initialKnowledgePointId || selectedKpId.value), preferQuestion = selectedQuestionId.value) {
  const token = ++loadToken
  loading.value = true
  try {
    const [k, q] = await Promise.all([api.knowledgePoints(props.courseId), api.questions(props.courseId)])
    if (token !== loadToken) return
    knowledge.value = k
    questions.value = q
    const kpId = k.some(x => x.id === preferKp) ? preferKp : (k.find(x => x.review_status === 'pending') ?? k[0])?.id ?? ''
    const questionId = q.some(x => x.version_id === preferQuestion)
      ? preferQuestion : (q.find(x => x.review_status === 'pending') ?? q[0])?.version_id ?? ''
    if (kpId) await selectKnowledge(kpId)
    else { selectedKpId.value = ''; kpDetail.value = null }
    if (questionId) await selectQuestion(questionId)
    else { selectedQuestionId.value = ''; questionDetail.value = null }
  } catch (error) {
    if (token === loadToken) feedback(error)
  } finally {
    if (token === loadToken) loading.value = false
  }
}
watch(() => props.courseId, () => {
  ++loadToken
  ++kpToken
  ++questionToken
  selectedKpId.value = ''
  selectedQuestionId.value = ''
  kpDetail.value = null
  questionDetail.value = null
  generated.value = null
  resetFeedback()
  void reload('', '')
}, { immediate: true })

watch(() => props.initialKnowledgePointId, value => {
  if (value && knowledge.value.some(point => point.id === value) && selectedKpId.value !== value) {
    void selectKnowledge(value)
  }
})

watch(() => props.initialTab, value => {
  if (value && tab.value !== value) {
    tab.value = value
    resetFeedback()
  }
})
function switchTab(value: Tab) {
  if (props.embedded) { emit('navigate-tab', value); return }
  tab.value = value
  showTools.value = false
  showBatchReview.value = false
  knowledgeTool.value = 'none'
  resetFeedback()
}
function toggleReviewMode() {
  showBatchReview.value = !showBatchReview.value
  resetFeedback()
}
function toggleKnowledgeTool(value: Exclude<KnowledgeTool, 'none'>) {
  knowledgeTool.value = knowledgeTool.value === value ? 'none' : value
  resetFeedback()
}
async function importedExternal(payload: { kind: 'questions' | 'knowledge'; ids: string[]; created: number; reused?: number }) {
  if (payload.kind === 'questions') {
    filter.value = 'pending'
    await reload(selectedKpId.value, payload.ids[0] || '')
    if (props.embedded) emit('navigate-tab', 'questions')
    else tab.value = 'questions'
    success.value = '已导入 ' + payload.created + ' 道候选题。可以先盲测，再选择批量审核或核对风险题。'
  } else {
    await reload(payload.ids[0] || '', selectedQuestionId.value)
    if (props.embedded) emit('navigate-tab', 'knowledge')
    else tab.value = 'knowledge'
    success.value = '已导入 ' + payload.created + ' 个新知识点，复用 ' + (payload.reused || 0) + ' 个；请核对并审核。'
  }
  emit('updated')
}
function chooseFilter(value: Filter) {
  filter.value = value
  if (!visibleQuestions.value.some(x => x.version_id === selectedQuestionId.value)) {
    const first = visibleQuestions.value[0]?.version_id
    if (first) void selectQuestion(first)
    else { selectedQuestionId.value = ''; questionDetail.value = null }
  }
}
async function selectKnowledge(id: string) {
  const token = ++kpToken
  selectedKpId.value = id
  kpDetail.value = null
  editingKnowledge.value = false
  generated.value = null
  resetFeedback()
  try {
    const detail = await api.knowledgePoint(id)
    if (token !== kpToken || id !== selectedKpId.value) return
    kpDetail.value = detail
    kpName.value = detail.canonical_name
    kpSummary.value = detail.summary || ''
    kpDifficulty.value = detail.difficulty || 2
  } catch (error) {
    if (token === kpToken) feedback(error)
  }
}
async function selectQuestion(id: string) {
  const token = ++questionToken
  selectedQuestionId.value = id
  answerRevealed.value = false
  questionDetail.value = null
  editingQuestion.value = false
  reviewConfirmed.value = false
  resetFeedback()
  try {
    const detail = await api.questionVersion(id)
    if (token !== questionToken || id !== selectedQuestionId.value) return
    questionDetail.value = detail
    fillQuestionForm(detail)
  } catch (error) {
    if (token === questionToken) feedback(error)
  }
}
function fillQuestionForm(q: QuestionVersion) {
  formStem.value = q.stem
  formExplanation.value = q.explanation || ''
  formDifficulty.value = q.difficulty
  formOptions.value = { A: q.options.A || '', B: q.options.B || '', C: q.options.C || '', D: q.options.D || '' }
  formAnswer.value = typeof q.correct_answer === 'string' ? q.correct_answer : 'A'
  formMultiple.value = Array.isArray(q.correct_answer) ? [...q.correct_answer] : ['A','C']
  formShortAnswer.value = typeof q.correct_answer === 'string' ? q.correct_answer : ''
  formBoolean.value = q.correct_answer === true
}
async function saveKnowledge() {
  const detail = kpDetail.value
  if (!detail || busy.value) return
  if (!kpName.value.trim() || !kpSummary.value.trim()) {
    feedback(new Error('请填写知识点名称与摘要。'))
    return
  }
  busy.value = true
  resetFeedback()
  try {
    const updated = await api.updateKnowledgePoint(detail.id, {
      canonical_name: kpName.value.trim(),
      summary: kpSummary.value.trim(),
      difficulty: kpDifficulty.value,
    })
    kpDetail.value = updated
    editingKnowledge.value = false
    success.value = updated.review_status === 'pending' ? '已保存修改。知识点需要重新审核。' : '知识点内容没有变化。'
    await reload(updated.id)
    success.value = '知识点已保存，修改后的内容处于待审核状态（如有变化）。'
    emit('updated')
  } catch (error) { feedback(error) }
  finally { busy.value = false }
}
async function reviewKnowledge(decision: 'approved' | 'rejected') {
  if (!kpDetail.value || busy.value) return
  const id = kpDetail.value.id
  busy.value = true
  resetFeedback()
  try {
    await api.reviewKnowledgePoint(id, decision)
    await reload(id)
    success.value = decision === 'approved' ? '知识点审核通过，现在可以尝试 AI 出题。' : '知识点已驳回。修改后会回到待审核。'
    emit('updated')
  } catch (error) { feedback(error) }
  finally { busy.value = false }
}
async function generate() {
  if (!kpDetail.value || !canGenerate.value || busy.value) return
  const types: ('single_choice' | 'multiple_choice' | 'true_false' | 'short_answer')[] = []
  if (useSingle.value) types.push('single_choice')
  if (useBoolean.value) types.push('true_false')
  if (useMulti.value) types.push('multiple_choice')
  if (useShort.value) types.push('short_answer')
  if (!types.length) { feedback(new Error('请至少选择一种题型。')); return }
  busy.value = true
  resetFeedback()
  generated.value = null
  const knowledgeId = kpDetail.value.id
  try {
    const result = await api.generateQuestions(knowledgeId, {
      count: questionCount.value, question_types: types, provider: selectedProvider.value,
    })
    generated.value = result
    if (result.created) {
      await reload(knowledgeId, result.question_version_ids[0])
      // reload clears generation message when selecting another knowledge point;
      // retain it here for explicit feedback.
      generated.value = result
      if (props.embedded) emit('navigate-tab', 'questions')
      else tab.value = 'questions'
      filter.value = 'pending'
      const firstId = result.question_version_ids[0]
      if (firstId) await selectQuestion(firstId)
      success.value = '生成 ' + result.created + ' 道候选题。可以先盲测，再选择审核方式。'
      emit('updated')
    } else {
      success.value = '模型已返回，但没有题目通过校验。请查看下面的警告，或换一个知识点重试。'
    }
  } catch (error) { feedback(error) }
  finally { busy.value = false }
}
function beginRevision() {
  if (!questionDetail.value) return
  fillQuestionForm(questionDetail.value)
  editingQuestion.value = true
  reviewConfirmed.value = false
  resetFeedback()
}
async function saveQuestion() {
  const q = questionDetail.value
  if (!q || busy.value) return
  if (formStem.value.trim().length < 8 || formExplanation.value.trim().length < 8) {
    feedback(new Error('题干和解析都至少需要 8 个字符。'))
    return
  }
  if (q.question_type === 'single_choice' || q.question_type === 'multiple_choice') {
    if (['A','B','C','D'].some(k => !formOptions.value[k]?.trim()) ||
        new Set(['A','B','C','D'].map(k => formOptions.value[k].trim())).size !== 4) {
      feedback(new Error('四个选项都必须填写，且不能重复。'))
      return
    }
  }
  if (q.question_type === 'multiple_choice' && formMultiple.value.length < 2) {
    feedback(new Error('多选题必须选择至少两个正确选项。')); return
  }
  if (q.question_type === 'short_answer' && formShortAnswer.value.trim().length < 8) {
    feedback(new Error('简答题必须填写至少 8 个字符的参考答案。')); return
  }
  const revision: QuestionRevision = {
    stem: formStem.value.trim(),
    explanation: formExplanation.value.trim(),
    difficulty: formDifficulty.value,
    correct_answer: q.question_type === 'single_choice' ? formAnswer.value
      : q.question_type === 'multiple_choice' ? [...formMultiple.value].sort()
      : q.question_type === 'short_answer' ? formShortAnswer.value.trim() : formBoolean.value,
  }
  if (q.question_type === 'single_choice' || q.question_type === 'multiple_choice') {
    revision.options = Object.fromEntries(['A', 'B', 'C', 'D'].map(k => [k, formOptions.value[k].trim()]))
  }
  busy.value = true
  resetFeedback()
  try {
    const next = await api.reviseQuestion(q.id, revision)
    await reload(selectedKpId.value, next.id)
    filter.value = 'pending'
    if (props.embedded) emit('navigate-tab', 'questions')
    else tab.value = 'questions'
    editingQuestion.value = false
    success.value = '已创建第 ' + next.version_no + ' 版。新版本仍需审核，旧版本不会被覆盖。'
    emit('updated')
  } catch (error) { feedback(error) }
  finally { busy.value = false }
}
async function reviewQuestion(decision: 'approved' | 'rejected') {
  const q = questionDetail.value
  if (!q || busy.value) return
  if (decision === 'approved' && !reviewConfirmed.value) {
    feedback(new Error('请先核对正确答案、解析和课件来源，然后勾选审核确认。'))
    return
  }
  busy.value = true
  resetFeedback()
  try {
    await api.reviewQuestion(q.id, decision)
    await reload(selectedKpId.value, q.id)
    filter.value = 'all'
    success.value = decision === 'approved'
      ? '题目审核通过，已进入可用于测评的题库。'
      : '题目已驳回。需要修改时可创建新版本。'
    emit('updated')
  } catch (error) { feedback(error) }
  finally { busy.value = false }
}
function previewAvailable(source: SourceEvidence): boolean {
  return source.original_filename.toLowerCase().endsWith('.pdf')
}
</script>

<template>
  <div class="qb">
    <div v-if="!props.embedded" class="section-heading qb-heading">
      <div>
        <p class="eyebrow">QUESTION WORKSPACE</p>
        <h1>题库工作台<span class="heading-dot">.</span></h1>
        <p class="section-description">
          {{ tab === 'overview' ? '准备知识、生成题目、审核入库，按自己的节奏完成每一步。'
          : tab === 'batch' ? '按章节和知识簇批量生成，不必为每个知识点单独出题。'
          : tab === 'questions' ? '集中管理候选题与正式题目，批量和逐题审核互不干扰。'
          : tab === 'knowledge' ? '整理知识点及其来源，复杂的审核工具按需打开。'
          : '导入外部 AI 的题目或知识点。' }}
        </p>
      </div>
      <div class="qb-heading-actions">
        <button v-if="tab !== 'overview'" class="button button-outline button-small"
          type="button" @click="switchTab('overview')"><LayoutDashboard :size="16"/> 返回概览</button>
        <button class="button button-outline button-small" type="button" :disabled="loading || busy"
          @click="reload()"><RefreshCw :size="16"/> 刷新</button>
      </div>
    </div>

    <div v-if="!props.embedded" class="qb-workspace-nav" role="tablist" aria-label="题库工作区">
      <button role="tab" :aria-selected="tab === 'overview'" :class="{active:tab==='overview'}" @click="switchTab('overview')"><LayoutDashboard :size="17"/> 概览</button>
      <button role="tab" :aria-selected="tab === 'batch'" :class="{active:tab==='batch'}" @click="switchTab('batch')"><Sparkles :size="17"/> 生成题目</button>
      <button role="tab" :aria-selected="tab === 'questions'" :class="{active:tab==='questions'}" @click="switchTab('questions')"><ClipboardCheck :size="17"/> 审核与题库 <span v-if="pendingQuestions" class="qb-count">{{pendingQuestions}}</span></button>
      <button role="tab" :aria-selected="tab === 'knowledge'" :class="{active:tab==='knowledge'}" @click="switchTab('knowledge')"><BookOpen :size="17"/> 知识点 <span v-if="pendingKnowledge" class="qb-count">{{pendingKnowledge}}</span></button>
      <button role="tab" :aria-selected="tab === 'external'" :class="{active:tab==='external'}" @click="switchTab('external')"><Settings2 :size="17"/> 外部工具</button>
    </div>

    <div v-if="banner" class="qb-alert qb-error" role="alert"><AlertCircle :size="18"/> {{banner}}</div>
    <div v-if="success" class="qb-alert qb-success" role="status"><CheckCircle2 :size="18"/> {{success}}</div>
    <div v-if="tab === 'knowledge' && generated?.provider" class="qb-alert qb-success" role="status">
      <Sparkles :size="17"/> 本次出题：{{ generated.model || generated.provider }}，新建 {{generated.created}} 道候选题。
    </div>
    <div v-if="tab === 'knowledge' && generated?.warnings.length" class="qb-alert qb-warning" role="status">
      <AlertCircle :size="17"/> {{generated.warnings.length}} 个候选未通过自动校验，请检查课件来源后重试。
    </div>

    <div v-if="tab === 'questions' && !loading" class="qb-section-header">
      <div><strong>{{showBatchReview?'批量审核':'题目浏览与单题审核'}}</strong>
        <p>{{showBatchReview?'程序先排除来源与结构异常的题目，再由你确认发布。':'按状态或题干搜索，选择题目查看详情。答案默认隐藏以免影响自测。'}}</p></div>
      <div class="qb-section-actions">
        <button type="button" class="button button-outline button-small"
          :disabled="!pendingQuestions" @click="emit('blind-trial')"><BookOpen :size="16"/> 候选题盲测</button>
        <button type="button" class="button button-primary button-small" @click="toggleReviewMode">
          <ClipboardCheck :size="16"/>{{showBatchReview?'返回题目列表':'批量审核（'+pendingQuestions+'）'}}
        </button>
      </div>
    </div>
    <div v-if="loading" class="qb-empty panel"><LoaderCircle class="spinning" :size="26" /> 正在读取题库…</div>
    <div v-else-if="tab === 'overview'" class="qb-overview">
      <div class="qb-overview-hero panel">
        <div><p class="eyebrow">你的题库进展</p><h2>从知识，到题目，再到练习</h2>
          <p>不需要按 1307 个知识点逐个出题或逐题审批。选择当下要做的事，即可进入对应工作区。</p></div>
        <div class="qb-overview-metrics">
          <span><strong>{{knowledge.length}}</strong>知识点</span>
          <span><strong>{{questions.length}}</strong>题目</span>
          <span><strong>{{approvedQuestions}}</strong>已入库</span>
        </div>
      </div>
      <div class="qb-steps">
        <button class="qb-step panel" type="button" @click="switchTab('knowledge')">
          <span class="qb-step-index">01</span><BookOpen :size="23"/>
          <strong>准备知识</strong><p>查看课程知识点、修订来源和审核异常项目。</p>
          <span class="qb-step-foot">{{pendingKnowledge}} 个待审核知识点 <ArrowRight :size="17"/></span>
        </button>
        <button class="qb-step panel" type="button" @click="switchTab('batch')">
          <span class="qb-step-index">02</span><Sparkles :size="23"/>
          <strong>生成题目</strong><p>按章节聚合知识点，批量生成综合题并记录进度。</p>
          <span class="qb-step-foot">进入批量出题 <ArrowRight :size="17"/></span>
        </button>
        <button class="qb-step panel" type="button" @click="switchTab('questions')">
          <span class="qb-step-index">03</span><ClipboardCheck :size="23"/>
          <strong>审核入库</strong><p>一键审核符合条件的题目，风险题集中核对。</p>
          <span class="qb-step-foot">{{pendingQuestions}} 道待审核题目 <ArrowRight :size="17"/></span>
        </button>
      </div>
      <div class="qb-overview-practice panel">
        <div><strong>想先测测自己？</strong><p>候选题可先盲测，整套答完才显示答案；未审核成绩不更新正式 BKT。</p></div>
        <button class="button button-primary" type="button" :disabled="!pendingQuestions" @click="emit('blind-trial')">
          <FileQuestion :size="17"/> 不看答案，开始盲测
        </button>
      </div>
    </div>
    <BulkQuestionStudio v-else-if="tab === 'batch'" :course-id="props.courseId" @updated="reload();emit('updated')" />
    <ExternalTransfer v-else-if="tab === 'external'" :course-id="props.courseId"
      :knowledge-points="knowledge" :preferred-knowledge-point-id="selectedKpId"
      :preferred-document-id="props.initialDocumentId" @imported="importedExternal" />

    <div v-else-if="tab === 'knowledge'" class="qb-knowledge-layout">
      <div class="qb-section-header">
        <div><strong>{{knowledgeTool==='batch-review'?'批量审核知识点':knowledgeTool==='ai-review'?'AI 知识审核':'知识点与课件来源'}}</strong>
          <p>默认浏览和修改知识点，需要大批量处理时再打开审核工具。</p></div>
        <div class="qb-section-actions">
          <button class="button button-outline button-small" :class="{ 'qb-mode-active':knowledgeTool==='batch-review' }"
            @click="toggleKnowledgeTool('batch-review')">
            <ClipboardCheck :size="16"/>{{knowledgeTool==='batch-review'?'返回知识列表':'批量通过'}}
          </button>
          <button class="button button-outline button-small" :class="{ 'qb-mode-active':knowledgeTool==='ai-review' }"
            @click="toggleKnowledgeTool('ai-review')">
            <Sparkles :size="16"/>{{knowledgeTool==='ai-review'?'返回知识列表':'AI 二次核验'}}
          </button>
        </div>
      </div>
      <DirectBulkApproval v-if="knowledgeTool==='batch-review'" :course-id="props.courseId" @updated="reload();emit('updated')" />
      <KnowledgeAIReviewPanel v-else-if="knowledgeTool==='ai-review'" :course-id="props.courseId" :points="knowledge"
        :selected-id="selectedKpId" @updated="reload();emit('updated')" @select="selectKnowledge" />
      <div v-else class="qb-columns">
      <aside class="qb-list panel">
        <div class="qb-list-head"><strong>课程知识点</strong><span>{{visibleKnowledge.length}} / {{knowledge.length}} 项</span></div>
        <div class="qb-list-search"><input v-model="knowledgeSearch" type="search" placeholder="搜索知识点名称…" aria-label="搜索知识点名称"/></div>
        <div class="qb-filter">
          <button :class="{active:knowledgeStatus==='all'}" @click="knowledgeStatus='all'">全部</button>
          <button :class="{active:knowledgeStatus==='pending'}" @click="knowledgeStatus='pending'">待审核</button>
          <button :class="{active:knowledgeStatus==='approved'}" @click="knowledgeStatus='approved'">已通过</button>
        </div>
        <div v-if="!knowledge.length" class="qb-empty-small">暂无知识点。请先导入课程资料并提取知识点。</div>
        <div class="qb-list-scroll">
          <button v-for="point in visibleKnowledge" :key="point.id" class="qb-list-item"
            :class="{ active: selectedKpId === point.id }" @click="selectKnowledge(point.id)">
            <span class="qb-list-title">{{ point.canonical_name }}</span>
            <span class="qb-list-sub"><span class="qb-pill" :class="statusClass(point.review_status)">{{ statusName(point.review_status) }}</span><ChevronRight :size="15" /></span>
          </button>
        </div>
      </aside>

      <section class="qb-detail panel">
        <div v-if="!kpDetail" class="qb-empty"><BookOpen :size="28" /> 选择左侧知识点查看详情</div>
        <template v-else>
          <div class="qb-detail-top"><span class="qb-eyebrow">KNOWLEDGE POINT</span><span class="qb-pill" :class="statusClass(kpDetail.review_status)">{{ statusName(kpDetail.review_status) }}</span></div>
          <template v-if="editingKnowledge">
            <div class="qb-form">
              <label>知识点名称<input v-model="kpName" maxlength="160" /></label>
              <label>知识点摘要<textarea v-model="kpSummary" rows="4" maxlength="2000"></textarea></label>
              <label>难度<select v-model.number="kpDifficulty"><option :value="1">1 · 基础</option><option :value="2">2 · 中等</option><option :value="3">3 · 进阶</option></select></label>
              <div class="qb-actions">
                <button class="button button-primary" :disabled="busy" @click="saveKnowledge"><LoaderCircle v-if="busy" class="spinning" :size="16" /> 保存修改</button>
                <button class="button button-outline" :disabled="busy" @click="editingKnowledge = false">取消</button>
              </div>
            </div>
          </template>
          <template v-else>
            <h2>{{ kpDetail.canonical_name }}</h2>
            <p class="qb-detail-text">{{ kpDetail.summary || '暂无摘要' }}</p>
            <div class="qb-inline-meta"><span>知识难度 {{ kpDetail.difficulty ?? '未设置' }} / 3</span><span>来源 {{ kpDetail.sources.length }} 条</span></div>
            <div class="qb-actions">
              <button class="button button-outline button-small" :disabled="busy" @click="editingKnowledge = true"><Pencil :size="16" /> 修改知识点</button>
              <button v-if="kpDetail.review_status !== 'approved'" class="button button-primary button-small" :disabled="busy" @click="reviewKnowledge('approved')"><CheckCircle2 :size="16" /> 审核通过</button>
              <button v-if="kpDetail.review_status !== 'rejected'" class="button button-danger button-small" :disabled="busy" @click="reviewKnowledge('rejected')"><XCircle :size="16" /> 驳回</button>
            </div>
          </template>

          <details class="qb-collapsible">
          <summary><FileText :size="17"/> 课件来源（{{kpDetail.sources.length}} 条） <span>展开核对</span></summary>
          <div v-if="!kpDetail.sources.length" class="qb-hint">这个知识点没有关联课件来源，暂时不能用 AI 出题。</div>
          <div v-for="source in kpDetail.sources" :key="source.chunk_id" class="qb-source">
            <div class="qb-source-heading"><span>{{ source.original_filename }} · 第 {{ source.page_start }} 页</span>
              <span v-if="source.content_kind === 'figure_description'" class="qb-pill caution"><Image :size="12" /> AI 图示描述</span>
              <span v-else class="qb-pill neutral">文本来源</span></div>
            <p>{{ source.evidence_note || '暂无引文说明' }}</p>
            <a v-if="previewAvailable(source)" :href="sourcePreviewUrl(source)" target="_blank" rel="noopener noreferrer">查看 PDF 原始页面 <ArrowRight :size="14" /></a>
          </div>

          </details>
          <div class="qb-knowledge-generate-shortcut">
            <div><strong>需要生成题目？</strong><p>建议按章节批量出题。只有特殊知识点才需要单独生成。</p></div>
            <button class="button button-outline button-small" type="button" @click="switchTab('batch')"><Sparkles :size="16"/> 批量出题</button>
            <button class="button button-outline button-small" type="button"
              @click="showSingleGenerate=!showSingleGenerate">{{showSingleGenerate?'收起单点出题':'单点出题（高级）'}}</button>
          </div>
          <div v-if="showSingleGenerate" class="qb-single-generate">
          <div class="qb-source-title"><Sparkles :size="18"/><strong>基于当前知识点生成</strong></div>
          <p class="qb-hint">使用本地 Qwen 生成题目，生成结果均为待审核。图示描述可能包含识别误差，请核对原图。</p>
          <div class="qb-generation-form">
            <label>出题模型<select v-model="selectedProvider" :disabled="busy">
              <option value="default">默认模型（{{ aiStatus?.default === 'mimo' ? 'MiMo' : 'Ollama' }}）</option>
              <option value="ollama" :disabled="aiStatus ? !aiStatus.providers.find(p => p.id === 'ollama')?.available : false">本地 Ollama</option>
              <option value="mimo" :disabled="aiStatus ? !aiStatus.providers.find(p => p.id === 'mimo')?.available : false">小米 MiMo · Function Calling</option>
            </select></label>
            <label>数量<select v-model.number="questionCount" :disabled="busy"><option :value="1">1 道</option><option :value="2">2 道</option><option :value="3">3 道</option><option :value="4">4 道</option></select></label>
            <fieldset :disabled="busy"><legend>题型</legend><label><input v-model="useSingle" type="checkbox" /> 单选题</label><label><input v-model="useBoolean" type="checkbox" /> 判断题</label>
            <label><input v-model="useMulti" type="checkbox" /> 多选题</label>
            <label><input v-model="useShort" type="checkbox" /> 简答题</label></fieldset>
          </div>
          <p class="qb-hint" v-if="aiStatus && !aiStatus.providers.some(p => p.id === 'mimo' && p.available)">
            尚未配置小米 MiMo：请在左侧「AI 模型设置」中填写按量付费 API Key（sk-），保存后即可使用。
          </p>
          <p class="qb-hint" v-if="selectedModel?.id === 'mimo'">MiMo {{ selectedModel.model }}：按需调用课件检索函数；不会预先上传整份课件。API Key 仅配置在后端。</p>
          <p class="qb-hint" v-else>本地 {{ selectedModel?.model || 'Ollama' }}：只使用知识点关联的少量课件片段。</p>
          <button class="button button-primary qb-generate-button" :disabled="!canGenerate || busy || editingKnowledge || selectedModel?.available === false" @click="generate">
            <LoaderCircle v-if="busy" class="spinning" :size="17" /><Sparkles v-else :size="17" /> {{ busy ? 'AI 正在生成或保存…' : 'AI 生成待审核题目' }}
            <ArrowRight :size="17" />
          </button>
          <div v-if="!canGenerate" class="qb-hint">请先审核通过知识点，并确保它关联了课件来源。</div>
          <div v-if="generated && generated.warnings.length" class="qb-warning">
            <strong>模型输出校验提醒</strong>
            <p v-for="(warning, i) in generated.warnings" :key="i">{{ warning }}</p>
          </div>
          </div>
        </template>
      </section>
      </div>
    </div>

    <QuestionBulkApproval v-else-if="tab === 'questions' && showBatchReview" :course-id="props.courseId" @updated="reload();emit('updated')" />
    <div v-else-if="tab === 'questions'" class="qb-columns">
      <aside class="qb-list panel">
        <div class="qb-list-head"><strong>课程题目</strong><span>{{visibleQuestions.length}} / {{questions.length}} 道</span></div>
        <div class="qb-list-search"><input v-model="questionSearch" type="search" placeholder="搜索题干关键词…" aria-label="搜索题干"/></div>
        <div class="qb-filter">
          <button v-for="f in filters" :key="f"
            :class="{ active: filter === f }" @click="chooseFilter(f)">{{ f === 'all' ? '全部' : statusName(f) }}</button>
        </div>
        <div v-if="!visibleQuestions.length" class="qb-empty-small">
          {{ questionSearch.trim() ? '没有找到匹配的题目。' : filter === 'pending' ? '暂无待审核题目，可以进入生成中心创建题目。' : '当前筛选下暂无题目。' }}
          <button v-if="filter === 'pending' && !questionSearch.trim()" class="qb-text-button" @click="switchTab('batch')">去批量出题 <ArrowRight :size="15" /></button>
        </div>
        <div class="qb-list-scroll">
          <button v-for="item in visibleQuestions" :key="item.version_id" class="qb-list-item"
            :class="{ active: selectedQuestionId === item.version_id }" @click="selectQuestion(item.version_id)">
            <span class="qb-list-title">{{ shortName(item.stem) }}</span>
            <span class="qb-list-sub">
              <span class="qb-pill" :class="statusClass(item.review_status)">{{ statusName(item.review_status) }}</span>
              <span>{{ item.question_type === 'single_choice' ? '单选' : '判断' }} · V{{ item.version_no }}</span>
              <ChevronRight :size="15" />
            </span>
          </button>
        </div>
      </aside>

      <section class="qb-detail panel">
        <div v-if="!questionDetail" class="qb-empty"><FileQuestion :size="28" /> 选择左侧题目查看详情</div>
        <template v-else>
          <div class="qb-detail-top"><span class="qb-eyebrow">QUESTION · VERSION {{ questionDetail.version_no }}</span>
            <span class="qb-pill" :class="statusClass(questionDetail.review_status)">{{ statusName(questionDetail.review_status) }}</span></div>
          <template v-if="editingQuestion">
            <div class="qb-form">
              <label>题干<textarea v-model="formStem" rows="3" maxlength="600"></textarea></label>
              <template v-if="questionDetail.question_type === 'single_choice' || questionDetail.question_type === 'multiple_choice'">
                <label v-for="letter in ['A','B','C','D']" :key="letter">选项 {{ letter }}
                  <input v-model="formOptions[letter]" maxlength="250" /></label>
                <label v-if="questionDetail.question_type === 'single_choice'">正确答案<select v-model="formAnswer"><option v-for="letter in ['A','B','C','D']" :key="letter" :value="letter">{{ letter }}</option></select></label>
                <fieldset v-else><legend>正确选项（至少两项）</legend><label v-for="letter in ['A','B','C','D']" :key="letter"><input v-model="formMultiple" type="checkbox" :value="letter"/> {{letter}}</label></fieldset>
              </template>
              <label v-else-if="questionDetail.question_type === 'true_false'">正确答案<select v-model="formBoolean"><option :value="true">正确</option><option :value="false">错误</option></select></label>
              <label v-else>参考答案<textarea v-model="formShortAnswer" rows="6" maxlength="2000"></textarea></label>
              <label>题目解析<textarea v-model="formExplanation" rows="4" maxlength="1500"></textarea></label>
              <label>难度<select v-model.number="formDifficulty"><option :value="1">1 · 基础</option><option :value="2">2 · 中等</option><option :value="3">3 · 进阶</option></select></label>
              <p class="qb-hint">保存修改将创建新版本（待审核），不会覆盖曾用于测评的旧版本。原有课件证据保持不变，修改答案后请重新核对来源。</p>
              <div class="qb-actions"><button class="button button-primary" :disabled="busy" @click="saveQuestion"><LoaderCircle v-if="busy" class="spinning" :size="16" /> 保存为新版本</button>
                <button class="button button-outline" :disabled="busy" @click="editingQuestion = false">取消</button></div>
            </div>
          </template>
          <template v-else>
            <div class="qb-inline-meta"><span>{{ ({single_choice:'单选题',multiple_choice:'多选题',true_false:'判断题',short_answer:'简答题'} as Record<string,string>)[questionDetail.question_type] }}</span><span>难度 {{ questionDetail.difficulty }}/3</span></div>
            <h2>{{ questionDetail.stem }}</h2>
            <div v-if="questionDetail.review_status === 'pending' && !answerRevealed" class="qb-warning">
              <strong>候选题答案已遮盖，避免影响自测</strong>
              <p>题目选项可以正常阅读。若你是准备人工核对，可主动揭晓；若要自测请用上方「不看答案直接盲测」。</p>
              <button class="button button-outline button-small" type="button" @click="answerRevealed = true">
                显示正确答案与解析（会剧透）
              </button>
            </div>
            <div class="qb-options" v-if="questionDetail.question_type === 'single_choice' || questionDetail.question_type === 'multiple_choice'">
              <div v-for="letter in ['A','B','C','D']" :key="letter"
                :class="{ correct: (questionDetail.review_status !== 'pending' || answerRevealed) && (Array.isArray(questionDetail.correct_answer) ? questionDetail.correct_answer.includes(letter) : (Array.isArray(questionDetail.correct_answer) ? questionDetail.correct_answer.includes(letter) : questionDetail.correct_answer === letter)) }">
                <strong>{{ letter }}</strong><span>{{ questionDetail.options[letter] }}</span>
                <CheckCircle2 v-if="(questionDetail.review_status !== 'pending' || answerRevealed) && questionDetail.correct_answer === letter" :size="18" />
              </div>
            </div>
            <div class="qb-options" v-else-if="questionDetail.question_type === 'true_false'">
              <div :class="{ correct: (questionDetail.review_status !== 'pending' || answerRevealed) && questionDetail.correct_answer === true }"><strong>A</strong><span>正确</span></div>
              <div :class="{ correct: (questionDetail.review_status !== 'pending' || answerRevealed) && questionDetail.correct_answer === false }"><strong>B</strong><span>错误</span></div>
            </div>
            <p v-else-if="questionDetail.question_type === 'short_answer'" class="qb-hint">本题为开放式作答，请在练习页面填写答案后对照参考要点。</p>
            <div v-if="questionDetail.review_status !== 'pending' || answerRevealed" class="qb-explanation"><strong>正确答案：{{ asAnswer(questionDetail.correct_answer) }}</strong><span>题目解析</span><p>{{ questionDetail.explanation || '暂无解析' }}</p></div>
            <div class="qb-actions"><button class="button button-outline button-small" :disabled="busy" @click="beginRevision"><Pencil :size="16" /> 修改并创建新版本</button></div>
          </template>

          <div class="qb-section-divider"></div>
          <div class="qb-source-title"><BookOpen :size="18"/><strong>本题实际考查的知识点</strong></div>
          <div class="qb-inline-meta">
            <span v-for="point in questionDetail.knowledge_points" :key="point.knowledge_point_id">
              {{ point.is_primary ? '主考查 · ' : '关联考查 · ' }}
              {{ knowledge.find(k => k.id === point.knowledge_point_id)?.canonical_name || point.knowledge_point_id }}
            </span>
          </div>
          <p v-if="questionDetail.knowledge_points.length > 1" class="qb-hint">
            组合题的多个考点由 AI 提议、课件引用辅助核验，仍需逐项人工审核。一次答对不能证明所有考点均已掌握。
          </p>
          <details class="qb-collapsible qb-question-sources">
          <summary><ShieldCheck :size="18"/> 课件引用与原文核验（{{questionDetail.sources.length}} 条） <span>按需展开</span></summary>
          <div v-if="questionDetail.review_status === 'pending' && !answerRevealed" class="qb-hint">
            课件引文可能直接包含正确答案，已随答案一并隐藏。需要审核时请主动点击上方「显示正确答案与解析」。
          </div>
          <div v-for="source in (questionDetail.review_status === 'pending' && !answerRevealed ? [] : questionDetail.sources)" :key="source.chunk_id" class="qb-source">
            <div class="qb-source-heading"><span>{{ source.original_filename }} · 第 {{ source.page_start }} 页</span>
              <span v-if="source.content_kind === 'figure_description'" class="qb-pill caution"><Image :size="12" /> 未验证视觉描述</span>
              <span v-else class="qb-pill neutral">文本引用</span></div>
            <p>{{ source.evidence_note || '暂无引文说明' }}</p>
            <a v-if="previewAvailable(source)" :href="sourcePreviewUrl(source)" target="_blank" rel="noopener noreferrer">打开 PDF 原页核对 <ArrowRight :size="14" /></a>
          </div>
          <div v-if="qSourcesAreVisual && (questionDetail.review_status !== 'pending' || answerRevealed)" class="qb-warning"><AlertCircle :size="17" /><span>此题来源包含 AI 对图片的描述。即使引文匹配，也不能证明原图识别和答案一定正确。请打开原页检查。</span></div>
          </details>

          <template v-if="!editingQuestion && (questionDetail.review_status === 'pending' || questionDetail.review_status === 'draft')">
            <div class="qb-section-divider"></div>
            <label class="qb-confirm"><input v-model="reviewConfirmed" type="checkbox" /><span>我已核对课件来源、正确答案、干扰选项和解析，确认可以用于正式练习。</span></label>
            <div class="qb-actions"><button class="button button-primary" :disabled="busy || !reviewConfirmed" @click="reviewQuestion('approved')"><CheckCircle2 :size="17" /> 审核通过</button>
              <button class="button button-danger" :disabled="busy" @click="reviewQuestion('rejected')"><XCircle :size="17" /> 驳回题目</button></div>
          </template>
          <div v-if="questionDetail.review_status !== 'pending' && questionDetail.review_status !== 'draft'" class="qb-hint qb-reviewed">
            <ClipboardCheck :size="17" /> 本版本已审核。需要修改时请创建新版本并重新审核。
          </div>
        </template>
      </section>
    </div>
    <p class="qb-bottom-note">这是本地单用户管理工作台。管理员可以查看题目答案；真实多用户上线前需要加入权限控制。</p>
  </div>
</template>

<style scoped>
.qb { display:grid; gap:22px; min-width:0; animation:rise .25s ease }
.qb-intro { display:flex; align-items:center; gap:18px; padding:21px 26px; background:linear-gradient(100deg,#fff,#f4fbf9) }
.qb-intro-icon { width:56px;height:56px;display:grid;place-items:center;border-radius:16px;background:#e0f5ef;color:#159b89;flex:0 0 56px }
.qb-intro > div:nth-child(2) { flex:1 }
.qb-intro strong { font-size:15px }
.qb-intro p { font-size:11px;color:#81969f;margin:6px 0 0;line-height:1.8 }
.qb-stats { display:flex;align-items:center;gap:18px;white-space:nowrap }
.qb-stats > span { display:flex;flex-direction:column;align-items:center;font-size:23px;font-weight:800;color:#194453 }
.qb-stats small { font-size:10px;color:#91a3ac;font-weight:600 }
.qb-alert { display:flex;align-items:flex-start;gap:10px;padding:13px 16px;border-radius:10px;font-size:12px;line-height:1.7 }
.qb-error { background:#fff5ef;color:#a85b3d;border:1px solid #f1cfb9 }
.qb-success { background:#eaf8f1;color:#19795b;border:1px solid #c3e9d6 }
.qb-tabs { display:flex;align-items:center;gap:9px;border-bottom:1px solid #e4ebed }
.qb-tabs button { display:flex;align-items:center;gap:9px;padding:13px 19px;background:none;border:0;border-bottom:3px solid transparent;color:#869da6;font-weight:750;font-size:13px }
.qb-tabs button.active { color:#128e82;border-bottom-color:#17b09e }
.qb-count { display:inline-grid;place-items:center;width:21px;height:21px;border-radius:50%;background:#fff2e7;color:#bd7f45;font-size:11px }
.qb-columns { display:grid;grid-template-columns:minmax(230px,.75fr) minmax(0,1.65fr);gap:18px;align-items:start }
.qb-list { overflow:hidden }
.qb-list-head { display:flex;align-items:center;justify-content:space-between;gap:10px;padding:22px;border-bottom:1px solid #edf1f2 }
.qb-list-head strong { font-size:14px }
.qb-list-head > span { font-size:11px;color:#9caeb8 }
.qb-list-scroll { max-height:680px;overflow:auto }
.qb-list-item { width:100%;display:grid;gap:9px;text-align:left;border:0;border-left:3px solid transparent;border-bottom:1px solid #f0f3f4;background:#fff;padding:16px 19px;color:#233f4c }
.qb-list-item:hover { background:#f7fbfa }
.qb-list-item.active { background:#ecf9f5;border-left-color:#20aa98 }
.qb-list-title { font-size:12px;font-weight:750;line-height:1.8;word-break:break-word }
.qb-list-sub { display:flex;align-items:center;gap:10px;color:#94a5af;font-size:10px }
.qb-list-sub svg { margin-left:auto;flex:0 0 15px }
.qb-filter { display:flex;flex-wrap:wrap;gap:5px;padding:12px 13px;background:#fbfdfd }
.qb-filter button { border:0;border-radius:7px;background:transparent;color:#8499a5;font-size:11px;padding:7px 9px }
.qb-filter button.active { color:#118d80;background:#e2f6ee;font-weight:800 }
.qb-detail { padding:26px 30px 30px; min-height:405px;min-width:0 }
.qb-detail-top { display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:19px }
.qb-eyebrow { letter-spacing:1.5px;color:#1ba596;font-size:10px;font-weight:800 }
.qb-detail h2 { font-size:19px;line-height:1.75;margin:0 0 13px;word-break:break-word }
.qb-detail-text { font-size:12px;line-height:2.05;color:#617e89;margin-bottom:17px;white-space:pre-wrap }
.qb-inline-meta { display:flex;gap:10px;flex-wrap:wrap;margin-bottom:17px }
.qb-inline-meta span { color:#7895a0;font-size:11px;padding:6px 9px;border-radius:6px;background:#f2f7f8 }
.qb-pill { display:inline-flex;align-items:center;gap:4px;padding:5px 9px;border-radius:20px;font-size:10px;font-weight:750;white-space:nowrap }
.qb-pill.pending { color:#b97840;background:#fff1e5 }
.qb-pill.approved { color:#16906c;background:#e4f8ed }
.qb-pill.rejected { color:#b96967;background:#fff0ee }
.qb-pill.caution { color:#ae7a41;background:#fff0db }
.qb-pill.neutral { color:#567c94;background:#eaf2f8 }
.qb-actions { display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin-top:18px }
.qb-actions .button { min-height:40px }
.button-danger { color:#b75a52;background:#fff6f5;border-color:#f4d6d2 }
.button-danger:not(:disabled):hover { background:#ffece9 }
.qb-section-divider { height:1px;background:#edf2f3;margin:27px 0 22px }
.qb-source-title { display:flex;align-items:center;gap:9px;color:#1f5360;margin-bottom:12px;font-size:12px }
.qb-source-title svg { color:#159a89 }
.qb-source { background:#f8fbfc;border:1px solid #e7eef0;border-radius:11px;padding:13px 15px;margin:10px 0 }
.qb-source-heading { display:flex;align-items:center;justify-content:space-between;gap:9px;flex-wrap:wrap }
.qb-source-heading > span:first-child { font-size:11px;font-weight:800;color:#486671;word-break:break-all }
.qb-source p { margin:9px 0 0;color:#6c8691;font-size:11px;line-height:1.9;white-space:pre-wrap;overflow-wrap:anywhere }
.qb-source a { color:#168e83;font-weight:800;font-size:11px;display:inline-flex;gap:6px;align-items:center;margin-top:10px }
.qb-hint { color:#8a9ea8;font-size:11px;line-height:1.85;margin:10px 0 }
.qb-generation-form { display:flex;flex-wrap:wrap;align-items:end;gap:20px;margin:17px 0 }
.qb-generation-form > label { font-size:11px;font-weight:800;color:#486671;display:grid;gap:8px }
.qb-generation-form select { border:1px solid #dce7e9;border-radius:8px;min-height:39px;padding:5px 28px 5px 12px;background:#fff;color:#254956 }
.qb-generation-form fieldset { display:flex;flex-wrap:wrap;gap:15px;border:0;margin:0;padding:0;font-size:11px }
.qb-generation-form legend { font-weight:800;color:#486671;margin-bottom:8px }
.qb-generation-form fieldset label { display:flex;gap:7px;align-items:center;color:#43636c }
.qb input[type=checkbox] { accent-color:#149b88;width:16px;height:16px;cursor:pointer }
.qb-generate-button { margin-top:4px;min-width:228px }
.qb-generate-button svg:last-child { margin-left:auto }
.qb-warning { border:1px solid #f0d8ac;border-radius:10px;background:#fff9ed;padding:12px 14px;color:#986d3c;font-size:11px;line-height:1.9;margin-top:14px;display:flex;align-items:flex-start;gap:9px }
.qb-warning strong { display:block }
.qb-warning p { margin:5px 0 }
.qb-options { display:grid;gap:9px;margin:19px 0 }
.qb-options > div { display:flex;gap:13px;align-items:center;padding:11px 13px;border-radius:9px;border:1px solid #e6eef0;font-size:12px;color:#3d5967;min-height:48px }
.qb-options > div.correct { background:#eafaef;border-color:#89d9b0;color:#176f59 }
.qb-options strong { display:grid;place-items:center;width:27px;height:27px;background:#eff4f6;border-radius:7px;flex:0 0 27px }
.qb-options .correct strong { background:#24a383;color:#fff }
.qb-options span { flex:1 }
.qb-explanation { padding:17px 20px;border-radius:11px;background:#f0f8f6;display:grid;gap:9px;color:#2d7569 }
.qb-explanation strong { font-size:12px }
.qb-explanation > span { border-top:1px solid #dceee8;padding-top:10px;font-size:11px;font-weight:800 }
.qb-explanation p { margin:0;font-size:12px;line-height:1.95;white-space:pre-wrap;overflow-wrap:anywhere }
.qb-form { display:grid;gap:15px }
.qb-form label { display:grid;gap:8px;font-weight:800;font-size:11px;color:#4e727c }
.qb-form input:not([type=checkbox]),.qb-form textarea,.qb-form select { border:1px solid #d9e5e9;border-radius:9px;min-height:41px;padding:10px 12px;color:#214454;background:#fff;font:inherit;font-size:12px;width:100% }
.qb-form textarea { resize:vertical;line-height:1.8 }
.qb-confirm { display:flex;align-items:flex-start;gap:10px;font-size:12px;line-height:1.85;color:#416b70;cursor:pointer }
.qb-confirm input { flex:0 0 16px;margin-top:4px }
.qb-reviewed { display:flex;align-items:center;gap:9px;margin-top:20px }
.qb-text-button { display:flex;align-items:center;gap:5px;color:#129b88;border:0;background:none;padding:10px 0;font-size:12px;font-weight:800 }
.qb-empty { min-height:270px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;color:#91aab4;text-align:center;font-size:12px;padding:25px }
.qb-empty-small { color:#8ba2ab;font-size:12px;line-height:2;padding:21px }
.qb-bottom-note { font-size:10px;line-height:1.8;color:#94a8b2;margin:0 }
@media(max-width:1050px) { .qb-columns { grid-template-columns:1fr } .qb-list-scroll { max-height:280px } }
@media(max-width:700px) {
  .qb-intro { flex-wrap:wrap;padding:18px }
  .qb-intro > div:nth-child(2) { flex-basis:calc(100% - 76px) }
  .qb-stats { width:100%;justify-content:flex-start }
  .qb-detail { padding:19px }
  .qb-tabs button { font-size:11px;padding:11px 9px;gap:6px }
}
.qb-knowledge-layout{display:grid;gap:17px;min-width:0}

/* Workflow-first information architecture */
.qb{gap:16px}
.qb-heading{align-items:center;gap:18px}
.qb-heading-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.qb-workspace-nav{display:flex;align-items:center;gap:5px;padding:5px;background:#eef4f5;border:1px solid #dfe9ea;border-radius:14px;overflow-x:auto;scrollbar-width:thin}
.qb-workspace-nav button{display:inline-flex;align-items:center;justify-content:center;gap:9px;padding:12px 17px;white-space:nowrap;border:1px solid transparent;border-radius:10px;background:transparent;color:#698792;cursor:pointer;font-size:13px;font-weight:720;transition:background .2s,color .2s,border-color .2s}
.qb-workspace-nav button:hover{color:#216d70;background:#f8fcfc}
.qb-workspace-nav button.active{color:#147e73;background:#fff;border-color:#d4e6e4;box-shadow:0 2px 10px rgba(34,92,94,.06)}
.qb-section-header{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:17px 20px;border-radius:13px;background:#f8fbfc;border:1px solid #e0eaec;flex-wrap:wrap}
.qb-section-header strong{font-size:16px;color:#214751}
.qb-section-header p{font-size:12px;color:#78909b;line-height:1.7;margin:5px 0 0}
.qb-section-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.qb-section-actions button{white-space:nowrap}
.qb-mode-active{border-color:#74c6b7!important;color:#167e71!important;background:#e5f7f1!important}
.qb-overview{display:grid;gap:17px}
.qb-overview-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:30px 32px;background:linear-gradient(110deg,#ffffff 20%,#effaf8 100%)}
.qb-overview-hero h2{font-size:24px;line-height:1.45;color:#1c4b55;margin:5px 0 8px}
.qb-overview-hero p:not(.eyebrow){font-size:13px;line-height:1.8;color:#68868f;max-width:620px;margin:0}
.qb-overview-metrics{display:flex;gap:20px;flex-shrink:0}
.qb-overview-metrics span{display:flex;flex-direction:column;align-items:center;color:#6a9198;font-size:11px;gap:5px}
.qb-overview-metrics strong{font-size:29px;line-height:1.2;font-variant-numeric:tabular-nums;color:#198e83}
.qb-steps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.qb-step{min-width:0;display:flex;flex-direction:column;align-items:flex-start;gap:12px;padding:23px 23px 19px;border:1px solid #dfe9e9;background:#fff;text-align:left;cursor:pointer;color:#188f82;transition:transform .2s,box-shadow .2s,border-color .2s}
.qb-step:hover{transform:translateY(-2px);border-color:#9ccfc5;box-shadow:0 12px 24px rgba(16,94,88,.07)}
.qb-step-index{font-size:12px;letter-spacing:1.5px;color:#98b0b7;font-weight:800}
.qb-step strong{font-size:18px;line-height:1.35;color:#294e57}
.qb-step p{font-size:13px;line-height:1.85;color:#738c97;margin:0;flex:1}
.qb-step-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;border-top:1px solid #e8eff0;padding-top:14px;font-size:12px;color:#158e81;font-weight:700}
.qb-overview-practice{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:19px 24px}
.qb-overview-practice strong{font-size:15px;color:#234b56}
.qb-overview-practice p{font-size:12px;color:#718c97;line-height:1.7;margin:5px 0 0}
.qb-list-search{padding:12px 14px;border-bottom:1px solid #edf2f3}
.qb-list-search input{width:100%;min-height:40px;border-radius:9px;border:1px solid #d5e4e7;background:#fafcfc;color:#2a505c;padding:10px 12px;font-size:13px;outline:none}
.qb-list-search input:focus{border-color:#52bbaa;box-shadow:0 0 0 3px rgba(29,167,145,.1)}
.qb-list-head{padding:17px 19px}
.qb-list-title{font-size:13px;line-height:1.6}
.qb-list-sub{font-size:11px}
.qb-detail{padding:24px;min-height:400px}
.qb-detail h2{font-size:20px;line-height:1.6}
.qb-detail-text,.qb-options>div{font-size:13px}
.qb-source-title,.qb-hint,.qb-warning,.qb-source p{font-size:12px}
.qb-source-heading>span:first-child{font-size:12px}
.qb-advanced-details,.qb-collapsible{border:1px solid #e0eaec;border-radius:11px;background:#fafcfc;margin-top:17px;padding:0 15px 15px}
.qb-collapsible:not([open]){padding-bottom:0}
.qb-collapsible>summary{cursor:pointer;display:flex;align-items:center;gap:9px;padding:14px 0;color:#345b65;font-weight:750;font-size:13px;list-style:none}
.qb-collapsible>summary::-webkit-details-marker{display:none}
.qb-collapsible>summary span{margin-left:auto;font-size:11px;font-weight:500;color:#76959d}
.qb-collapsible[open]>summary{border-bottom:1px solid #e5edee;margin-bottom:12px}
.qb-knowledge-generate-shortcut{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:20px;padding:15px 17px;border:1px solid #dce9e9;border-radius:11px;background:#f6faf9}
.qb-knowledge-generate-shortcut>div{flex:1;min-width:200px}
.qb-knowledge-generate-shortcut strong{font-size:13px;color:#27545d}
.qb-knowledge-generate-shortcut p{font-size:11px;color:#73919a;line-height:1.7;margin:4px 0 0}
.qb-single-generate{margin-top:14px;padding:18px;background:#f7fafb;border:1px solid #dfe8ea;border-radius:12px}
.qb-knowledge-layout{gap:14px}
.qb-question-sources{margin:22px 0}
@media(max-width:1100px){
  .qb-overview-hero{align-items:flex-start;flex-direction:column}
  .qb-overview-metrics{width:100%;justify-content:flex-start}
}
@media(max-width:800px){
  .qb-steps{grid-template-columns:1fr}
  .qb-overview-hero{padding:23px}
  .qb-overview-practice{align-items:flex-start;flex-direction:column}
  .qb-workspace-nav button{padding:11px 13px;font-size:12px}
  .qb-heading{align-items:flex-start;flex-direction:column}
}
@media(max-width:520px){
  .qb-heading-actions{width:100%}
  .qb-section-header{padding:14px}
  .qb-section-actions{width:100%}
  .qb-section-actions .button{flex:1;justify-content:center}
  .qb-overview-metrics{gap:18px}
  .qb-overview-metrics strong{font-size:24px}
  .qb-step{padding:18px}
}

</style>
