# KnowTrace 软件需求规格说明书（SRS v1.1）

> 更新日期：2026-10-28。本版取代旧 SRS v1.0 的功能优先级；数据结构以 `docs/data-model.md` 和 `database/schema.sql` 为准。

## 目标

面向只有 PDF/PPTX、电子书、课堂笔记而**没有现成题库**的大学生：将学习资料整理为可追溯的知识点与练习题，记录答题序列，使用知识追踪算法估计掌握度，并根据薄弱点开展个性化复习。

第一阶段为本地单用户，数据模型允许多学科；首个演示课程为《计算机网络》。

## 业务闭环（最终目标）

1. 新建课程、章节，上传原始课件。
2. 解析原文和图文结构；保留来源文件、页码/幻灯片、文本片段与哈希。
3. AI 依据原文提取知识点和候选关系，支持去重和人工审核。
4. 按选定知识点自动生成客观题、答案、解析、难度及对应证据，确认后发布。
5. 学生在线答题，系统自动判分并保存完整作答事件。
6. BKT 按题目**主要知识点**更新掌握概率；推荐弱项知识点和练习。

AI 输出不自动等于事实；缺失证据或审核未通过的题目不能用于正式测评。

## 开发里程碑

| 阶段 | 范围 | 验收 |
|---|---|---|
| M1（已开始） | 数据库、课程/章节/知识点管理、可提取文本的 PDF/PPTX 上传与分块 | API 正常响应，来源页码保留，自动化测试通过 |
| M2 | 资料处理任务、AI 知识点/关系抽取、自动出题、审核发布 | 每道已发布题目都有可查证的原始资料 |
| M3 | 在线测评、自动判分、答题记录、BKT 状态更新 | 可幂等提交答题，掌握状态有历史事件 |
| M4 | 个性化推荐、知识关系图、学习分析页面 | 能完成测评—分析—复习的闭环 |

非 MVP：扫描文档 OCR、复杂公式图像理解、主观题自动评分、面向公网的多用户系统。

## M1 已实现的 API

- `GET /health`
- `POST /api/courses`、`GET /api/courses`
- `GET /api/courses/{course_id}`
- `POST/GET /api/courses/{course_id}/chapters`
- `POST/GET /api/courses/{course_id}/knowledge-points`
- `POST/GET /api/courses/{course_id}/documents`
- `GET /api/documents/{document_id}/chunks`

当前知识点需人工创建，BKT 模块只提供独立算法，不与 API 关联；AI 生成尚未实现。

## 持久化规范

使用 `database/schema.sql` 定义的 19 张表。重要数据链：

`courses / chapters → documents / document_chunks → knowledge_points → question_versions / question_sources → quiz_sessions / quiz_items / answer_records → mastery_states / mastery_events`。

- 知识点、题目、关系都应保留来源和审核状态。
- 关系必须明确区分“先修于”“包含”“相关”等，不以课件章节顺序推断先修。
- 审核通过的题目版本不可覆盖；编辑应创建新版本。
- 作答历史保留逐题结果和时序；掌握度是统计模型估计，并非正式学力评定。

## 安全与质量

本地数据不应上传公开 GitHub；上传文件最大 30 MiB。当前 API 没有认证，只适用于可信本机环境。外部大模型接口（后续）必须保护 API Key，对资料出站进行明确告知。

M1 的验收包含：健康检查、课程及章节/知识点归属、PDF 两页来源、PPTX 上传、非法格式拒绝、BKT 更新与参数校验；后续 M2/M3 需要真实题目审核集及预测指标对比。
