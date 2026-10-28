# KnowTrace：知识点编辑与人工审核（v0.2.1）

AI 生成的知识点始终以待审核状态进入知识库。**人工修改名称或摘要之后，原始课件引用仍保留，不会覆盖原文。** 修改一个已审核的知识点会将其状态重新设为 pending，必须再次审核。

## 更新本地后端

在仓库根目录执行 git pull origin main，重新启动 FastAPI：

~~~bash
cd backend
uvicorn knowtrace.main:app --reload
~~~

服务启动时自动建立事件日志表，不需要删除或重建已有数据库；原有课程、文档和知识点数据会保留。

Swagger：http://127.0.0.1:8000/docs

## 逐条审核知识点

1. 在 GET /api/documents/{document_id}/knowledge-points 查询模型提取的列表，复制一个知识点 id。
2. 在 GET /api/knowledge-points/{knowledge_point_id} 查看当前摘要和 sources。每条来源都带有原始文件名、页码、来源类型与 evidence_note。对于 figure_description，证据是 AI 的图像解读，不是原文。
3. 对需要调整的知识点，调用 PATCH /api/knowledge-points/{knowledge_point_id}。请求体可以只包含需要修改的字段，例如：

~~~json
{
  "summary": "数据通信系统由源系统、传输系统和目的系统三大部分组成。"
}
~~~

还可修改 canonical_name、difficulty（1–3）或 chapter_id（必须属于同一门课程）。可将 summary、difficulty、chapter_id 设为 null 清除；canonical_name 不允许为空。没有变化的请求不会重复触发审核。

4. 核实源材料后，POST /api/knowledge-points/{knowledge_point_id}/review：

~~~json
{"decision":"approved"}
~~~

如果内容不可靠，可以使用 {"decision":"rejected"}。编辑被驳回的条目后，它会重新回到 pending。

5. GET /api/knowledge-points/{knowledge_point_id}/history 可以查看编辑前后内容及审核记录。

## 限制与保护

- 目前仅提供 FastAPI/Swagger 接口，尚未完成网页上的编辑表单。
- 重新生成的 AI 知识点仍需要人工核对；系统不会宣称视觉理解内容已经得到原文验证。
- 同一课程下更新为已有的知识点名称会返回 409；不属于该课程的章节 ID 会返回 422。
- 这一版保留所有原始引用，不提供在线修改课件引文的能力；如果原始解析出错，需要返回文档解析/视觉理解流程检查。
- 当前为本地单用户原型，没有登录和权限控制，不应暴露到公网。
