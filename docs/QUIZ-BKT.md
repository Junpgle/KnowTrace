# KnowTrace v0.4.0：测评、答题记录与 BKT 掌握度

本版完成**后端 API** 的基本测评闭环：已审核题库 → 创建测评 → 作答 → 自动判分 → 保存学习事件 → BKT 更新掌握概率 → 课程学习报告。

目前仍通过 Swagger 进行测试；可点击操作的正式 Vue 前端，以及动态自适应选题留待后续版本。此版的 normal 测评按既定顺序从最新、已审核题目中选题，不是自适应测评。

## 更新与启动

在 KnowTrace 仓库根目录执行：

~~~bash
git pull origin main
export KNOWTRACE_TEXT_MODEL=qwen3.5:4b
export KNOWTRACE_VISION_MODEL=gemma4:12b
cd backend
uvicorn knowtrace.main:app --reload
~~~

继续使用已有的 SQLite 数据库；启动时执行现有 schema 的增量 IF NOT EXISTS 初始化，不需要删除数据或重新上传 PDF。

打开 http://127.0.0.1:8000/docs ，版本应为 0.4.0。

## 先确保有已审核题目

你的课程 ID：

4e39a9e6-1d6e-479c-a438-501b3739243e

你之前生成的第一道题的 version_id：

6742b4b8-acd0-46d3-bbd1-122ecca1eae4

如果你已经将题目修订为版本 2，则应**审核最新版本**，旧版本不再作为可选试题。可以用 GET /api/courses/{course_id}/questions 查看最新题目版本和状态；只对确认为正确的题目调用 POST /api/question-versions/{version_id}/review 并提交 {"decision":"approved"}。

## 创建第一次测评

Swagger：POST /api/courses/{course_id}/quiz-sessions

Request body：

~~~json
{
  "count": 1,
  "mode": "normal"
}
~~~

每次测评支持 1–20 道题，题量不能大于当前课程中“最新版本已审核，且主知识点已审核”的可用题目数。第一次只选择 1 道题就够了。

返回的 id 是 session_id；items 中每道题的 quiz_item_id 是接下来提交答案时使用的 ID。

**答题前，测评接口不包含标准答案和解析**。但本地原型的后台管理接口仍可直接查看题目答案，没有区分学生/管理员权限，不应暴露公网。

## 提交答案

Swagger：POST /api/quiz-items/{quiz_item_id}/answer

单选题：

~~~json
{
  "client_request_id": "practice-20261028-001",
  "response": "B",
  "elapsed_ms": 12000
}
~~~

判断题将 response 设为 JSON 布尔值 true 或 false，而不是字符串 "true"。

注意：每次新作答都需要不同的 client_request_id。相同题目用相同 ID 和相同答案重试，会返回已保存的结果，不会重复更新掌握度；想重新练习同一道题，需要创建新测评。

作答后返回 is_correct、correct_answer、explanation，以及 mastery 中的 before、posterior、after（BKT 更新值）。

## 查看测评及学习报告

- GET /api/quiz-sessions/{session_id}：测评进度与已答题结果；全部作答完成时 status 为 completed。
- GET /api/courses/{course_id}/learning-report：累计答题数、正确数、正确率和各知识点的 p_mastery / observations / accuracy。

BKT 初始参数目前固定为 p_initial=0.2、p_learn=0.1、p_guess=0.2、p_slip=0.1，仅用于教学原型。概率是模型估计，不是经过校准的学生能力分数。下一步计划加入**自适应选题**和与历史正确率的预测对照实验。

## 自动化回归测试

~~~bash
python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest -q backend/tests
~~~

测试使用模拟 AI 响应及临时 SQLite 数据库，不会修改你真实的课程数据库。当前版本的后端 API 尚无用户登录/认证，请仅在可信的本地环境运行。
