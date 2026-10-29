# KnowTrace v0.9.0 — 知识关系工作台与学习路线

## 使用步骤

在左侧打开“知识星系 · 3D”，点击右上角“提取 / 审核关系”。

1. 选择“AI 提取关系”，选定一个已审核知识点，可再指定第二个知识点。系统只选择当前课程最多12个已审核知识点，以及每点最多2条来源片段。可用本地 Ollama、小米 MiMo API 或复制提示词给其他 AI，粘贴 JSON 结果导入。
2. 选择“审核关系”，核查起点到终点方向、关系类型、解释和证据原页；勾选确认后批准或驳回。未经批准的关系不会成为 3D 语义连线。
3. 选择“学习路线”，系统只根据已审核的“必要前置关系”做拓扑排序；满足前置约束时，优先推荐未测评和 BKT 掌握度偏低的知识点。没有必要前置边时明确显示“优先清单，并非严格先修路径”。

## 关系类型

- prerequisite_required：A 必须先于 B；批准时阻止循环依赖。
- prerequisite_recommended：建议先学 A，再学 B。
- related：概念相关，不代表先修依赖。
- uses：A 使用 B。
- compares：对比关系。

图谱里原有的章节归属关系来自数据库，金色知识语义连线仅展示通过审核的关系。AI 生成的关系永远先进入 pending；证据存在不能代替人工教学判断。

## 证据与 MiMo

每条关系必须引用来自其中一个端点所关联的真实课件片段，并提供连续至少10个字符的逐字引文。跨课程、错误 chunk_id、虚构引文和重复关系会被拒绝。图示 AI 描述（figure_description）未经过原图确认，审核时必须打开原页检查。

选择小米 MiMo API 时，首次发送**知识点名称、摘要和来源目录，不发送课件正文**。模型只在需要时调用受限的 read_source_chunk(chunk_id)，默认最多6次工具调用，单次片段最多1600字符。使用本地 Ollama 或手动复制提示词时会提供少量选定的正文，手动粘贴到第三方平台前须确认课件允许分享。

## 后端接口

- GET /api/courses/{course_id}/knowledge-relations：关系及来源概况。
- POST /api/courses/{course_id}/relations/prompt：外部 AI 提示词。
- POST /api/courses/{course_id}/relations/import：解析外部 AI JSON 候选。
- POST /api/courses/{course_id}/relations/generate：Ollama / MiMo 出关系。
- POST /api/courses/{course_id}/relations/manual：手动创建待审核关系（需要真实片段和逐字引文）。
- POST /api/knowledge-relations/{relation_id}/review：批准或驳回。
- GET /api/courses/{course_id}/study-path?limit=12：基于必要前置的推荐顺序。

关系 JSON 结构示例（请替换成提示词中的真实ID及实际原文）：

~~~json
{"items":[{
  "source_kp_id":"真实知识点ID",
  "target_kp_id":"另一个真实知识点ID",
  "relation_type":"prerequisite_recommended",
  "rationale":"说明两者为什么存在教学上的联系",
  "source_chunk_id":"课件真实chunk_id",
  "quote":"对应来源原文中连续不少于十字符的引文"
}]}
~~~

审核历史写入 knowledge_relation_events；这次不会迁移或清空原有题库和 BKT 数据。

## 尚未实现的部分

大规模课程全量关系分批提取、章节自动归属、按指定知识点作答、真实多用户权限系统仍属于后续工作。BKT 掌握度只是学习概率估计，不是能力评级。本地单用户版本不可直接开放公网。
