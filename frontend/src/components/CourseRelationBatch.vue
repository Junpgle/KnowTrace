<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, CheckCircle2, CirclePause, GitBranch, LoaderCircle,
  Play, RefreshCw, RotateCcw, ShieldCheck, Sparkles } from '@lucide/vue'
import { api, type AIProviderId, type RelationBatch, type RelationBulkPreview,
  type RelationType, type BridgePreview } from '../api'

const props=defineProps<{courseId:string; approvedCount:number}>()
const emit=defineEmits<{updated:[]}>()
const provider=ref<AIProviderId>('default')
const focusLimit=ref(30)
const perFocus=ref(2)
const mode=ref<'single'|'grouped'|'bridge'>('grouped')
const groupSize=ref(12)
const bridgeInfo=ref<BridgePreview|null>(null)
const bridgeLoading=ref(false)
const bridgeError=ref('')
let bridgeToken=0
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
watch([provider,focusLimit,perFocus,groupSize,mode],()=>{confirmCost.value=false})
async function loadBridgePreview(){
  const n=++bridgeToken
  if(mode.value!=='bridge'){bridgeInfo.value=null;bridgeError.value='';return}
  bridgeInfo.value=null;bridgeError.value='';bridgeLoading.value=true
  try{
    const preview=await api.bridgePreview(props.courseId,focusLimit.value,groupSize.value)
    if(n===bridgeToken)bridgeInfo.value=preview
  }catch(e){
    if(n===bridgeToken)bridgeError.value=e instanceof Error?e.message:'候选预览失败'
  }finally{if(n===bridgeToken)bridgeLoading.value=false}
}
watch([mode,focusLimit,groupSize,()=>props.courseId],()=>{
  void loadBridgePreview()
},{immediate:true})
watch(chosenTypes,()=>{confirmApproval.value=false},{deep:true})
onBeforeUnmount(()=>{if(polling)clearInterval(polling);++token;++bridgeToken})
async function start(){
  if(busy.value||active.value)return
  if(focusLimit.value>30&&!confirmCost.value){
    error.value='超过30个知识点需要先确认模型调用成本。';return
  }
  error.value='';message.value='';busy.value=true
  try{
    const r=await api.startRelationBatch(props.courseId,{
      provider:provider.value,max_focus_points:focusLimit.value,
      per_focus:perFocus.value,mode:mode.value,group_size:groupSize.value,
      confirm_large_run:confirmCost.value
    })
    message.value=mode.value==='bridge'
      ? '已筛选 '+r.total+' 组跨章节候选（'+r.total_points+' 个参与点次）。AI 仅分析候选组，不穷举所有跨章节组合。'
      : mode.value==='grouped'
        ? '已将 '+r.total_points+' 个知识点分为 '+r.total+' 组，逐条核验后进入待审核区。'
        : '已启动 '+r.total+' 个知识点的逐点分析（兼容模式）。'
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
      <label>每点关系候选上限<select v-model.number="perFocus" :disabled="active||busy">
        <option :value="1">1 条</option><option :value="2">2 条</option>
        <option :value="3">3 条</option><option :value="5">5 条</option>
      </select></label>
    </div>
    <div class="rb-setup rb-strategy">
      <label>分析策略<select v-model="mode" :disabled="active||busy">
        <option value="grouped">第一轮 · 章节内相似知识点分组</option>
        <option value="bridge">第二轮 · 跨章节桥接关系（推荐补全）</option>
        <option value="single">逐点分析（旧模式）</option>
      </select></label>
      <label v-if="mode!=='single'">每组知识点数<select v-model.number="groupSize" :disabled="active||busy">
        <option :value="6">6 个（输入较短）</option>
        <option :value="8">8 个</option>
        <option :value="12">12 个（推荐）</option>
        <option :value="16">16 个（输入较长）</option>
      </select></label>
    </div>
    <p class="rb-hint" v-if="mode==='grouped'">
      先按真实章节划分；未分章的知识点按原始课件来源聚合，再根据名称、摘要和共享课件片段的相似程度分组。
      每组调用一次 AI，模型可比较组内任意两个知识点，只有真实原文支持的候选关系才会保存。
      单组最多输出 18 条候选，不保证每组一定有关系。
    </p>
    <p class="rb-hint" v-else-if="mode==='bridge'">
      跨章节桥接：通过课程名称、知识点名称、摘要及共享课件证据，快速检索不同真实章节（未分章按课件区分）的候选点，
      按小组调用 AI，且仅保存真正跨章节的关系。此轮与章节内任务独立；已完成的跨章节候选组合不再重复提交。
      找不到足够匹配线索时会提示，不会强行编造关系。
    </p>
    <p class="rb-hint" v-else>
      旧版逐点模式：每个知识点单独分析，最多考虑11个候选，可能较慢。已经完成的知识点会被跳过。
    </p>
    <div v-if="mode==='bridge'" class="rb-bridge-preview">
      <strong>跨章节候选预估（不会调用 AI）</strong>
      <p v-if="bridgeLoading">正在分析章节归属与低成本候选匹配…</p>
      <p v-else-if="bridgeError" role="alert" class="rb-alert error">{{bridgeError}}</p>
      <template v-else-if="bridgeInfo">
        <div class="rb-bridge-stats">
          <span>真实章节 {{bridgeInfo.actual_chapters}}</span>
          <span>未归类 {{bridgeInfo.unassigned_points}}</span>
          <span>课件 {{bridgeInfo.source_documents}} 份</span>
          <span>候选 {{bridgeInfo.candidate_groups}} 组</span>
          <span>参与 {{bridgeInfo.participating_points}} 个点次</span>
        </div>
        <p>{{bridgeInfo.note}}</p>
        <button type="button" class="rb-refresh" :disabled="bridgeLoading" @click="loadBridgePreview">
          <RefreshCw :size="14"/>重新估算
        </button>
      </template>
    </div>
    <p v-if="approvedCount<2" class="rb-alert error">
      当前可用的已审核知识点不足 2 个。请先到「题库管理 → 知识点审核 → 直接批量通过」批准有课件来源的知识点。
    </p>
    <label v-if="focusLimit>30" class="rb-check">
      <input v-model="confirmCost" type="checkbox"/>
      我确认本轮最多处理 {{focusLimit}} 个知识点；
      {{mode==='single'?'逐点模式可能发起同等数量的多轮模型请求':'预计不超过 '+Math.ceil(focusLimit/2)+' 组候选请求（实际按可检索的章节匹配情况决定）'}}。
      MiMo 按实际 Token 收费；分组模式可能增加单次输入长度。
    </label>
    <button type="button" class="button button-primary" :disabled="active||busy||approvedCount<2||(focusLimit>30&&!confirmCost)||(mode==='bridge'&&(!bridgeInfo||bridgeInfo.candidate_groups===0))" @click="start">
      <LoaderCircle v-if="busy" class="spinning" :size="16"/><Play v-else :size="16"/>
      开始批量建立关系
    </button>
    <div v-if="current" class="rb-progress">
      <div class="rb-progress-head"><strong>本轮任务：{{current.status==='running'?'分析中':current.status==='stopping'?'等待当前请求完成':current.status==='succeeded'?'已完成':current.status==='interrupted'?'服务重启后中断':'已停止'}}</strong>
        <span>{{current.completed_points}} / {{current.total_points}} 个知识点 ·
        已完成 {{current.completed}} / {{current.total}} 组 · {{current.progress}}%</span></div>
      <div class="rb-track"><span :style="{width:current.progress+'%'}"/></div>
      <p>已生成 <strong>{{current.created_relations}}</strong> 条待审核关系；跳过 {{current.skipped}} 组/点；校验提醒 {{current.warning_count}} 条。</p>
      <p v-if="current.current_focus">当前正在分析{{current.mode==='single'?'知识点':'知识点分组'}}，代表 ID：{{current.current_focus}}</p>
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
.rb-bridge-preview{padding:13px;background:#fff;border:1px solid #d5e8e2;border-radius:10px;display:grid;gap:10px;color:#4b8076;font-size:11px}
.rb-bridge-preview p{font-size:11px;line-height:1.75;color:#79948e;margin:0}
.rb-bridge-stats{display:flex;flex-wrap:wrap;gap:7px}
.rb-bridge-stats span{border-radius:7px;padding:7px;background:#edf8f4;color:#217a6a;font-size:10px}
.rb-bridge-preview .rb-refresh{justify-self:start;display:flex;align-items:center;gap:6px;font-size:10px}
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