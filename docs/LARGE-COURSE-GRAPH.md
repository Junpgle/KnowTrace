# KnowTrace v1.4.0：1307个知识点构建完整3D关系图

## 操作路径

1. 进入「题库管理 → 知识点审核」，使用「信任模型结果 · 直接批量通过」。关系生成仅将已审核、有关联课件证据的知识点作为端点。
2. 打开「知识星系 · 3D」。图谱前端请求2000个节点，API最大支持2500，能加载包含1307个知识点的完整课程。星球使用Three.js InstancedMesh批量绘制。
3. 点击「提取 / 审核关系」，打开「整门课程 · 一键构建知识关系」。
4. 先选择小米MiMo或本地Ollama，分析30个点，每个知识点最多生成2条关系。系统每个焦点最多选取11个候选，优先比较共享课件来源及同章知识点，必须核验原文证据。
5. 任务完成后，在「集中审核已生成关系」中选择相关、建议先学、必要前置等关系类型，确认后批量批准。必要前置会防止循环，AI视觉描述来源默认跳过、明确同意后可选择允许。
6. 试运行满意后，选择剩余课程知识点规模，明确确认模型调用费用，再启动。以前已经分析完的焦点自动跳过；支持停止、续跑、服务重启后恢复。
7. 刷新3D图谱；节点间金色语义连线只来自被批准的关系。课程/章节隶属线不是知识先修关系。

## 真实限制

- 当前一个焦点至少调用一次关系提取流程，MiMo Function Calling可能额外触发多次云端请求，1307个焦点可能产生较多费用，优先试30个。
- 只在有限候选和真实来源证据之间挖掘关系，不能保证每个跨章前置关系都被找到；不会凭同章、相似名称就伪造严格先修。
- 未分类知识点仍可能聚在「未归类知识点」星系，现阶段没有自动章节分类。
- 后端运行依赖本地单进程线程模型，不支持多实例同时恢复同一个关系任务。
- 用户批量批准不代表AI提出的关系必定正确，建议先批准「相关、建议先修」，必要前置边更谨慎。

## API

- GET /api/courses/{course_id}/knowledge-graph?limit=2000
- POST /api/courses/{course_id}/relation-batches
- GET /api/courses/{course_id}/relation-batches
- POST /api/relation-batches/{id}/stop
- POST /api/relation-batches/{id}/resume
- GET /api/courses/{course_id}/relations/bulk-review-preview
- POST /api/courses/{course_id}/relations/bulk-approve

后端测试：PYTHONPATH=backend python -m pytest -q backend/tests
前端构建：cd frontend && npm run build

新增测试涵盖1307节点图谱、批量停止/恢复、重复焦点跳过、关系循环阻止和图像证据确认。
