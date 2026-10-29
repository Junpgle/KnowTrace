/** Typed, same-origin API client. Vite proxies /api to FastAPI during local development. */

export type RelationType = 'prerequisite_required' | 'prerequisite_recommended'
  | 'related' | 'uses' | 'compares'

export interface RelationSummary {
  id: string
  source_kp_id: string
  target_kp_id: string
  source_name: string
  target_name: string
  relation_type: RelationType
  rationale: string
  review_status: 'pending' | 'approved' | 'rejected'
  sources: SourceEvidence[]
}

export interface RelationTaskOptions {
  focus_id: string
  target_id?: string | null
  count: number
}

export interface RelationImportResult {
  created: number
  relation_ids: string[]
  warnings: string[]
  review_status: 'pending'
  origin: 'external' | 'api'
  provider?: string
  tool_calls?: number
  retrieved_chunks?: number
  usage?: { total_tokens?: number; prompt_tokens?: number; completion_tokens?: number }
}

export interface StudyPath {
  course_id: string
  items: {
    id: string
    name: string
    mastery: number | null
    observations: number
    rank: number
  }[]
  total: number
  required_edges: number
  method: string
  notice: string
}

export interface Chapter {
  id: string
  course_id: string
  parent_chapter_id: string | null
  title: string
  ordinal: number
}

export interface GraphNode {
  id: string
  kind: 'course' | 'chapter' | 'knowledge'
  name: string
  chapter_id: string | null
  summary: string | null
  difficulty: number | null
  review_status: 'pending' | 'approved' | 'rejected'
  mastery: number | null
  observations: number
  source_count: number
  question_count: number
  virtual: boolean
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: 'contains' | 'prerequisite_required' | 'prerequisite_recommended'
    | 'related' | 'uses' | 'compares'
  structural: boolean
  verified: boolean
  rationale: string | null
}

export interface KnowledgeGraph {
  course_id: string
  course_name: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    total_knowledge_points: number
    shown_knowledge_points: number
    approved_points: number
    pending_points: number
    mastery_estimated_points: number
    approved_semantic_edges: number
    truncated: boolean
    limit: number
  }
  notice: string
}

export interface Course {
  id: string
  title: string
  description: string | null
  code: string | null
  created_at: string
}

export interface QuestionSummary {
  version_id: string
  question_id: string
  version_no: number
  question_type: 'single_choice' | 'true_false'
  stem: string
  difficulty: number
  review_status: 'draft' | 'pending' | 'approved' | 'rejected'
  primary_knowledge_point_id: string | null
}

export interface QuizAnswerResult {
  answer_id: string
  response: string | boolean
  is_correct: boolean
  correct_answer: string | boolean
  explanation: string | null
  answered_at: string
  elapsed_ms?: number | null
}

export interface QuizItem {
  quiz_item_id: string
  position: number
  question_version_id: string
  primary_knowledge_point_id: string
  question_type: 'single_choice' | 'true_false'
  stem: string
  options: Record<string, string>
  answered: boolean
  result?: QuizAnswerResult
}

export interface QuizSession {
  id: string
  course_id: string
  mode: 'normal'
  status: 'active' | 'completed' | 'cancelled'
  started_at: string
  finished_at: string | null
  total: number
  answered: number
  correct: number
  items: QuizItem[]
}

export interface MasterySnapshot {
  model: string
  before: number
  posterior: number
  after: number
}

export interface AnswerFeedback {
  answer_id: string
  quiz_item_id: string
  response: string | boolean
  is_correct: boolean
  correct_answer: string | boolean
  explanation: string | null
  knowledge_point_id: string
  mastery: MasterySnapshot
  answered_at: string
}

export interface KnowledgeReport {
  knowledge_point_id: string
  canonical_name: string
  p_mastery: number | null
  observations: number | null
  answered: number
  correct: number
  accuracy: number | null
}

export interface LearningReport {
  course_id: string
  user_id: string
  sessions: number
  answered: number
  correct: number
  accuracy: number | null
  mastery_model: string
  bkt_warning: string
  knowledge_points: KnowledgeReport[]
}


export interface DocumentUploadResult {
  id: string
  course_id: string
  filename: string
  total_pages: number
  chunks: number
  status: 'ready'
}

export interface DocumentPageQuality {
  page: number
  extracted_characters_approx: number
  needs_review: boolean
  reason: 'no_text' | 'low_text' | null
}
export interface DocumentQuality {
  document_id: string
  filename: string
  total_pages: number
  pages_needing_review: number
  warning: string
  pages: DocumentPageQuality[]
}
export interface DocumentChunk {
  id: string
  chunk_index: number
  page_start: number
  page_end: number
  content_kind: string
  content_text: string
}
export interface ExtractKnowledgeResult {
  job_id: string
  created: number
  reused: number
  created_ids: string[]
  review_status: 'pending'
  vision_pages: number[]
  skipped_sparse_pages: number[]
  warnings: string[]
}
export interface CourseDocument {
  id: string
  original_filename: string
  total_pages: number
  status: string
  created_at: string
}
export interface ExternalPrompt {
  prompt: string
  privacy_warning: string
  requires_review: boolean
  source_chunks?: number
  available_pages?: number[]
  skipped_pages?: number[]
}
export interface ExternalQuestionImportResult {
  job_id: string
  created: number
  question_version_ids: string[]
  review_status: 'pending'
  origin: 'manual_external'
  warnings: string[]
}
export interface ExternalKnowledgeImportResult {
  job_id: string
  created: number
  reused: number
  created_ids: string[]
  review_status: 'pending'
  skipped_pages: number[]
  origin: 'manual_external'
  warnings: string[]
}

export type KnowledgeAIRecommendation = 'approve' | 'revise' | 'reject'
export interface KnowledgeAIReview {
  id: string
  course_id: string
  knowledge_point_id: string
  provider: string
  model: string
  fingerprint: string
  recommendation: KnowledgeAIRecommendation
  confidence: 'high' | 'medium' | 'low'
  rationale: string
  issues: string[]
  evidence_chunk_id: string | null
  evidence_quote: string | null
  verified_text: boolean
  source_kind: string | null
  stale: boolean
  created_at: string
}
export interface KnowledgeAIReviewBatch {
  id: string
  course_id: string
  provider: AIProviderId
  next_index: number
  completed: number
  total: number
  errors: number
  status: 'running' | 'stopping' | 'stopped' | 'interrupted' | 'succeeded'
  last_error: string | null
  progress: number
  created_at: string
  updated_at: string
}
export type BatchStatus = 'running' | 'stopping' | 'stopped' | 'interrupted' | 'succeeded' | 'failed'
export interface BatchExtraction {
  id: string
  course_id: string
  document_id: string
  provider: AIProviderId
  pages_per_batch: number
  include_visual: number
  next_page: number
  total_pages: number
  completed_pages: number
  created_points: number
  reused_points: number
  skipped_pages: number
  status: BatchStatus
  last_error: string | null
  created_at: string
  updated_at: string
  progress: number
  stop_note: string
}
export interface AISettings {
  default: 'ollama' | 'mimo'
  providers: AIProviderInfo[]
  security: string
  mimo_key_configured: boolean
  mimo_key_source: string
  ollama_url: string
  ollama_text_model: string
  ollama_vision_model: string
  mimo_model: string
}
export interface AISettingsPatch {
  default_provider?: 'ollama' | 'mimo'
  mimo_model?: 'mimo-v2.6-flash' | 'mimo-v2.6-pro'
  mimo_api_key?: string
  clear_mimo_key?: boolean
  ollama_url?: string
  ollama_text_model?: string
  ollama_vision_model?: string
}

export type AIProviderId = 'default' | 'ollama' | 'mimo'

export interface AIProviderInfo {
  id: 'ollama' | 'mimo'
  label: string
  model: string
  available: boolean
  vision_model: string
  function_calling: boolean
}
export interface AIProviderStatus {
  default: 'ollama' | 'mimo'
  providers: AIProviderInfo[]
  security: string
}

export type ReviewStatus = 'pending' | 'approved' | 'rejected'

export interface SourceEvidence {
  chunk_id: string
  document_id: string
  original_filename: string
  page_start: number
  page_end?: number
  content_kind: 'text' | 'figure_description' | 'table' | 'mixed'
  evidence_note: string | null
}

export interface KnowledgePoint {
  id: string
  course_id: string
  chapter_id: string | null
  canonical_name: string
  summary: string | null
  difficulty: number | null
  review_status: ReviewStatus
}

export interface KnowledgePointDetail extends KnowledgePoint {
  sources: SourceEvidence[]
  created_job_id: string | null
}

export interface QuestionVersion {
  id: string
  question_id: string
  version_no: number
  question_type: 'single_choice' | 'true_false'
  stem: string
  options: Record<string, string>
  correct_answer: string | boolean
  explanation: string | null
  difficulty: number
  review_status: 'draft' | ReviewStatus
  approved_at: string | null
  sources: SourceEvidence[]
  knowledge_points: { knowledge_point_id: string; is_primary: number }[]
}

export interface GenerationResult {
  job_id: string
  provider?: string
  model?: string
  tool_calls?: number
  retrieved_chunks?: number
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
  created: number
  question_version_ids: string[]
  review_status: 'pending'
  warnings: string[]
}

export interface QuestionRevision {
  stem?: string
  options?: Record<string, string>
  correct_answer?: string | boolean
  explanation?: string
  difficulty?: number
}

export function documentPagePreviewUrl(documentId: string, page: number): string {
  return base + '/api/documents/' + encodeURIComponent(documentId) +
    '/pages/' + encodeURIComponent(String(page)) + '/preview'
}
export function sourcePreviewUrl(source: SourceEvidence): string {
  return documentPagePreviewUrl(source.document_id, source.page_start)
}

const base = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(base + path, {
      ...options,
      headers: {
        Accept: 'application/json',
        ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError(0, '无法连接后端。请确认 FastAPI 正在 127.0.0.1:8000 运行。')
  }
  if (!response.ok) {
    let detail: unknown
    try {
      const payload: { detail?: unknown } = await response.json()
      detail = payload.detail
    } catch {
      detail = undefined
    }
    const message = typeof detail === 'string' ? detail : ({
      404: '找不到相应记录，请刷新重试。',
      413: '文件超过 30 MiB 上限。',
      415: '不支持此文件类型。仅接受 PDF 和 PPTX。',
      502: '模型调用或回复解析失败，请检查后端日志。',
      409: '当前操作与已有状态冲突，请刷新后重试。',
      422: '提交的数据不符合要求。',
      503: '后端暂时无法提供服务。',
    } as Record<number, string>)[response.status] || '请求失败，请稍后重试。'
    throw new ApiError(response.status, message)
  }
  return await response.json() as T
}

export const api = {
  reviewKnowledgeWithAI: (id: string, provider: AIProviderId = 'default') =>
    request<KnowledgeAIReview>('/api/knowledge-points/' + encodeURIComponent(id) + '/ai-review',
      { method: 'POST', body: JSON.stringify({ provider }) }),
  knowledgeAIReviews: (courseId: string) =>
    request<KnowledgeAIReview[]>('/api/courses/' + encodeURIComponent(courseId) + '/ai-knowledge-reviews'),
  applyKnowledgeAIReviews: (courseId: string, reviewIds: string[]) =>
    request<{ applied: string[]; skipped: { id: string; reason: string }[] }>(
      '/api/courses/' + encodeURIComponent(courseId) + '/ai-knowledge-reviews/apply',
      { method: 'POST', body: JSON.stringify({ confirmed: true, review_ids: reviewIds }) }),
  knowledgeAIReviewBatches: (courseId: string) =>
    request<KnowledgeAIReviewBatch[]>('/api/courses/' + encodeURIComponent(courseId) + '/ai-review-batches'),
  startKnowledgeAIReviewBatch: (courseId: string, provider: AIProviderId, limit = 200) =>
    request<KnowledgeAIReviewBatch>('/api/courses/' + encodeURIComponent(courseId) + '/ai-review-batches',
      { method: 'POST', body: JSON.stringify({ provider, limit }) }),
  stopKnowledgeAIReviewBatch: (id: string) =>
    request<KnowledgeAIReviewBatch>('/api/ai-review-batches/' + encodeURIComponent(id) + '/stop',
      { method: 'POST' }),
  resumeKnowledgeAIReviewBatch: (id: string) =>
    request<KnowledgeAIReviewBatch>('/api/ai-review-batches/' + encodeURIComponent(id) + '/resume',
      { method: 'POST' }),
  aiSettings: () => request<AISettings>('/api/settings/ai'),
  saveAISettings: (body: AISettingsPatch) =>
    request<AISettings>('/api/settings/ai', {method:'PATCH', body:JSON.stringify(body)}),
  testAIConnection: (provider:'mimo'|'ollama') =>
    request<{provider:string;ok:boolean;message:string}>('/api/settings/ai/test?provider='+provider,
      {method:'POST'}),
  batchExtractions: (docId: string) =>
    request<BatchExtraction[]>('/api/documents/'+encodeURIComponent(docId)+'/batch-extractions'),
  startBatchExtraction: (docId: string, options: {
    provider: AIProviderId; pages_per_batch: number; include_visual: boolean
  }) => request<BatchExtraction>('/api/documents/'+encodeURIComponent(docId)+'/batch-extractions',
      {method:'POST',body:JSON.stringify(options)}),
  batchExtraction: (id: string) =>
    request<BatchExtraction>('/api/batch-extractions/'+encodeURIComponent(id)),
  stopBatchExtraction: (id: string) =>
    request<BatchExtraction>('/api/batch-extractions/'+encodeURIComponent(id)+'/stop',
      {method:'POST'}),
  resumeBatchExtraction: (id: string) =>
    request<BatchExtraction>('/api/batch-extractions/'+encodeURIComponent(id)+'/resume',
      {method:'POST'}),
  chapters: (courseId: string) =>
    request<Chapter[]>('/api/courses/' + encodeURIComponent(courseId) + '/chapters'),
  createChapter: (courseId: string, data: { title: string; parent_chapter_id?: string | null; ordinal?: number }) =>
    request<Chapter>('/api/courses/' + encodeURIComponent(courseId) + '/chapters',
      { method: 'POST', body: JSON.stringify(data) }),
  assignKnowledgeChapter: (id: string, chapterId: string | null) =>
    request<KnowledgePointDetail>('/api/knowledge-points/' + encodeURIComponent(id),
      { method: 'PATCH', body: JSON.stringify({ chapter_id: chapterId }) }),
  relations: (courseId: string) =>
    request<RelationSummary[]>('/api/courses/' + encodeURIComponent(courseId) + '/knowledge-relations'),
  relationPrompt: (courseId: string, options: RelationTaskOptions) =>
    request<{ prompt: string; point_count: number; source_chunks: number; privacy_warning: string }>(
      '/api/courses/' + encodeURIComponent(courseId) + '/relations/prompt',
      { method: 'POST', body: JSON.stringify(options) }),
  importRelations: (courseId: string, options: RelationTaskOptions & { response_text: string }) =>
    request<RelationImportResult>('/api/courses/' + encodeURIComponent(courseId) + '/relations/import',
      { method: 'POST', body: JSON.stringify(options) }),
  generateRelations: (courseId: string, options: RelationTaskOptions & { provider: AIProviderId }) =>
    request<RelationImportResult>('/api/courses/' + encodeURIComponent(courseId) + '/relations/generate',
      { method: 'POST', body: JSON.stringify(options) }),
  createRelation: (courseId: string, relation: {
    source_kp_id: string
    target_kp_id: string
    relation_type: RelationType
    rationale: string
    source_chunk_id: string
    quote: string
  }) => request<{id:string; review_status:'pending'}>(
    '/api/courses/' + encodeURIComponent(courseId) + '/relations/manual',
    { method: 'POST', body: JSON.stringify(relation) }),
  reviewRelation: (id: string, decision: 'approved' | 'rejected') =>
    request<{id:string; review_status:'approved' | 'rejected'}>(
      '/api/knowledge-relations/' + encodeURIComponent(id) + '/review',
      { method: 'POST', body: JSON.stringify({ decision }) }),
  studyPath: (courseId: string, limit = 12) =>
    request<StudyPath>('/api/courses/' + encodeURIComponent(courseId) + '/study-path?limit=' + limit),
  knowledgeGraph: (courseId: string, options: { limit?: number, include_pending?: boolean } = {}) => {
    const params = new URLSearchParams()
    params.set('limit', String(options.limit ?? 300))
    params.set('include_pending', String(options.include_pending ?? true))
    return request<KnowledgeGraph>('/api/courses/' + encodeURIComponent(courseId)
      + '/knowledge-graph?' + params.toString())
  },
  health: () => request<{ status: string }>('/health'),
  courses: () => request<Course[]>('/api/courses'),
  questions: (courseId: string) => request<QuestionSummary[]>(
    '/api/courses/' + encodeURIComponent(courseId) + '/questions',
  ),
  report: (courseId: string) => request<LearningReport>(
    '/api/courses/' + encodeURIComponent(courseId) + '/learning-report',
  ),
  startQuiz: (courseId: string, count: number) => request<QuizSession>(
    '/api/courses/' + encodeURIComponent(courseId) + '/quiz-sessions',
    { method: 'POST', body: JSON.stringify({ count, mode: 'normal' }) },
  ),
  quiz: (sessionId: string) => request<QuizSession>(
    '/api/quiz-sessions/' + encodeURIComponent(sessionId),
  ),
  answer: (quizItemId: string, body: {
    client_request_id: string
    response: string | boolean
    elapsed_ms: number
  }) => request<AnswerFeedback>(
    '/api/quiz-items/' + encodeURIComponent(quizItemId) + '/answer',
    { method: 'POST', body: JSON.stringify(body) },
  ),

  createCourse: (data: {title:string;description?:string|null;code?:string|null}) =>
    request<Course>('/api/courses', {method:'POST',body:JSON.stringify(data)}),
  uploadDocument: (courseId: string, file: File) => {
    const form = new FormData()
    form.append('file',file,file.name)
    return request<DocumentUploadResult>('/api/courses/' + encodeURIComponent(courseId) + '/documents',
      {method:'POST',body:form})
  },
  documentQuality: (docId: string) =>
    request<DocumentQuality>('/api/documents/' + encodeURIComponent(docId) + '/quality'),
  documentChunks: (docId: string, limit = 50, offset = 0) =>
    request<DocumentChunk[]>('/api/documents/' + encodeURIComponent(docId) +
      '/chunks?limit='+encodeURIComponent(String(limit))+'&offset='+encodeURIComponent(String(offset))),
  extractKnowledge: (docId: string, options: {
    page_start:number; page_end:number; include_visual:boolean; provider?: AIProviderId
  }) => request<ExtractKnowledgeResult>('/api/documents/'+encodeURIComponent(docId)+'/extract-knowledge',
       {method:'POST',body:JSON.stringify(options)}),
  documents: (courseId: string) => request<CourseDocument[]>(
    '/api/courses/' + encodeURIComponent(courseId) + '/documents',
  ),
  externalQuestionPrompt: (kpId: string, data: { count: number; question_types: ('single_choice' | 'true_false')[] }) =>
    request<ExternalPrompt>('/api/knowledge-points/' + encodeURIComponent(kpId) + '/external-question-prompt',
      { method: 'POST', body: JSON.stringify(data) }),
  importExternalQuestions: (kpId: string, data: { count: number; question_types: ('single_choice' | 'true_false')[]; response_text: string }) =>
    request<ExternalQuestionImportResult>('/api/knowledge-points/' + encodeURIComponent(kpId) + '/import-external-questions',
      { method: 'POST', body: JSON.stringify(data) }),
  externalKnowledgePrompt: (docId: string, data: { page_start: number; page_end: number }) =>
    request<ExternalPrompt>('/api/documents/' + encodeURIComponent(docId) + '/external-knowledge-prompt',
      { method: 'POST', body: JSON.stringify(data) }),
  importExternalKnowledge: (docId: string, data: { page_start: number; page_end: number; response_text: string }) =>
    request<ExternalKnowledgeImportResult>('/api/documents/' + encodeURIComponent(docId) + '/import-external-knowledge',
      { method: 'POST', body: JSON.stringify(data) }),
  knowledgePoints: (courseId: string) => request<KnowledgePoint[]>(
    '/api/courses/' + encodeURIComponent(courseId) + '/knowledge-points',
  ),
  knowledgePoint: (id: string) => request<KnowledgePointDetail>(
    '/api/knowledge-points/' + encodeURIComponent(id),
  ),
  updateKnowledgePoint: (id: string, update: { canonical_name?: string; summary?: string | null; difficulty?: number | null }) =>
    request<KnowledgePointDetail>('/api/knowledge-points/' + encodeURIComponent(id),
      { method: 'PATCH', body: JSON.stringify(update) }),
  reviewKnowledgePoint: (id: string, decision: 'approved' | 'rejected') =>
    request<{ id: string; review_status: ReviewStatus }>(
      '/api/knowledge-points/' + encodeURIComponent(id) + '/review',
      { method: 'POST', body: JSON.stringify({ decision }) }),
  aiProviders: () => request<AIProviderStatus>('/api/ai/providers'),
  generateQuestions: (id: string, options: { count: number; question_types: ('single_choice' | 'true_false')[]; provider?: AIProviderId }) =>
    request<GenerationResult>('/api/knowledge-points/' + encodeURIComponent(id) + '/generate-questions',
      { method: 'POST', body: JSON.stringify(options) }),
  questionVersion: (id: string) => request<QuestionVersion>(
    '/api/question-versions/' + encodeURIComponent(id),
  ),
  reviewQuestion: (id: string, decision: 'approved' | 'rejected') =>
    request<{ id: string; review_status: ReviewStatus }>(
      '/api/question-versions/' + encodeURIComponent(id) + '/review',
      { method: 'POST', body: JSON.stringify({ decision }) }),
  reviseQuestion: (id: string, update: QuestionRevision) =>
    request<QuestionVersion>('/api/question-versions/' + encodeURIComponent(id) + '/revise',
      { method: 'POST', body: JSON.stringify(update) }),
  questionVersions: (id: string) => request<QuestionVersion[]>(
    '/api/questions/' + encodeURIComponent(id) + '/versions',
  ),
}
