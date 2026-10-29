<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { CheckCircle2, Eye, EyeOff, KeyRound, LoaderCircle, RefreshCw, Save, Settings2, ShieldAlert, Wifi } from '@lucide/vue'
import { api, type AISettings } from '../api'

const props = defineProps<{ courseName?: string }>()
const data = ref<AISettings | null>(null)
const loading = ref(true)
const saving = ref(false)
const testing = ref<'ollama'|'mimo'|null>(null)
const msg = ref('')
const error = ref('')
const defaultProvider = ref<'ollama'|'mimo'>('ollama')
const mimoModel = ref<'mimo-v2.6-flash'|'mimo-v2.6-pro'>('mimo-v2.6-flash')
const mimoKey = ref('')
const showKey = ref(false)
const clearKey = ref(false)
const ollamaUrl = ref('http://127.0.0.1:11434')
const ollamaText = ref('')
const ollamaVision = ref('')
const keyInput = ref<HTMLInputElement|null>(null)
function unpack(info:AISettings) {
  data.value=info
  defaultProvider.value=info.default
  mimoModel.value=info.mimo_model==='mimo-v2.6-pro'?'mimo-v2.6-pro':'mimo-v2.6-flash'
  ollamaUrl.value=info.ollama_url
  ollamaText.value=info.ollama_text_model
  ollamaVision.value=info.ollama_vision_model
}
async function reload(){
  loading.value=true;error.value=''
  try{unpack(await api.aiSettings())}
  catch(e){error.value=e instanceof Error?e.message:'设置读取失败'}
  finally{loading.value=false}
}
async function save(){
  saving.value=true;error.value='';msg.value=''
  try {
    const info=await api.saveAISettings({
      default_provider:defaultProvider.value,mimo_model:mimoModel.value,
      ...(mimoKey.value.trim()?{mimo_api_key:mimoKey.value.trim()}:{}),
      clear_mimo_key:clearKey.value,
      ollama_url:ollamaUrl.value.trim(),
      ollama_text_model:ollamaText.value.trim(),
      ollama_vision_model:ollamaVision.value.trim(),
    })
    unpack(info)
    mimoKey.value='';clearKey.value=false;showKey.value=false
    msg.value='设置已保存至本机后端，新发起的 AI 请求将使用这些配置。'
  }catch(e){error.value=e instanceof Error?e.message:'保存失败'}
  finally{saving.value=false}
}
async function test(which:'ollama'|'mimo'){
  testing.value=which;error.value='';msg.value=''
  try { const result=await api.testAIConnection(which);msg.value=result.message }
  catch(e){error.value=e instanceof Error?e.message:'连接测试失败'}
  finally{testing.value=null}
}
onMounted(()=>{void reload()})
</script>

<template>
  <div class="ai-settings-page">
    <header class="as-header">
      <div><p class="eyebrow">SYSTEM CONFIGURATION</p><h1>AI 模型设置<span class="heading-dot">.</span></h1>
        <p class="section-description">统一配置本地 Ollama 和小米 MiMo。无需在终端重复输入 API Key。</p></div>
      <button class="button button-outline button-small" :disabled="loading||saving" @click="reload"><RefreshCw :size="16"/> 刷新</button>
    </header>
    <p v-if="error" class="as-alert error" role="alert">{{ error }}</p>
    <p v-if="msg" class="as-alert success" role="status"><CheckCircle2 :size="16"/>{{ msg }}</p>
    <div v-if="loading" class="panel as-loading"><LoaderCircle class="spinning" :size="23"/> 读取本地设置…</div>
    <template v-else-if="data">
      <div class="as-grid">
        <section class="panel as-panel">
          <div class="as-panel-title"><span class="as-icon"><KeyRound :size="22"/></span><div><h2>小米 MiMo API</h2><p>云端多模态识别 · 知识点提取 · 出题 · 关系分析</p></div></div>
          <div :class="['as-state',{ok:data.mimo_key_configured}]"><span></span>
            {{data.mimo_key_configured?'已配置 API Key':'尚未配置 API Key'}}
            <small v-if="data.mimo_key_source==='environment'">（来自终端环境变量）</small>
          </div>
          <label class="as-field">MiMo API Key
            <div class="as-key-input"><input ref="keyInput" v-model="mimoKey" :type="showKey?'text':'password'"
              autocomplete="new-password" placeholder="sk-…（留空则保留已存密钥）"/>
              <button type="button" :aria-label="showKey?'隐藏密钥':'显示密钥'" @click="showKey=!showKey">
                <EyeOff v-if="showKey" :size="18"/><Eye v-else :size="18"/>
              </button></div>
          </label>
          <label class="as-field">MiMo 模型
            <select v-model="mimoModel">
              <option value="mimo-v2.6-flash">MiMo v2.6 Flash</option>
              <option value="mimo-v2.6-pro">MiMo v2.6 Pro</option>
            </select>
          </label>
          <label class="as-check"><input v-model="clearKey" type="checkbox" :disabled="!!mimoKey.trim()"/>
            清除后端本地保存的 MiMo Key（不会清除终端环境变量）</label>
          <p class="as-note">密钥只提交给当前本机后端。页面和状态接口不会回显密钥；请使用小米按量付费的 sk- Key。配置后可测试连接，测试调用可能产生少量费用。</p>
          <button type="button" class="button button-outline" :disabled="!!testing||saving" @click="test('mimo')">
            <LoaderCircle v-if="testing==='mimo'" class="spinning" :size="17"/><Wifi v-else :size="17"/> 测试 MiMo 连接
          </button>
        </section>
        <section class="panel as-panel">
          <div class="as-panel-title"><span class="as-icon local"><Settings2 :size="22"/></span><div><h2>本地 Ollama</h2><p>本机运行，课件文字不发送至云端</p></div></div>
          <label class="as-field">本地 Ollama 地址
            <input v-model="ollamaUrl" placeholder="http://127.0.0.1:11434"/></label>
          <label class="as-field">文本模型
            <input v-model="ollamaText" placeholder="例如 qwen3.5:4b"/></label>
          <label class="as-field">视觉模型
            <input v-model="ollamaVision" placeholder="例如 gemma4:12b"/></label>
          <p class="as-note">只允许本机 localhost / 127.0.0.1 连接地址。需要先在 Ollama 中下载对应模型。测试连接只检测 Ollama 服务，不保证模型已下载。</p>
          <button type="button" class="button button-outline" :disabled="!!testing||saving" @click="test('ollama')">
            <LoaderCircle v-if="testing==='ollama'" class="spinning" :size="17"/><Wifi v-else :size="17"/> 测试本地服务
          </button>
        </section>
      </div>
      <section class="panel as-default">
        <div><h2>默认 AI 提供商</h2><p>单次生成时仍可覆盖默认值。整份资料批量任务会记录开始时选择的提供商。</p></div>
        <select v-model="defaultProvider" aria-label="默认AI提供商">
          <option value="ollama">本地 Ollama</option>
          <option value="mimo">小米 MiMo API</option>
        </select>
      </section>
      <div class="as-actions">
        <p><ShieldAlert :size="17"/> 本功能针对本地单用户版本；请不要将 8000/5173 端口开放公网，也不要提交密钥到 GitHub。</p>
        <button type="button" class="button button-primary" :disabled="saving" @click="save">
          <LoaderCircle v-if="saving" class="spinning" :size="17"/><Save v-else :size="17"/> 保存模型设置
        </button>
      </div>
    </template>
  </div>
</template>
<style scoped>
.ai-settings-page{display:grid;gap:19px}
.as-header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.as-header h1{font-size:29px;margin:6px 0}
.as-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;align-items:stretch}
.as-panel{display:flex;flex-direction:column;align-items:stretch;gap:17px;padding:24px}
.as-panel-title{display:flex;align-items:center;gap:13px}
.as-panel-title h2{font-size:17px;margin:0 0 5px;color:#2a4a59}
.as-panel-title p{font-size:11px;color:#93a4ab;margin:0;line-height:1.7}
.as-icon{width:48px;height:48px;display:grid;place-items:center;background:#e8f0ff;color:#6a86d5;border-radius:13px}
.as-icon.local{background:#e6f8f1;color:#24a28e}
.as-state{display:flex;align-items:center;gap:8px;background:#f6f8fa;border-radius:9px;padding:12px;font-size:11px;color:#95a0ae}
.as-state.ok{background:#e8f9f2;color:#2b9478}
.as-state>span{height:8px;width:8px;border-radius:50%;background:#9facb5}
.as-state.ok>span{background:#2dbd97}
.as-state small{font-size:10px}
.as-field{display:grid;gap:8px;font-size:11px;font-weight:800;color:#607f8c}
.as-field>input,.as-field>select,.as-key-input>input{width:100%;min-width:0;height:42px;border:1px solid #d7e5e8;border-radius:9px;padding:10px 12px;background:white;color:#2a4e5d;font-size:12px}
.as-key-input{display:flex;align-items:center;position:relative}
.as-key-input>input{padding-right:48px}
.as-key-input>button{position:absolute;right:5px;top:5px;height:32px;width:35px;display:grid;place-items:center;background:none;border:0;color:#668b99}
.as-note,.as-check{font-size:10px;color:#879fa8;line-height:1.9;margin:0}
.as-check{display:flex;align-items:center;gap:9px}
.as-check input{accent-color:#25a790;height:16px;width:16px}
.as-panel .button{align-self:start}
.as-default{padding:23px;display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}
.as-default h2{font-size:15px;margin:0 0 7px}
.as-default p{font-size:11px;color:#849ca5;margin:0;line-height:1.7}
.as-default select{border:1px solid #d7e5e8;border-radius:9px;padding:11px;background:#fff;color:#436b77;font-size:12px;min-width:170px}
.as-actions{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.as-actions>p{display:flex;gap:9px;max-width:550px;font-size:10px;line-height:1.8;color:#8ca1a9}
.as-actions>p svg{flex:0 0 17px}
.as-alert{display:flex;align-items:center;gap:9px;font-size:12px;padding:12px 16px;border-radius:10px}
.as-alert.error{background:#fff2ee;color:#a56351}.as-alert.success{background:#ebf9f4;color:#248b73}
.as-loading{padding:80px;text-align:center;display:flex;justify-content:center;gap:12px;color:#6f9c9c}
@media(max-width:850px){.as-grid{grid-template-columns:1fr}}
</style>