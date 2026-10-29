<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, CheckCircle2, CirclePause, GitBranch, LoaderCircle,
  Play, RefreshCw, RotateCcw, ShieldCheck, Sparkles } from '@lucide/vue'
import { api, type AIProviderId, type RelationBatch, type RelationBulkPreview,
  type RelationType } from '../api'

const props=defineProps<{courseId:string; approvedCount:number}>()
const emit=defineEmits<{updated:[]}>()
const provider=ref<AIProviderId>('default')
const focusLimit=ref(30)
const perFocus=ref(2)
const confirmCost=ref(false)
const confirmApproval=ref(false)
const allowVisual=ref(false)
const chosenTypes=ref<RelationType[]>(['related','prerequisite_recommended'])
const jobs=ref<RelationBatch[]>([])
const reviewSummary=ref<RelationBulkPreview|null>(null)
const busy=ref(false)
const message=ref('')
const error=ref('')
let polling:ReturnType<typeof setInterval>|null=null
let token=0
let loading=false
const current=computed(()=>jobs.value[0]??null)
const active=computed(()=>current.value?.status==='running'||current.value?.status==='stopping')
const options=computed(()=>{
  const values=[30,100,300,500,1000,props.approvedCount]
  return [...new Set(values.filter(n=>n>0&&n<=2000))]
    .sort((a,b)=>a-b)
})
const labels:Record<RelationType,string>={
  prerequisite_required:'必要前置（严格先修）',
  prerequisite_recommended:'建议先学',
  related:'相关',
  uses:'使用',
  compares:'对比',
}
const chosenCount=computed(()=>reviewSummary.value?.types
  .filter(row=>chosenTypes.value.includes(row.relation_type))
  .reduce((sum,r)=>sum+r.total,0)??0)
async function reload(){
  if(loading)return
  const currentToken=++token
  loading=true
  try{
    const [batchList,preview]=await Promise.all([
      api.relationBatches(props.courseId),api.relationBulkPreview(props.courseId)
    ])
    if(currentToken!==token)return
    jobs.value=batchList
    reviewSummary.value=preview
  }catch(e){if(currentToken===token)error.value=e instanceof Error?e.message:'无法加载关系任务'}
  finally{loading=false}
}
function schedule(){
  if(polling){clearInterval(polling);polling=null}
  if(active.value)polling=setInterval(()=>{void reload()},2300)
}
watch(active,schedule)
watch(()=>props.courseId,()=>{
  ++token;jobs.value=[];reviewSummary.value=null
  focusLimit.value=30;confirmCost.value=false;confirmApproval.value=false
  message.value='';error.value=''
  void reload()
},{immediate:true})
watch([provider,focusLimit,perFocus],()=>{confirmCost.value=false})
watch(chosenTypes,()=>{confirmApproval.value=false},{deep:true})
onBeforeUnmount(()=>{if(polling)clearInterval(polling);++token})
async function start(){
  if(busy.value||active.value)return
  if(focusLimit.value>30&&!confirmCost.value){
    error.value='超过30个知识点需要先确认模型调用成本。';return
  }
  error.value='';message.value='';busy.value=true
  try{
    const r=await api.startRelationBatch(props.courseId,{
      provider:provider.value,max_focus_points:focusLimit.value,
      per_focus:perFocus.value,confirm_large_run:confirmCost.value
    })
    message.value='已启动 '+r.total+' 个知识点的批量关系发现。每个知识点调用一次模型，生成候选关系后再统一审核。'
    await reload()
  }catch(e){error.value=e instanceof Error?e.message:'批量生成启动失败'}
  finally{busy.value=false}
}
async function action(next:'stop'|'resume'){
  if(!current.value||busy.value)return
  error.value='';busy.value=true
  try{
    if(next==='stop')await api.stopRelationBatch(current.value.id)
    else await api.resumeRelationBatch(current.value.id)
    await reload()
    message.value=next==='stop'?'已请求在当前知识点完成后停止。':'已从上次检查点恢复生成。'
  }catch(e){error.value=e instanceof Error?e.message:'任务操作失败'}
  finally{busy.value=false}
}
async function approve(){
  if(!confirmApproval.value||!chosenTypes.value.length||busy.value)return
  busy.value=true;error.value=''
  try{
    const result=await api.approveRelationTypes(props.courseId,chosenTypes.value,allowVisual.value)
    message.value='已批准 '+result.approved+' 条关系，跳过 '+result.skipped+' 条（其中 '+result.cycles+' 条存在必要前置环，'+result.visual+' 条需要核对原图）。'
    confirmApproval.value=false
    await reload()
    emit('updated')
  }catch(e){error.value=e instanceof Error?e.message:'关系批量审批失败'}
  finally{busy.value=false}
}
</script>

<template>
  <section class="rb">
    <div class="rb-title"><div><Sparkles :size="21"/></div>
      <span><strong>整门课程 · 一键构建知识关系</strong>
      <small>适合数百至数千个知识点。自动按真实课件来源寻找候选关联，不需要逐个点选。</small></span>
      <button type="button" class="rb-refresh" aria-label="刷新关系进度" @click="reload"><RefreshCw :size="17"/></button>
    </div>
    <p v-if="error" role="alert" class="rb-alert error">{{error}}</p>
    <p v-if="message" role="status" class="rb-alert success">{{message}}</p>
    <div class="rb-setup">
      <label>AI 提供商<select v-model="provider" :disabled="active||busy">
        <option value="default">使用模型设置中的默认服务</option>
        <option value="mimo">小米 MiMo API（按量计费）</option>
        <option value="ollama">本地 Ollama</option>
      </select></label>
      <label>本轮分析知识点<select v-model.number="focusLimit" :disabled="active||busy">
        <option v-for="count in options" :key="count" :value="count">{{count===30?'先试运行 30 个':count+' 个'}}</option>
      </select></label>
      <label>每点最多关系数<select v-model.number="perFocus" :disabled="active||busy">
        <option :value="1">1 条</option><option :value="2">2 条</option>
        <option :value="3">3 条</option><option :value="5">5 条</option>
      </select></label>
    </div>
    <p class="rb-hint">只分析已审核、有关联课件证据的知识点。每个重点最多比较 11 个候选点，优先共享课件片段与同章节内容。找不到可靠证据时允许不生成连线。</p>
    <p v-if="approvedCount<2" class="rb-alert error">
      当前可用的已审核知识点不足 2 个。请先到「题库管理 → 知识点审核 → 直接批量通过」批准有课件来源的知识点。
    </p>
    <label v-if="focusLimit>30" class="rb-check">
      <input v-model="confirmCost" type="checkbox"/>
      我确认本轮最多处理 {{focusLimit}} 个知识点；MiMo 每个知识点可能触发多次模型请求及工具调用并产生费用，且会按需读取课件内容。
    </label>
    <button type="button" class="button button-primary" :disabled="active||busy||approvedCount<2||(focusLimit>30&&!confirmCost)" @click="start">
      <LoaderCircle v-if="busy" class="spinning" :size="16"/><Play v-else :size="16"/>
      开始批量建立关系
    </button>
    <div v-if="current" class="rb-progress">
      <div class="rb-progress-head"><strong>本轮任务：{{current.status==='running'?'分析中':current.status==='stopping'?'等待当前请求完成':current.status==='succeeded'?'已完成':current.status==='interrupted'?'服务重启后中断':'已停止'}}</strong>
        <span>{{current.completed}} / {{current.total}} · {{current.progress}}%</span></div>
      <div class="rb-track"><span :style="{width:current.progress+'%'}"/></div>
      <p>已生成 <strong>{{current.created_relations}}</strong> 条待审核关系；跳过 {{current.skipped}} 个知识点；校验提醒 {{current.warning_count}} 条。</p>
      <p v-if="current.current_focus">当前正在分析知识点 ID：{{current.current_focus}}</p>
      <p v-if="current.last_error" class="rb-alert error"><AlertTriangle :size="15"/>{{current.last_error}}</p>
      <div class="rb-actions">
        <button v-if="current.status==='running'" type="button" class="button button-outline button-small" :disabled="busy" @click="action('stop')"><CirclePause :size="16"/>当前项完成后停止</button>
        <button v-if="current.status==='stopped'||current.status==='interrupted'" type="button" class="button button-outline button-small" :disabled="busy" @click="action('resume')"><RotateCcw :size="16"/>从检查点继续</button>
      </div>
    </div>

    <div class="rb-review">
      <div class="rb-subtitle"><ShieldCheck :size="18"/><strong>集中审核已生成关系</strong>
        <small>待审核 {{reviewSummary?.total??0}} 条</small></div>
      <p class="rb-hint">可以按关系类型批量确认，不需要一条条点。必要前置会检查循环；AI 图像描述不是原始文档的人工核验证据，默认跳过。</p>
      <div v-if="reviewSummary?.types.length" class="rb-types">
        <label v-for="row in reviewSummary.types" :key="row.relation_type">
          <input v-model="chosenTypes" type="checkbox" :value="row.relation_type" />
          {{labels[row.relation_type]}} <strong>{{row.total}}</strong>
          <small v-if="row.visual">图像待核 {{row.visual}}</small>
        </label>
      </div>
      <p v-else class="rb-hint">还没有待审核关系，请先运行一轮 AI 发现。</p>
      <label v-if="chosenCount>0" class="rb-check">
        <input v-model="allowVisual" type="checkbox" @change="confirmApproval=false"/>
        我明确接受 AI 图像描述可能存在识别错误，允许批量采纳这类关系（默认关闭）。
      </label>
      <label v-if="chosenCount>0" class="rb-check">
        <input v-model="confirmApproval" type="checkbox"/>
        我确认按选中的关系类型批量批准最多 {{chosenCount}} 条候选关联，并接受尚可能存在 AI 推断误差。
      </label>
      <button type="button" class="button button-outline" :disabled="busy||chosenCount===0||!confirmApproval" @click="approve">
        <GitBranch :size="16"/>批量批准所选关系
      </button>
    </div>
  </section>
</template>
<style scoped>
.rb{border:1px solid #cde8e2;background:#f5fcf9;border-radius:12px;padding:17px;display:grid;gap:13px}
.rb-title,.rb-subtitle{display:flex;align-items:center;gap:10px}
.rb-title>div{width:39px;height:39px;display:grid;place-items:center;border-radius:10px;background:#e4f6ef;color:#1b9b86}
.rb-title span{flex:1;min-width:0;display:grid;gap:5px}
.rb-title strong{font-size:14px;color:#2b6062}
.rb-title small{font-size:10px;line-height:1.6;color:#7c9a98}
.rb-refresh{background:none;border:0;color:#368d7b;padding:6px}
.rb-setup{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.rb-setup label{display:grid;gap:7px;font-size:10px;font-weight:750;color:#648780}
.rb-setup select{background:white;border:1px solid #ccdfdc;border-radius:8px;min-width:0;width:100%;padding:9px;font-size:11px;color:#3c6c69}
.rb-hint{font-size:11px;color:#7c9b97;line-height:1.8;margin:0}
.rb-check{font-size:11px;display:flex;align-items:flex-start;gap:8px;color:#3c7970;line-height:1.8}
.rb-check input,.rb-types input{accent-color:#1aa28c;margin-top:4px}
.rb>.button,.rb-review>.button{justify-self:start}
.rb-progress{padding:13px;background:white;border:1px solid #d4e7e3;border-radius:10px;display:grid;gap:10px}
.rb-progress-head{display:flex;justify-content:space-between;gap:10px;font-size:11px;color:#327e71}
.rb-track{height:8px;background:#dbece8;border-radius:20px;overflow:hidden}
.rb-track span{display:block;height:100%;background:#20b79c;border-radius:20px}
.rb-progress p{font-size:10px;line-height:1.8;color:#7b9497;margin:0;overflow-wrap:anywhere}
.rb-actions{display:flex;gap:8px}
.rb-review{display:grid;gap:11px;border-top:1px solid #dceae7;padding-top:15px}
.rb-subtitle{color:#3c766a}
.rb-subtitle strong{font-size:12px}
.rb-subtitle small{font-size:11px;color:#7d9797;margin-left:auto}
.rb-types{display:flex;gap:12px;flex-wrap:wrap}
.rb-types label{font-size:11px;color:#47766f;display:flex;gap:6px;align-items:center;line-height:1.6}
.rb-types label strong{color:#188e7c}
.rb-types label small{color:#bd8558}
.rb-alert{padding:11px;border-radius:8px;font-size:11px;line-height:1.7;margin:0}
.rb-alert.error{background:#fff1e8;color:#ab684f}
.rb-alert.success{background:#e6f7ef;color:#247b67}
@media(max-width:700px){.rb-setup{grid-template-columns:1fr}}
</style>