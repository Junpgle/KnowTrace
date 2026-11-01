<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, CheckCircle2, ClipboardCheck, FileCheck2, LoaderCircle, RefreshCw, ShieldAlert } from '@lucide/vue'
import { api, type Chapter, type QuestionBulkApprovalPreview } from '../api'

const props = defineProps<{ courseId: string }>()
const emit = defineEmits<{ updated: [] }>()
const preview = ref<QuestionBulkApprovalPreview | null>(null)
const chapters = ref<Chapter[]>([])
const selected = ref<string[]>([])
const chapterId = ref('')
const showRisk = ref(false)
const confirmed = ref(false)
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const error = ref('')
let seq = 0

const candidates = computed(() => (preview.value?.questions ?? [])
  .filter(x => !chapterId.value || x.chapter_id === chapterId.value))
const ready = computed(() => candidates.value.filter(x => x.eligible))
const risk = computed(() => candidates.value.filter(x => !x.eligible))
const chosen = computed(() => ready.value.filter(x => selected.value.includes(x.id)))
const allSelected = computed(() => ready.value.length > 0 &&
  ready.value.every(q => selected.value.includes(q.id)))

async function refresh(resetSelection = false) {
  const token = ++seq
  loading.value = true
  error.value = ''
  try {
    const [p, c] = await Promise.all([
      api.questionBulkApprovalPreview(props.courseId),
      api.chapters(props.courseId),
    ])
    if (token !== seq) return
    preview.value = p
    chapters.value = c
    const permitted = new Set(p.questions.filter(q => q.eligible).map(q => q.id))
    selected.value = resetSelection ? [...permitted] :
      selected.value.filter(id => permitted.has(id))
    if (!selected.value.length && !resetSelection) selected.value = [...permitted]
    confirmed.value = false
  } catch (e) {
    if (token === seq) error.value = e instanceof Error ? e.message : '无法读取批量审核预览'
  } finally {
    if (token === seq) loading.value = false
  }
}
function toggleAll() {
  const ids = ready.value.map(q => q.id)
  if (allSelected.value) selected.value = selected.value.filter(id => !ids.includes(id))
  else selected.value = [...new Set([...selected.value,...ids])]
  confirmed.value = false
}
async function approve() {
  if (!confirmed.value || !chosen.value.length || saving.value) return
  const ids = chosen.value.map(x => x.id)
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const r = await api.approveQuestionsBulk(props.courseId, ids)
    selected.value = []
    await refresh(true)
    message.value = '已批量通过 '+r.applied.length+' 道题；跳过 '+r.skipped.length+
      ' 道。已通过题目进入正式练习，风险题仍留在待审核列表。'
    if (r.skipped.length) {
      message.value += ' 跳过原因：'+r.skipped.slice(0,3).map(x => x.reason).join('；')
    }
    emit('updated')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '批量审核操作失败'
  } finally { saving.value = false }
}
watch(() => props.courseId, () => {
  ++seq
  preview.value=null;selected.value=[];confirmed.value=false
  message.value='';error.value='';chapterId.value=''
  void refresh(true)
},{immediate:true})
watch(chapterId, () => { confirmed.value = false })
watch(selected, () => { confirmed.value = false })
</script>

<template>
  <section class="panel bqr">
    <div class="bqr-header">
      <div class="bqr-symbol"><FileCheck2 :size="21"/></div>
      <div class="bqr-heading">
        <h2>一键审核题目</h2>
        <p>不用逐题翻看答案。先核查课件证据、题目结构和映射，再一次通过低风险候选题。</p>
      </div>
      <button type="button" class="bqr-refresh" :disabled="loading||saving"
        aria-label="刷新一键审核预览" @click="refresh(true)"><RefreshCw :size="18" :class="{spinning:loading}"/></button>
    </div>
    <div v-if="error" class="bqr-alert error" role="alert"><AlertTriangle :size="16"/>{{ error }}</div>
    <div v-if="message" class="bqr-alert success" role="status"><CheckCircle2 :size="16"/>{{ message }}</div>
    <div class="bqr-stats">
      <div><strong>{{preview?.total ?? '—'}}</strong><span>待审核题目</span></div>
      <div><strong>{{preview?.eligible ?? '—'}}</strong><span>符合批量检查</span></div>
      <div><strong>{{preview?.flagged ?? '—'}}</strong><span>需单独核对</span></div>
      <div><strong>{{chosen.length}}</strong><span>已选择通过</span></div>
    </div>
    <div class="bqr-tools">
      <label>审核范围
        <select v-model="chapterId" :disabled="loading||saving">
          <option value="">全部章节</option>
          <option v-for="c in chapters" :key="c.id" :value="c.id">{{c.title}}</option>
        </select>
      </label>
      <button type="button" :disabled="!ready.length||loading||saving" @click="toggleAll">
        {{allSelected?'取消全选':'选中符合条件的全部题目'}}
      </button>
    </div>
    <div class="bqr-list">
      <label v-for="q in ready" :key="q.id" class="bqr-item">
        <input v-model="selected" type="checkbox" :value="q.id" :disabled="saving||loading"/>
        <span class="bqr-stem">{{q.stem}}</span>
        <small>{{q.knowledge_count}} 知识点 · {{q.source_count}} 课件引用</small>
      </label>
      <p v-if="!ready.length" class="bqr-empty">
        {{loading?'正在加载待审核题目…':'当前范围暂无可一键通过的题目。可展开风险项检查原因。'}}
      </p>
    </div>
    <div class="bqr-risk" v-if="risk.length">
      <button type="button" @click="showRisk=!showRisk">
        <ShieldAlert :size="16"/> {{risk.length}} 道题需要单独核对
        <span>{{showRisk?'收起':'查看原因'}}</span>
      </button>
      <div v-if="showRisk" class="bqr-risk-items">
        <div v-for="q in risk" :key="q.id" class="bqr-risk-item">
          <strong>{{q.stem}}</strong>
          <small>{{q.issues.join('；')}}</small>
        </div>
      </div>
    </div>
    <div class="bqr-confirm" v-if="chosen.length">
      <p><ShieldAlert :size="17"/>
        已选中 {{chosen.length}} 道题。程序验证课件引文、关联知识点、版本和题目字段，
        但不能证明 AI 答案或解析准确。直接通过后将进入正式题库和练习统计。
      </p>
      <label><input v-model="confirmed" type="checkbox"/>
        我确认跳过逐题人工核对，接受 AI 候选答案可能存在错误，并批量批准所选题目。</label>
      <button class="button button-primary" type="button" :disabled="!confirmed||loading||saving" @click="approve">
        <LoaderCircle v-if="saving" class="spinning" :size="17"/>
        <ClipboardCheck v-else :size="17"/>
        {{saving?'正在审核…':'一键审核通过（'+chosen.length+' 道）'}}
      </button>
    </div>
    <p class="bqr-note">无需调用 AI，不消耗额外 Token。存在图片识别、证据或映射异常及疑似重复的题目不会被自动批准；此功能不等于 AI 内容真实性审核。审核操作写入独立记录。</p>
  </section>
</template>

<style scoped>
.bqr{display:grid;gap:15px;padding:20px}
.bqr-header{display:flex;align-items:center;gap:12px}
.bqr-symbol{height:44px;width:44px;display:grid;place-items:center;flex:0 0 44px;border-radius:12px;background:#e8f7f1;color:#138f80}
.bqr-heading{flex:1;min-width:0}
.bqr-heading h2{margin:0 0 4px;color:#204d5b;font-size:17px}
.bqr-heading p{margin:0;color:#698995;font-size:12px;line-height:1.65}
.bqr-refresh{border:0;background:transparent;color:#638c99;cursor:pointer;padding:7px}
.bqr-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}
.bqr-stats>div{display:grid;gap:5px;padding:13px 11px;background:#f0f8f7;border-radius:10px}
.bqr-stats strong{color:#148f82;font-size:24px;line-height:1.2}
.bqr-stats span{color:#67838e;font-size:11px}
.bqr-tools{display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap}
.bqr-tools label{display:grid;gap:5px;font-size:12px;color:#496d78}
.bqr-tools select{padding:8px 12px;max-width:300px;min-width:200px;background:white;color:#355766;border:1px solid #c8dcde;border-radius:8px}
.bqr-tools button{background:none;border:0;cursor:pointer;color:#148e80;font-size:12px;font-weight:700}
.bqr-list{display:grid;gap:6px;max-height:280px;overflow:auto}
.bqr-item{display:flex;align-items:center;gap:10px;padding:11px 13px;background:#f6fafb;border-radius:8px;line-height:1.6;color:#385f6a;font-size:12px;cursor:pointer}
.bqr-item input,.bqr-confirm input{accent-color:#159c8b}
.bqr-item input{flex:0 0 auto}
.bqr-stem{flex:1;min-width:0;overflow-wrap:anywhere}
.bqr-item small{color:#8ba3ad;white-space:nowrap}
.bqr-empty{color:#8097a1;font-size:12px}
.bqr-risk{border:1px solid #efdfc5;background:#fffaf1;border-radius:10px;overflow:hidden}
.bqr-risk>button{width:100%;display:flex;gap:9px;align-items:center;cursor:pointer;border:0;background:transparent;color:#966f3b;padding:11px 13px;font-size:12px;font-weight:700;text-align:left}
.bqr-risk>button span{margin-left:auto}
.bqr-risk-items{display:grid;max-height:230px;overflow:auto;border-top:1px solid #efe1cc}
.bqr-risk-item{display:grid;gap:4px;padding:11px 13px;border-bottom:1px solid #f2e9dc}
.bqr-risk-item strong{color:#654f33;font-size:12px;font-weight:600}
.bqr-risk-item small{color:#a66f35;font-size:11px}
.bqr-confirm{display:grid;justify-items:start;gap:11px;padding:16px;background:#eaf8f3;border-radius:11px;border:1px solid #c9e8db}
.bqr-confirm p{display:flex;gap:7px;font-size:12px;color:#477970;margin:0;line-height:1.8}
.bqr-confirm p svg{flex:0 0 17px;margin-top:3px}
.bqr-confirm label{font-size:12px;color:#346e64;line-height:1.8;display:flex;gap:8px;align-items:flex-start}
.bqr-note{font-size:11px;color:#8da2aa;margin:0;line-height:1.8}
.bqr-alert{display:flex;gap:9px;align-items:center;padding:11px;border-radius:9px;font-size:12px}
.bqr-alert.error{background:#fff1ed;color:#a05038}.bqr-alert.success{background:#e7f7f0;color:#247b6a}
@media(max-width:700px){.bqr{padding:15px}.bqr-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.bqr-item{flex-wrap:wrap}.bqr-item small{width:100%;padding-left:23px}.bqr-tools select{min-width:0;max-width:100%}}
</style>
