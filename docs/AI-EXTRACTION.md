# KnowTrace v0.2.0：AI 知识点提取（实验版）

## 功能

从已上传的 PDF/PPTX 课件中选取连续 1–4 页，用本地 Ollama 模型提取候选知识点。可针对低文本量 PDF 使用视觉模型补充（每次最多 2 页）；PPTX 暂不支持视觉处理。模型结果必须提供对应页面的逐字引文；证据不符的知识点不会保存。通过验证的知识点仍为待审核状态，并保留来源文件、页码与引文，人工审核后才可批准。

> 这不是全书自动扫描、自动知识图谱或自动出题。引文匹配不保证模型解释准确，视觉描述尤其需要核实。

## 安装本地模型

先安装并启动 [Ollama](https://ollama.com/)。示例模型（也可配置其他已安装的兼容模型）：

~~~bash
ollama pull qwen2.5:7b
ollama pull qwen2.5vl:3b
ollama list
~~~

macOS / Linux：在启动 FastAPI **之前**执行：

~~~bash
export KNOWTRACE_TEXT_MODEL=qwen2.5:7b
export KNOWTRACE_VISION_MODEL=qwen2.5vl:3b
~~~

Windows PowerShell：

~~~powershell
$env:KNOWTRACE_TEXT_MODEL="qwen2.5:7b"
$env:KNOWTRACE_VISION_MODEL="qwen2.5vl:3b"
~~~

本地 Ollama URL 默认为 http://127.0.0.1:11434，仅允许本地回环。修改模型环境变量后重启后端。模型会消耗内存和存储，不需要额外购买 API。

~~~bash
python -m pip install -r backend/requirements.txt
cd backend
uvicorn knowtrace.main:app --reload
~~~

访问 http://127.0.0.1:8000/docs 。

## 使用《2025-第2章数据通信A.pdf》进行验证

已上传课件可以继续使用原来的 document_id，无需重新上传。

1. 打开 POST /api/documents/{document_id}/extract-knowledge。
2. 对第 1–3 页填写请求：

~~~json
{
  "page_start": 1,
  "page_end": 3,
  "include_visual": true
}
~~~

第 2 页只有少量可提取文字，接口会尝试通过视觉模型理解其通信系统模型图。

3. 返回的 created 表示新建待审核知识点数量，warnings 包含校验失败原因。没有足够可信证据时 created 可能为零。
4. 调用 GET /api/documents/{document_id}/knowledge-points 查看页码、证据、来源类型和审核状态。
5. 人工审核后，调用 POST /api/knowledge-points/{knowledge_point_id}/review，请求体是 {"decision":"approved"} 或 {"decision":"rejected"}。

## 状态与局限

- 文本模型未配置时返回 503；模型 JSON 格式错误时返回 502；文字不足且未使用视觉模型时返回 422。
- AI 生成的图示解释被标记为 figure_description，不能当作 PDF 中的原文。质量检查始终只统计原始文本。
- 一次最多处理 4 页，其中最多 2 页启用视觉理解，不建议直接提交整本教材。
- 本版本没有账号认证，只能在可信本机运行，不应直接暴露公网；课程资料不要上传到公共仓库。
- 当前自动化测试通过的是模拟模型驱动的逻辑测试，真实模型的提取质量需要在用户电脑上进一步验证。

## 回归测试

~~~bash
python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest -q backend/tests
~~~

Windows PowerShell：先设置 PYTHONPATH 为 backend，再运行 pytest -q backend/tests。
