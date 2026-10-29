-- KnowTrace 逻辑数据库 v1.0 (SQLite 3.35+)
-- 设计稿，不代表已部署。连接 SQLite 时每个连接执行 PRAGMA foreign_keys=ON;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    code TEXT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(owner_user_id, code)
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    parent_chapter_id TEXT REFERENCES chapters(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (id <> parent_chapter_id)
);
CREATE INDEX IF NOT EXISTS idx_chapters_course_parent ON chapters(course_id, parent_chapter_id, ordinal);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    uploaded_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    original_filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    total_pages INTEGER CHECK (total_pages IS NULL OR total_pages >= 0),
    supersedes_document_id TEXT REFERENCES documents(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded','processing','ready','failed','archived')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK (id <> supersedes_document_id)
);
CREATE INDEX IF NOT EXISTS idx_documents_course ON documents(course_id, created_at);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    chunk_index INTEGER NOT NULL,
    page_start INTEGER NOT NULL CHECK (page_start >= 1),
    page_end INTEGER NOT NULL CHECK (page_end >= page_start),
    content_kind TEXT NOT NULL DEFAULT 'text' CHECK (content_kind IN ('text','table','figure_description','mixed')),
    content_text TEXT NOT NULL,
    content_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document_pages ON document_chunks(document_id, page_start, page_end);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    document_id TEXT REFERENCES documents(id) ON DELETE RESTRICT,
    job_type TEXT NOT NULL CHECK (job_type IN ('document_parse','knowledge_extract','relation_extract','question_generate','question_validate')),
    engine_name TEXT NOT NULL,
    engine_version TEXT,
    prompt_version TEXT,
    input_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_course ON processing_jobs(course_id, job_type, created_at);

-- Whole-document knowledge extraction (separate from per-request processing_jobs).
-- A checkpoint advances only after a complete 1–4-page chunk commits.
CREATE TABLE IF NOT EXISTS batch_extractions (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK (provider IN ('default','ollama','mimo')),
    pages_per_batch INTEGER NOT NULL CHECK (pages_per_batch BETWEEN 1 AND 4),
    include_visual INTEGER NOT NULL DEFAULT 0 CHECK (include_visual IN (0,1)),
    next_page INTEGER NOT NULL DEFAULT 1 CHECK (next_page >= 1),
    total_pages INTEGER NOT NULL CHECK (total_pages >= 1),
    range_start INTEGER NOT NULL DEFAULT 1,
    range_end INTEGER,
    work_stage TEXT NOT NULL DEFAULT 'queued',
    in_flight_start INTEGER,
    in_flight_end INTEGER,
    in_flight_started_at TEXT,
    completed_pages INTEGER NOT NULL DEFAULT 0,
    created_points INTEGER NOT NULL DEFAULT 0,
    reused_points INTEGER NOT NULL DEFAULT 0,
    skipped_pages INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running'
       CHECK (status IN ('running','stopping','stopped','succeeded','failed','interrupted')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_batch_extractions_document ON batch_extractions(document_id,created_at);

-- Persisted, per-task notifications (including errors when user changes pages).
CREATE TABLE IF NOT EXISTS task_notifications (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES batch_extractions(id) ON DELETE RESTRICT,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    level TEXT NOT NULL CHECK(level IN ('success','error','info')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_task_notifications_recent ON task_notifications(created_at,id);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    chapter_id TEXT REFERENCES chapters(id) ON DELETE RESTRICT,
    stable_code TEXT,
    canonical_name TEXT NOT NULL,
    summary TEXT,
    difficulty INTEGER CHECK (difficulty BETWEEN 1 AND 3),
    created_job_id TEXT REFERENCES processing_jobs(id) ON DELETE SET NULL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(course_id, stable_code)
);
CREATE INDEX IF NOT EXISTS idx_kps_course_chapter ON knowledge_points(course_id, chapter_id);

CREATE TABLE IF NOT EXISTS knowledge_point_sources (
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE RESTRICT,
    evidence_note TEXT,
    PRIMARY KEY (knowledge_point_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_kp_sources_chunk ON knowledge_point_sources(chunk_id);

-- Append-only history for manual edits and knowledge point approval decisions.
CREATE TABLE IF NOT EXISTS knowledge_point_events (
    id TEXT PRIMARY KEY,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('edited','approved','rejected')),
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_kp_events_point_time ON knowledge_point_events(knowledge_point_id,created_at);


-- AI pre-review never changes canonical knowledge review_status by itself.
-- Each proposal is tied to a fingerprint of point fields and cited source text.
CREATE TABLE IF NOT EXISTS ai_knowledge_reviews (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    recommendation TEXT NOT NULL CHECK(recommendation IN ('approve','revise','reject')),
    confidence TEXT NOT NULL CHECK(confidence IN ('high','medium','low')),
    rationale TEXT NOT NULL,
    issues_json TEXT NOT NULL DEFAULT '[]',
    evidence_chunk_id TEXT,
    evidence_quote TEXT,
    verified_text INTEGER NOT NULL DEFAULT 0 CHECK(verified_text IN (0,1)),
    source_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_review_course ON ai_knowledge_reviews(course_id,created_at);
CREATE INDEX IF NOT EXISTS idx_ai_review_point ON ai_knowledge_reviews(knowledge_point_id,created_at);

CREATE TABLE IF NOT EXISTS ai_knowledge_review_batches (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK(provider IN ('default','ollama','mimo')),
    point_ids_json TEXT NOT NULL,
    next_index INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL,
    errors INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('running','stopping','stopped','interrupted','succeeded')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_review_batches_course ON ai_knowledge_review_batches(course_id,created_at);

-- AI course-level relation discovery operates on a stable point ID snapshot.
-- Each successful focus call checkpoints independently so stop/resume is safe.
CREATE TABLE IF NOT EXISTS relation_batch_runs (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK(provider IN ('default','ollama','mimo')),
    focus_ids_json TEXT NOT NULL,
    per_focus INTEGER NOT NULL CHECK(per_focus BETWEEN 1 AND 5),
    mode TEXT NOT NULL DEFAULT 'single' CHECK(mode IN ('single','grouped','bridge')),
    group_size INTEGER NOT NULL DEFAULT 1,
    total_points INTEGER,
    completed_points INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL CHECK(total > 0),
    next_index INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    created_relations INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN
      ('running','stopping','stopped','interrupted','succeeded')),
    last_error TEXT,
    current_focus TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_relation_batch_course
    ON relation_batch_runs(course_id,created_at);

CREATE TABLE IF NOT EXISTS knowledge_relations (
    id TEXT PRIMARY KEY,
    source_kp_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    target_kp_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('prerequisite_required','prerequisite_recommended','contains','related','uses','compares')),
    rationale TEXT,
    created_job_id TEXT REFERENCES processing_jobs(id) ON DELETE SET NULL,
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK(source_kp_id <> target_kp_id),
    UNIQUE(source_kp_id, target_kp_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_kp_id, relation_type);

CREATE TABLE IF NOT EXISTS knowledge_relation_events (
    id TEXT PRIMARY KEY,
    relation_id TEXT NOT NULL REFERENCES knowledge_relations(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK(action IN ('approved','rejected')),
    before_status TEXT NOT NULL,
    after_status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_relation_events_relation
    ON knowledge_relation_events(relation_id,created_at);

CREATE TABLE IF NOT EXISTS knowledge_relation_sources (
    relation_id TEXT NOT NULL REFERENCES knowledge_relations(id) ON DELETE RESTRICT,
    chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE RESTRICT,
    evidence_note TEXT,
    PRIMARY KEY (relation_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    origin TEXT NOT NULL CHECK (origin IN ('ai','manual','imported')),
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_questions_course ON questions(course_id);

CREATE TABLE IF NOT EXISTS question_versions (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL CHECK(version_no >= 1),
    question_type TEXT NOT NULL CHECK (question_type IN ('single_choice','true_false')),
    stem TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    correct_answer_json TEXT NOT NULL,
    explanation TEXT,
    difficulty INTEGER NOT NULL CHECK(difficulty BETWEEN 1 AND 3),
    review_status TEXT NOT NULL DEFAULT 'draft' CHECK (review_status IN ('draft','pending','approved','rejected')),
    created_job_id TEXT REFERENCES processing_jobs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    approved_at TEXT,
    UNIQUE(question_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_qversions_question ON question_versions(question_id, version_no);
-- 防止已发布题目的题干/答案/解析被原地修改；改题必须建新版本
CREATE TRIGGER IF NOT EXISTS trg_approved_question_version_immutable
BEFORE UPDATE ON question_versions
WHEN OLD.review_status = 'approved'
BEGIN
  SELECT RAISE(ABORT, 'approved question version is immutable');
END;

CREATE TABLE IF NOT EXISTS question_knowledge_points (
    question_version_id TEXT NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
    PRIMARY KEY (question_version_id, knowledge_point_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_question_one_primary
    ON question_knowledge_points(question_version_id) WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS idx_qkp_kp ON question_knowledge_points(knowledge_point_id);

CREATE TABLE IF NOT EXISTS question_sources (
    question_version_id TEXT NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    chunk_id TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE RESTRICT,
    evidence_note TEXT,
    PRIMARY KEY (question_version_id, chunk_id)
);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    mode TEXT NOT NULL CHECK (mode IN ('normal','adaptive')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','cancelled')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_user_course ON quiz_sessions(user_id, course_id, started_at);

CREATE TABLE IF NOT EXISTS quiz_items (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES quiz_sessions(id) ON DELETE RESTRICT,
    question_version_id TEXT NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    primary_knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 1),
    selection_reason TEXT,
    shown_at TEXT,
    UNIQUE(session_id, position),
    UNIQUE(session_id, question_version_id)
);

CREATE TABLE IF NOT EXISTS answer_records (
    id TEXT PRIMARY KEY,
    quiz_item_id TEXT NOT NULL UNIQUE REFERENCES quiz_items(id) ON DELETE RESTRICT,
    client_request_id TEXT NOT NULL UNIQUE,
    response_json TEXT NOT NULL,
    is_correct INTEGER NOT NULL CHECK (is_correct IN (0,1)),
    elapsed_ms INTEGER CHECK (elapsed_ms IS NULL OR elapsed_ms >= 0),
    answered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS mastery_states (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    model_key TEXT NOT NULL DEFAULT 'bkt_v1',
    p_mastery REAL NOT NULL CHECK (p_mastery >= 0.0 AND p_mastery <= 1.0),
    observations INTEGER NOT NULL DEFAULT 0 CHECK(observations >= 0),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (user_id, knowledge_point_id, model_key)
);

CREATE TABLE IF NOT EXISTS mastery_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    knowledge_point_id TEXT NOT NULL REFERENCES knowledge_points(id) ON DELETE RESTRICT,
    answer_record_id TEXT NOT NULL REFERENCES answer_records(id) ON DELETE RESTRICT,
    model_key TEXT NOT NULL,
    p_before REAL NOT NULL CHECK(p_before BETWEEN 0 AND 1),
    p_posterior REAL NOT NULL CHECK(p_posterior BETWEEN 0 AND 1),
    p_after REAL NOT NULL CHECK(p_after BETWEEN 0 AND 1),
    parameters_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(answer_record_id, knowledge_point_id, model_key)
);
CREATE INDEX IF NOT EXISTS idx_mastery_events_user_kp_time
    ON mastery_events(user_id, knowledge_point_id, created_at);