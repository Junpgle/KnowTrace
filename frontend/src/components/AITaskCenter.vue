<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { AlertCircle, Bell, CheckCheck, CheckCircle2, CirclePause, Clock3,
  LoaderCircle, RefreshCw, RotateCcw, X } from '@lucide/vue'
import { api, type TaskCenter, type TaskNotification } from '../api'

const visible=ref(false)
const payload=ref<TaskCenter|null>(null)
const loading=ref(false)
const actionId=ref('')
const error=ref('')
const toasts=ref<TaskNotification[]>([])
const seen=new Set<string>()
let initialized=false
let polling:ReturnType<typeof setInterval>|null=null
const timeouts=new Map<string,ReturnType<typeof setTimeout>>()
let refreshToken=0
let pending=false
const active=computed(()=>payload.value?.tasks.filter(t=>['running','stopping'].includes(t.status))??[])
const recent=computed(()=>payload.value?.tasks??[])
const unread=computed(()=>payload.value?.unread_count??0)
const statusLabel=(status:string)=>({
  running:'正在处理',stopping:'等待当前批次完成',succeeded:'已完成',
  failed:'失败，可重试',stopped:'已停止',interrupted:'中断，可继续',
} as Record<string,string>)[status]??status
function closeToast(id:string){
  toasts.value=toasts.value.filter(t=>t.id!==id)
  const handle=timeouts.get(id)
  if(handle)clearTimeout(handle)
  timeouts.delete(id)
}
function pushToast(note:TaskNotification){
  // Multiple files finish together: each gets its own message.
  toasts.value=[note,...toasts.value].slice(0,4)
  const timeout=setTimeout(()=>closeToast(note.id),8500)
  timeouts.set(note.id,timeout)
}
async function refresh(){
  if(pending)return
  pending=true
  loading.value=true
  const token=++refreshToken
  try{
    const result=await api.taskCenter()
    if(token!==refreshToken)return
    if(initialized){
      const incoming=result.notifications.filter(n=>!seen.has(n.id))
      // Oldest to newest, preserving each concurrent task's result.
      for(const note of incoming.slice().reverse())pushToast(note)
    }
    for(const n of result.notifications)seen.add(n.id)
    initialized=true
    payload.value=result
    error.value=''
  }catch(e){
    if(token===refreshToken)error.value=e instanceof Error?e.message:'无法连接任务中心'
  }finally{pending=false;if(token===refreshToken)loading.value=false}
}
async function act(id:string,what:'stop'|'resume'){
  if(actionId.value)return
  actionId.value=id;error.value=''
  try{
    if(what==='stop')await api.stopBatchExtraction(id)
    else await api.resumeBatchExtraction(id)
    await refresh()
  }catch(e){error.value=e instanceof Error?e.message:'操作失败'}
  finally{actionId.value=''}
}
async function markRead(){
  try{
    await api.markTaskNotificationsRead()
    await refresh()
  }catch(e){error.value=e instanceof Error?e.message:'标记失败'}
}
function openCenter(){
  visible.value=true
  void refresh()
}
onMounted(()=>{
  void refresh()
  polling=setInterval(()=>{void refresh()},1800)
  document.addEventListener('visibilitychange', onVisibility)
})
function onVisibility(){
  if(!document.hidden)void refresh()
}
onBeforeUnmount(()=>{
  ++refreshToken
  if(polling)clearInterval(polling)
  document.removeEventListener('visibilitychange',onVisibility)
  for(const timer of timeouts.values())clearTimeout(timer)
  timeouts.clear()
})
</script>
<template>
  <div class="task-center-root">
    <button type="button" class="tc-bell" :aria-label="'AI 任务中心，'+active.length+' 个活动任务，'+unread+' 条未读通知'"
      :aria-expanded="visible" title="AI 任务与通知" @click="openCenter">
      <Bell :size="19"/>
      <span v-if="active.length" class="tc-running">{{active.length}}</span>
      <span v-else-if="unread" class="tc-unread">{{unread>99?'99+':unread}}</span>
    </button>
    <div v-if="visible" class="tc-mask" @click.self="visible=false">
      <section class="tc-drawer" role="dialog" aria-modal="true" aria-label="AI 任务中心">
        <div class="tc-head">
          <div><p>AI TASK CENTER</p><h2>提取任务与站内通知</h2>
            <small>独立追踪每份资料，不会因切换页面而丢失进度</small></div>
          <button class="tc-close" type="button" aria-label="关闭任务中心" @click="visible=false"><X :size="21"/></button>
        </div>
        <p v-if="error" class="tc-error" role="alert"><AlertCircle :size="15"/>{{error}}</p>
        <div class="tc-summary">
          <span><LoaderCircle v-if="active.length" class="spinning" :size="17"/><Clock3 v-else :size="17"/>进行中 <strong>{{active.length}}</strong></span>
          <span>未读通知 <strong>{{unread}}</strong></span>
          <button type="button" :disabled="loading" @click="refresh"><RefreshCw :size="16" :class="{spinning:loading}"/>刷新</button>
        </div>
        <div class="tc-scroll">
          <h3>提取进度 <small>同时最多 {{payload?.concurrency_limit??2}} 个模型请求</small></h3>
          <p v-if="!recent.length" class="tc-empty">暂无提取任务。请到资料库选择页面或整份资料开始处理。</p>
          <article v-for="task in recent" :key="task.id" class="tc-task" :class="{failed:task.status==='failed'}">
            <div class="tc-task-title"><strong :title="task.filename">{{task.filename}}</strong>
              <span :class="task.status">{{statusLabel(task.status)}}</span></div>
            <p class="tc-course">{{task.course_name}} · {{task.mode==='range'?'选定页面':'整份资料'}} · {{task.provider}}</p>
            <div class="tc-progress-line"><strong>{{task.completed_pages}} / {{task.page_count}} 页</strong>
              <span>{{task.progress}}%</span></div>
            <div class="tc-track"><span :style="{width:task.progress+'%'}"></span></div>
            <div v-if="task.status==='running' || task.status==='stopping'" class="tc-stage">
              <LoaderCircle v-if="task.work_stage==='extracting'" :size="14" class="spinning"/>
              <Clock3 v-else :size="14"/>
              {{task.work_stage==='extracting'
                ? '正在分析第 '+task.in_flight_start+'–'+task.in_flight_end+' 页（模型请求中，等待本批结果）'
                : '等待空闲的 AI 并发处理位置'}}
            </div>
            <p v-if="task.last_error" class="tc-failure"><AlertCircle :size="14"/>{{task.last_error}}</p>
            <div class="tc-task-bottom"><span>新增 {{task.created_points}} · 复用 {{task.reused_points}} · 跳过 {{task.skipped_pages}} 页</span>
              <button v-if="task.status==='running'" :disabled="!!actionId" @click="act(task.id,'stop')"><CirclePause :size="14"/>停止</button>
              <button v-if="['failed','stopped','interrupted'].includes(task.status)" :disabled="!!actionId" @click="act(task.id,'resume')">
                <RotateCcw :size="14"/>继续 / 重试</button>
            </div>
          </article>
          <div class="tc-notice-title"><h3>通知记录</h3>
            <button type="button" :disabled="!unread" @click="markRead"><CheckCheck :size="15"/>全部已读</button></div>
          <p v-if="!payload?.notifications.length" class="tc-empty">任务完成或失败时，会在这里留下可追溯记录。</p>
          <article v-for="note in payload?.notifications??[]" :key="note.id" class="tc-message" :class="{read:note.is_read}">
            <CheckCircle2 v-if="note.level==='success'" :size="19" class="tc-success"/>
            <AlertCircle v-else-if="note.level==='error'" :size="19" class="tc-danger"/>
            <Clock3 v-else :size="19" class="tc-info"/>
            <div><strong>{{note.title}} · {{note.filename}}</strong><p>{{note.message}}</p><small>{{note.created_at}}</small></div>
            <span v-if="!note.is_read" class="tc-new"></span>
          </article>
        </div>
        <p class="tc-footnote">页面约每 1.8 秒获取一次后端任务检查点。百分比仅代表已完成页面，不是模型当前请求内部的实际进度；正在分析的页码单独显示。</p>
      </section>
    </div>
    <div class="tc-toasts" aria-live="polite" aria-atomic="false">
      <div v-for="toast in toasts" :key="toast.id" class="tc-toast" :class="toast.level">
        <CheckCircle2 v-if="toast.level==='success'" :size="21"/>
        <AlertCircle v-else-if="toast.level==='error'" :size="21"/>
        <Clock3 v-else :size="21"/>
        <button type="button" class="tc-toast-body" @click="openCenter">
          <strong>{{toast.title}} · {{toast.filename}}</strong><span>{{toast.message}}</span>
        </button>
        <button type="button" aria-label="关闭通知" class="tc-toast-close" @click="closeToast(toast.id)"><X :size="16"/></button>
      </div>
    </div>
  </div>
</template>
<style scoped>
.task-center-root{display:contents}
.tc-bell{position:relative;display:grid;place-items:center;width:39px;height:39px;border:1px solid #d6e8eb;border-radius:11px;background:#fff;color:#467987;cursor:pointer}
.tc-running,.tc-unread{position:absolute;top:-6px;right:-7px;min-width:18px;height:18px;border-radius:10px;padding:0 4px;font-size:10px;display:grid;place-items:center;color:white;background:#149f8b}
.tc-unread{background:#df8e49}
.tc-mask{position:fixed;inset:0;z-index:7000;background:#091a2659;display:flex;justify-content:flex-end}
.tc-drawer{display:flex;flex-direction:column;width:min(100%,490px);height:100%;background:#f8fbfc;box-shadow:-10px 0 50px #0b2a3a29}
.tc-head{display:flex;justify-content:space-between;gap:15px;padding:23px 24px 18px;background:white;border-bottom:1px solid #e2ecee}
.tc-head h2{font-size:18px;margin:4px 0 6px;color:#2d5665}
.tc-head p{font-size:10px;letter-spacing:.14em;color:#18a290;margin:0;font-weight:850}
.tc-head small{font-size:10px;color:#93a9b2}
.tc-close{width:32px;height:32px;border:0;background:#f2f8f9;border-radius:9px;color:#678791}
.tc-summary{display:flex;gap:11px;flex-wrap:wrap;align-items:center;padding:13px 21px;border-bottom:1px solid #e5eeee;background:#fff}
.tc-summary>span{display:flex;gap:7px;align-items:center;font-size:11px;color:#7e97a0}
.tc-summary strong{color:#2f5865}
.tc-summary button{display:flex;align-items:center;gap:5px;border:0;background:none;margin-left:auto;color:#178f7d;font-size:11px}
.tc-scroll{flex:1;overflow:auto;padding:18px 20px;display:grid;align-content:start;gap:12px}
.tc-scroll>h3,.tc-notice-title h3{font-size:13px;color:#3d6170;margin:0}
.tc-scroll>h3 small{font-size:10px;color:#94a9b1;font-weight:500;margin-left:6px}
.tc-empty{padding:18px;background:#fff;color:#8aa0ab;font-size:11px;border:1px dashed #dbe9ec;border-radius:10px;line-height:1.8}
.tc-task{border:1px solid #dfebec;background:#fff;border-radius:12px;padding:15px;display:grid;gap:9px}
.tc-task.failed{border-color:#edcfca}
.tc-task-title{display:flex;align-items:flex-start;justify-content:space-between;gap:9px}
.tc-task-title strong{font-size:12px;color:#315565;min-width:0;overflow-wrap:anywhere}
.tc-task-title>span{font-size:10px;white-space:nowrap;border-radius:10px;padding:4px 7px;background:#f4f7f9;color:#8aa2aa}
.tc-task-title>span.running{color:#168f7d;background:#e7f8f0}.tc-task-title>span.failed{color:#b55f53;background:#fff0eb}.tc-task-title>span.succeeded{color:#20896e;background:#ecf9f1}
.tc-course{font-size:10px;color:#8fa4ae;margin:0}
.tc-progress-line{display:flex;justify-content:space-between;font-size:10px;color:#598b83}
.tc-track{height:7px;background:#e3eeee;border-radius:9px;overflow:hidden}
.tc-track>span{height:100%;display:block;background:#25b69c;border-radius:9px;transition:width .4s}
.tc-stage{display:flex;align-items:center;gap:7px;color:#3a9387;font-size:10px;line-height:1.6}
.tc-failure{display:flex;gap:6px;align-items:flex-start;color:#b66d51;font-size:11px;line-height:1.7;margin:0}
.tc-failure svg{flex:0 0 14px}
.tc-task-bottom{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;font-size:10px;color:#879da7}
.tc-task-bottom button{border:1px solid #d7e9e6;color:#168e7d;border-radius:7px;background:#f5fcfa;display:flex;align-items:center;gap:5px;padding:7px;font-size:10px}
.tc-notice-title{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px}
.tc-notice-title button{display:flex;align-items:center;gap:5px;background:none;border:0;color:#168e7d;font-size:10px}
.tc-message{display:flex;align-items:flex-start;gap:10px;padding:12px;border-radius:10px;border:1px solid #e3edef;background:white}
.tc-message.read{opacity:.72}
.tc-message>svg{flex:0 0 19px}
.tc-success{color:#20a37c}.tc-danger{color:#ca6c50}.tc-info{color:#7c9bb1}
.tc-message>div{flex:1;min-width:0}
.tc-message strong{font-size:11px;color:#456776}
.tc-message p{font-size:10px;line-height:1.8;color:#78919d;margin:6px 0}
.tc-message small{font-size:9px;color:#aab7bb}
.tc-new{height:7px;width:7px;border-radius:50%;background:#e2a568;flex:0 0 7px;margin-top:4px}
.tc-footnote{padding:12px 20px;background:white;margin:0;border-top:1px solid #e3edef;color:#91a3aa;font-size:10px;line-height:1.75}
.tc-error{padding:9px 17px;background:#fff0ed;color:#b16d53;font-size:11px;display:flex;align-items:center;gap:7px}
.tc-toasts{position:fixed;right:19px;top:75px;z-index:7100;max-width:min(410px,calc(100vw - 38px));display:grid;gap:9px;pointer-events:none}
.tc-toast{pointer-events:auto;display:flex;align-items:flex-start;gap:9px;padding:13px;background:white;border:1px solid #e0e9ea;border-left:4px solid #5e9eaf;border-radius:10px;box-shadow:0 7px 26px #1538482a;color:#7192a0}
.tc-toast.success{border-left-color:#26b391;color:#219478}.tc-toast.error{border-left-color:#cb7259;color:#b26850}
.tc-toast-body{border:0;background:none;text-align:left;display:grid;gap:5px;padding:0;flex:1;color:inherit}
.tc-toast-body strong{font-size:12px;color:#355d68}
.tc-toast-body span{font-size:10px;line-height:1.7;color:#75909a}
.tc-toast-close{border:0;background:none;padding:2px;color:#8aa1a9}
@media(max-width:650px){.tc-toasts{top:70px;right:9px}.tc-drawer{width:100%}}
</style>