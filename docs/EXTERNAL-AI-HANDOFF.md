# KnowTrace v0.7.0 — 外部 AI 复制提示词与回填识别

本功能无需连接云 API、无需 API Key。使用任意其他 AI 平台（ChatGPT、Gemini、DeepSeek、Claude、MiMo 网页端等）时，用户自行控制将哪些资料分享给对方。

## 使用方式

打开 Vue 页面 http://127.0.0.1:5173 → **题库管理 → 3. 外部 AI 接力**。

### A. 让外部 AI 生成练习题

1. 在“生成练习题”模式中选择**已审核知识点**，选择 1–4 道题及单选题/判断题。
2. 点击“生成可复制提示词”，预览后点击“复制全部”。
3. 将提示词粘贴到你选择的 AI 平台，要求模型按 JSON 格式回复。不要将本地未公开的课件发送到你不信任的服务。
4. 把外部 AI 的回复粘贴回 KnowTrace，点击“识别并导入待审核区”。
5. 后端会重新从数据库查询所选知识点及对应课件片段，检查题目 JSON 结构、答案类型、选项唯一性、引用页码/ID、连续逐字引文和重复题干。未通过的候选不会入库，会返回 warnings。
6. 成功保存的题目只会进入 **pending 待审核**；回到“AI 题目审核”逐题核对，必要时修改版本，然后再批准。

**重要**：此工作流的提示词包括选定知识点关联的 **最多4个来源片段（每个最多2200字符）**，并不是将整份课件发送给外部 AI。外部网页模型不能调用你本机的 MiMo Function Calling 检索工具，因此不能仅发送片段目录；需要将选定的文本实际复制给它。出题时遵循原有 evidence 验证规则。

### B. 让外部 AI 提取知识点

1. 切到“提取知识点”，选择已上传且状态为 ready 的课件（PDF/PPTX）。
2. 选择起止页码，每次 1–4 页。系统只使用这些页的可提取文本；如果某页缺少足够文字，且已经存在本地视觉识别的 figure_description，则可使用该图示描述。无可读文本的页面会跳过。
3. 点击生成、复制提示词，交给外部 AI 处理，再粘贴回复。
4. 系统严格检查知识点字段（名称、摘要、难度、页码）及逐字引用；保留来源片段 ID 与文件页码；同名知识点会复用而不是重复创建。
5. 新知识点全部在 pending 中等待你人工审核。复用已有知识点时不自动覆盖既有解释或审核状态。

**注意**：普通 PDF 图片本身不会自动转成可复制的视觉内容。若需要外部 AI 真正识图，可在平台手动附上该页原图，同时仍应以 KnowTrace 中可检验的引文为准；对于视觉页建议先使用 Gemma 4 12B 完成本地视觉提取。AI 生成的图示描述不等于原文。

## 输出格式示例

外部 AI 最好只输出下面这样的 JSON，允许包在 Markdown 的 json 代码块中，也可有少量说明性文字。

**单选题**（chunk_id 必须替换成提示词给出的真实 ID，quote 需要从提示词中的该来源逐字复制）：

~~~json
{
  "items": [
    {
      "question_type": "single_choice",
      "stem": "数据通信系统通常分为哪三个主要部分？",
      "options": {
        "A": "源系统、传输系统、目的系统",
        "B": "网络层、数据链路层、物理层",
        "C": "应用层、传输层、网络层",
        "D": "输入设备、处理器、输出设备"
      },
      "correct_answer": "A",
      "explanation": "根据课件描述，数据通信系统由源系统、传输系统和目的系统构成。",
      "difficulty": 1,
      "source_chunk_id": "替换为真实chunk_id",
      "quote": "替换为真实来源中的连续引文"
    }
  ]
}
~~~

**知识点**：

~~~json
{
  "items": [
    {
      "name": "信源的定义",
      "summary": "信源产生原始的待传送信号。",
      "page": 3,
      "quote": "替换为选定页面中的连续原文（至少10字符）",
      "difficulty": 1
    }
  ]
}
~~~

以上是格式示例，**直接提交会因引文或 ID 不真实而被拒绝**。请使用 KnowTrace 为当前课程实际生成的提示词。

## API 端点

所有导入接口只会读取服务器已有的数据，**不调用外部云 AI 或 Ollama**：

- POST /api/knowledge-points/{id}/external-question-prompt
- POST /api/knowledge-points/{id}/import-external-questions
- POST /api/documents/{id}/external-knowledge-prompt
- POST /api/documents/{id}/import-external-knowledge

出题提示词请求体：

~~~json
{"count": 2, "question_types": ["single_choice", "true_false"]}
~~~

出题导入请求体在上述字段上加 response_text（AI 的完整输出）。

知识点提示词请求体：

~~~json
{"page_start": 1, "page_end": 3}
~~~

知识点导入请求体在上述字段上加 response_text。JSON 响应包含 created、created_ids 或 question_version_ids、warnings；知识点导入还包含 reused 和 skipped_pages。

导入来源分别使用 questions.origin=imported 及 processing_jobs.engine_name=manual_external，方便与自动调用模型区分。原有课程、题库和 BKT 记录不会清空。

## 限制与安全

- 手动复制提示词会向用户选定的第三方服务传输**真实课件片段**，建议只对可分享的课程资料使用。
- 格式化识别支持原始 JSON、单个 JSON Markdown 代码块或说明文字包裹的唯一 items JSON 对象；非结构化自然语言不保证能够识别。出现多个候选 JSON 对象时会要求用户重新整理。
- 只接受有逐字证据且与当前所选课程、文档、知识点一致的候选。不能因为模型口头声称引用正确就跳过后端检验。
- 对同一课程中已有题干、知识点名称去重；不会重复插入已存在记录。
- 引文匹配**不能**自动证明单选答案唯一正确，必须人工审题。
- 当前仍是本地单用户应用，没有账户权限，不应暴露到公网。
