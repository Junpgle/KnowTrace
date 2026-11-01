# KnowTrace v0.3.0：AI 智能题库（实验版）

目标：从**已审核**的知识点及课件来源生成单选题或判断题，经过结构/来源校验后进入人工审核队列。

## 环境与启动

在 KnowTrace 仓库根目录：

~~~bash
git pull origin main
export KNOWTRACE_TEXT_MODEL=qwen3.5:4b
export KNOWTRACE_VISION_MODEL=qwen3.5:4b
cd backend
uvicorn knowtrace.main:app --reload
~~~

Ollama 应用需要已运行，ollama list 可列出 qwen3.5:4b。打开 http://127.0.0.1:8000/docs 。

## 用你的《数据通信A》课件试运行

已审核的“数据通信系统的模型”知识点 ID：
7ce346bd-d6df-40ec-834d-43bda0495ece

1. 打开 POST /api/knowledge-points/{knowledge_point_id}/generate-questions，填入上述 ID。
2. 请求体：

~~~json
{
  "count": 2,
  "question_types": ["single_choice", "true_false"]
}
~~~

响应提供 created（新题数）、question_version_ids 和 warnings（拒绝原因）。若引文不符合规则，created 可以为 0，不能将其解释为模型没有执行。

3. GET /api/courses/{course_id}/questions 查看课程题库。你的课程 ID 为：
4e39a9e6-1d6e-479c-a438-501b3739243e
4. GET /api/question-versions/{version_id} 查看题干、选项、正确答案、解析、来源文件/页码、证据和所属知识点。
5. 人工核对后 POST /api/question-versions/{version_id}/review：

~~~json
{"decision":"approved"}
~~~

或者使用 {"decision":"rejected"} 拒绝。已审核版本不能原地修改。

6. 修改时使用 POST /api/question-versions/{version_id}/revise 创建新版本，示例：

~~~json
{"stem": "数据通信系统的源系统主要承担什么任务？"}
~~~

也可修改 options、correct_answer、explanation、difficulty。新版本仍为 pending，须重新审核。GET /api/questions/{question_id}/versions 可查看版本历史。

**注意**：这个知识点基于第 2 页 AI 生成的图示描述（figure_description），不是 PDF 可复制的原文。需特别核对题目事实、选项和解析。后续可审核第 3 页文本型知识点，再对比质量。

## 自动检查的范围

- 只允许已批准且具有 ready 课件来源的知识点出题，每次最多 4 道。
- single_choice 要有 A/B/C/D 四个不同选项，正确答案必须为 A、B、C、D 之一。
- true_false 正确答案是 JSON 布尔值 true 或 false。
- 题目必须标注 source_chunk_id 和来源中的连续逐字引文。错误引文、重复题干、无效结构将被拒绝。
- **引文存在不等于答案正确。** 单选唯一正确性、判断题真假、解释与材料一致性必须由人审核。
- 已批准的题目版本受到数据库触发器保护，不能直接覆盖；修订生成新版本并继承来源。
- 目前没有学生测评页面、答题记录、BKT 接入，也没有用户认证，仅适用于可信本机，不应暴露公网。

## 自动测试

~~~bash
python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest -q backend/tests
~~~

自动测试使用模拟模型，未对真实 Qwen3.5 的出题质量给出保证。

## 题目一键审核（2026-11 新增）

进入「题库管理 → 题目审核」，顶部「一键审核题目」面板会先检查全部**最新待审核题目**，提供分章筛选、批量勾选以及风险题清单。默认全选通过程序检查的题目，无需逐题查看标准答案。明确勾选确认，再点击「一键审核通过」。

**检查内容**：结构与答案字段合法；主知识点唯一且全部关联知识点已通过审核；引用来源属于本课程并已就绪；文本引文能在课件片段中逐字匹配；不存在需复核的 AI 映射说明、图片识别来源及完全相同的题干。检查失败的题仍为 pending，必须单独核对，不能通过这个接口强行批准。

重要：这些只是形式、引文和知识点状态检查，**绝不表示 AI 的正确选项、解析或干扰项已经过真实性核验**。用户通过确认框自愿跳过逐题核对，可能导致错误题进入正式测评与 BKT。若更关心严格诊断可信度，请继续使用「候选题盲测」，或只批准已人工复核的题目。

- `GET /api/courses/{course_id}/questions/bulk-approval-preview`：返回待审题量、可直接批准数量、风险项目及原因；预览**不包含正确答案和解析**。
- `POST /api/courses/{course_id}/questions/bulk-approve`：用户明确提交 `question_version_ids`、`confirmed: true` 和 `accept_unverified_answers: true`。使用数据库事务按当前状态重新校验；旧版本、其他课程、非待审或风险题会跳过，且不会回滚已合法的本批其他题。
- 审核写入新增的 `question_bulk_review_events`；保留原题目版本和先前作答记录。重复提交不会重复创建审核记录。
- 不额外调用 AI，不产生新的 Token 费用；仍只适合可信本地单用户环境。
