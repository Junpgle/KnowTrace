# KnowTrace v1.0.0 — 资料库与课程创建

## 使用方式

1. 访问 http://127.0.0.1:5173 ，从侧边栏“新建课程”创建自己的学习科目。初次使用也无需前往 Swagger。
2. 在“资料导入与管理”中点击选择文件或拖入 PDF/PPTX。单次最多10份，单文件最多30 MiB，保存在本地 KnowTrace 后端。
3. 在文件列表中选择课件，查看页数、PDF原页预览、提取原文与文字质量提示。低文本提示是启发式判断，不等于OCR准确率。
4. 选择1–4页，使用本地 Ollama 或明确选择小米 MiMo API 提取知识点。对于低文字量的 PDF 图片页，可开启视觉识别（每批最多 2 页）：选择 Ollama 时使用本地视觉模型；选择小米 MiMo v2.6 时直接将渲染的 PNG 图片以 Base64 方式发送给云端多模态模型，无需 Ollama 视觉模型。
5. 提取后点击“前往知识点审核”，在题库管理中逐项审核，再生成练习题与知识星系图谱。
6. 也可以选择“复制提示词，交给其他AI提取”，利用此前实现的外部AI手动接力功能。

## 后端接口

- POST /api/courses — 新建课程。
- POST /api/courses/{course_id}/documents — multipart/form-data，上传字段为 file，自动保存与解析。
- GET /api/courses/{course_id}/documents — 获取课件列表。
- GET /api/documents/{document_id}/quality — 获取逐页文字量质量提示。
- GET /api/documents/{document_id}/chunks — 获取已抽取的片段，支持分页。
- GET /api/documents/{document_id}/pages/{page_number}/preview — PDF原页图像。
- POST /api/documents/{document_id}/extract-knowledge — 指定page_start、page_end、include_visual和provider。

上传资料**不会自动发送到云端 AI**。只有用户主动选择 MiMo 并开始提取时才会发送被选中的页面文字；外部AI提示词由用户自己决定是否分享。AI提取的知识点全部处于pending待审核状态。

## 已知限制

- 目前仅支持PDF/PPTX导入，单文件最大30 MiB。
- PDF 可预览原始页面，低文字量的 PDF 页面可选 MiMo/Ollama 多模态识别；PPTX 目前只提供已解析文字，不支持原幻灯片图片预览或自动视觉识别。
- 普通上传展示“正在上传与解析”，不显示未经测量的假进度条。
- 尚无资料删除、重命名及文件夹管理API，因此也不提供误导性按钮。
- 资料较长时，原文片段需要逐批加载；视觉识别需要已安装本地模型。
- 本地单用户应用不要直接暴露到公网。

## 主要代码

- frontend/src/components/DocumentLibrary.vue
- frontend/src/App.vue
- frontend/src/api.ts
- frontend/src/style.css

## 更新启动

从仓库根目录执行：

    git pull origin main
    cd frontend
    npm install
    npm run dev

后端使用已有 Python 环境运行：

    cd backend
    uvicorn knowtrace.main:app --reload

前端可执行 npm run typecheck 和 npm run build 验证，CI自动运行相应检查。

## v1.1.0 全资料批处理

新版资料库在单次1–4页提取区域之前增加“一键处理整份资料”，按设定的1/2/4页批量提交模型，每批完成后保存进度；可以停止后续批次或中断后继续。模型配置入口为左侧“AI 模型设置”，支持在本机保存 MiMo Key 和 Ollama 模型参数。详情见 [AI 设置与批量任务](AI-SETTINGS-AND-BATCH.md)。
