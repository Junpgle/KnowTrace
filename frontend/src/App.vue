<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue'
import {
  Activity, AlertCircle, ArrowRight, Award, BookOpen, Check,
  CheckCircle2, ChevronDown, ChevronRight, CircleHelp, ClipboardList, ClipboardCheck,
  Clock3, FileQuestion, GraduationCap, LayoutDashboard, Lightbulb,
  LoaderCircle, Menu, Play, RefreshCw, RotateCcw, Sparkles, Target,
  TrendingUp, X, XCircle, Orbit, FolderOpen, Plus, Upload,
} from '@lucide/vue'
import QuestionBank from './components/QuestionBank.vue'
// Load the WebGL and Three.js bundle only when the user opens the graph.
const KnowledgeGalaxy = defineAsyncComponent(() => import('./components/KnowledgeGalaxy.vue'))
const DocumentLibrary = defineAsyncComponent(() => import('./components/DocumentLibrary.vue'))
import { api, ApiError, type AnswerFeedback, type Course, type KnowledgeReport,
  type LearningReport, type QuestionSummary, type QuizAnswerResult,
  type QuizItem, type QuizSession, type KnowledgePoint } from './api'

type View = 'overview' | 'practice' | 'report' | 'bank' | 'galaxy' | 'library'
type Choice = string | boolean | null
const view = ref<View>('overview')
const mobileMenu = ref(false)
const showCourseForm = ref(false)
const newCourseName = ref('')
const newCourseDescription = ref('')
const creatingCourse = ref(false)
const bankEntryTab = ref<'knowledge' | 'questions' | 'external'>('questions')
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
const index = ref(0)
const selected = ref<Choice>(null)
const questionStarted = ref(Date.now())
const pendingRequest = ref<{ itemId: string, key: string, response: string | boolean, elapsedMs: number } | null>(null)
const latestMastery = ref<{ itemId: string, before: number, after: number } | null>(null)

const currentCourse = computed(() => courses.value.find(c => c.id === courseId.value))
const approvedCount = computed(() => {
  const approvedIds = new Set(knowledgePoints.value.filter(k => k.review_status === 'approved').map(k => k.id))
  return questions.value.filter(q => q.review_status === 'approved' &&
    q.primary_knowledge_point_id !== null && approvedIds.has(q.primary_knowledge_point_id)).length
})
const pendingCount = computed(() => questions.value.filter(q => q.review_status === 'pending').length)
const maxCount = computed(() => Math.min(20, approvedCount.value))
const countOptions = computed(() => Array.from({ length: maxCount.value }, (_, i) => i + 1))
const currentItem = computed<QuizItem | null>(() => quiz.value?.items[index.value] ?? null)
const currentResult = computed<QuizAnswerResult | undefined>(() => currentItem.value?.result)
const currentOptions = computed(() => {
  if (!currentItem.value) return [] as { key: string | boolean, label: string, text: string }[]
  if (currentItem.value.question_type === 'true_false')
    return [{ key: true, label: 'A', text: '正确' }, { key: false, label: 'B', text: '错误' }]
  return ['A', 'B', 'C', 'D'].map(key => ({ key, label: key, text: currentItem.value?.options[key] ?? '' }))
})
const effectiveChoice = computed<Choice>(() =>
  currentItem.value?.answered ? currentResult.value?.response ?? null : selected.value)
const avgMastery = computed<number | null>(() => {
  const values = (report.value?.knowledge_points ?? [])
    .filter(p => p.observations && p.p_mastery !== null)
    .map(p => p.p_mastery as number)
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
})
const sortedKnowledge = computed(() => [...(report.value?.knowledge_points ?? [])].sort((a, b) => {
  if (Boolean(a.observations) !== Boolean(b.observations)) return a.observations ? -1 : 1
  return (a.p_mastery ?? 1) - (b.p_mastery ?? 1)
}))
const viewTitle = computed(() => ({ overview: '学习总览', practice: '专项练习', report: '学习分析', bank: '题库管理', galaxy: '知识星系', library: '资料库' })[view.value])
const isComplete = computed(() => quiz.value?.status === 'completed')
const canAnswer = computed(() => Boolean(currentItem.value && !currentItem.value.answered
  && selected.value !== null && !submitting.value
  && (!pendingRequest.value || pendingRequest.value.response === selected.value)))

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : Math.round(value * 100) + '%'
}
function masteryClass(value: number | null): string {
  return value === null ? 'unknown' : value >= .8 ? 'strong' : value >= .5 ? 'growing' : 'needs-work'
}
function masteryLabel(point: KnowledgeReport): string {
  return !point.observations || point.p_mastery === null ? '尚未测评'
    : point.p_mastery >= .8 ? '掌握较好' : point.p_mastery >= .5 ? '持续巩固' : '建议复习'
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
  banner.value = ''
}
function firstUnanswered() {
  if (!quiz.value) return
  const next = quiz.value.items.findIndex(item => !item.answered)
  chooseIndex(next >= 0 ? next : Math.max(quiz.value.items.length - 1, 0))
}
function navigate(destination: View) {
  if (destination === 'bank') bankEntryTab.value = 'questions'
  view.value = destination
  mobileMenu.value = false
  banner.value = ''
  if (destination === 'practice' && quiz.value?.status === 'active') firstUnanswered()
}

function openBankTab(tab: 'knowledge' | 'questions' | 'external') {
  navigate('bank')
  bankEntryTab.value = tab
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
    navigate('library')
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
  banner.value = ''
  try {
    const [qResult, rResult, kResult] = await Promise.allSettled([api.questions(id), api.report(id), api.knowledgePoints(id)])
    if (version !== requestVersion) return
    if (qResult.status === 'fulfilled') questions.value = qResult.value
    else showError(qResult.reason)
    if (rResult.status === 'fulfilled') report.value = rResult.value
    else showError(rResult.reason)
    if (kResult.status === 'fulfilled') knowledgePoints.value = kResult.value
    else showError(kResult.reason)
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
  void loadCourseData(value)
})
async function refresh() {
  if (!courseId.value || refreshing.value) return
  refreshing.value = true
  try {
    const [q, r, k] = await Promise.all([api.questions(courseId.value), api.report(courseId.value), api.knowledgePoints(courseId.value)])
    questions.value = q
    report.value = r
    knowledgePoints.value = k
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
    quiz.value = await api.startQuiz(courseId.value, Math.min(count.value, maxCount.value))
    localStorage.setItem(sessionKey(courseId.value), quiz.value.id)
    latestMastery.value = null
    chooseIndex(0)
  } catch (error) {
    if (error instanceof ApiError && error.status === 409)
      showError(new Error('可用题目不足，或题目所属知识点尚未审核。请检查题库及知识点的审核状态。'))
    else showError(error)
  } finally { starting.value = false }
}
function selectChoice(choice: string | boolean) {
  if (currentItem.value && !currentItem.value.answered && !submitting.value && !pendingRequest.value)
    selected.value = choice
}
function formatChoice(value: string | boolean | null | undefined): string {
  return value === true ? '正确' : value === false ? '错误' : value == null ? '—' : String(value)
}
function applyAnswer(itemId: string, answer: AnswerFeedback, elapsedMs: number) {
  if (!quiz.value) return
  const item = quiz.value.items.find(i => i.quiz_item_id === itemId)
  if (!item || item.answered) return
  item.answered = true
  item.result = { answer_id: answer.answer_id, response: answer.response, is_correct: answer.is_correct,
    correct_answer: answer.correct_answer, explanation: answer.explanation,
    answered_at: answer.answered_at, elapsed_ms: elapsedMs }
  latestMastery.value = { itemId, before: answer.mastery.before, after: answer.mastery.after }
  quiz.value.answered += 1
  if (answer.is_correct) quiz.value.correct += 1
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
      <div class="sidebar-group-label">学习工作台</div>
      <nav class="sidebar-nav" aria-label="主导航">
        <button type="button" :class="{ active: view === 'overview' }" @click="navigate('overview')"><LayoutDashboard :size="19" /><span>学习总览</span></button>
        <button type="button" :class="{ active: view === 'practice' }" @click="navigate('practice')"><BookOpen :size="19" /><span>开始练习</span><span v-if="quiz?.status === 'active'" class="nav-dot"></span></button>
        <button type="button" :class="{ active: view === 'bank' }" @click="navigate('bank')"><ClipboardCheck :size="19" /><span>题库管理</span><span v-if="pendingCount" class="nav-dot"></span></button>
        <button type="button" :class="{ active: view === 'library' }" @click="navigate('library')"><FolderOpen :size="19" /><span>资料导入与管理</span></button>
        <button type="button" :class="{ active: view === 'galaxy' }" @click="navigate('galaxy')"><Orbit :size="19" /><span>知识星系 · 3D</span></button>
        <button type="button" :class="{ active: view === 'report' }" @click="navigate('report')"><TrendingUp :size="19" /><span>学习报告</span></button>
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
          <button class="icon-button refresh-button" aria-label="刷新数据" title="刷新数据" :disabled="refreshing || !courseId" @click="refresh"><RefreshCw :size="18" :class="{ spinning: refreshing }" /></button>
          <div class="topbar-avatar">学</div>
        </div>
      </header>
      <main class="page-content">
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
                <div class="hero-actions"><button class="button button-light" @click="navigate('library')"><Upload :size="18" /> 导入课件资料</button><button class="hero-secondary" @click="navigate('practice')">开始练习 <ChevronRight :size="18" /></button></div>
              </div>
              <div class="hero-art" aria-hidden="true">
                <div class="orbit orbit-1"></div><div class="orbit orbit-2"></div>
                <div class="hero-note note-a"><span class="note-dot teal"></span>学习进度 <span>持续记录</span></div>
                <div class="hero-knowledge"><div class="hero-knowledge-label">当前估计掌握度</div><div class="hero-knowledge-value">{{ percent(avgMastery) }}</div><div class="mini-bars"><span v-for="n in 12" :key="n" :style="{ height: (16 + (n * 7 + n % 3 * 11) % 40) + 'px' }"></span></div><div class="hero-knowledge-bottom"><span>知识追踪 BKT</span><Activity :size="17" /></div></div>
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
                <div v-else class="empty-preview"><div class="empty-preview-icon"><Target :size="25" /></div><strong>你的学习轨迹即将开始</strong><p>完成第一次练习后，这里会展示各知识点的掌握度估计。</p></div>
              </section>
              <section class="panel side-panel">
                <div class="panel-heading"><div><p class="eyebrow">NEXT STEP</p><h2>继续学习</h2></div><div class="small-pill">推荐操作</div></div>
                <div class="continue-illustration"><div class="continue-icon"><BookOpen :size="26" /></div><span class="continue-lines"></span><span class="continue-lines short"></span></div>
                <div class="continue-body"><h3>{{ quiz?.status === 'active' ? '上次练习还未结束' : approvedCount ? '准备好开始了吗？' : '先准备几道练习题' }}</h3>
                  <p v-if="quiz?.status === 'active'">已经完成 {{ quiz.answered }}/{{ quiz.total }} 道题，可以从上次的位置继续。</p>
                  <p v-else-if="approvedCount">从 {{ currentCourse?.title }} 的已审核题库中出题，作答后即可查看解析。</p><p v-else>课程暂时没有已审核题目，请先完成 AI 出题和人工审核。</p>
                  <button class="button button-primary continue-button" @click="navigate('practice')">{{ quiz?.status === 'active' ? '继续上次测评' : '前往练习' }} <ArrowRight :size="18" /></button>
                  <button v-if="pendingCount || !approvedCount" class="text-link pending-hint qb-home-link" @click="navigate('bank')">{{ pendingCount ? pendingCount + ' 道题待审核 · 去处理' : '去生成并审核题目' }} <ArrowRight :size="15" /></button>
                </div>
              </section>
            </div>
            <div class="small-footnote"><Lightbulb :size="16" /><span>BKT 掌握度是基于作答行为的模型估计，不代表经过校准的真实能力分数。</span></div>
          </div>

          <div v-else-if="view === 'practice'" class="view-stack">
            <div class="section-heading"><div><p class="eyebrow">PRACTICE STUDIO</p><h1>专项练习<span class="heading-dot">.</span></h1><p class="section-description">{{ quiz ? '专注于眼前这一题，让每次作答都有收获。' : '选择题量，开始一次有迹可循的练习。' }}</p></div>
              <button v-if="quiz" class="button button-outline button-small" @click="quiz = null"><RotateCcw :size="16" /> 新测评设置</button>
            </div>
            <template v-if="!quiz">
              <div class="setup-layout">
                <section class="panel setup-panel">
                  <div class="setup-eyebrow"><div class="setup-icon"><BookOpen :size="24" /></div><span>创建一场新的测评</span></div>
                  <h2>今天想练习多少道题？</h2><p>我们会从 <strong>{{ currentCourse?.title }}</strong> 的最新已审核题目中选取练习。</p>
                  <div class="setup-field"><label for="question-count">练习题量</label>
                    <div v-if="maxCount" class="count-control"><button aria-label="减少一道题" :disabled="count <= 1" @click="count--">−</button>
                      <select id="question-count" v-model.number="count"><option v-for="number in countOptions" :key="number" :value="number">{{ number }} 道题</option></select>
                      <button aria-label="增加一道题" :disabled="count >= maxCount" @click="count++">+</button></div>
                    <div v-else class="empty-count">暂无可供练习的已审核题目</div>
                  </div>
                  <div class="setup-points"><span><CheckCircle2 :size="17" /> 作答后即时反馈</span><span><Activity :size="17" /> 更新知识掌握度</span><span><Clock3 :size="17" /> 自动保存学习记录</span></div>
                  <button class="button button-primary start-button" :disabled="maxCount < 1 || starting" @click="startQuiz"><LoaderCircle v-if="starting" :size="18" class="spinning" /><Play v-else :size="18" fill="currentColor" />{{ starting ? '正在创建测评…' : '开始练习' }} <ArrowRight :size="18" /></button>
                  <div class="setup-footer">当前模式：<strong>普通顺序练习</strong> · 自适应选题将在后续版本加入</div>
                </section>
                <aside class="panel setup-aside"><div class="setup-aside-graphic"><div class="abstract-ring ring-one"></div><div class="abstract-ring ring-two"></div><Target :size="60" :stroke-width="1.5" /></div>
                  <h3>用练习发现真正的薄弱点</h3><p>每道题都关联知识点；答案提交后，BKT 会根据历史作答动态估计掌握概率。</p>
                  <div class="aside-divider"></div><div class="aside-stats"><span>已审核题目</span><strong>{{ approvedCount }}</strong></div><div class="aside-stats"><span>待审核题目</span><strong>{{ pendingCount }}</strong></div>
                  <button v-if="!approvedCount || pendingCount" class="text-link setup-admin" @click="navigate('bank')">前往题库生成与审核 <ArrowRight :size="16" /></button>
                </aside>
              </div>
            </template>
            <template v-else>
              <div class="quiz-layout">
                <section class="quiz-main">
                  <div class="quiz-progress panel"><div class="quiz-progress-top"><span class="quiz-progress-title"><span class="pulse-dot"></span>{{ isComplete ? '本次练习已完成' : '正在进行 · 普通练习' }}</span><strong>{{ quiz.answered }} <span>/ {{ quiz.total }} 题</span></strong></div>
                    <div class="progress-track big"><span :style="{ width: (quiz.total ? quiz.answered / quiz.total * 100 : 0) + '%' }"></span></div></div>
                  <div v-if="isComplete" class="completion-banner"><div class="completion-icon"><Award :size="28" /></div><div><strong>本轮练习完成！</strong><span>答对 {{ quiz.correct }} / {{ quiz.total }} 题，所有作答已保存。</span></div><button class="button button-light" @click="navigate('report')">查看报告 <ArrowRight :size="17" /></button></div>
                  <div v-if="currentItem" class="question-card panel">
                    <div class="question-head"><span class="type-tag"><FileQuestion :size="15" /> {{ currentItem.question_type === 'single_choice' ? '单选题' : '判断题' }}</span><span class="question-index">QUESTION {{ String(currentItem.position).padStart(2, '0') }}</span></div>
                    <h2>{{ currentItem.stem }}</h2><p class="question-instruction">{{ currentItem.question_type === 'single_choice' ? '请选择一个最符合题意的答案' : '请判断下列说法是否正确' }}</p>
                    <div class="options-list">
                      <button v-for="option in currentOptions" :key="String(option.key)" class="answer-option" :class="{
                        selected: effectiveChoice === option.key && !currentItem.answered,
                        'option-correct': currentItem.answered && currentResult?.correct_answer === option.key,
                        'option-wrong': currentItem.answered && currentResult?.response === option.key && !currentResult?.is_correct
                      }" :disabled="currentItem.answered || submitting || !!pendingRequest" @click="selectChoice(option.key)">
                        <span class="option-letter">{{ option.label }}</span><span class="option-text">{{ option.text }}</span>
                        <CheckCircle2 v-if="currentItem.answered && currentResult?.correct_answer === option.key" :size="21" class="option-state" />
                        <XCircle v-else-if="currentItem.answered && currentResult?.response === option.key && !currentResult?.is_correct" :size="21" class="option-state" />
                        <span v-else class="option-circle"></span>
                      </button>
                    </div>
                    <div v-if="currentItem.answered && currentResult" class="answer-feedback" :class="{ incorrect: !currentResult.is_correct }">
                      <div class="feedback-heading"><CheckCircle2 v-if="currentResult.is_correct" :size="22" /><XCircle v-else :size="22" /><strong>{{ currentResult.is_correct ? '回答正确，继续保持！' : '再巩固一下这个知识点' }}</strong></div>
                      <p>正确答案：<strong>{{ formatChoice(currentResult.correct_answer) }}</strong></p>
                      <div class="explanation-block"><span>题目解析</span><p>{{ currentResult.explanation || '暂无解析' }}</p></div>
                      <div v-if="latestMastery?.itemId === currentItem.quiz_item_id" class="mastery-change">
                        <Activity :size="16" /> BKT 估计掌握度 <strong>{{ percent(latestMastery.before) }} → {{ percent(latestMastery.after) }}</strong>
                      </div>
                    </div>
                    <div class="question-actions"><div class="answer-safety"><Lightbulb :size="17" /><span>{{ currentItem.answered ? '答案与解析已显示' : '提交前不会显示正确答案' }}</span></div>
                      <button v-if="!currentItem.answered" class="button button-primary submit-button" :disabled="!canAnswer" @click="submit"><LoaderCircle v-if="submitting" :size="18" class="spinning" /><Check v-else :size="18" />{{ submitting ? '正在保存答案…' : pendingRequest ? '重试提交答案' : '提交答案' }}</button>
                      <button v-else class="button button-primary submit-button" @click="nextQuestion">{{ isComplete ? '查看学习报告' : '下一题' }} <ArrowRight :size="18" /></button>
                    </div>
                  </div>
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
                  <div class="panel quiz-aside-note"><Activity :size="22" /><h3>每一次作答，都在更新你的学习画像。</h3><p>答题记录会用于 BKT 知识追踪；掌握概率只是一项模型估计。</p></div>
                </aside>
              </div>
            </template>
          </div>

          <QuestionBank v-else-if="view === 'bank'" :course-id="courseId" :initial-tab="bankEntryTab" @updated="refresh" />
          <DocumentLibrary v-else-if="view === 'library'" :course-id="courseId" :course-name="currentCourse?.title || '课程'"
            @updated="refresh" @open-review="openBankTab('knowledge')" @open-external="openBankTab('external')" />
          <KnowledgeGalaxy v-else-if="view === 'galaxy'" :course-id="courseId"
            :course-name="currentCourse?.title || '课程'"
            @open-bank="navigate('bank')" @open-practice="navigate('practice')" />
          <div v-else class="view-stack">
            <div class="section-heading"><div><p class="eyebrow">LEARNING ANALYTICS</p><h1>学习报告<span class="heading-dot">.</span></h1><p class="section-description">发现学习中的进步，也正视尚未掌握的部分。</p></div>
              <button class="button button-outline button-small" @click="refresh"><RefreshCw :size="17" :class="{ spinning: refreshing }" /> 更新报告</button></div>
            <section class="report-top-grid">
              <div class="panel report-feature"><div class="report-feature-heading"><div class="stat-icon white-icon"><Activity :size="23" /></div><span>知识掌握度 · BKT 模型</span></div><div class="report-big-value">{{ percent(avgMastery) }}<span v-if="avgMastery !== null"> / 100%</span></div>
                <p>已练习知识点的平均估计掌握概率</p><div class="report-feature-bottom"><Sparkles :size="17" /> {{ avgMastery === null ? '完成一次测评，就能建立你的第一条学习轨迹。' : '持续练习，观察掌握度随作答变化。' }}</div></div>
              <div class="report-stat-stack"><div class="panel report-stat"><div class="stat-icon blue-bg"><ClipboardList :size="21" /></div><div><span>累计作答</span><strong>{{ report?.answered ?? 0 }} <small>道</small></strong></div></div>
                <div class="panel report-stat"><div class="stat-icon orange-bg"><Target :size="21" /></div><div><span>累计正确率</span><strong>{{ percent(report?.accuracy) }}</strong></div></div></div>
            </section>
            <section class="panel mastery-table-panel">
              <div class="panel-heading"><div><p class="eyebrow">PER KNOWLEDGE POINT</p><h2>知识点掌握分析</h2></div><span class="table-count">{{ report?.knowledge_points.length ?? 0 }} 个知识点</span></div>
              <div v-if="!report?.knowledge_points.length" class="empty-preview report-empty"><CircleHelp :size="30" /><strong>暂无知识点</strong><p>先从课件提取并审核知识点，再开始练习。</p></div>
              <template v-else><div class="table-head"><span>知识点</span><span>掌握度估计</span><span>练习次数</span><span>状态</span></div>
                <div v-for="point in sortedKnowledge" :key="point.knowledge_point_id" class="mastery-row">
                  <div class="mastery-row-name"><div class="mastery-row-icon"><BookOpen :size="17" /></div><strong>{{ point.canonical_name }}</strong></div>
                  <div class="mastery-cell"><span class="mastery-score" :class="masteryClass(point.p_mastery)">{{ point.observations ? percent(point.p_mastery) : '—' }}</span><div class="progress-track"><span :class="masteryClass(point.p_mastery)" :style="{ width: point.observations ? percent(point.p_mastery) : '0%' }"></span></div></div>
                  <div class="mastery-row-count">{{ point.observations ?? 0 }} 次<span v-if="point.answered"> · 正确 {{ point.correct }}</span></div>
                  <div><span class="mastery-status" :class="masteryClass(point.p_mastery)">{{ masteryLabel(point) }}</span></div>
                </div></template>
            </section>
            <div class="report-disclaimer"><AlertCircle :size="18" /><span>BKT 使用初始教学参数，属于概率估计；数据较少时结果可能波动，不应作为正式成绩或真实能力的绝对判断。</span></div>
            <div class="report-bottom-action"><button class="button button-primary" @click="navigate('practice')">继续练习 <ArrowRight :size="18" /></button></div>
          </div>
        </template>
      </main>
      <footer class="page-footer"><span>© 2026 知迹 KnowTrace</span><span>让每一次学习，都有迹可循。</span><span>本地学习原型 · v1.0.0</span></footer>
    </div>
  </div>
</template>
