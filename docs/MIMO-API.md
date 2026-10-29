# KnowTrace v0.6.0：小米 MiMo API 与按需 Function Calling

## 官方协议

- Xiaomi MiMo 官方平台：https://mimo.mi.com
- 使用 OpenAI 兼容的 Chat Completions 接口： https://api.xiaomimimo.com/v1/chat/completions
- 支持 tools、tool_choice=auto、多轮 tool_calls。开发文档提示模型在开启 thinking 时返回 reasoning_content，需要在后续多轮消息中保留；KnowTrace 已实现。
- 默认模型：mimo-v2.6-flash（可改为 mimo-v2.6-pro）；接口在后台运行，不暴露 API Key。
- 官方资料：https://mimo.mi.com/docs/zh-CN/api/chat/openai-api

## MacBook 配置

在启动 FastAPI 的终端运行。请勿将 API Key 写入仓库或发到聊天中：

~~~bash
# macOS zsh：输入后回车，终端不会回显密钥
read -s "MIMO_API_KEY?小米 MiMo API Key: "
echo
export MIMO_API_KEY

# 原有本地模型保持可用
export KNOWTRACE_TEXT_MODEL=qwen3.5:4b
export KNOWTRACE_VISION_MODEL=gemma4:12b

# 可选：默认仍使用 Ollama；网页里可以单次选择 MiMo 出题
export KNOWTRACE_AI_PROVIDER=ollama
export KNOWTRACE_MIMO_MODEL=mimo-v2.6-flash
export KNOWTRACE_MIMO_THINKING=disabled

cd backend
uvicorn knowtrace.main:app --reload
~~~

也可以将 KNOWTRACE_AI_PROVIDER=mimo，使知识点提取和默认出题使用 MiMo 文本 API，复杂课件图片仍优先交给本地 KNOWTRACE_VISION_MODEL（Gemma 4 12B）分析。文本 API 模型与图像模型可以分开配置。

若需要更强推理模型，改为 KNOWTRACE_MIMO_MODEL=mimo-v2.6-pro 并重启后端。

**注意**：一旦选择 MiMo，模型实际检索并读取的课程片段会发送给小米的云端 API。处理保密、个人敏感或无上传权限的课件时，应选择本地 Ollama。

## Vue 中使用

1. 启动原有后端和前端，打开 http://127.0.0.1:5173。
2. 进入“题库管理 → 知识点审核”，选中已通过审核且具有课件来源的知识点。
3. 在“出题模型”中选择“小米 MiMo · Function Calling”，选 1–4 道单选或判断题，点击生成。
4. 生成后切换到“AI 题目审核”，核查题目正确性与 PDF 原页，再批准。
5. 界面显示本次 MiMo 的工具调用次数、读取的正文片段数和 Token 总量（若服务返回统计）。

已有课程、知识点、题库和 BKT 作答记录无需清空或迁移。只审核题目、正常答题时不调用 MiMo API。

## 按需检索机制（重点）

MiMo 出题时，首次请求只提供：
- 已批准知识点的名称和简短摘要
- 少量关联资料的 chunk_id、文件名、页码及来源类型
- 题型、题量以及模型可以调用的工具说明

**没有把 PDF 全文、所有知识点或数据库整体发送给模型。**

MiMo 自主决定是否调用：
- read_source_chunk(chunk_id)：读取**一个**经过授权的来源片段，最多 1800 字符。
- search_source_chunks(query, limit)：在此知识点关联的资料文档中按关键词查找，最多返回 3 个仅含短预览的结果；需要逐字证据时再读取片段。

每次最多 6 个工具调用（包括无效调用）；服务器校验工具名、参数和来源范围，拒绝跨课程/跨资料读取；AI 不能使用自己编造的 chunk_id。当模型没有真实读取正文却试图直接给出题目时，来源校验会拦截，因此可能出现 created=0。

还保留了后端规则检查：题型、四个互异选项、JSON 布尔答案、逐字引文、重复题目。**引文真实存在≠题目答案正确**，仍需人工审核，尤其要核查 AI 视觉描述的原始图片。

MiMo 多轮 Function Calling 每增加一轮也会增加推理次数和 Token 消耗，所以不保证比一次性发送少量文字更便宜；本实现的目标是减少无关课件正文的传输，并限制检索范围。可通过响应中的 tool_calls、retrieved_chunks、usage 做真实对比。

知识点提取接口仍要求明确指定 1–4 页，其 MiMo 请求只包含这些页的短文本；这一阶段尚未改造成跨全课程的工具式提取。视觉解析保持本地 Gemma。

## API 接口与状态

GET /api/ai/providers 会列出 Ollama、MiMo 的可用情况、模型和是否支持 Function Calling；**不会返回 API Key**。

POST /api/knowledge-points/{id}/generate-questions 新增 provider 选项：

~~~json
{
  "provider": "mimo",
  "count": 1,
  "question_types": ["single_choice"]
}
~~~

provider 也可填 default 或 ollama。响应新增 provider、model、tool_calls、retrieved_chunks、usage。原有返回字段保持兼容。

POST /api/documents/{id}/extract-knowledge 也可通过 provider=mimo 选择 MiMo 文本提取，不过必须在 body 中指定明确的页码范围。

## 排查

- 503：缺少 MIMO_API_KEY、模型未配置或参数不受支持。
- 502：网络问题、API Key 无效、额度不足、模型 API 返回错误、响应不符合协议。后端不会回传可能含敏感信息的 API 错误正文。
- created=0：模型没有调用工具读取正文、引用与真实片段不符或题目结构不符合校验规则；可看 warnings。
- 函数调用太多：每次限制最多 6 次，不允许无限循环；失败不会插入未经验证的题目。
- API 模型只支持官方 https://api.xiaomimimo.com/v1；该限制可避免误将 API Key 发到任意恶意地址。

## 安全与测试边界

当前 KnowTrace 仍是**本地单用户原型**，没有用户登录/权限隔离。不要将 8000 或 5173 端口开放公网，也不要把 API Key 填入网页或提交到 GitHub。后端测试模拟 API 响应，不会消耗你真实的 MiMo 额度；由于没有你的 API Key，真实 API 的延迟和服务端行为仍需在你的 MacBook 上验证。
