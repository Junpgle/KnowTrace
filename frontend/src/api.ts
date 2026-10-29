/** Typed, same-origin API client. Vite proxies /api to FastAPI during local development. */
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

export function sourcePreviewUrl(source: SourceEvidence): string {
  return base + '/api/documents/' + encodeURIComponent(source.document_id)
    + '/pages/' + source.page_start + '/preview'
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
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
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
      409: '当前操作与已有状态冲突，请刷新后重试。',
      422: '提交的数据不符合要求。',
      503: '后端暂时无法提供服务。',
    } as Record<number, string>)[response.status] || '请求失败，请稍后重试。'
    throw new ApiError(response.status, message)
  }
  return await response.json() as T
}

export const api = {
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
