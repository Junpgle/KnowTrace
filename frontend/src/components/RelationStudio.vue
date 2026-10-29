<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  AlertTriangle, ArrowRight, CheckCircle2, ClipboardCopy, FileText, GitBranch,
  LoaderCircle, RefreshCw, ShieldCheck, Sparkles, XCircle,
} from '@lucide/vue'
import CourseRelationBatch from './CourseRelationBatch.vue'
import { api, sourcePreviewUrl, type AIProviderId, type GraphNode,
  type RelationSummary, type StudyPath, type RelationTaskOptions } from '../api'

const props = defineProps<{ courseId: string; knowledge: GraphNode[]; focusId: string | null }>()
const emit = defineEmits<{ updated: []; select: [id: string] }>()
type Mode = 'suggest' | 'review' | 'path'
const mode = ref<Mode>('review')
const focus = ref('')
const target = ref('')
const count = ref(2)
const provider = ref<AIProviderId>('default')
const prompt = ref('')
const originalOptions = ref<RelationTaskOptions | null>(null)
const reply = ref('')
const warning = ref('')
const success = ref('')
const issues = ref<string[]>([])
const relations = ref<RelationSummary[]>([])
const path = ref<StudyPath | null>(null)
const loading = ref(false)
const busy = ref(false)
const confirm = ref<Record<string, boolean>>({})
const approved = computed(() => props.knowledge.filter(n=>n.kind==='knowledge' && n.review_status==='approved'))
const pending = computed(() => relations.value.filter(r=>r.review_status==='pending'))
const reviewed = computed(() => relations.value.filter(r=>r.review_status!=='pending'))
const canSuggest = computed(() => focus.value && (target.value === '' || target.value !== focus.value) && approved.value.length >= 2)
const relationNames: Record<string,string> = {
  prerequisite_required:'必要前置', prerequisite_recommended:'建议先学',
  related:'相关概念', uses:'使用', compares:'对比',
}
function feedback(e: unknown) { warning.value=e instanceof Error?e.message:'操作失败，请检查后端服务。'; success.value='' }
function wipe() { prompt.value='';originalOptions.value=null;reply.value='';issues.value=[];warning.value='';success.value='' }
function options():RelationTaskOptions {
  return {focus_id:focus.value, target_id:target.value||null, count:count.value}
}
let seq=0
async function refresh() {
  const n=++seq
  loading.value=true
  try {
    const [rows,ordered]=await Promise.all([api.relations(props.courseId),api.studyPath(props.courseId)])
    if(n!==seq)return
    relations.value=rows;path.value=ordered
  } catch(e) {if(n===seq)feedback(e)}
  finally {if(n===seq)loading.value=false}
}
watch(() => props.courseId, () => {
  focus.value=''
  target.value=''
  wipe()
  void refresh()
}, {immediate:true})
watch(() => props.focusId, value => {
  if(value && approved.value.some(n=>n.id===value))focus.value=value
})
watch(approved,points=>{
  if(!points.some(p=>p.id===focus.value))focus.value=points[0]?.id||''
  if(target.value && !points.some(p=>p.id===target.value))target.value=''
}, {immediate:true})
watch([focus,target,count,provider],wipe)
async function prepare() {
  if(!canSuggest.value||busy.value)return
  const snapshot=options()
  wipe()
  busy.value=true
  try {
    const data=await api.relationPrompt(props.courseId,snapshot)
    prompt.value=data.prompt
    originalOptions.value=snapshot
    success.value='提示词已生成。请复制到其他 AI，再把 JSON 回复粘贴回来。'
  } catch(e){feedback(e)}
  finally {busy.value=false}
}
async function copy() {
  try {
    await navigator.clipboard.writeText(prompt.value)
    success.value='已复制提示词。'
  } catch {warning.value='无法访问剪贴板，请选择文本后按 Command+C / Ctrl+C 复制。'}
}
async function importReply() {
  if(!originalOptions.value||!reply.value.trim()||busy.value)return
  warning.value='';success.value='';issues.value=[];busy.value=true
  try {
    const result=await api.importRelations(props.courseId,{
      ...originalOptions.value,response_text:reply.value,
    })
    issues.value=result.warnings
    success.value='导入 '+result.created+' 条待审核关系。'
    await refresh()
    if(result.created) {mode.value='review';emit('updated')}
  } catch(e){feedback(e)}
  finally {busy.value=false}
}
async function generate() {
  if(!canSuggest.value||busy.value)return
  wipe()
  busy.value=true
  try {
    const result=await api.generateRelations(props.courseId,{...options(),provider:provider.value})
    issues.value=result.warnings
    success.value='模型提出 '+result.created+' 条待审核关系。'+
      (result.provider==='mimo'?' MiMo 读取片段 '+(result.retrieved_chunks??0)+' 个；工具调用 '+(result.tool_calls??0)+' 次。':'')
    await refresh()
    if(result.created){mode.value='review';emit('updated')}
  } catch(e){feedback(e)}
  finally {busy.value=false}
}
async function review(id:string,decision:'approved'|'rejected') {
  if(busy.value)return
  if(decision==='approved'&&!confirm.value[id]){warning.value='请先核对关系方向、原始课件和证据后勾选确认。';return}
  warning.value='';busy.value=true
  try {
    await api.reviewRelation(id,decision)
    await refresh()
    success.value=decision==='approved'?'关系已批准，并已同步到知识星系。':'候选关系已驳回。'
    emit('updated')
  }catch(e){feedback(e)}
  finally {busy.value=false}
}
function percent(value:number|null) {return value===null?'未测评':Math.round(value*100)+'%'}
</script>

<template>
  <section class="rs panel">
    <div class="rs-head">
      <div><p class="eyebrow">KNOWLEDGE RELATIONS</p><h2>星际航路 · 关系工作台</h2>
        <p>AI 只提出候选关系，人工核对后才能出现在 3D 图谱中。</p></div>
      <button type="button" class="button button-outline button-small" :disabled="loading||busy" @click="refresh">
        <RefreshCw :size="16" /> 刷新
      </button>
    </div>
    <div class="rs-tabs" role="tablist">
      <button type="button" role="tab" :aria-selected="mode==='suggest'" :class="{active:mode==='suggest'}" @click="mode='suggest'">
        <Sparkles :size="16" /> AI 提取关系
      </button>
      <button type="button" role="tab" :aria-selected="mode==='review'" :class="{active:mode==='review'}" @click="mode='review'">
        <ShieldCheck :size="16" /> 审核关系 <b v-if="pending.length">{{ pending.length }}</b>
      </button>
      <button type="button" role="tab" :aria-selected="mode==='path'" :class="{active:mode==='path'}" @click="mode='path'">
        <GitBranch :size="16" /> 学习路线
      </button>
    </div>
    <CourseRelationBatch :course-id="props.courseId" :approved-count="approved.length"
      @updated="refresh();emit('updated')" />
    <p v-if="warning" role="alert" class="rs-notice error">{{ warning }}</p>
    <p v-if="success" role="status" class="rs-notice success">{{ success }}</p>
    <div v-if="issues.length" class="rs-notice caution"><strong>校验提醒（{{ issues.length }}）：</strong>
      <p v-for="(item,i) in issues" :key="i">{{ item }}</p>
    </div>
    <div v-if="loading" class="rs-empty"><LoaderCircle class="spinning" :size="24" /> 读取关系和学习路线…</div>
    <div v-else-if="mode==='suggest'" class="rs-pane">
      <p class="rs-hint">一次最多分析 12 个已审核知识点、每个知识点最多 2 个来源片段。必要前置要求更严格：不能仅凭相似名称或同一章节认定。</p>
      <div class="rs-form">
        <label>核心知识点<select v-model="focus">
          <option v-for="p in approved" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select></label>
        <label>指定对比对象（可选）<select v-model="target">
          <option value="">自动选择附近知识点</option>
          <option v-for="p in approved.filter(x=>x.id!==focus)" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select></label>
        <label>最多生成<select v-model.number="count">
          <option v-for="n in 5" :key="n" :value="n">{{ n }} 条</option>
        </select></label>
        <label>调用方式<select v-model="provider">
          <option value="default">后端默认模型</option>
          <option value="ollama">本地 Qwen / Ollama</option>
          <option value="mimo">小米 MiMo（按需 Function Calling）</option>
        </select></label>
      </div>
      <div class="rs-actions">
        <button class="button button-primary" type="button" :disabled="!canSuggest||busy" @click="generate">
          <LoaderCircle v-if="busy" class="spinning" :size="16" /><Sparkles v-else :size="16" /> 使用 AI 提取候选关系
        </button>
        <button class="button button-outline" type="button" :disabled="!canSuggest||busy" @click="prepare"><ClipboardCopy :size="16" /> 复制给外部 AI</button>
      </div>
      <p class="rs-hint">MiMo 首次仅接收知识点目录和来源元数据；只在工具调用时读取相关课件。手动复制提示词则会附带少量真实原文。</p>
      <div v-if="prompt" class="rs-prompt">
        <div class="rs-prompt-top"><strong>复制以下提示词</strong><button type="button" @click="copy"><ClipboardCopy :size="15" /> 复制全部</button></div>
        <textarea readonly :value="prompt" rows="9" aria-label="外部 AI 关系提示词"></textarea>
        <p>复制到外部平台前，确认这些课件允许共享。</p>
        <label>粘贴外部 AI 返回的 JSON
          <textarea v-model="reply" rows="7" maxlength="100000" placeholder="将外部 AI 的 JSON 答复粘贴到这里…" />
        </label>
        <button type="button" class="button button-primary" :disabled="!reply.trim()||busy" @click="importReply">
          <ShieldCheck :size="16" /> 校验并导入待审核关系
        </button>
      </div>
    </div>
    <div v-else-if="mode==='review'" class="rs-pane">
      <p class="rs-hint">先核对关系方向、解释、课件原页和逐字引文；批准的必要前置关系还会检查有无循环依赖。</p>
      <div v-if="!pending.length" class="rs-empty"><CheckCircle2 :size="23" /> 暂无待审核关系。可以前往 AI 提取关系。</div>
      <article v-for="row in pending" :key="row.id" class="rs-card">
        <div class="rs-card-title">
          <span class="rs-type">{{ relationNames[row.relation_type] || row.relation_type }}</span>
          <span class="rs-flow">{{ row.source_name }} <ArrowRight :size="16" /> {{ row.target_name }}</span>
        </div>
        <p class="rs-rationale">{{ row.rationale }}</p>
        <div v-for="src in row.sources" :key="src.chunk_id" class="rs-source">
          <FileText :size="16" />
          <div><strong>{{ src.original_filename }} · 第 {{ src.page_start }} 页</strong>
            <small v-if="src.content_kind==='figure_description'">AI 图像描述未经原图核验</small>
            <p>{{ src.evidence_note }}</p>
            <a v-if="src.original_filename.toLowerCase().endsWith('.pdf')" :href="sourcePreviewUrl(src)"
              target="_blank" rel="noopener noreferrer">打开 PDF 原页核对 <ArrowRight :size="13" /></a>
          </div>
        </div>
        <label class="rs-confirm"><input v-model="confirm[row.id]" type="checkbox" />我已核对关系方向、合理性和原始证据</label>
        <div class="rs-actions">
          <button type="button" class="button button-primary" :disabled="busy||!confirm[row.id]" @click="review(row.id,'approved')">
            <CheckCircle2 :size="16" /> 审核通过
          </button>
          <button type="button" class="button button-outline" :disabled="busy" @click="review(row.id,'rejected')">
            <XCircle :size="16" /> 驳回
          </button>
        </div>
      </article>
      <div v-if="reviewed.length" class="rs-reviewed">
        <strong>已处理关系 · {{ reviewed.length }} 条</strong>
        <div v-for="row in reviewed" :key="row.id">
          <span :class="row.review_status">{{ row.review_status==='approved'?'已通过':'已驳回' }}</span>
          {{ row.source_name }} → {{ row.target_name }}
          <small>{{ relationNames[row.relation_type] }}</small>
        </div>
      </div>
    </div>
    <div v-else class="rs-pane">
      <p class="rs-hint">{{ path?.notice || '未加载学习路线。' }}</p>
      <div v-if="!path?.items.length" class="rs-empty"><AlertTriangle :size="22" /> 暂无已审核知识点可安排。</div>
      <div v-else class="rs-path">
        <button v-for="step in path.items" :key="step.id" type="button" @click="emit('select',step.id)">
          <strong>{{ step.rank.toString().padStart(2,'0') }}</strong>
          <span><b>{{ step.name }}</b><small>学习观测 {{ step.observations }} 次 · BKT {{ percent(step.mastery) }}</small></span>
          <ArrowRight :size="16" />
        </button>
        <p class="rs-hint">本次显示 {{ path.items.length }} / {{ path.total }} 个知识点。必要前置关系 {{ path.required_edges }} 条；课程数据更新后点击“刷新”。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.rs{padding:23px;min-width:0;display:grid;gap:16px}
.rs-head{display:flex;gap:15px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}
.rs-head h2{font-size:18px;margin:4px 0 7px}
.rs-head p:not(.eyebrow){margin:0;color:#8b9eaa;font-size:11px;line-height:1.8}
.rs-tabs{display:flex;gap:5px;flex-wrap:wrap;border-bottom:1px solid #e2eaec}
.rs-tabs button{display:flex;align-items:center;gap:8px;border:0;border-bottom:3px solid transparent;background:none;color:#8399a4;padding:13px 15px;font-size:12px;font-weight:750}
.rs-tabs button.active{border-color:#1ca995;color:#178e81}
.rs-tabs b{background:#ffeadb;color:#ad7041;padding:3px 7px;border-radius:13px}
.rs-pane{display:grid;gap:13px}
.rs-hint{font-size:11px;line-height:1.8;color:#839ba7;margin:0}
.rs-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.rs-form label,.rs-prompt label{display:grid;gap:7px;color:#567783;font-size:11px;font-weight:750}
.rs-form select{height:42px;border:1px solid #d8e7eb;background:#fff;border-radius:9px;padding:6px 11px;color:#284c5a;min-width:0;font-size:11px}
.rs-actions{display:flex;gap:9px;flex-wrap:wrap}
.rs-actions .button{min-height:40px}
.rs-notice{padding:12px 15px;border-radius:9px;font-size:11px;line-height:1.8;margin:0}
.rs-notice p{margin:4px 0}
.rs-notice.error{color:#ad5a41;border:1px solid #f1d3c4;background:#fff5ef}
.rs-notice.success{color:#197b66;border:1px solid #bce6d7;background:#eaf9f1}
.rs-notice.caution{color:#a07849;background:#fff8e9;border:1px solid #ecd6a8}
.rs-prompt{display:grid;gap:12px;background:#f6fafb;padding:16px;border:1px solid #e1eff0;border-radius:11px;min-width:0}
.rs-prompt-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.rs-prompt-top strong{font-size:12px}
.rs-prompt-top button{border:0;background:#e3f5f0;color:#168e81;border-radius:7px;padding:8px 12px;font-weight:750;font-size:11px;display:flex;align-items:center;gap:7px}
.rs-prompt textarea{width:100%;resize:vertical;min-width:0;min-height:135px;border:1px solid #d4e3e7;border-radius:8px;padding:13px;font:12px/1.8 ui-monospace,monospace;background:#fff;color:#304e5b}
.rs-prompt p{margin:0;font-size:10px;color:#ad875b}
.rs-empty{display:flex;align-items:center;justify-content:center;gap:10px;padding:36px 15px;color:#8c9faa;font-size:12px;background:#f7fafa;border-radius:11px;text-align:center}
.rs-card{padding:17px;border:1px solid #e2edee;border-radius:11px;background:#fff;display:grid;gap:11px}
.rs-card-title{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.rs-type{font-size:10px;color:#168e80;border-radius:20px;background:#e3f6f0;padding:5px 10px;font-weight:800}
.rs-flow{display:flex;align-items:center;gap:7px;color:#2a4c5c;font-size:12px;font-weight:800;flex-wrap:wrap}
.rs-rationale{color:#617d88;font-size:12px;line-height:1.8;margin:0}
.rs-source{display:flex;align-items:flex-start;gap:9px;border:1px solid #e2e9ee;background:#f5f9fb;border-radius:9px;padding:12px;color:#698a98}
.rs-source>div{min-width:0;display:grid;gap:7px}
.rs-source strong{font-size:10px;overflow-wrap:anywhere}
.rs-source small{color:#be8854;font-size:10px}
.rs-source p{margin:0;font-size:10px;color:#899fa8;line-height:1.7;overflow-wrap:anywhere}
.rs-source a{color:#138f83;font-size:10px;display:flex;gap:7px;align-items:center}
.rs-confirm{display:flex;align-items:flex-start;gap:8px;color:#507a80;font-size:11px;line-height:1.8}
.rs-confirm input{accent-color:#1ca18f;width:16px;height:16px}
.rs-reviewed{border-top:1px solid #e8eeee;padding-top:16px;display:grid;gap:8px;color:#6e8791;font-size:11px}
.rs-reviewed>strong{color:#3b5d69}
.rs-reviewed>div{display:flex;gap:9px;flex-wrap:wrap}
.rs-reviewed span{font-weight:800}
.rs-reviewed span.approved{color:#1d987e}.rs-reviewed span.rejected{color:#ac7070}
.rs-reviewed small{color:#96a7ae}
.rs-path{display:grid;gap:8px}
.rs-path button{display:flex;align-items:center;gap:13px;text-align:left;background:#f6fbfa;border:1px solid #dcebe8;border-radius:10px;padding:11px 14px;color:#28635d;width:100%}
.rs-path button>strong{font-size:20px;color:#31b69b}
.rs-path button>span{flex:1;min-width:0;display:grid;gap:4px}
.rs-path button b{font-size:12px}
.rs-path button small{font-size:10px;color:#86a29b}
@media(max-width:700px){.rs{padding:16px}.rs-form{grid-template-columns:1fr}.rs-tabs button{font-size:11px;padding:11px 7px}}
</style>
