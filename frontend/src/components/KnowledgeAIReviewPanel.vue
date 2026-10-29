<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertCircle, ArrowRight, CheckCircle2, CirclePause, ClipboardCheck,
  FileSearch, LoaderCircle, Play, RefreshCw, ShieldAlert, Sparkles, XCircle } from '@lucide/vue'
import { api, type AIProviderId, type KnowledgePoint, type KnowledgeAIReview,
  type KnowledgeAIReviewBatch } from '../api'

const props=defineProps<{courseId:string; points:KnowledgePoint[]; selectedId:string}>()
const emit=defineEmits<{updated:[];select:[id:string]}>()
const reviews=ref<KnowledgeAIReview[]>([])
const jobs=ref<KnowledgeAIReviewBatch[]>([])
const selectedBatch=ref('')
const provider=ref<AIProviderId>('default')
const loading=ref(false)
const busy=ref(false)
const error=ref('')
const info=ref('')
const accepted=ref(false)
const cloudConsent=ref(false)
const selectedCandidates=ref<string[]>([])
const batch=computed(()=>jobs.value.find(x=>x.id===selectedBatch.value)??jobs.value[0]??null)
const active=computed(()=>batch.value?.status==='running'||batch.value?.status==='stopping')
const suggestions=computed(()=>reviews.value.filter(r=>r.recommendation==='approve'
  &&r.confidence==='high'&&r.verified_text&&!r.issues.length&&!r.stale
  &&r.source_kind==='text'))
const pendingCount=computed(()=>props.points.filter(k=>k.review_status==='pending').length)
const latestSelected=computed(()=>reviews.value.find(r=>r.knowledge_point_id===props.selectedId)??null)
const reviewLabel=(r:KnowledgeAIReview)=>r.recommendation==='approve'?'建议通过':
  r.recommendation==='reject'?'建议驳回':'建议复核或修改'
const batchLabel=(v:string)=>({
  running:'AI 正在逐条审核',stopping:'等待当前知识点审核结束后停止',
  stopped:'已停止，可继续',interrupted:'服务重启中断，可继续',
  succeeded:'本次预审完成',
} as Record<string,string>)[v]??v
let seq=0
let poll:ReturnType<typeof setInterval>|null=null
async function load(){
  const token=++seq
  loading.value=true
  try{
    const [r,j]=await Promise.all([
      api.knowledgeAIReviews(props.courseId),
      api.knowledgeAIReviewBatches(props.courseId),
    ])
    if(token!==seq)return
    reviews.value=r;jobs.value=j
    if(!j.some(item=>item.id===selectedBatch.value))selectedBatch.value=j[0]?.id??''
    selectedCandidates.value=selectedCandidates.value.filter(id=>suggestions.value.some(k=>k.id===id))
  }catch(e){if(token===seq)error.value=e instanceof Error?e.message:'审核记录读取失败'}
  finally{if(token===seq)loading.value=false}
}
function updateTimer(){
  if(poll){clearInterval(poll);poll=null}
  if(active.value)poll=setInterval(()=>{void load()},2400)
}
watch(active,updateTimer)
watch(()=>props.courseId,()=>{
  ++seq;reviews.value=[];jobs.value=[];selectedBatch.value=''
  selectedCandidates.value=[];accepted.value=false;cloudConsent.value=false;info.value='';error.value=''
  void load()
},{immediate:true})
onBeforeUnmount(()=>{if(poll)clearInterval(poll);++seq})
async function runOne(){
  if(!props.selectedId||busy.value||active.value)return
  if(provider.value!=='ollama'&&!cloudConsent.value){error.value='请先确认可能使用云端模型并产生 API 费用。';return}
  busy.value=true;error.value='';info.value=''
  try{
    const result=await api.reviewKnowledgeWithAI(props.selectedId,provider.value)
    await load()
    info.value='AI 已审核当前知识点：'+reviewLabel(result)+'。请核对理由与原文。'
  }catch(e){error.value=e instanceof Error?e.message:'当前知识点预审失败'}
  finally{busy.value=false}
}
async function startBatch(){
  if(busy.value)return
  if(provider.value!=='ollama'&&!cloudConsent.value){error.value='请先确认云端模型的数据分享与调用费用。';return}
  busy.value=true;error.value='';info.value='';accepted.value=false
  try{
    const created=await api.startKnowledgeAIReviewBatch(props.courseId,provider.value,200)
    selectedBatch.value=created.id
    info.value='已启动批量 AI 预审，最多处理200个尚无AI建议的待审核知识点。'
    await load()
  }catch(e){error.value=e instanceof Error?e.message:'无法启动预审'}
  finally{busy.value=false}
}
async function stopBatch(){
  if(!batch.value||busy.value)return
  busy.value=true;error.value=''
  try{
    await api.stopKnowledgeAIReviewBatch(batch.value.id)
    info.value='已请求停止。当前知识点模型调用结束后不再审核后续知识点。'
    await load()
  }catch(e){error.value=e instanceof Error?e.message:'停止失败'}
  finally{busy.value=false}
}
async function resumeBatch(){
  if(!batch.value||busy.value)return
  busy.value=true;error.value=''
  try{
    await api.resumeKnowledgeAIReviewBatch(batch.value.id)
    info.value='任务已从已完成的知识点继续。'
    await load()
  }catch(e){error.value=e instanceof Error?e.message:'恢复失败'}
  finally{busy.value=false}
}
function selectAllEligible(){
  if(selectedCandidates.value.length===Math.min(200,suggestions.value.length))selectedCandidates.value=[]
  else selectedCandidates.value=suggestions.value.slice(0,200).map(r=>r.id)
  accepted.value=false
}
async function acceptSelected(){
  if(!accepted.value||!selectedCandidates.value.length||busy.value)return
  busy.value=true;error.value='';info.value=''
  try{
    const result=await api.applyKnowledgeAIReviews(props.courseId,selectedCandidates.value)
    info.value='已采纳 '+result.applied.length+' 条经过验证的建议；跳过 '+result.skipped.length+' 条不满足条件的结果。'
    selectedCandidates.value=[];accepted.value=false
    await load()
    emit('updated')
  }catch(e){error.value=e instanceof Error?e.message:'批量采纳失败'}
  finally{busy.value=false}
}
watch(provider,()=>{cloudConsent.value=false})
function pointName(kid:string){return props.points.find(p=>p.id===kid)?.canonical_name??kid}
</script>
<template>
  <section class="panel ai-audit">
    <div class="ai-head">
      <div class="ai-heading"><span class="ai-logo"><FileSearch :size="22"/></span>
        <div><h2>AI 知识点预审</h2>
          <p>核对原文证据、知识点准确性与重复风险；AI 建议不直接改变正式审核状态。</p></div>
      </div>
      <button type="button" class="button button-outline button-small" :disabled="busy||loading" @click="load"><RefreshCw :size="15"/>刷新</button>
    </div>
    <p v-if="error" class="ai-feedback bad" role="alert"><AlertCircle :size="16"/>{{error}}</p>
    <p v-if="info" class="ai-feedback good" role="status"><CheckCircle2 :size="16"/>{{info}}</p>
    <div class="ai-actions">
      <label>审核模型
        <select v-model="provider" :disabled="busy||active">
          <option value="default">设置中的默认模型</option>
          <option value="mimo">小米 MiMo</option>
          <option value="ollama">本地 Ollama</option>
        </select>
      </label>
      <button type="button" class="button button-outline" :disabled="busy||active||!selectedId" @click="runOne">
        <LoaderCircle v-if="busy" class="spinning" :size="15"/><Sparkles v-else :size="15"/>审核当前知识点
      </button>
      <button type="button" class="button button-primary" :disabled="busy||active||pendingCount===0" @click="startBatch">
        <Play :size="16"/>批量 AI 预审（{{pendingCount}} 待审核）
      </button>
    </div>
    <label v-if="provider!=='ollama'" class="ai-confirm">
      <input v-model="cloudConsent" type="checkbox"/>
      我确认默认模型可能是 MiMo：云端审核会发送知识点及最多两个课件片段，可能产生 API 费用。
    </label>
    <div v-if="batch" class="ai-progress">
      <div class="ai-progress-top"><strong>{{batchLabel(batch.status)}}</strong>
        <span>{{batch.completed}} / {{batch.total}} 项 · {{batch.progress}}%</span></div>
      <div class="ai-track"><span :style="{width:batch.progress+'%'}"></span></div>
      <p v-if="batch.last_error" class="ai-feedback bad"><AlertCircle :size="15"/>{{batch.last_error}}</p>
      <div class="ai-actions">
        <button v-if="batch.status==='running'" class="button button-outline button-small" :disabled="busy" @click="stopBatch"><CirclePause :size="16"/>完成本项后停止</button>
        <button v-if="['stopped','interrupted'].includes(batch.status)" class="button button-outline button-small" :disabled="busy" @click="resumeBatch"><Play :size="16"/>从第 {{batch.next_index+1}} 项继续</button>
        <small v-if="batch.errors">{{batch.errors}} 个知识点因更改或异常被跳过</small>
      </div>
    </div>
    <div v-if="latestSelected" class="ai-selected">
      <div class="ai-result-head"><strong>当前知识点的 AI 建议</strong><span :class="['ai-verdict',latestSelected.recommendation]">{{reviewLabel(latestSelected)}}</span></div>
      <p>{{latestSelected.rationale}}</p>
      <small>提供商：{{latestSelected.provider}} / {{latestSelected.model}}；证据：{{latestSelected.verified_text?'原文逐字匹配':'尚未完成原文验证'}}；AI 信心：{{latestSelected.confidence}}（不等于正确率）</small>
      <p v-if="latestSelected.stale" class="ai-feedback bad">知识点发生变化或已由人工审核，旧建议已失效。</p>
      <ul v-if="latestSelected.issues.length"><li v-for="(issue,i) in latestSelected.issues" :key="i">{{issue}}</li></ul>
      <p v-if="latestSelected.evidence_quote" class="ai-quote">引用：“{{latestSelected.evidence_quote}}”</p>
    </div>
    <div class="ai-verified">
      <div class="ai-result-head"><strong>可批量采纳的建议（{{suggestions.length}}）</strong>
        <button type="button" class="ai-link" @click="selectAllEligible">{{selectedCandidates.length===Math.min(200,suggestions.length)?'取消全选':'全选合格建议'}}</button></div>
      <p class="ai-caption">仅列出 AI 建议通过、信心高、无问题、文字课件逐字引文已校验、且知识点未被修改的结果。图像描述和存疑知识点始终需要人工审核。</p>
      <div v-if="!suggestions.length" class="ai-none">暂无符合条件的建议。运行批量预审后，其他结果可在知识点列表中逐条查看。</div>
      <div v-else class="ai-candidates">
        <label v-for="item in suggestions.slice(0,200)" :key="item.id">
          <input v-model="selectedCandidates" type="checkbox" :value="item.id" @change="accepted=false"/>
          <span>{{pointName(item.knowledge_point_id)}}</span>
          <button type="button" class="ai-link" @click.prevent="emit('select',item.knowledge_point_id)">查看详情 <ArrowRight :size="13"/></button>
        </label>
      </div>
      <label v-if="selectedCandidates.length" class="ai-confirm">
        <input v-model="accepted" type="checkbox"/>
        我已了解 AI 审核可能误判，确认采纳选中的 {{selectedCandidates.length}} 条建议，改为正式审核通过。
      </label>
      <button v-if="selectedCandidates.length" type="button" class="button button-primary"
        :disabled="busy||!accepted" @click="acceptSelected"><ClipboardCheck :size="16"/>确认批量采纳（{{selectedCandidates.length}}）</button>
    </div>
    <p class="ai-privacy"><ShieldAlert :size="16"/>选择 MiMo 时，只会将当前知识点及最多两个有限长度的关联课件片段发送给云端。大批量处理可能产生 API 费用；停止在单个模型请求结束后生效。</p>
  </section>
</template>
<style scoped>
.ai-audit{display:grid;gap:16px;padding:21px;min-width:0}
.ai-head,.ai-heading,.ai-actions,.ai-result-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.ai-heading{justify-content:flex-start}
.ai-logo{background:#eaf7f3;color:#139a88;display:grid;place-items:center;width:45px;height:45px;border-radius:12px}
.ai-heading h2{margin:0 0 5px;font-size:16px}
.ai-heading p{margin:0;font-size:11px;color:#879fa9;line-height:1.8}
.ai-actions{justify-content:flex-start}
.ai-actions label{display:grid;gap:7px;font-size:11px;color:#668896;font-weight:700}
.ai-actions select{height:40px;min-width:160px;border:1px solid #dbe8eb;border-radius:9px;background:#fff;padding:7px;font-size:12px;color:#315869}
.ai-actions .button{min-height:39px}
.ai-actions small{font-size:10px;color:#a18e72}
.ai-progress{border:1px solid #dcebe8;background:#f2faf8;padding:15px;border-radius:12px;display:grid;gap:10px}
.ai-progress-top{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:#247d70}
.ai-track{height:8px;background:#d1e7e1;border-radius:8px;overflow:hidden}
.ai-track span{display:block;height:100%;background:#2cb7a0;border-radius:8px;transition:width .4s}
.ai-selected,.ai-verified{display:grid;gap:11px;padding:15px;border:1px solid #e1ebec;border-radius:11px}
.ai-result-head strong{font-size:12px;color:#365764}
.ai-verdict{padding:5px 10px;border-radius:8px;font-size:10px;background:#fff4de;color:#99734b}
.ai-verdict.approve{background:#e5f8ee;color:#188668}
.ai-verdict.reject{background:#fff0ef;color:#b16050}
.ai-selected p{font-size:11px;line-height:1.9;color:#527985;margin:0}
.ai-selected small{font-size:10px;color:#97a9b1}
.ai-selected ul{margin:0;padding-left:18px;color:#a77f57;font-size:11px;line-height:1.8}
.ai-selected p.ai-quote{background:#f6fafb;padding:11px;border-radius:7px;color:#568b85}
.ai-caption,.ai-privacy{color:#8aa0aa;font-size:10px;line-height:1.9;margin:0}
.ai-privacy{display:flex;gap:8px;align-items:flex-start}
.ai-privacy svg{flex:0 0 16px}
.ai-none{font-size:11px;color:#9babb4;background:#f7fafb;padding:14px}
.ai-candidates{display:grid;gap:7px;max-height:240px;overflow:auto}
.ai-candidates label{display:flex;gap:10px;align-items:center;font-size:11px;color:#557784;background:#f8fbfb;padding:11px;border-radius:8px}
.ai-candidates label span{flex:1}
.ai-candidates input,.ai-confirm input{accent-color:#169d87}
.ai-link{display:flex;gap:5px;align-items:center;color:#168d80;border:0;background:none;font-size:10px;font-weight:800}
.ai-confirm{display:flex;gap:10px;color:#4c7d76;font-size:11px;line-height:1.7}
.ai-verified>.button{justify-self:start;min-height:40px}
.ai-feedback{display:flex;align-items:flex-start;gap:9px;font-size:11px;line-height:1.8;padding:11px;border-radius:9px}
.ai-feedback.bad{background:#fff4ef;color:#b06b50}
.ai-feedback.good{background:#eaf9f2;color:#267d6b}
</style>
