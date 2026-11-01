<script setup lang="ts">
import { defineAsyncComponent, ref, watch } from 'vue'
import {
  ArrowRight, BookOpen, ClipboardCheck, FileText, Network,
  Sparkles, Layers3, ShieldCheck,
} from '@lucide/vue'
// Keep heavy PDF/graph modules out of the default learning bundle.
const DocumentLibrary = defineAsyncComponent(() => import('./DocumentLibrary.vue'))
const QuestionBank = defineAsyncComponent(() => import('./QuestionBank.vue'))
const KnowledgeGalaxy = defineAsyncComponent(() => import('./KnowledgeGalaxy.vue'))

type Section = 'materials' | 'knowledge' | 'relations' | 'questions'
type QuestionMode = 'questions' | 'batch' | 'external'

const props = defineProps<{
  courseId: string
  courseName: string
  initialSection?: Section
  initialQuestionTab?: 'overview' | 'knowledge' | 'questions' | 'batch' | 'external'
  initialDocumentId?: string
}>()
const emit = defineEmits<{
  updated: []
  'blind-trial': []
  'open-practice': []
}>()

const section = ref<Section>(props.initialSection ?? 'materials')
const questionMode = ref<QuestionMode>('questions')
const documentId = ref(props.initialDocumentId ?? '')
const sectionItems = [
  { id: 'materials' as const, label: '资料', description: '上传、解析与提取', icon: FileText },
  { id: 'knowledge' as const, label: '知识点', description: '校对与归类', icon: BookOpen },
  { id: 'relations' as const, label: '关系图谱', description: '探索与关系审核', icon: Network },
  { id: 'questions' as const, label: '题目', description: '生成、审核与管理', icon: ClipboardCheck },
]
function changeSection(target: Section) {
  section.value = target
}
function jumpQuestionMode(next: 'overview' | 'knowledge' | 'questions' | 'batch' | 'external') {
  if (next === 'knowledge') {
    section.value = 'knowledge'
  } else {
    if (next !== 'overview') questionMode.value = next
    section.value = 'questions'
  }
}
function chooseQuestionMode(next: QuestionMode) {
  questionMode.value = next
  section.value = 'questions'
}
function fromDocumentReview() { section.value = 'knowledge' }
function fromDocumentExternal(id: string) {
  documentId.value = id
  chooseQuestionMode('external')
}

watch(() => props.initialSection, next => {
  if (next) section.value = next
})
watch(() => props.initialQuestionTab, next => {
  if (next && next !== 'overview') jumpQuestionMode(next)
})
watch(() => props.initialDocumentId, next => {
  if (next) documentId.value = next
})
watch(() => props.courseId, () => {
  documentId.value = ''
  questionMode.value = 'questions'
  section.value = props.initialSection ?? 'materials'
})
</script>

<template>
  <div class="content-hub">
    <header class="content-hub-heading">
      <div>
        <p class="eyebrow">COURSE CONTENT</p>
        <h1>课程内容<span class="heading-dot">.</span></h1>
        <p class="section-description">统一管理 {{ courseName }} 的资料、知识点、关系图谱与题目。</p>
      </div>
    </header>

    <nav class="content-hub-nav" aria-label="课程内容模块">
      <button v-for="item in sectionItems" :key="item.id" type="button"
        :class="{ active: section === item.id }" :aria-current="section === item.id ? 'page' : undefined"
        @click="changeSection(item.id)">
        <span class="content-hub-nav-icon"><component :is="item.icon" :size="20" /></span>
        <span class="content-hub-nav-copy"><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
        <ArrowRight class="content-hub-nav-arrow" :size="15" />
      </button>
    </nav>

    <div class="content-hub-body">
      <DocumentLibrary v-if="section === 'materials'" :course-id="courseId" :course-name="courseName"
        embedded @updated="emit('updated')" @open-review="fromDocumentReview"
        @open-external="fromDocumentExternal" />

      <QuestionBank v-else-if="section === 'knowledge'" :key="courseId + ':knowledge'"
        :course-id="courseId" initial-tab="knowledge" embedded
        @updated="emit('updated')" @blind-trial="emit('blind-trial')"
        @navigate-tab="jumpQuestionMode" />

      <KnowledgeGalaxy v-else-if="section === 'relations'" :course-id="courseId"
        :course-name="courseName" embedded
        @open-bank="chooseQuestionMode('questions')" @open-practice="emit('open-practice')" />

      <section v-else class="content-hub-questions">
        <div class="content-hub-subnav" role="tablist" aria-label="题目工作区">
          <button type="button" role="tab" :aria-selected="questionMode === 'questions'"
            :class="{active:questionMode==='questions'}" @click="chooseQuestionMode('questions')">
            <ShieldCheck :size="17" /> 题库与审核
          </button>
          <button type="button" role="tab" :aria-selected="questionMode === 'batch'"
            :class="{active:questionMode==='batch'}" @click="chooseQuestionMode('batch')">
            <Sparkles :size="17" /> 批量出题
          </button>
          <button type="button" role="tab" :aria-selected="questionMode === 'external'"
            :class="{active:questionMode==='external'}" @click="chooseQuestionMode('external')">
            <Layers3 :size="17" /> 外部 AI 接力
          </button>
        </div>
        <QuestionBank :key="courseId + ':questions'" :course-id="courseId"
          :initial-tab="questionMode" :initial-document-id="documentId" embedded
          @updated="emit('updated')" @blind-trial="emit('blind-trial')"
          @navigate-tab="jumpQuestionMode" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.content-hub{display:grid;gap:21px;min-width:0}
.content-hub-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}
.content-hub-heading h1{margin:3px 0 7px}
.content-hub-nav{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px}
.content-hub-nav button{display:flex;align-items:center;gap:11px;min-width:0;padding:16px 13px;text-align:left;background:#fff;border:1px solid #dde8eb;border-radius:13px;cursor:pointer;color:#6a8490;transition:background .2s,border-color .2s,box-shadow .2s}
.content-hub-nav button:hover{background:#f7fbfa;border-color:#aad8ce}
.content-hub-nav button.active{background:#ecf8f4;border-color:#72cbb9;box-shadow:inset 0 0 0 1px rgba(33,168,149,.12);color:#128777}
.content-hub-nav-icon{width:39px;height:39px;display:grid;place-items:center;flex:0 0 39px;border-radius:11px;background:#f2f7f8;color:inherit}
.content-hub-nav button.active .content-hub-nav-icon{background:#d5f1e9}
.content-hub-nav-copy{display:grid;gap:4px;min-width:0;flex:1}
.content-hub-nav-copy strong{color:#284a58;font-size:15px}
.content-hub-nav-copy small{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#8198a0}
.content-hub-nav button.active strong{color:#12796f}
.content-hub-nav-arrow{opacity:0;flex:0 0 15px}
.content-hub-nav button:hover .content-hub-nav-arrow,.content-hub-nav button.active .content-hub-nav-arrow{opacity:1}
.content-hub-body{min-width:0}
.content-hub-questions{display:grid;gap:14px}
.content-hub-subnav{display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #e1e9eb;padding-bottom:11px}
.content-hub-subnav button{display:inline-flex;align-items:center;gap:7px;padding:10px 13px;background:transparent;border:1px solid transparent;border-radius:8px;color:#718f97;font-size:13px;font-weight:650;cursor:pointer}
.content-hub-subnav button.active{border-color:#bee5da;background:#e8f7f1;color:#138878}
.content-hub-subnav button:hover:not(.active){background:#f3f7f8;color:#315d65}
@media(max-width:1150px){.content-hub-nav{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:590px){.content-hub-nav{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
.content-hub-nav button{padding:12px 9px;gap:7px}
.content-hub-nav-icon{width:31px;height:31px;flex-basis:31px}
.content-hub-nav-copy strong{font-size:13px}.content-hub-nav-copy small{font-size:10px}
.content-hub-nav-arrow{display:none}
.content-hub-subnav{flex-wrap:nowrap;overflow-x:auto}
.content-hub-subnav button{font-size:12px;white-space:nowrap}
}
</style>
