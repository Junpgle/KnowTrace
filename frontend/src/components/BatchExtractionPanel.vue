<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertCircle, ArrowRight, CheckCircle2, CirclePause, FileStack,
  LoaderCircle, Play, RefreshCw, RotateCcw, ShieldAlert } from '@lucide/vue'
import { api, type AIProviderId, type BatchExtraction, type CourseDocument } from '../api'

const props=defineProps<{document:CourseDocument}>()
const emit=defineEmits<{updated:[];openReview:[]}>()
const jobs=ref<BatchExtraction[]>([])
const selectedJobId=ref('')
const busy=ref(false)
const loading=ref(false)
const problem=ref('')
const message=ref('')
const selectedProvider=ref<AIProviderId>('ollama')
const batchSize=ref(2)
const visual=ref(false)
const consent=ref(false)
const job=computed(()=>jobs.value.find(j=>j.id===selectedJobId.value)??jobs.value[0]??null)
const active=computed(()=>job.value?.status==='running'||job.value?.status==='stopping')
let timer:ReturnType<typeof setInterval>|null=null
let requestId=0
const isPdf=computed(()=>props.document.original_filename.toLowerCase().endsWith('.pdf'))
const statusText=(status:string)=>({
  running:'正在处理',stopping:'等待本批结束后停止',stopped:'已停止，可继续',
  interrupted:'服务重启中断，可继续',succeeded:'整份资料已处理完毕',
  failed:'处理失败',
} as Record<string,string>)[status]||status
function err(e:unknown){problem.value=e instanceof Error?e.message:'操作失败'}
async function load(){
  const id=++requestId
  loading.value=true
  try{
    const rows=await api.batchExtractions(props.document.id)
    if(id===requestId) {
      jobs.value=rows
      if (!rows.some(r=>r.id===selectedJobId.value)) selectedJobId.value=rows[0]?.id??''
    }
  }catch(e){if(id===requestId)err(e)}
  finally{if(id===requestId)loading.value=false}
}
function stopTimer(){if(timer){clearInterval(timer);timer=null}}
function updateTimer(){
  stopTimer()
  if(active.value) timer=setInterval(()=>{void load()},2200)
}
watch(()=>props.document.id,()=>{stopTimer();jobs.value=[];selectedJobId.value='';problem.value='';message.value='';void load()},{immediate:true})
watch(active,updateTimer)
onBeforeUnmount(()=>{stopTimer();++requestId})
async function start(){
  if(busy.value)return
  if(selectedProvider.value==='mimo'&&!consent.value){
    problem.value='云端批量处理会将每批课件文字发送给小米 MiMo，请先确认。';return
  }
  busy.value=true;problem.value='';message.value=''
  try{
    const created=await api.startBatchExtraction(props.document.id,{
      provider:selectedProvider.value,
      pages_per_batch:batchSize.value,
      include_visual:visual.value,
    })
    selectedJobId.value=created.id
    message.value='已启动整份资料处理任务。可随时离开页面，之后回来查看进度。'
    await load()
  }catch(e){err(e)}
  finally{busy.value=false}
}
async function stop(){
  if(!job.value||busy.value)return
  busy.value=true;problem.value='';message.value=''
  try{
    await api.stopBatchExtraction(job.value.id)
    message.value='已请求停止；当前进行中的模型请求结束后不再处理后续页面。'
    await load()
  }catch(e){err(e)}
  finally{busy.value=false}
}
async function resume(){
  if(!job.value||busy.value)return
  busy.value=true;problem.value='';message.value=''
  try{
    await api.resumeBatchExtraction(job.value.id)
    message.value='已从上次完成的页面继续。'
    await load()
  }catch(e){err(e)}
  finally{busy.value=false}
}
watch(selectedProvider,()=>{if(selectedProvider.value==='mimo')visual.value=false;consent.value=false})
watch(batchSize,()=>{if(visual.value&&batchSize.value>2)visual.value=false})
</script>

<template>
  <section class="panel batch-panel">
    <div class="batch-heading"><div class="batch-icon"><FileStack :size="22"/></div>
      <div><h2>一键处理整份资料</h2>
        <p>逐批提取知识点并保存，每批完成后记录进度；已有知识点会自动去重和复用。</p>
      </div>
      <button type="button" class="batch-refresh" :disabled="loading" aria-label="刷新批量任务" @click="load">
        <RefreshCw :size="17" :class="{spinning:loading}"/>
      </button>
    </div>
    <p v-if="problem" class="batch-feedback bad" role="alert"><AlertCircle :size="17"/>{{ problem }}</p>
    <p v-if="message" class="batch-feedback good" role="status"><CheckCircle2 :size="17"/>{{ message }}</p>
    <label v-if="jobs.length>1" class="batch-history">任务历史
      <select v-model="selectedJobId">
        <option v-for="item in jobs" :key="item.id" :value="item.id">
          {{item.created_at}} · {{statusText(item.status)}} · {{item.completed_pages}}/{{item.total_pages}} 页
        </option>
      </select>
    </label>
    <div v-if="job" class="batch-progress">
      <div class="batch-line"><strong>{{statusText(job.status)}}</strong>
        <span>{{ job.completed_pages }}/{{ job.total_pages }} 页 · {{ job.progress }}%</span></div>
      <div class="batch-track"><span :style="{width:job.progress+'%'}"></span></div>
      <div class="batch-metrics"><span>新增知识点 <b>{{ job.created_points }}</b></span>
        <span>复用 <b>{{ job.reused_points }}</b></span>
        <span>跳过空白/图片页 <b>{{ job.skipped_pages }}</b></span></div>
      <p v-if="job.last_error" class="batch-feedback bad"><AlertCircle :size="16"/>
        {{job.last_error}} · 可检查模型设置后点击继续。</p>
      <p v-if="job.status==='stopping'" class="batch-hint">正在等当前批次结束；无法立即终止已发送的模型请求。</p>
      <div class="batch-buttons">
        <button v-if="job.status==='running'" class="button button-outline" :disabled="busy" @click="stop">
          <CirclePause :size="17"/> 停止后续处理</button>
        <button v-if="job.status==='stopped'||job.status==='interrupted'" class="button button-primary"
          :disabled="busy" @click="resume"><RotateCcw :size="17"/> 从第 {{job.next_page}} 页继续</button>
        <button v-if="job.status==='succeeded'" class="button button-outline" @click="emit('openReview')">
          <CheckCircle2 :size="16"/> 审核提取结果 <ArrowRight :size="16"/></button>
      </div>
    </div>
    <div v-if="!active" class="batch-config">
      <div class="batch-fields">
        <label>处理模型<select v-model="selectedProvider">
          <option value="ollama">本地 Ollama</option>
          <option value="mimo">小米 MiMo API（可能收费）</option>
          <option value="default">使用设置中的默认模型</option>
        </select></label>
        <label>每批页数<select v-model.number="batchSize">
          <option :value="1">1 页</option><option :value="2">2 页</option>
          <option :value="4">4 页</option>
        </select></label>
      </div>
      <label v-if="isPdf&&selectedProvider==='ollama'&&batchSize<=2" class="batch-check">
        <input v-model="visual" type="checkbox" />
        低文字量 PDF 页启用本地视觉模型（每批最多两页）</label>
      <label v-if="selectedProvider==='mimo'||selectedProvider==='default'" class="batch-check">
        <input v-model="consent" type="checkbox" />
        我理解使用云端模型时，所选页面文字会分批传给提供商，可能产生 API 费用</label>
      <div class="batch-buttons">
        <button type="button" class="button button-primary" :disabled="busy||active||!document.total_pages" @click="start">
          <LoaderCircle v-if="busy" class="spinning" :size="17"/><Play v-else :size="17"/>
          {{job?.status==='succeeded'?'重新处理整份资料':'开始处理全部 '+document.total_pages+' 页'}}</button>
      </div>
    </div>
    <p class="batch-hint"><ShieldAlert :size="15"/>
      每批模型调用完成后才会更新进度。停止不会撤回已完成批次，不会删除题库或审核记录。关闭网页不会中断后端任务，重启后端会标记为“已中断”，可手动继续。</p>
  </section>
</template>

<style scoped>
.batch-panel{padding:22px;display:grid;gap:16px;min-width:0}
.batch-heading{display:flex;align-items:center;gap:11px}
.batch-heading>div:nth-child(2){flex:1;min-width:0}
.batch-heading h2{font-size:16px;margin:0 0 7px}
.batch-heading p{font-size:11px;color:#89a0a9;line-height:1.8;margin:0}
.batch-icon{background:#e3f5f0;color:#209e88;display:grid;place-items:center;width:43px;height:43px;flex:0 0 43px;border-radius:13px}
.batch-refresh{background:none;border:0;color:#7194a0;padding:7px}
.batch-history{display:grid;gap:7px;font-size:11px;color:#6c8892}
.batch-history select{border:1px solid #d7e5e8;border-radius:9px;padding:9px;background:#fff;color:#355d6b;max-width:100%}
.batch-progress{display:grid;gap:11px;padding:15px;border:1px solid #c8e8e1;background:#f1fbf8;border-radius:12px}
.batch-line{display:flex;justify-content:space-between;align-items:center;gap:12px;color:#276b60;font-size:12px}
.batch-line span{font-size:11px;color:#518f82}
.batch-track{height:9px;background:#d2e8e3;border-radius:20px;overflow:hidden}
.batch-track span{display:block;height:100%;border-radius:20px;background:linear-gradient(90deg,#27a59d,#59cf9b);transition:width .3s}
.batch-metrics{display:flex;gap:12px;flex-wrap:wrap;font-size:10px;color:#7b9c93}
.batch-metrics b{color:#218a77}
.batch-config{display:grid;gap:13px}
.batch-fields{display:grid;grid-template-columns:2fr 1fr;gap:12px}
.batch-fields label{display:grid;gap:8px;font-size:11px;color:#678693;font-weight:800}
.batch-fields select{min-height:40px;border:1px solid #d7e5e8;border-radius:9px;padding:8px;color:#345c65;background:white;min-width:0}
.batch-check{display:flex;gap:10px;align-items:flex-start;font-size:11px;color:#6b8993;line-height:1.8}
.batch-check input{accent-color:#1b9b89;width:16px;height:16px;margin-top:3px;flex:0 0 16px}
.batch-buttons{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.batch-buttons .button{min-height:40px}
.batch-hint{display:flex;align-items:flex-start;gap:7px;font-size:10px;color:#97a8af;line-height:1.75;margin:0}
.batch-hint svg{flex:0 0 15px}
.batch-feedback{display:flex;gap:9px;align-items:flex-start;padding:11px 13px;border-radius:9px;font-size:11px;line-height:1.8}
.batch-feedback svg{flex:0 0 17px}
.batch-feedback.bad{background:#fff4ed;color:#a5734f}
.batch-feedback.good{background:#e7f8f1;color:#238a71}
@media(max-width:600px){.batch-fields{grid-template-columns:1fr}}
</style>