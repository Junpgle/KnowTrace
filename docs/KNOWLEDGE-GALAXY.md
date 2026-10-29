# KnowTrace v0.9.0 — 3D 知识星系（Vue + Three.js）

## 已实现的第一版

入口：Vue 左侧菜单 **“知识星系 · 3D”**，无需单独运行图谱服务。知识图谱使用现有课程/章节/知识点/题目/BKT/关系 SQLite 表，新增一个只读 API，不改变数据库模式。

- **课程恒星**：课程名位于星系中心。
- **章节行星**：数据库 chapters 中的真实章节，围绕课程呈环形分布。
- **知识点星球**：knowledge_points 表中的真实知识点；暂无章节的知识点归入明确标记为**虚拟分组**的“未归类知识点”，不是新增数据库章节。
- **知识连线**：细蓝线表示现有课程/章节/知识点的归属；**金色线仅表示 knowledge_relations 中 review_status=approved 的关系**。目前图谱中可能没有已审核的知识关系，此时不会制造虚构的前置关系。
- **掌握度颜色**：对已审核知识点，有 BKT 历史才按估计掌握度着色；未测评用灰蓝、未审核用灰色，不把“没有观测”等同于“掌握度为0”。
- **操作**：拖动旋转、滚轮缩放、点击选中和自动视角聚焦、返回总览；搜索知识点、章节筛选、已掌握/待巩固/未测评筛选、是否显示未审核点/语义关系；切换 3D/可访问的列表模式。
- **右侧详情**：名称、摘要、审核状态、掌握度、关联已审核题目数、课件来源页码、已审核的关系与理由；可以点击关联节点、预览 PDF 原页，跳转题库管理和课程练习。

本版本没有部署新图库服务器，也没有把知识点传给第三方平台。Three.js 的包是前端安装依赖，星系直接浏览器渲染。3D 组件采用懒加载，打开其他页面不会加载 WebGL 场景。

## 启动与构建

原来运行的 FastAPI + Vite 两个终端保持不变：

~~~bash
git pull origin main

# 终端一（按已有的 Python 环境运行）
cd backend
uvicorn knowtrace.main:app --reload

# 终端二，从仓库根目录开始
cd frontend
npm install
npm run dev
~~~

打开 http://127.0.0.1:5173，点击左侧 **知识星系 · 3D**。

构建测试：

~~~bash
cd frontend
npm run typecheck
npm run build
~~~

后端回归测试：

~~~bash
python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend python -m pytest -q backend/tests
~~~

CI 会在前端代码或后端 API 文件变更时执行相应构建/回归检查。若浏览器没有 WebGL 加速，画布会显示说明，可切换“列表”模式访问知识点；手机端支持触摸交互，但推荐用桌面体验大规模 3D 图谱。

## API

GET /api/courses/{course_id}/knowledge-graph?limit=300&include_pending=true

- 只读。参数 limit 范围 1–600。
- 响应包括 nodes、edges、stats、notice。
- nodes.kind 为 course、chapter、knowledge；ID 规则：课程 course:UUID、章节 chapter:UUID、知识点保留原有 ID。虚拟分组使用 unclassified:UUID。
- edges.structural=true 代表数据库归属关系；structural=false 只代表已审核的关系。
- 返回的 BKT mastery 为概率估计，且未做过测评时返回 null。
- 只返回状态 active 的课程，已归档的知识点不显示。大图按名称排序后截取前 limit 个知识点，并返回 truncated=true，不能把未加载的数据理解为不存在。
- 关系来源于已有 knowledge_relations；关系来源证据、审核编辑功能仍在后续迭代计划中。
- 来源详情继续使用 GET /api/knowledge-points/{id}，不在图谱首个响应中传输大量课件正文。

### 结构示例（示意，不是你的真实数据）

~~~json
{
  "nodes": [
    {"id":"course:course1","kind":"course","name":"计算机网络","mastery":null},
    {"id":"chapter:ch2","kind":"chapter","name":"第2章 数据通信","mastery":null},
    {"id":"point1","kind":"knowledge","name":"数据通信系统模型","mastery":0.73}
  ],
  "edges": [
    {"source":"course:course1","target":"chapter:ch2","type":"contains","structural":true},
    {"source":"chapter:ch2","target":"point1","type":"contains","structural":true}
  ]
}
~~~

## 文件结构

- backend/knowtrace/graph_api.py — 图谱聚合查询接口、BKT 与审核关系过滤
- backend/tests/test_graph.py — 结构、课程隔离、审核过滤与数量限制
- frontend/src/api.ts — 图谱 TypeScript 类型与客户端
- frontend/src/components/Graph3DCanvas.vue — Three.js WebGL 画布、相机、光照、坐标计算、鼠标拾取、销毁与资源释放
- frontend/src/components/KnowledgeGalaxy.vue — 星系工作台、过滤、来源和关系详情
- frontend/src/App.vue — 异步加载的知识星系导航入口

技术选择：**原生 Three.js + Vue 3**，避免叠加 TresJS、3d-force-graph 等重量级封装。使用 OrbitControls 控制旋转/缩放，Raycaster 进行星球点击检测。节点的位置由前端确定性布局计算，**距离不表达语义相似程度**。官方 OrbitControls 文档：https://threejs.org/docs/pages/OrbitControls

性能措施：画布初版默认最多展示300知识点；像素比最多1.5，静态480粒子、基础光照和单一球体几何，浏览器后台时暂停渲染，离开页面时释放 WebGL 纹理/几何体/材质及事件监听；提供列表视图。

## 下一阶段（尚未实现）

1. 知识关系提取 → 审核 → 展示，严格区分前置依赖、相关、对比，且保存来源与审核记录。
2. 章节图谱按需分页、聚类与层次布局，承载数千知识点。
3. 根据已审核前置依赖生成个性化学习路径，而不是从章节顺序推测“必须先学”。
4. 题库支持按指定知识点筛选测评；当前“课程练习”按钮进入普通课程练习，不声称已有专项练习筛选。
5. 可以按用户偏好启用/禁用动效、增加键盘节点导航和镜头预设。

此系统还是**本地单用户**课程设计原型，尚无账号和鉴权，不可直接开放公网。

## v0.9.0 已实现：关系审核与学习路线

知识星系右上角点击“提取 / 审核关系”可展开关系工作台。支持 Ollama / MiMo 有界按需检索和外部 AI 复制提示词，核验逐字引用后保存待审核关系。人工批准后语义连线才出现；必要前置关系阻止成环，审核事件写入历史表。学习路线使用必要前置拓扑排序，BKT 只在可选项中作为优先级。章节整理支持创建章节和移动知识点；移动后需要重新审核。详见 [关系工作台](RELATION-STUDIO.md)。
