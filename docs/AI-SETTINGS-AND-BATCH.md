# KnowTrace v1.1.0 — AI 设置与整份资料批量处理

## 设置界面

左侧“AI 模型设置”支持保存 MiMo API Key、选择 MiMo Flash/Pro、设置默认 Ollama/MiMo、配置本地 Ollama 地址与文本/视觉模型。可点击“测试连接”验证服务，MiMo 测试会产生少量 API 费用。

- 保存位置：SQLite 数据库所在文件夹的 ai_settings.json，macOS/Linux 以 0600 权限写入，文件重命名采用原子替换。
- GET /api/settings/ai 从不返回密钥，只返回是否已配置。PATCH /api/settings/ai 只允许本地请求（127.0.0.1、localhost、::1）。
- 有效的 MiMo Key 需要 sk- 开头；Token Plan 套餐密钥不能填入应用后端。
- 之前通过终端 export MIMO_API_KEY 的方式仍然可用；保存新 Key 后优先读取网页设置。
- 请勿将本机 8000/5173 端口开放到公网。本地文件存储不是专业密钥保险箱；正式多用户部署前应有账户鉴权及系统密钥管理。
- Git 忽略 data/*、backend/data/ 和 *.sqlite3；不要把设置文件复制到仓库。

## 整份 PDF/PPTX 一键处理

入口：资料导入与管理 → 选择文件 → “一键处理整份资料”。

1. 选择 Ollama/MiMo/默认模型，设置每批1、2或4页，确认云端调用风险。
2. 点击“开始处理全部N页”。FastAPI 的独立后台线程开始逐批调用现有的知识点提取 API：每次仅提交该批次页面。
3. 页面显示批次已完成页数、真实比例、新知识点数量、复用数量、跳过空白/图片页数量，活动任务每约2秒刷新。
4. 点击“停止后续处理”后当前模型调用仍可能完成（且云端可能已计费），但下一批不会启动，已完成批次不会撤销。
5. 再次点击“从第N页继续”，根据数据库检查点继续，不重复处理已经提交成功的批次。
6. 浏览器关闭后后端仍在运行；如果 FastAPI 进程重启，正在执行的任务会被标记为“已中断”，用户回到页面可以手动继续。
7. 完成后进入“知识点审核”，所有提取结果是 pending，不会自动批准或进入正式学习。

数据库在 batch_extractions 表保存批次检查点。原有 processing_jobs 每次仍保存具体的AI调用记录。尚不支持跨多进程/多台机器的可靠队列，应只用单worker的本地开发模式（uvicorn默认是一个worker，不能增加--workers N）。

## 边界与安全

- 停止是**批次边界协作式停止**，不能立即强制取消模型 HTTP 请求。为减少响应等待，建议每批1或2页。
- 当选定页面只有图片或空白且视觉模式关闭时会跳过并累计统计；视觉模式只允许 PDF、每批最多2页，并依赖本地 Ollama 视觉模型。
- 云端 MiMo 批量处理会重复调用 API，消耗 Token/额度；请先用1–2页的“单次提取”确认模型和输出效果，再对整份资料运行。
- 若模型服务不可用或返回错误，会暂停并记录失败页码；修正设置后手动继续。
- 对于当前不支持的图像内容，完成百分比仅代表页面**处理完毕或已跳过**，不代表已完整理解所有课件，也不意味着提取的知识点已通过人工审核。
- 暂无并发任务队列、多账户隔离、跨进程可恢复执行、任务进度 WebSocket 或按知识点一键生成全部题目。当前批处理针对**整份资料的知识点提取**。
- 课程数据和已审核题目不会因停止批量任务而删除。

## API

- GET /api/settings/ai
- PATCH /api/settings/ai
- POST /api/settings/ai/test?provider=mimo|ollama
- POST /api/documents/{id}/batch-extractions
- GET /api/documents/{id}/batch-extractions
- GET /api/batch-extractions/{id}
- POST /api/batch-extractions/{id}/stop
- POST /api/batch-extractions/{id}/resume

## 更新

从仓库根目录 git pull origin main，使用旧的 FastAPI、Vue 启动命令即可。数据库表由已有的 initialize() 自动添加，不会清空旧数据。

后端测试：PYTHONPATH=backend python -m pytest -q backend/tests

前端构建：cd frontend && npm run build

两套 GitHub Actions 必须为绿色才可声明 CI 已通过。
