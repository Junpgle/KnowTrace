<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertCircle, CheckCircle2, Layers3, LoaderCircle, PauseCircle,
  Play, RefreshCw, RotateCcw, ShieldCheck, Sparkles } from '@lucide/vue'
import { api, type AIProviderId, type AIProviderStatus, type Chapter,
  type QuestionBatch, type QuestionBatchPreview } from '../api'

const props = defineProps<{courseId: string}>()
const emit = defineEmits<{updated: []}>()
const provider = ref<AIProviderId>('default')
const chapterId = ref('')
const groupSize = ref(4)
const maxGroups = ref(1)
const questionsPerGroup = ref(1)
const single = ref(true)
const bool = ref(true)
const confirmed = ref(false)
const chapters = ref<Chapter[]>([])
const providers = ref<AIProviderStatus | null>(null)
const preview = ref<QuestionBatchPreview | null>(null)
const batches = ref<QuestionBatch[]>([])
const fetching = ref(false)
const operating = ref(false)
const error = ref('')
const success = ref('')
const active = computed(() => batches.value.find(b =>
  ['queued','running','stopping'].includes(b.status)))
const latest = computed(() => batches.value[0] ?? null)
let poller: ReturnType<typeof setInterval> | undefined
let version = 0

function message(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败，请检查后端状态'
}
function statusText(status: QuestionBatch['status']) {
  return ({queued:'排队中',running:'运行中',stopping:'正在停止',stopped:'已暂停',
    interrupted:'已中断，可继续',succeeded:'已完成'} as Record<string,string>)[status]
}
async function load(full = false) {
  const token = ++version
  fetching.value = full
  try {
    const [batchList, plan] = await Promise.all([
      api.questionBatches(props.courseId),
      api.questionBatchPreview(props.courseId,groupSize.value,chapterId.value || null),
    ])
    if(token !== version)return
    const before = batches.value[0]
    batches.value = batchList
    preview.value = plan
    if(before && ['running','stopping'].includes(before.status) &&
       batchList[0]?.id === before.id && !['running','stopping'].includes(batchList[0].status)) {
      emit('updated')
    }
    if(full) {
      const [cs, ps] = await Promise.all([api.chapters(props.courseId),api.aiProviders()])
      if(token !== version)return
      chapters.value = cs
      providers.value = ps
    }
  } catch(e) {
    if(token === version)error.value = message(e)
  } finally {
    if(token === version)fetching.value = false
  }
}
async function start() {
  if(operating.value || active.value || !confirmed.value || !single.value && !bool.value)return
  error.value = '';success.value = '';operating.value = true
  try{
    const result = await api.startQuestionBatch(props.courseId,{
      provider:provider.value,chapter_id:chapterId.value || null,
      group_size:groupSize.value,max_groups:maxGroups.value,
      questions_per_group:questionsPerGroup.value,
      question_types:[...(single.value ? ['single_choice' as const] : []),
        ...(bool.value ? ['true_false' as const] : [])],
      confirmed:true,
    })
    success.value = '已建立 ' + result.total_groups + ' 个生成检查点，结果均进入待审核。'
    confirmed.value = false
    await load()
  }catch(e){error.value=message(e)}
  finally{operating.value=false}
}
async function stop(id: string){
  if(operating.value)return
  error.value='';operating.value=true
  try{await api.stopQuestionBatch(id);success.value='正在请求安全停止。当前模型请求结束后暂停。';await load()}
  catch(e){error.value=message(e)}finally{operating.value=false}
}
async function resume(id: string){
  if(operating.value)return
  error.value='';operating.value=true
  try{await api.resumeQuestionBatch(id);success.value='已恢复未完成的知识簇；成功的分组不会重做。';await load()}
  catch(e){error.value=message(e)}finally{operating.value=false}
}
watch(() => [props.courseId, chapterId.value,groupSize.value],()=>{
  confirmed.value=false
  void load(true)
},{immediate:true})
poller=setInterval(()=>{if(active.value)void load()},3500)
onBeforeUnmount(()=>{if(poller)clearInterval(poller);version++})
</script>

<template>
  <div class="bqs">
    <section class="bqs-intro">
      <div class="bqs-heading"><Layers3 :size="23"/><div>
        <h2>智能批量题库建设</h2><p>章节与课件来源聚合 · 已审核关系辅助组簇 · AI 一题多知识点 · 来源校验 · 人工审核</p>
      </div></div>
      <button type="button" class="bqs-refresh" :disabled="fetching" @click="load(true)">
        <RefreshCw :size="16"/> 刷新
      </button>
    </section>
    <div v-if="error" class="bqs-error" role="alert"><AlertCircle :size="17"/>{{ error }}</div>
    <div v-if="success" class="bqs-success" role="status"><CheckCircle2 :size="17"/>{{ success }}</div>

    <section class="bqs-stats">
      <div><strong>{{ preview?.eligible ?? '—' }}</strong><span>具备可用来源的已审核知识点</span></div>
      <div><strong>{{ preview?.approved_covered ?? '—' }}</strong><span>已审核题目覆盖点</span></div>
      <div><strong>{{ preview?.pending_covered ?? '—' }}</strong><span>待审核题目涉及点</span></div>
      <div><strong>{{ preview?.uncovered ?? '—' }}</strong><span>待补题知识点</span></div>
    </section>

    <section class="bqs-form panel">
      <div class="bqs-section"><h3><Sparkles :size="17"/> 创建出题计划</h3>
        <p>先小批量试运行，再根据审核通过率和覆盖缺口逐步补题。批次只包含尚未被待审核或已审核题目覆盖的知识点。</p>
      </div>
      <div class="bqs-grid">
        <label>章节
          <select v-model="chapterId" :disabled="Boolean(active)">
            <option value="">全部章节（仍按章分组）</option>
            <option v-for="chapter in chapters" :value="chapter.id" :key="chapter.id">{{ chapter.title }}</option>
          </select>
        </label>
        <label>AI 服务
          <select v-model="provider" :disabled="Boolean(active)">
            <option value="default">默认 AI 服务（{{ providers?.default ?? '当前设置' }}）</option>
            <option value="ollama">本地 Ollama</option>
            <option value="mimo">小米 MiMo（可能产生费用）</option>
          </select>
        </label>
        <label>每组候选知识点
          <select v-model.number="groupSize" :disabled="Boolean(active)">
            <option v-for="n in [4,6,8,10,12]" :key="n" :value="n">{{ n }} 个</option>
          </select>
        </label>
        <label>本次最多处理分组
          <select v-model.number="maxGroups" :disabled="Boolean(active)">
            <option v-for="n in [1,3,5,10,20,30,50]" :key="n" :value="n">{{ n }} 组</option>
          </select>
        </label>
        <label>每组尝试出题
          <select v-model.number="questionsPerGroup" :disabled="Boolean(active)">
            <option v-for="n in [1,2,3,4]" :value="n" :key="n">{{ n }} 道</option>
          </select>
        </label>
        <fieldset :disabled="Boolean(active)"><legend>题型</legend>
          <label><input type="checkbox" v-model="single"/> 单选题</label>
          <label><input type="checkbox" v-model="bool"/> 判断题</label>
        </fieldset>
      </div>
      <div class="bqs-preview">
        <strong>预计可组成 {{ preview?.estimated_groups ?? '—' }} 个知识簇</strong>
        <span>本次最多调用 {{ Math.min(maxGroups,preview?.estimated_groups ?? 0) }} 次 AI，
          计划生成不超过 {{ Math.min(maxGroups,preview?.estimated_groups ?? 0)*questionsPerGroup }} 道候选题；实际可能更少。</span>
        <details v-if="preview?.preview_groups.length">
          <summary>查看前 {{ preview.preview_groups.length }} 个候选知识簇</summary>
          <div v-for="(group,index) in preview.preview_groups" :key="index">
            <b>第 {{ index+1 }} 簇</b> {{ group.names.join('、') }}
          </div>
        </details>
      </div>
      <label class="bqs-confirm"><input v-model="confirmed" type="checkbox" :disabled="Boolean(active)"/>
        <span>我确认开始调用所选 AI（MiMo 可能按 Token 收费）；生成结果只进入待审核题库，不能自动证明题目正确。</span>
      </label>
      <button type="button" class="button button-primary" :disabled="operating || fetching || Boolean(active) ||
        !confirmed || !preview?.uncovered || !single && !bool" @click="start">
        <LoaderCircle v-if="operating" :size="17" class="spinning"/><Play v-else :size="17"/>
        开始批量生成候选题
      </button>
      <p class="bqs-caution"><ShieldCheck :size="16"/> 组合题的多个知识点必须分别匹配原文证据；答题表现暂不能视为多个知识点的独立掌握证明。</p>
    </section>

    <section class="bqs-form panel">
      <div class="bqs-section"><h3>批次记录与断点续跑</h3>
        <p>每组完成后保存进度，刷新页面不会重新运行已完成分组。关闭后端进程会中断当前模型请求，须手动恢复。</p>
      </div>
      <div v-if="!batches.length" class="bqs-empty">尚未创建批量出题任务。</div>
      <article v-for="batch in batches" :key="batch.id" class="bqs-job">
        <div class="bqs-job-head">
          <strong>{{ batch.created_at.slice(0,19).replace('T',' ') }} · {{ statusText(batch.status) }}</strong>
          <div class="bqs-job-actions">
            <button v-if="['running','queued'].includes(batch.status)" type="button"
              :disabled="operating" @click="stop(batch.id)"><PauseCircle :size="15"/> 停止</button>
            <button v-if="['stopped','interrupted'].includes(batch.status)" type="button"
              :disabled="operating || Boolean(active)" @click="resume(batch.id)"><RotateCcw :size="15"/> 继续</button>
          </div>
        </div>
        <div class="bqs-meter"><div :style="{width: batch.progress+'%'}"/></div>
        <p>{{ batch.completed_groups }} / {{ batch.total_groups }} 组 · 已生成 {{ batch.created_questions }} 道待审核候选题 · 校验提醒 {{ batch.warning_count }} 条</p>
        <p v-if="batch.last_error" :class="batch.status === 'succeeded' ? 'bqs-hint' : 'bqs-error-text'"
        :role="batch.status === 'succeeded' ? 'status' : 'alert'">
        {{ batch.status === 'succeeded' ? '候选题校验提醒（已通过的题目仍然保存）：' : '失败原因：' }}{{ batch.last_error }}
      </p>
      <p v-if="batch.status === 'interrupted'" class="bqs-hint">
        任务已停止，不会自动重复调用 AI。请先检查失败原因，再决定是否手动继续；
        如遇 JSON 截断，可新建 4 个知识点 / 每组 1 题的小批次验证。
      </p>
      <p v-if="batch.status === 'succeeded' && batch.created_questions === 0" class="bqs-hint">
        本批没有保存合格题目。请展开下方检查点查看引用或字段校验提醒；该状态不代表成功出题。
      </p>
        <details v-if="batch.units.length">
          <summary>检查点明细（最近 {{ batch.units.length }} 组）</summary>
          <div v-for="unit in batch.units" :key="unit.id" class="bqs-unit">
            <span>组 {{ unit.ordinal }} · {{ unit.status }} · {{ unit.knowledge_point_ids.length }} 点 · {{ unit.created_questions }} 题</span>
            <small v-if="unit.last_error">
              {{ unit.status === 'succeeded' ? '候选题校验提醒：' : '处理异常：' }}{{ unit.last_error }}
            </small>
          </div>
        </details>
      </article>
    </section>
  </div>
</template>

<style scoped>
.bqs{display:grid;gap:18px;min-width:0}
.bqs-intro{display:flex;align-items:center;justify-content:space-between;gap:15px}
.bqs-heading{display:flex;align-items:flex-start;gap:12px;color:#168f86}
.bqs-heading h2{margin:0;color:#173f4a;font-size:21px}
.bqs-heading p,.bqs-section p{margin:5px 0 0;color:#65818a;font-size:13px;line-height:1.7}
.bqs-refresh{display:flex;gap:7px;align-items:center;padding:8px 12px;background:#fff;border:1px solid #c5dbdc;border-radius:9px;color:#257e77;cursor:pointer}
.bqs-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.bqs-stats>div{display:flex;flex-direction:column;gap:5px;padding:18px;border:1px solid #d5e6e7;background:#fff;border-radius:13px}
.bqs-stats strong{font-size:27px;color:#0b8e83}
.bqs-stats span{font-size:12px;color:#657d85}
.bqs-form{padding:22px;display:grid;gap:17px}
.bqs-section h3{display:flex;align-items:center;gap:9px;margin:0;font-size:17px;color:#204551}
.bqs-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
.bqs-grid>label,.bqs-grid fieldset{display:flex;flex-direction:column;gap:7px;min-width:0;font-size:13px;color:#4c6a76;font-weight:600}
.bqs-grid select{padding:10px;border-radius:9px;background:white;border:1px solid #d0e0e2;max-width:100%;color:#214453}
.bqs-grid fieldset{border:1px solid #d0e0e2;border-radius:9px;flex-direction:row;flex-wrap:wrap;align-items:center;padding:6px 10px}
.bqs-grid legend{padding:0 5px}
.bqs-grid fieldset label{display:flex;align-items:center;gap:5px;font-weight:400}
.bqs-preview{display:grid;gap:6px;background:#f3faf8;border:1px solid #d2e8e2;border-radius:12px;padding:15px;color:#28605e;font-size:13px}
.bqs-preview span{color:#577c79}
.bqs-preview details,.bqs-job details{margin-top:8px;line-height:1.8}
.bqs-preview summary,.bqs-job summary{cursor:pointer}
.bqs-preview details div{padding:4px;border-bottom:1px solid #d9eae6}
.bqs-confirm{display:flex;align-items:flex-start;gap:8px;font-size:13px;line-height:1.7;color:#526f76}
.bqs-caution{display:flex;gap:8px;align-items:center;margin:0;font-size:12px;color:#687e88}
.bqs-error,.bqs-success{padding:12px;display:flex;align-items:center;gap:8px;border-radius:10px;font-size:13px}
.bqs-error{background:#fff3eb;color:#a44837}.bqs-success{background:#e8f8f1;color:#18775a}
.bqs-job{border:1px solid #d4e2e4;border-radius:12px;padding:16px;display:grid;gap:9px}
.bqs-job-head{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:14px}
.bqs-job-actions{display:flex;gap:8px}
.bqs-job-actions button{display:flex;align-items:center;gap:5px;padding:7px 10px;border-radius:8px;color:#1c7a72;border:1px solid #b7dcd5;background:#f5fbfa;cursor:pointer}
.bqs-job-actions button:disabled{opacity:.5;cursor:not-allowed}
.bqs-meter{height:8px;border-radius:99px;overflow:hidden;background:#e6eff0}
.bqs-meter>div{height:100%;background:#23b49f;transition:width .25s}
.bqs-job p{margin:0;color:#647c84;font-size:12px}
.bqs-job .bqs-error-text{color:#a84842;overflow-wrap:anywhere;line-height:1.6}
.bqs-job .bqs-hint{color:#92622c;background:#fff8e8;border-radius:8px;padding:10px 12px;line-height:1.6}
.bqs-unit{display:grid;gap:2px;padding:5px 0;border-top:1px solid #e4eded;font-size:12px;color:#455f6d}
.bqs-unit small{color:#a16b44}
.bqs-empty{padding:24px;color:#8a9ba0;text-align:center;font-size:13px}
@media(max-width:840px){.bqs-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.bqs-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.bqs-grid{grid-template-columns:1fr}.bqs-stats{gap:7px}.bqs-form{padding:14px}}
</style>
