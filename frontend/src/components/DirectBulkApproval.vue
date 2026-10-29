<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertCircle, CheckCircle2, ClipboardCheck, FileCheck2,
  Image, LoaderCircle, RefreshCw, ShieldAlert } from '@lucide/vue'
import { api, type DirectApprovalPreview } from '../api'

const props=defineProps<{courseId:string}>()
const emit=defineEmits<{updated:[]}>()
const preview=ref<DirectApprovalPreview|null>(null)
const selected=ref<string[]>([])
const loading=ref(false)
const saving=ref(false)
const confirmed=ref(false)
const error=ref('')
const success=ref('')
const showAll=ref(false)
let token=0
const eligible=computed(()=>preview.value?.points.filter(p=>p.eligible)??[])
const chosen=computed(()=>eligible.value.filter(p=>selected.value.includes(p.id)))
const chosenVisual=computed(()=>chosen.value.filter(p=>p.has_visual).length)
const chosenDupes=computed(()=>chosen.value.filter(p=>p.duplicate_name).length)
const candidates=computed(()=>showAll.value?preview.value?.points??[]:
  preview.value?.points.filter(p=>p.eligible&&p.ai_generated)??[])
async function reload(){
  const n=++token
  loading.value=true;error.value=''
  try{
    const result=await api.directApprovalPreview(props.courseId)
    if(n!==token)return
    preview.value=result
    selected.value=selected.value.filter(id=>result.points.some(p=>p.id===id&&p.eligible))
  }catch(e){if(n===token)error.value=e instanceof Error?e.message:'读取待审核知识点失败'}
  finally{if(n===token)loading.value=false}
}
function toggleAll(){
  const ids=candidates.value.filter(p=>p.eligible).map(p=>p.id)
  if(ids.length&&ids.every(id=>selected.value.includes(id)))
    selected.value=selected.value.filter(id=>!ids.includes(id))
  else selected.value=Array.from(new Set([...selected.value,...ids]))
  confirmed.value=false
}
async function apply(){
  if(!confirmed.value||!chosen.value.length||saving.value)return
  saving.value=true;error.value='';success.value=''
  try{
    const result=await api.directBulkApprove(props.courseId,selected.value)
    selected.value=[];confirmed.value=false
    await reload()
    success.value='已直接通过 '+result.applied.length+' 个知识点；跳过 '+result.skipped.length+' 个无效或来源不足的知识点。'
    emit('updated')
  }catch(e){error.value=e instanceof Error?e.message:'批量通过失败'}
  finally{saving.value=false}
}
watch(()=>props.courseId,()=>{
  ++token;selected.value=[];confirmed.value=false;preview.value=null;error.value='';success.value=''
  void reload()
},{immediate:true})
watch(selected,()=>{confirmed.value=false})
</script>
<template>
  <section class="panel db-approve">
    <div class="db-heading">
      <div class="db-heading-icon"><FileCheck2 :size="21"/></div>
      <div><h2>信任模型结果 · 直接批量通过</h2>
        <p>不再调用第二次 AI 审核，手动选中需要通过的知识点，一次确认即可。</p></div>
      <button type="button" class="db-refresh" :disabled="loading||saving" aria-label="刷新待审核列表" @click="reload"><RefreshCw :size="17" :class="{spinning:loading}"/></button>
    </div>
    <p v-if="error" class="db-alert error" role="alert"><AlertCircle :size="16"/>{{error}}</p>
    <p v-if="success" class="db-alert success" role="status"><CheckCircle2 :size="16"/>{{success}}</p>
    <div class="db-stats">
      <span>待审核 <strong>{{preview?.points.length??0}}</strong></span>
      <span>有课件证据 <strong>{{preview?.eligible??0}}</strong></span>
      <span>已选择 <strong>{{selected.length}}</strong></span>
    </div>
    <div class="db-toolbar">
      <label class="db-all-toggle"><input v-model="showAll" type="checkbox" @change="confirmed=false"/>
        显示所有待审核知识点（默认仅列出 AI 生成项）</label>
      <button type="button" :disabled="!candidates.length" @click="toggleAll">
        {{candidates.filter(p=>p.eligible).every(p=>selected.includes(p.id))&&candidates.length?'取消全选':'全选符合条件的知识点'}}
      </button>
    </div>
    <p v-if="!candidates.length" class="db-empty">当前没有符合筛选条件的待审核知识点。</p>
    <div v-else class="db-list">
      <label v-for="point in candidates" :key="point.id" :class="['db-item',{disabled:!point.eligible}]">
        <input v-model="selected" type="checkbox" :value="point.id" :disabled="!point.eligible"/>
        <span class="db-item-name">{{point.canonical_name}}</span>
        <span v-if="point.has_visual" class="db-tag visual"><Image :size="12"/>图片识别</span>
        <span v-if="point.duplicate_name" class="db-tag warning"><AlertCircle :size="12"/>可能重复</span>
        <small>{{point.eligible?point.source_count+' 条来源':'缺少课件证据，无法直接通过'}}</small>
      </label>
    </div>
    <div v-if="chosen.length" class="db-confirm">
      <p><ShieldAlert :size="16"/>
        你已选择 {{chosen.length}} 个知识点，含 {{chosenVisual}} 个图片来源、{{chosenDupes}} 个可能同名重复项。
        直接通过后，这些知识点将进入正式知识图谱和后续出题流程，但模型仍可能存在遗漏或概念错误。</p>
      <label><input v-model="confirmed" type="checkbox"/>
        我确认跳过第二轮 AI 审核，直接批准这 {{chosen.length}} 个知识点，并愿意后续自行修订。</label>
      <button type="button" class="button button-primary" :disabled="!confirmed||saving" @click="apply">
        <LoaderCircle v-if="saving" :size="17" class="spinning"/><ClipboardCheck v-else :size="17"/>
        确认直接批量通过（{{chosen.length}}）
      </button>
    </div>
    <p class="db-note">此操作仅更新你选中的待审核项目，不会调用 AI，也不会额外产生 Token 费用；没有关联课程证据的项目不允许批量通过。所有操作均写入知识点审核历史。</p>
  </section>
</template>
<style scoped>
.db-approve{display:grid;gap:14px;padding:21px}
.db-heading{display:flex;align-items:center;gap:12px}
.db-heading-icon{width:43px;height:43px;flex:0 0 43px;display:grid;place-items:center;background:#e8f7f1;border-radius:12px;color:#1b9b83}
.db-heading>div:nth-child(2){flex:1;min-width:0}
.db-heading h2{font-size:15px;margin:0 0 6px;color:#2d5865}
.db-heading p,.db-note{font-size:11px;color:#8da1aa;line-height:1.8;margin:0}
.db-refresh{display:grid;place-items:center;border:0;background:none;color:#7195a0;padding:7px}
.db-stats{display:flex;flex-wrap:wrap;gap:9px}
.db-stats>span{padding:9px 12px;background:#f0f8f7;border-radius:8px;font-size:11px;color:#67928a}
.db-stats strong{color:#188b75}
.db-toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.db-all-toggle{display:flex;gap:7px;align-items:center;font-size:11px;color:#678795}
.db-toolbar button{border:0;background:none;font-size:11px;color:#168f80;font-weight:800;cursor:pointer}
.db-list{display:grid;gap:6px;max-height:275px;overflow:auto;padding-right:3px}
.db-item{display:flex;align-items:center;gap:9px;padding:10px 12px;background:#f7fafb;border-radius:9px;font-size:11px;color:#4d7181}
.db-item-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.db-item small{font-size:10px;color:#90a8af}
.db-item input,.db-all-toggle input,.db-confirm input{accent-color:#18a28b}
.db-item.disabled{opacity:.5}
.db-tag{display:flex;align-items:center;gap:4px;border-radius:8px;padding:4px 6px;font-size:9px;white-space:nowrap}
.db-tag.visual{color:#7b77ae;background:#eeeafa}.db-tag.warning{color:#ad8350;background:#fff1df}
.db-confirm{background:#f2faf7;border:1px solid #cdeae1;border-radius:11px;padding:15px;display:grid;gap:12px}
.db-confirm p{display:flex;gap:8px;align-items:flex-start;color:#66897e;line-height:1.8;font-size:11px;margin:0}
.db-confirm p svg{flex:0 0 16px}
.db-confirm label{font-size:11px;color:#427d71;line-height:1.8;display:flex;gap:8px;align-items:flex-start}
.db-confirm .button{justify-self:start}
.db-alert{display:flex;gap:8px;align-items:center;padding:11px 14px;border-radius:9px;font-size:11px}
.db-alert.error{color:#ad6d54;background:#fff4ee}.db-alert.success{color:#238675;background:#eaf9f3}
.db-note{font-size:10px}
.db-empty{font-size:11px;color:#96a9af}
@media(max-width:700px){.db-item{flex-wrap:wrap}.db-item small{width:100%;padding-left:23px}}
</style>