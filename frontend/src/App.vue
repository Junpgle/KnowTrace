<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue'
import {
  Activity, AlertCircle, ArrowRight, Award, BookOpen, Check,
  CheckCircle2, ChevronDown, ChevronRight, CircleHelp, ClipboardList,
  Clock3, FileQuestion, GraduationCap, LayoutDashboard, Lightbulb,
  LoaderCircle, Menu, Play, RefreshCw, RotateCcw, Sparkles, Target,
  TrendingUp, X, XCircle, FolderOpen, Network, Plus, Upload, Settings,
} from '@lucide/vue'
const CourseContentHub = defineAsyncComponent(() => import('./components/CourseContentHub.vue'))
const KnowledgeGalaxy = defineAsyncComponent(() => import('./components/KnowledgeGalaxy.vue'))
import AITaskCenter from './components/AITaskCenter.vue'
const AISettings = defineAsyncComponent(() => import('./components/AISettings.vue'))
import { api, ApiError, type AnswerFeedback, type Course, type KnowledgeReport,
  type LearningReport, type QuestionSummary, type QuizAnswerResult,
  type QuizItem, type QuizSession, type KnowledgePoint, type QuestionQuality } from './api'

type View = 'overview' | 'practice' | 'report' | 'content' | 'galaxy' | 'settings'
type Choice = string | boolean | string[] | null
const view = ref<View>('overview')
const mobileMenu = ref(false)
const showCourseForm = ref(false)
const newCourseName = ref('')
const newCourseDescription = ref('')
const creatingCourse = ref(false)
const contentSection = ref<'materials' | 'knowledge' | 'relations' | 'questions'>('materials')
const loading = ref(true)
const refreshing = ref(false)
const starting = ref(false)
const submitting = ref(false)
const connected = ref(false)
const banner = ref('')
const courses = ref<Course[]>([])
const courseId = ref('')
const questions = ref<QuestionSummary[]>([])
const knowledgePoints = ref<KnowledgePoint[]>([])
const report = ref<LearningReport | null>(null)
const quiz = ref<QuizSession | null>(null)
const count = ref(1)
const quizMode = ref<'normal' | 'adaptive' | 'blind'>('normal')
const quality = ref<QuestionQuality | null>(null)
const index = ref(0)
const selected = ref<Choice>(null)
const questionStarted = ref(Date.now())
const pendingRequest = ref<{ itemId: string, key: string, response: string | boolean | string[], elapsedMs: number } | null>(null)
const latestMastery = ref<{ itemId: string, before: number, after: number } | null>(null)
const grading = ref(false)
const gradingProvider = ref<'default'|'ollama'|'mimo'>('default')
const gradingNotice = ref('')

const currentCourse = computed(() => courses.value.find(c => c.id === courseId.value))
const approvedCount = computed(() => {
  const approvedIds = new Set(knowledgePoints.value.filter(k => k.review_status === 'approved').map(k => k.id))
  return questions.value.filter(q => q.review_status === 'approved' &&
    q.primary_knowledge_point_id !== null && approvedIds.has(q.primary_knowledge_point_id)).length
})
const pendingCount = computed(() => questions.value.filter(q => q.review_status === 'pending').length)
const maxCount = computed(() => Math.min(20,
  quizMode.value === 'adaptive' ? (quality.value?.latest_questions.approved_diagnostic ?? 0)
    : quizMode.value === 'blind' ? pendingCount.value : approvedCount.value))
const countOptions = computed(() => Array.from({ length: maxCount.value }, (_, i) => i + 1))
const currentItem = computed<QuizItem | null>(() => quiz.value?.items[index.value] ?? null)
const currentResult = computed<QuizAnswerResult | undefined>(() => currentItem.value?.result)
const currentOptions = computed(() => {
  if (!currentItem.value) return [] as { key: string | boolean, label: string, text: string }[]
  if (currentItem.value.question_type === 'true_false')
    return [{ key: true, label: 'A', text: '正确' }, { key: false, label: 'B', text: '错误' }]
  if (currentItem.value.question_type === 'short_answer') return []
  return ['A', 'B', 'C', 'D'].map(key => ({ key, label: key, text: currentItem.value?.options[key] ?? '' }))
})
const effectiveChoice = computed<Choice>(() =>
  currentItem.value?.answered ? (currentResult.value?.response ?? currentItem.value.submitted_response ?? null)
    : selected.value)
const evidenceSummary = computed(() => report.value?.evidence_summary)
const revealExperimentalBkt = ref(false)
const bktExperimentalMean = computed<number | null>(() => {
  // Not a course-level mastery rate. Never pool a sparse set of BKT states
  // into an official skill percentage; show this only as optional diagnostic.
  const vals = (report.value?.knowledge_points ?? [])
    .filter(p => p.evidence_level === 'repeated' && p.p_mastery !== null)
    .map(p => p.p_mastery as number)
  return vals.length ? vals.reduce((a,b)=>a+b,0)/vals.length : null
})
const sortedKnowledge = computed(() => [...(report.value?.knowledge_points ?? [])].sort((a, b) => {
  if (Boolean(a.observations) !== Boolean(b.observations)) return a.observations ? -1 : 1
  return (a.p_mastery ?? 1) - (b.p_mastery ?? 1)
}))
const viewTitle = computed(() => ({ overview: '学习总览', practice: '专项练习', report: '学习分析', content: '课程内容', galaxy: '知识图谱', settings: '模型设置' })[view.value])
const isComplete = computed(() => quiz.value?.status === 'completed')
const canAnswer = computed(() => Boolean(currentItem.value && !currentItem.value.answered
  && selected.value !== null && !submitting.value
  && (Array.isArray(selected.value) ? selected.value.length > 0 : 
      typeof selected.value !== 'string' || selected.value.trim().length > 0)
  && (!pendingRequest.value ||
      JSON.stringify(pendingRequest.value.response) === JSON.stringify(selected.value))))

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : Math.round(value * 100) + '%'
}
function masteryClass(value: number | null): string {
  return value === null ? 'unknown' : value >= .8 ? 'strong' : value >= .5 ? 'growing' : 'needs-work'
}
function masteryLabel(point: KnowledgeReport): string {
  if (point.evidence_level === 'unobserved') return '尚未测评'
  if (point.evidence_level === 'limited') return '数据不足'
  return '重复观测 · 未校准'
}
function showError(error: unknown) {
  banner.value = error instanceof Error ? error.message : '操作失败，请稍后再试。'
}
function sessionKey(id: string) { return 'knowtrace:last-session:' + id }
function chooseIndex(value: number) {
  if (!quiz.value?.items[value]) return
  index.value = value
  selected.value = null
  pendingRequest.value = null
  questionStarted.value = Date.now()
  gradingNotice.value = ''
  banner.value = ''
}
function firstUnanswered() {
  if (!quiz.value) return
  const next = quiz.value.items.findIndex(item => !item.answered)
  chooseIndex(next >= 0 ? next : Math.max(quiz.value.items.length - 1, 0))
}
function navigate(destination: View) {
  view.value = destination
  mobileMenu.value = false
  banner.value = ''
  if (destination === 'practice' && quiz.value?.status === 'active') firstUnanswered()
}
function openContentSection(target: 'materials' | 'knowledge' | 'relations' | 'questions') {
  contentSection.value = target
  navigate('content')
}
function startBlindFromBank() {
  // Blind candidate trials do not update formal mastery estimates.
  quizMode.value = 'blind'
  quiz.value = null
  count.value = Math.max(1, Math.min(10, pendingCount.value))
  navigate('practice')
}
async function createCourse() {
  const title = newCourseName.value.trim()
  if (!title || creatingCourse.value) return
  creatingCourse.value = true
  banner.value = ''
  try {
    const course = await api.createCourse({title,description:newCourseDescription.value.trim() || null})
    courses.value = [...courses.value,course]
    newCourseName.value = ''
    newCourseDescription.value = ''
    showCourseForm.value = false
    courseId.value = course.id
    localStorage.setItem('knowtrace:selected-course',course.id)
    if (courses.value.length === 1) await loadCourseData(course.id)
    await nextTick() // Allow the course-switch watcher to run before choosing the library.
    openContentSection('materials')
  } catch (e) { showError(e) }
  finally { creatingCourse.value = false }
}

let requestVersion = 0
async function loadCourseData(id: string) {
  const version = ++requestVersion
  loading.value = true
  quiz.value = null
  latestMastery.value = null
  questions.value = []
  knowledgePoints.value = []
  report.value = null
  quality.value = null
  banner.value = ''
  try {
    const [qResult, rResult, kResult, qualityResult] = await Promise.allSettled([
      api.questions(id), api.report(id), api.knowledgePoints(id), api.questionQuality(id)])
    if (version !== requestVersion) return
    if (qResult.status === 'fulfilled') questions.value = qResult.value
    else showError(qResult.reason)
    if (rResult.status === 'fulfilled') report.value = rResult.value
    else showError(rResult.reason)
    if (kResult.status === 'fulfilled') knowledgePoints.value = kResult.value
    else showError(kResult.reason)
    if (qualityResult.status === 'fulfilled') quality.value = qualityResult.value
    else showError(qualityResult.reason)
    count.value = Math.max(1, Math.min(count.value, Math.max(1, maxCount.value)))
    const saved = localStorage.getItem(sessionKey(id))
    if (saved) {
      try {
        const restored = await api.quiz(saved)
        if (version !== requestVersion) return
        if (restored.course_id === id) {
          quiz.value = restored
          if (restored.status === 'active') { firstUnanswered(); view.value = 'practice' }
        } else localStorage.removeItem(sessionKey(id))
      } catch { localStorage.removeItem(sessionKey(id)) }
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}
async function initialize() {
  loading.value = true
  try {
    courses.value = await api.courses()
    connected.value = true
    const saved = localStorage.getItem('knowtrace:selected-course')
    courseId.value = courses.value.find(c => c.id === saved)?.id ?? courses.value[0]?.id ?? ''
    if (courseId.value) await loadCourseData(courseId.value)
    else loading.value = false
  } catch (error) {
    connected.value = false
    loading.value = false
    showError(error)
  }
}
watch(courseId, (value, oldValue) => {
  if (!value || !oldValue || value === oldValue) return
  localStorage.setItem('knowtrace:selected-course', value)
  view.value = 'overview'
  contentSection.value = 'materials'
  void loadCourseData(value)
})
async function refresh() {
  if (!courseId.value || refreshing.value) return
  refreshing.value = true
  try {
    const [q, r, k, qualityResult] = await Promise.all([api.questions(courseId.value),
      api.report(courseId.value), api.knowledgePoints(courseId.value),
      api.questionQuality(courseId.value)])
    questions.value = q
    report.value = r
    knowledgePoints.value = k
    quality.value = qualityResult
    if (quiz.value) {
      quiz.value = await api.quiz(quiz.value.id)
      if (quiz.value.status === 'active') firstUnanswered()
    }
    banner.value = ''
  } catch (error) { showError(error) }
  finally { refreshing.value = false }
}
async function startQuiz() {
  if (!courseId.value || maxCount.value < 1 || starting.value) return
  starting.value = true
  banner.value = ''
  try {
    quiz.value = await api.startQuiz(courseId.value, Math.min(count.value, maxCount.value), quizMode.value)
    localStorage.setItem(sessionKey(courseId.value), quiz.value.id)
    latestMastery.value = null
    chooseIndex(0)
  } catch (error) {
    if (error instanceof ApiError && error.status === 409)
      showError(new Error(quizMode.value === 'adaptive'
        ? '薄弱点优先练习只使用已审核的单知识点诊断题，当前数量不足。'
        : quizMode.value === 'blind'
          ? '没有足够的待审核候选题（或关联的知识点尚未审核）。请降低题量或先生成候选题。'
          : '可用题目不足，或题目所属知识点尚未审核。请检查题库及知识点的审核状态。'))
    else showError(error)
  } finally { starting.value = false }
}
function selectChoice(choice: string | boolean) {
  if (!currentItem.value || currentItem.value.answered || submitting.value || pendingRequest.value) return
  if (currentItem.value.question_type === 'multiple_choice' && typeof choice === 'string') {
    const options = Array.isArray(selected.value) ? [...selected.value] : []
    selected.value = (options.includes(choice) ? options.filter(x => x !== choice)
      : [...options, choice]).sort()
  } else selected.value = choice
}
function formatChoice(value: string | boolean | string[] | null | undefined): string {
  return Array.isArray(value) ? value.join('、') :
    value === true ? '正确' : value === false ? '错误' : value == null ? '—' : String(value)
}
async function evaluateShortAnswer(mode: 'self'|'ai', verdict?: 'learned'|'not_learned') {
  const item = currentItem.value
  if (!item || item.question_type !== 'short_answer' || !item.answered ||
    !currentResult.value || item.evaluation || grading.value || !quiz.value) return
  grading.value = true
  gradingNotice.value = ''
  try {
    const evaluation = await api.evaluateShortAnswer(item.quiz_item_id, mode,
      mode === 'self' ? { verdict } : { provider: gradingProvider.value })
    item.evaluation = evaluation
    gradingNotice.value = mode === 'ai'
      ? 'AI 评阅完成；判断仅供复习参考，不更新 BKT。'
      : '自评已保存；不会被计入客观题正确率。'
  } catch (error) { showError(error) }
  finally { grading.value = false }
}
function applyAnswer(itemId: string, answer: AnswerFeedback, elapsedMs: number) {
  if (!quiz.value) return
  const item = quiz.value.items.find(i => i.quiz_item_id === itemId)
  if (!item || item.answered) return
  item.answered = true
  item.submitted_response = answer.response
  if (!answer.deferred && answer.is_correct !== undefined && answer.correct_answer !== undefined) {
    item.result = { answer_id: answer.answer_id, response: answer.response, is_correct: answer.is_correct,
      correct_answer: answer.correct_answer, explanation: answer.explanation ?? null,
      answered_at: answer.answered_at, elapsed_ms: elapsedMs }
  }
  latestMastery.value = answer.mastery
    ? { itemId, before: answer.mastery.before, after: answer.mastery.after }
    : null
  quiz.value.answered += 1
  if (answer.is_correct && quiz.value.correct !== null) quiz.value.correct += 1
  if (quiz.value.answered === quiz.value.total) quiz.value.status = 'completed'
}
async function submit() {
  const item = currentItem.value
  if (!item || item.answered || selected.value === null || submitting.value) return
  submitting.value = true
  banner.value = ''
  const pending = pendingRequest.value && pendingRequest.value.itemId === item.quiz_item_id ? pendingRequest.value : {
    itemId: item.quiz_item_id, key: crypto.randomUUID(), response: selected.value,
    elapsedMs: Math.min(86_400_000, Math.max(0, Date.now() - questionStarted.value)),
  }
  pendingRequest.value = pending
  try {
    const answer = await api.answer(item.quiz_item_id, {
      client_request_id: pending.key, response: pending.response, elapsed_ms: pending.elapsedMs,
    })
    applyAnswer(item.quiz_item_id, answer, pending.elapsedMs)
    pendingRequest.value = null
    if (quiz.value) {
      try { quiz.value = await api.quiz(quiz.value.id) } catch { /* result already shown */ }
    }
    if (courseId.value) {
      try { report.value = await api.report(courseId.value) } catch { /* refresh later */ }
    }
  } catch (error) {
    // A timeout can happen after the backend committed the answer.
    try {
      if (quiz.value) {
        quiz.value = await api.quiz(quiz.value.id)
        if (quiz.value.items.some(x => x.quiz_item_id === item.quiz_item_id && x.answered)) {
          pendingRequest.value = null
          return
        }
      }
    } catch { /* retry with the original idempotency key */ }
    showError(error)
  } finally { submitting.value = false }
}
function nextQuestion() {
  if (!quiz.value) return
  if (quiz.value.blind_pending && isComplete.value) {
    // Keep the whole corrected trial available for review after submission.
    chooseIndex(index.value + 1 < quiz.value.items.length ? index.value + 1 : 0)
    return
  }
  const next = quiz.value.items.findIndex((item, position) => position > index.value && !item.answered)
  if (next >= 0) chooseIndex(next)
  else if (!isComplete.value) firstUnanswered()
  else navigate('report')
}
onMounted(() => { void initialize() })
</script>

<template>
  <div class="app-shell">
    <div v-if="mobileMenu" class="sidebar-scrim" @click="mobileMenu = false"></div>
    <div v-if="showCourseForm" class="course-modal-overlay" @click.self="showCourseForm = false">
      <form class="course-modal panel" @submit.prevent="createCourse">
        <div class="course-modal-head"><div><p class="eyebrow">CREATE COURSE</p><h2>新建学习课程</h2></div>
          <button type="button" class="icon-button" aria-label="关闭" @click="showCourseForm = false"><X :size="20" /></button>
        </div>
        <label>课程名称<input v-model="newCourseName" maxlength="120" required placeholder="例如：计算机网络、操作系统" autofocus /></label>
        <label>课程描述（可选）<textarea v-model="newCourseDescription" maxlength="2000" rows="3" placeholder="这门课的学习目标和资料…" /></label>
        <div class="course-modal-actions"><button type="button" class="button button-outline" @click="showCourseForm=false">取消</button>
          <button type="submit" class="button button-primary" :disabled="creatingCourse || !newCourseName.trim()">
            <LoaderCircle v-if="creatingCourse" class="spinning" :size="16" /><Plus v-else :size="16" /> 创建并导入资料
          </button></div>
      </form>
    </div>
    <aside class="sidebar" :class="{ 'sidebar-open': mobileMenu }">
      <div class="brand-row">
        <div class="brand-mark"><Activity :size="25" :stroke-width="2.6" /></div>
        <div class="brand-text"><strong>知迹</strong><span>KNOWTRACE</span></div>
        <button class="mobile-close icon-button" aria-label="关闭菜单" @click="mobileMenu = false"><X :size="20" /></button>
      </div>
      <div class="sidebar-group-label">学习</div>
      <nav class="sidebar-nav" aria-label="学习导航">
        <button type="button" :class="{ active: view === 'overview' }" @click="navigate('overview')"><LayoutDashboard :size="19" /><span>学习总览</span></button>
        <button type="button" :class="{ active: view === 'practice' }" @click="navigate('practice')"><BookOpen :size="19" /><span>开始练习</span><span v-if="quiz?.status === 'active'" class="nav-dot"></span></button>
        <button type="button" :class="{ active: view === 'report' }" @click="navigate('report')"><TrendingUp :size="19" /><span>学习报告</span></button>
      </nav>
      <div class="sidebar-group-label sidebar-content-label">课程管理</div>
      <nav class="sidebar-nav" aria-label="课程管理导航">
        <button type="button" :class="{ active: view === 'content' }" @click="navigate('content')"><FolderOpen :size="19" /><span>课程内容</span><span v-if="pendingCount" class="nav-dot"></span></button>
        <button type="button" :class="{ active: view === 'galaxy' }" @click="navigate('galaxy')"><Network :size="19" /><span>知识图谱</span></button>
      </nav>
      <div class="sidebar-group-label course-label">正在学习</div>
      <div class="course-selector">
        <div class="course-selector-icon"><GraduationCap :size="21" /></div>
        <div class="course-selector-body"><span>当前课程</span>
          <select v-model="courseId" aria-label="切换当前课程" :disabled="!courses.length">
            <option v-if="!courses.length" value="">暂无课程</option>
            <option v-for="course in courses" :key="course.id" :value="course.id">{{ course.title }}</option>
          </select>
        </div>
        <ChevronDown :size="16" class="course-chevron" />
      </div>
      <button type="button" class="sidebar-add-course" @click="showCourseForm = true"><Plus :size="15" /> 新建课程</button>
      <button type="button" class="sidebar-add-course" @click="navigate('settings')"><Settings :size="15"/> AI 模型设置</button>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-motto"><Sparkles :size="18" /><p>不只是答对题目，<br />更要看见每一次进步。</p></div>
      <div class="sidebar-footer"><div class="avatar">学</div><div><strong>个人学习空间</strong><span>本地 · 单用户模式</span></div><span class="online-mini"></span></div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileMenu = true"><Menu :size="22" /></button>
          <span class="breadcrumb-muted">我的学习空间</span><ChevronRight :size="15" class="breadcrumb-chevron" /><strong>{{ viewTitle }}</strong>
        </div>
        <div class="topbar-right">
          <span class="connection-status" :class="{ offline: !connected }"><span class="status-dot"></span>{{ connected ? '本地服务已连接' : '等待连接' }}</span>
          <AITaskCenter />
          <button class="icon-button refresh-button" aria-label="刷新数据" title="刷新数据" :disabled="refreshing || !courseId" @click="refresh"><RefreshCw :size="18" :class="{ spinning: refreshing }" /></button>
          <div class="topbar-avatar">学</div>
        </div>
      </header>
      <main class="page-content" :class="{ 'page-content-galaxy': view === 'galaxy' }">
        <div v-if="banner" class="notice" role="alert"><AlertCircle :size="19" /><span>{{ banner }}</span><button aria-label="关闭提示" @click="banner = ''"><X :size="17" /></button></div>

        <div v-if="loading" class="loading-screen"><LoaderCircle :size="32" class="spinning" /><strong>正在整理你的学习空间…</strong><span>从本地 KnowTrace 后端读取课程与学习记录</span></div>
        <div v-else-if="!courses.length" class="blank-state panel">
          <div class="blank-illustration"><BookOpen :size="42" /></div><h2>从一门课程开始学习</h2>
          <p>创建一门课程，就能直接上传 PDF 或 PPTX 课件，不再需要打开 Swagger 操作。</p>
          <div class="blank-actions"><button class="button button-primary" @click="showCourseForm=true"><Plus :size="17" /> 新建第一门课程</button><button class="button button-outline" @click="initialize">重新连接</button></div>
        </div>

        <template v-else>
          <div v-if="view === 'overview'" class="view-stack">
            <div class="section-heading"><div><p class="eyebrow">YOUR LEARNING SPACE</p><h1>学习总览<span class="heading-dot">.</span></h1><p class="section-description">欢迎回来，继续积累属于你的知识轨迹。</p></div><div class="heading-aside"><span class="small-label">当前学科</span><strong>{{ currentCourse?.title }}</strong></div></div>
            <section class="hero">
              <div class="hero-content">
                <div class="hero-kicker"><Sparkles :size="15" /> 智能评测 · 让进步可见</div>
                <h2>每一次练习，<br /><span>都让知识更清晰。</span></h2>
                <p>从真实课件出发，用每一道题发现薄弱点，让学习不再凭感觉。</p>
                <div class="hero-actions"><button class="button button-light" @click="openContentSection('materials')"><Upload :size="18" /> 导入课件资料</button><button class="hero-secondary" @click="navigate('practice')">开始练习 <ChevronRight :size="18" /></button></div>
              </div>
              <div class="hero-art" aria-hidden="true">
                <div class="orbit orbit-1"></div><div class="orbit orbit-2"></div>
                <div class="hero-note note-a"><span class="note-dot teal"></span>学习进度 <span>持续记录</span></div>
                <div class="hero-knowledge"><div class="hero-knowledge-label">已获得测评证据的知识点</div>
                  <div class="hero-knowledge-value">{{ evidenceSummary?.observed_points ?? 0 }} <span class="hero-count-total">/ {{evidenceSummary?.approved_points ?? 0}}</span></div>
                  <div class="progress-track big"><span :style="{ width: percent(evidenceSummary?.observation_coverage) }"></span></div>
                  <div class="hero-knowledge-bottom"><span>证据覆盖 · 非掌握率</span><Activity :size="17" /></div></div>
                <div class="hero-note note-b"><CheckCircle2 :size="16" /> 为每次进步留下记录</div>
              </div>
            </section>

            <section class="stats-grid" aria-label="本课程学习数据">
              <div class="stat-card panel"><div class="stat-icon teal-bg"><FileQuestion :size="21" /></div><div class="stat-number">{{ approvedCount }}</div><div class="stat-label">可练习题目</div><span class="stat-helper">题目和知识点均已审核</span></div>
              <div class="stat-card panel"><div class="stat-icon blue-bg"><ClipboardList :size="21" /></div><div class="stat-number">{{ report?.answered ?? 0 }}</div><div class="stat-label">累计作答</div><span class="stat-helper">{{ report?.sessions ?? 0 }} 场测评记录</span></div>
              <div class="stat-card panel"><div class="stat-icon orange-bg"><Target :size="21" /></div><div class="stat-number">{{ percent(report?.accuracy) }}</div><div class="stat-label">累计正确率</div><span class="stat-helper">依据实际答题记录计算</span></div>
            </section>
            <div class="overview-grid">
              <section class="panel dashboard-panel">
                <div class="panel-heading"><div><p class="eyebrow">KNOWLEDGE TRACKING</p><h2>知识掌握情况</h2></div><button class="text-link" @click="navigate('report')">全部知识点 <ArrowRight :size="16" /></button></div>
                <div v-if="sortedKnowledge.some(p => p.observations)" class="knowledge-preview">
                  <div v-for="point in sortedKnowledge.filter(p => p.observations).slice(0,4)" :key="point.knowledge_point_id" class="knowledge-item">
                    <div class="knowledge-top"><strong :title="point.canonical_name">{{ point.canonical_name }}</strong><span :class="['mastery-text', masteryClass(point.p_mastery)]">{{ percent(point.p_mastery) }}</span></div>
                    <div class="progress-track"><span :class="masteryClass(point.p_mastery)" :style="{ width: percent(point.p_mastery) }"></span></div>
                    <div class="knowledge-meta"><span>{{ masteryLabel(point) }}</span><span>已练习 {{ point.observations }} 次</span></div>
                  </div>
                </div>
                <div v-else class="empty-preview"><div class="empty-preview-icon"><Target :size="25" /></div><strong>你的学习轨迹即将开始</strong><p>完成第一次练习后，这里会展示真实的答题记录和诊断覆盖情况。</p></div>
              </section>
              <section class="panel side-panel">
                <div class="panel-heading"><div><p class="eyebrow">NEXT STEP</p><h2>继续学习</h2></div><div class="small-pill">推荐操作</div></div>
                <div class="continue-illustration"><div class="continue-icon"><BookOpen :size="26" /></div><span class="continue-lines"></span><span class="continue-lines short"></span></div>
                <div class="continue-body"><h3>{{ quiz?.status === 'active' ? '上次练习还未结束' : approvedCount ? '准备好开始了吗？' : '先准备几道练习题' }}</h3>
                  <p v-if="quiz?.status === 'active'">已经完成 {{ quiz.answered }}/{{ quiz.total }} 道题，可以从上次的位置继续。</p>
                  <p v-else-if="approvedCount">从 {{ currentCourse?.title }} 的已审核题库中出题，作答后即可查看解析。</p><p v-else>课程暂时没有已审核题目，请先完成 AI 出题和人工审核。</p>
                  <button class="button button-primary continue-button" @click="navigate('practice')">{{ quiz?.status === 'active' ? '继续上次测评' : '前往练习' }} <ArrowRight :size="18" /></button>
                  <button v-if="pendingCount || !approvedCount" class="text-link pending-hint qb-home-link" @click="openContentSection('questions')">{{ pendingCount ? pendingCount + ' 道题待审核 · 去处理' : '去生成并审核题目' }} <ArrowRight :size="15" /></button>
                </div>
              </section>
            </div>
            <div class="small-footnote"><Lightbulb :size="16" /><span>首页展示的测评覆盖是已产生有效诊断记录的知识点数量，不是知识掌握率；BKT 模型参数尚未校准。</span></div>
          </div>

          <div v-else-if="view === 'practice'" class="view-stack">
            <div class="section-heading"><div><p class="eyebrow">PRACTICE STUDIO</p><h1>专项练习<span class="heading-dot">.</span></h1><p class="section-description">{{ quiz ? '专注于眼前这一题，让每次作答都有收获。' : '选择题量，开始一次有迹可循的练习。' }}</p></div>
              <button v-if="quiz" class="button button-outline button-small" @click="quiz = null"><RotateCcw :size="16" /> 新测评设置</button>
            </div>
            <template v-if="!quiz">
              <div class="setup-layout">
                <section class="panel setup-panel">
                  <div class="setup-eyebrow"><div class="setup-icon"><BookOpen :size="24" /></div><span>创建一场新的测评</span></div>
                  <h2>今天想练习多少道题？</h2><p>我们会从 <strong>{{ currentCourse?.title }}</strong> 的{{ quizMode === 'blind' ? '待审核候选题' : '最新已审核题目' }}中选取练习。</p>
                  <div class="setup-field">
                    <label for="quiz-mode">练习模式</label>
                    <select id="quiz-mode" v-model="quizMode" @change="count = Math.max(1,Math.min(count,maxCount))">
                      <option value="normal">普通练习 · 所有已审核题</option>
                      <option value="adaptive">薄弱点优先 · 单知识点诊断题</option>
                      <option value="blind">候选题盲测 · 无需提前查看答案</option>
                    </select>
                    <p class="qb-hint" v-if="quizMode === 'adaptive'">
                      优先安排尚缺少多样化作答证据的知识点；积累跨天多题记录后才参考未校准的 BKT 概率排序。
                      仅对已审核的单知识点单选/判断题建立 BKT 记录。可用诊断题 {{ quality?.latest_questions.approved_diagnostic ?? 0 }} 道。
                    </p>
                    <p class="qb-hint" v-if="quizMode === 'blind'">
                      无需逐题预审。所有题目提交完毕后才统一显示答案与解析；候选题未经人工确认，
                      可能存在错误，其成绩单独记录，不计入正式正确率或 BKT 掌握度。
                      当前待审核题目 {{ pendingCount }} 道。
                    </p>
                  </div>
                  <div class="setup-field"><label for="question-count">练习题量</label>
                    <div v-if="maxCount" class="count-control"><button aria-label="减少一道题" :disabled="count <= 1" @click="count--">−</button>
                      <select id="question-count" v-model.number="count"><option v-for="number in countOptions" :key="number" :value="number">{{ number }} 道题</option></select>
                      <button aria-label="增加一道题" :disabled="count >= maxCount" @click="count++">+</button></div>
                    <div v-else class="empty-count">{{ quizMode === 'blind' ? '暂无待审核候选题' : '暂无可供练习的已审核题目' }}</div>
                  </div>
                  <div class="setup-points"><span><CheckCircle2 :size="17" /> {{ quizMode === 'blind' ? '整套提交后公布解析' : '作答后即时反馈' }}</span><span><Activity :size="17" /> {{ quizMode === 'blind' ? '不更新 BKT 掌握度' : '单知识点题更新掌握度' }}</span><span><Clock3 :size="17" /> 自动保存学习记录</span></div>
                  <button class="button button-primary start-button" :disabled="maxCount < 1 || starting" @click="startQuiz"><LoaderCircle v-if="starting" :size="18" class="spinning" /><Play v-else :size="18" fill="currentColor" />{{ starting ? '正在创建测评…' : '开始练习' }} <ArrowRight :size="18" /></button>
                  <div class="setup-footer">当前模式：<strong>{{ quizMode === 'blind' ? '待审核题盲测' : quizMode === 'adaptive' ? '薄弱点优先诊断' : '普通顺序练习' }}</strong> · {{ quizMode === 'blind' ? '仅自测，不作正式掌握度判定' : '已审核综合题不单独更新 BKT' }}</div>
                </section>
                <aside class="panel setup-aside"><div class="setup-aside-graphic"><div class="abstract-ring ring-one"></div><div class="abstract-ring ring-two"></div><Target :size="60" :stroke-width="1.5" /></div>
                  <h3>用练习发现真正的薄弱点</h3><p>已审核单知识点题可用于 BKT 诊断；候选题盲测不会提前泄露答案，也不会计入正式掌握度。</p>
                  <div class="aside-divider"></div><div class="aside-stats"><span>已审核题目</span><strong>{{ approvedCount }}</strong></div><div class="aside-stats"><span>待审核题目</span><strong>{{ pendingCount }}</strong></div>
                  <button v-if="!approvedCount || pendingCount" class="text-link setup-admin" @click="openContentSection('questions')">前往题库生成与审核 <ArrowRight :size="16" /></button>
                </aside>
              </div>
            </template>
            <template v-else>
              <div class="quiz-layout">
                <section class="quiz-main">
                  <div class="quiz-progress panel"><div class="quiz-progress-top"><span class="quiz-progress-title"><span class="pulse-dot"></span>{{ isComplete ? '本次练习已完成' : quiz.blind_pending ? '正在进行 · 候选题盲测（答案暂隐藏）' : '正在进行 · 正式练习' }}</span><strong>{{ quiz.answered }} <span>/ {{ quiz.total }} 题</span></strong></div>
                    <div class="progress-track big"><span :style="{ width: (quiz.total ? quiz.answered / quiz.total * 100 : 0) + '%' }"></span></div></div>
                  <div v-if="isComplete" class="completion-banner"><div class="completion-icon"><Award :size="28" /></div><div><strong>本轮练习完成！</strong><span>客观题答对 {{quiz.correct ?? '—'}} / {{quiz.objective_total ?? quiz.total}} 题；{{ quiz.subjective_total ?? 0 }} 道简答题需自主核对。所有作答已保存。{{ quiz.blind_pending ? '候选答案未经人工确认，不计正式测评。' : '' }}</span></div><button class="button button-light" @click="quiz?.blind_pending ? chooseIndex(0) : navigate('report')">{{ quiz?.blind_pending ? '查看整套解析' : '查看报告' }} <ArrowRight :size="17" /></button></div>
                  <div v-if="currentItem" class="question-card panel">
                    <div class="question-head"><span class="type-tag"><FileQuestion :size="15" /> {{ ({single_choice:'单选题',multiple_choice:'多选题',true_false:'判断题',short_answer:'简答题'} as Record<string,string>)[currentItem.question_type] }}</span><span class="question-index">QUESTION {{ String(currentItem.position).padStart(2, '0') }}</span></div>
                    <h2>{{ currentItem.stem }}</h2><p class="question-instruction">{{ currentItem.question_type === 'single_choice' ? '请选择一个最符合题意的答案' :
 currentItem.question_type === 'multiple_choice' ? '请选择所有正确答案（须完全匹配，不计部分分）' :
 currentItem.question_type === 'short_answer' ? '先根据理解写出答案，提交后再与参考要点核对' :
 '请判断下列说法是否正确' }}</p>
                    <div v-if="currentItem.question_type === 'short_answer'" class="short-answer-box">
                      <textarea :value="typeof effectiveChoice === 'string' ? effectiveChoice : ''"
                        :disabled="currentItem.answered || submitting || !!pendingRequest"
                        maxlength="3000" rows="8" placeholder="用自己的语言写下理解、步骤或关键依据…"
                        @input="selected = ($event.target as HTMLTextAreaElement).value"></textarea>
                      <p class="qb-hint">最多 3000 字。答案保存后才展示参考答案，自评和 AI 评价不会写入 BKT。</p>
                    </div>
                    <div v-else class="options-list">
                      <button v-for="option in currentOptions" :key="String(option.key)" class="answer-option" :class="{
                        selected: (Array.isArray(effectiveChoice) ? effectiveChoice.includes(String(option.key)) : effectiveChoice === option.key) && !currentItem.answered,
                        'option-correct': currentItem.answered && (Array.isArray(currentResult?.correct_answer)
                          ? currentResult?.correct_answer.includes(String(option.key))
                          : currentResult?.correct_answer === option.key),
                        'option-wrong': currentItem.answered && (Array.isArray(currentResult?.response)
                          ? currentResult?.response.includes(String(option.key))
                          : currentResult?.response === option.key) && !currentResult?.is_correct
                      }" :disabled="currentItem.answered || submitting || !!pendingRequest" @click="selectChoice(option.key)">
                        <span class="option-letter">{{ option.label }}</span><span class="option-text">{{ option.text }}</span>
                        <CheckCircle2 v-if="currentItem.answered && (Array.isArray(currentResult?.correct_answer)
                          ? currentResult?.correct_answer.includes(String(option.key))
                          : currentResult?.correct_answer === option.key)" :size="21" class="option-state" />
                        <XCircle v-else-if="currentItem.answered && (Array.isArray(currentResult?.response)
                          ? currentResult?.response.includes(String(option.key))
                          : currentResult?.response === option.key) && !currentResult?.is_correct" :size="21" class="option-state" />
                        <span v-else class="option-circle"></span>
                      </button>
                    </div>
                    <div v-if="currentItem.answered && !currentResult && quiz.blind_pending" class="answer-feedback">
                      <div class="feedback-heading"><CheckCircle2 :size="22" /><strong>答案已保存，整套完成后统一揭晓</strong></div>
                      <p>可继续答下一题。中途刷新或关闭网页不会泄露答案；重新打开仍可接着作答。</p>
                    </div>
                    <div v-if="currentItem.answered && currentResult" class="answer-feedback" :class="{ incorrect: currentResult.is_correct === false }">
                      <div class="feedback-heading"><CheckCircle2 v-if="currentResult.is_correct !== false" :size="22" /><XCircle v-else :size="22" /><strong>{{ currentItem.question_type === 'short_answer' ? '请对照参考答案与关键要点' : currentResult.is_correct ? '回答正确，继续保持！' : '再巩固一下这个知识点' }}</strong></div>
                      <div v-if="currentItem.question_type === 'short_answer'" class="subjective-compare">
                        <div><strong>你的作答</strong><p>{{formatChoice(currentResult.response)}}</p></div>
                        <div><strong>参考答案</strong><p>{{formatChoice(currentResult.correct_answer)}}</p></div>
                      </div>
                      <p v-else>正确答案：<strong>{{ formatChoice(currentResult.correct_answer) }}</strong></p>
                      <div class="explanation-block"><span>题目解析</span><p>{{ currentResult.explanation || '暂无解析' }}</p></div>
                      <div v-if="currentItem.question_type === 'short_answer'" class="short-review">
                        <div v-if="currentItem.evaluation" class="qb-hint">
                          {{ currentItem.evaluation.mode === 'self' ? '自我评价' : 'AI 评阅' }}：
                          {{ ({ learned:'已学会', not_learned:'还需复习', uncertain:'暂不能确定'} as Record<string,string>)[currentItem.evaluation.verdict] }}
                          <p>{{currentItem.evaluation.feedback}}</p>
                        </div>
                        <template v-else>
                          <strong>对照后，你认为自己掌握了吗？</strong>
                          <div class="short-review-actions">
                            <button class="button button-outline" :disabled="grading" @click="evaluateShortAnswer('self','learned')">已学会</button>
                            <button class="button button-outline" :disabled="grading" @click="evaluateShortAnswer('self','not_learned')">还没学会</button>
                          </div>
                          <div class="short-review-ai">
                            <select v-model="gradingProvider" aria-label="简答题 AI 评阅服务">
                              <option value="default">默认 AI</option><option value="ollama">Ollama</option><option value="mimo">MiMo（可能产生费用）</option>
                            </select>
                            <button class="button button-outline" :disabled="grading" @click="evaluateShortAnswer('ai')">
                              {{ grading ? 'AI 正在评阅…' : '调用 AI 评阅（可选）' }}
                            </button>
                          </div>
                          <p class="qb-hint">AI 评阅可能出错；调用云端服务会发送题干、参考答案和你的作答。</p>
                        </template>
                        <p v-if="gradingNotice" class="qb-hint">{{gradingNotice}}</p>
                      </div>
                      <div v-if="latestMastery?.itemId === currentItem.quiz_item_id" class="mastery-change">
                        <Activity :size="16" /> BKT 实验模型更新（参数未校准） <strong>{{ percent(latestMastery.before) }} → {{ percent(latestMastery.after) }}</strong>
                      </div>
                    </div>
                    <div class="question-actions"><div class="answer-safety"><Lightbulb :size="17" /><span>{{ currentItem.answered
                          ? (currentResult ? '本题答案与解析已揭晓' : '已保存作答；全部完成后揭晓')
                          : (quiz.blind_pending ? '全卷提交前均不显示答案' : '提交前不会显示正确答案') }}</span></div>
                      <button v-if="!currentItem.answered" class="button button-primary submit-button" :disabled="!canAnswer" @click="submit"><LoaderCircle v-if="submitting" :size="18" class="spinning" /><Check v-else :size="18" />{{ submitting ? '正在保存答案…' : pendingRequest ? '重试提交答案' : '提交答案' }}</button>
                      <button v-else class="button button-primary submit-button" @click="nextQuestion">{{ isComplete ? (quiz.blind_pending ? '查看下一题解析' : '查看学习报告') : '下一题' }} <ArrowRight :size="18" /></button>
                    </div>
                  </div>
                  <p v-if="currentItem?.selection_reason" class="qb-hint">选题理由：{{ currentItem.selection_reason }}</p>
                  <p v-if="currentItem && currentItem.answered && latestMastery === null" class="qb-hint">
                    {{ quiz.blind_pending
                      ? '候选题尚未人工审核：作答记录独立保存，答案仅供对照，不写入正式正确率和 BKT。'
                      : currentItem.question_type === 'short_answer'
                        ? '简答题已经保存作答，可自主评估或选择 AI 评阅，但不直接更新 BKT。'
                        : '本题未产生 BKT 诊断更新（可能是综合题或 24 小时内重复作答）。' }}
                  </p>
                  <div class="quiz-footnote"><Clock3 :size="15" /> 数据保存在你本机的 KnowTrace 数据库中，重复提交不会重复计算掌握度。</div>
                </section>
                <aside class="quiz-aside">
                  <div class="panel question-navigator"><div class="navigator-heading"><span>答题导航</span><span>{{ quiz.answered }}/{{ quiz.total }}</span></div>
                    <div class="question-number-grid"><button v-for="(item, position) in quiz.items" :key="item.quiz_item_id"
                      :aria-label="'第 ' + (position + 1) + ' 题' + (item.answered ? '，已作答' : '')" :class="{ current: index === position, done: item.answered }" @click="chooseIndex(position)">
                      <Check v-if="item.answered" :size="16" /><span v-else>{{ position + 1 }}</span></button></div>
                    <div class="nav-legend"><span><i class="legend-current"></i> 当前题</span><span><i class="legend-done"></i> 已作答</span><span><i class="legend-empty"></i> 未作答</span></div>
                    <div class="navigator-footer"><button class="navigator-link" @click="firstUnanswered">跳转到未完成题 <ArrowRight :size="16" /></button></div>
                  </div>
                  <div class="panel quiz-aside-note"><Activity :size="22" /><h3>客观判分和主观自评分开记录。</h3><p>仅经过审核的单知识点客观题用于 BKT；简答自评与 AI 评阅只用于复习参考。</p></div>
                </aside>
              </div>
            </template>
          </div>

          <CourseContentHub v-else-if="view === 'content'" :course-id="courseId"
            :course-name="currentCourse?.title || '课程'" :initial-section="contentSection"
            @updated="refresh" @blind-trial="startBlindFromBank"
            @section-change="contentSection = $event"
            @open-practice="navigate('practice')" />
          <KnowledgeGalaxy v-else-if="view === 'galaxy'" :key="courseId" :course-id="courseId"
            :course-name="currentCourse?.title || '课程'"
            @open-bank="openContentSection('questions')" @open-manage="openContentSection('knowledge')"
            @open-practice="navigate('practice')" />
          <AISettings v-else-if="view === 'settings'" :course-name="currentCourse?.title" />
          <div v-else class="view-stack">
            <div class="section-heading"><div><p class="eyebrow">LEARNING ANALYTICS</p><h1>学习报告<span class="heading-dot">.</span></h1><p class="section-description">发现学习中的进步，也正视尚未掌握的部分。</p></div>
              <button class="button button-outline button-small" @click="refresh"><RefreshCw :size="17" :class="{ spinning: refreshing }" /> 更新报告</button></div>
            <section class="report-top-grid">
              <div class="panel report-feature"><div class="report-feature-heading"><div class="stat-icon white-icon"><Activity :size="23" /></div><span>测评证据覆盖 · 非掌握率</span></div>
                <div class="report-big-value">{{ evidenceSummary?.observed_points ?? 0 }}<span> / {{ evidenceSummary?.approved_points ?? 0 }} 个知识点</span></div>
                <p>这是已有客观诊断记录的知识点数量，不代表已经掌握的比例。</p>
                <div class="report-feature-bottom"><Sparkles :size="17"/> 满足跨日重复观察要求：{{ evidenceSummary?.repeated_evidence_points ?? 0 }} 个知识点。未充分观察的 BKT 概率不会显示为可靠结论。</div></div>
              <div class="report-stat-stack"><div class="panel report-stat"><div class="stat-icon blue-bg"><ClipboardList :size="21" /></div><div><span>累计作答</span><strong>{{ report?.answered ?? 0 }} <small>道</small></strong></div></div>
                <div class="panel report-stat"><div class="stat-icon orange-bg"><Target :size="21" /></div><div><span>累计正确率</span><strong>{{ percent(report?.accuracy) }}</strong></div></div></div>
            </section>
            <section class="panel mastery-table-panel">
              <div class="panel-heading"><div><p class="eyebrow">PER KNOWLEDGE POINT</p><h2>知识点掌握分析</h2></div><span class="table-count">{{ report?.knowledge_points.length ?? 0 }} 个知识点</span></div>
              <div v-if="!report?.knowledge_points.length" class="empty-preview report-empty"><CircleHelp :size="30" /><strong>暂无知识点</strong><p>先从课件提取并审核知识点，再开始练习。</p></div>
              <template v-else>
                <div class="qb-hint bkt-evidence-header">
                  <span>基础模型使用固定参数，所有百分比均未经课程数据校准。观察不足时默认隐藏试验值。</span>
                  <button type="button" class="button button-outline button-small" @click="revealExperimentalBkt=!revealExperimentalBkt">
                    {{ revealExperimentalBkt ? '隐藏原始 BKT 概率' : '查看原始 BKT（实验）' }}
                  </button>
                  <span v-if="revealExperimentalBkt && bktExperimentalMean !== null">
                    重复观测子集的试验均值 {{ percent(bktExperimentalMean) }}，不可外推为课程掌握率
                  </span>
                </div>
                <div class="table-head"><span>知识点</span><span>BKT 实验估计</span><span>证据数量</span><span>数据状态</span></div>
                <div v-for="point in sortedKnowledge" :key="point.knowledge_point_id" class="mastery-row">
                  <div class="mastery-row-name"><div class="mastery-row-icon"><BookOpen :size="17" /></div><strong>{{ point.canonical_name }}</strong></div>
                  <div class="mastery-cell">
                    <span class="mastery-score" :class="masteryClass(point.display_mastery)" :title="point.evidence_level === 'limited' ? '样本不足，暂不使用模型数值衡量掌握情况' : '未经参数校准的 BKT 计算值'">
                      {{ point.display_mastery !== null || revealExperimentalBkt ? percent(point.p_mastery) : '证据不足' }}
                    </span>
                    <div v-if="point.display_mastery !== null || revealExperimentalBkt" class="progress-track">
                      <span :class="masteryClass(point.p_mastery)" :style="{width: percent(point.p_mastery)}"></span>
                    </div>
                  </div>
                  <div class="mastery-row-count">
                    {{ point.observations ?? 0 }} 次 BKT 更新
                    <span> · {{point.distinct_questions}} 道独立题 · {{point.practice_days}} 天</span>
                    <span v-if="point.answered"> · 客观题正确 {{point.correct}}/{{point.answered}}</span>
                  </div>
                  <div><span class="mastery-status" :class="masteryClass(point.display_mastery)">{{ masteryLabel(point) }}</span></div>
                </div></template>
            </section>
            <div class="report-disclaimer"><AlertCircle :size="18" /><span>当前 BKT 仍使用未经数据校准的固定教学参数。至少 5 次诊断更新、3 道不同的题、跨 2 个日期，才展示该知识点的实验概率；这一阈值只是展示规则，不代表测评结果已获得科学验证。候选题、主观题、综合题不计 BKT。</span></div>
            <div class="report-bottom-action"><button class="button button-primary" @click="navigate('practice')">继续练习 <ArrowRight :size="18" /></button></div>
          </div>
        </template>
      </main>
      <footer class="page-footer"><span>© 2026 知迹 KnowTrace</span><span>让每一次学习，都有迹可循。</span><span>本地学习原型 · v1.6.0</span></footer>
    </div>
  </div>
</template>
