export type Role = 'demo' | 'staff' | 'quality'
export type ScopeKey = 'legal_entity' | 'department' | 'product' | 'license_type'
export type Scope = Partial<Record<ScopeKey, string | null>>
export type QuestionContext = Scope & { departments?: string[] }

export const scopeFields: ReadonlyArray<{ key: ScopeKey; label: string }> = [
  { key: 'legal_entity', label: 'Юридическое лицо' },
  { key: 'department', label: 'Подразделение' },
  { key: 'product', label: 'Продукт' },
  { key: 'license_type', label: 'Тип лицензии' },
]

export type Session = {
  role: Role
  staff_login_available: boolean
  quality_login_available: boolean
  visible_versions: number
  build_sha: string
}

export type Locator =
  | { type: 'pdf'; page_number: number; bbox: number[] }
  | { type: 'docx_paragraph'; section_path: string[]; paragraph_index: number; clause_number?: string }
  | { type: 'docx_table_cell'; section_path: string[]; table_path: { table_index?: number; row_index?: number; column_index?: number }[]; paragraph_index: number; clause_number?: string; column_header?: string }
  | { type: 'text'; section_path: string[]; line_start: number; line_end: number }

export type SourceVersion = {
  id: string
  document_id: string
  version_label: string
  revision_label?: string | null
  title: string
  publication_status: 'published' | 'unknown'
  effective_from: string | null
  effective_to: string | null
  publication_confirmed_on: string | null
  scope: Scope
  kind: 'regulations' | 'product_licensing'
}

export type Citation = SourceVersion & {
  document_version_id: string
  chunk_id: string
  block_id: string
  quote: string
  locator: Locator
  source_hash: string
  context?: { before: string; after: string }
}

export type Status = 'queued' | 'running' | 'completed' | 'failed'
export type Outcome = 'answered' | 'partial' | 'needs_clarification' | 'not_found' | 'conflict' | 'quality_answer' | 'out_of_scope'

export type Practice = {
  id: string
  block_id: string
  situation: string
  action_taken: string
  reviewed_at: string
}

export type CaseStatus = 'pending' | 'published' | 'dismissed' | 'withdrawn'

export type QualityMail = {
  id: string
  in_reply_to: string | null
  direction: 'request' | 'reply'
  sender: string
  recipient: string
  cc: string | null
  subject: string
  body: string
  sent_at: string
}

export type DepartmentComparisonRow = {
  department: string
  action: string
  deadline: string
  anchor: string
  condition: string | null
  citation_id: string
}

export type Answer = {
  outcome: Outcome
  message_code: string
  as_of: string
  citations: Citation[]
  departments: Array<{ name: string; citation_ids: string[] }>
  department_comparison?: DepartmentComparisonRow[]
  findings: Array<{ text: string; citation_ids: string[] }>
  corpus_changed: boolean
  clarification: { question: string; field: ScopeKey | null; options: string[] } | null
  quality_case: { id: string; status: CaseStatus; mail: QualityMail[] } | null
  quality_answer: { id: string; question: string; answer: string; reviewed_at: string } | null
  practices: Practice[]
  trace: Array<{
    step: 'retrieval' | 'assessment' | 'verification'
    lexical_hits?: number
    vector_hits?: number
    related_hits?: number
    candidates?: number
    vector_state?: string | null
    duration_ms?: number
    verified_citations?: number
    verified_findings?: number
  }>
  usage: {
    latency_ms: number
    chat_model?: string
    chat_calls: number
    embedding_calls: number
  }
}

export type QuestionSummary = {
  id: string
  parent_id: string | null
  question: string
  status: Status
  created_at: string
}

export type Question = QuestionSummary & {
  turns: string[]
  context: QuestionContext
  as_of: string
  result: Answer | { error_code: string; latency_ms: number } | null
}

export type QualityCase = {
  id: string
  status: CaseStatus
  question: string
  canonical_question: string | null
  answer: string | null
  effective_to: string | null
  aliases: string[]
  reason: string
  publication_block: 'quality_source_conflict' | 'quality_corpus_changed' | null
  as_of: string
  turns: string[]
  context: QuestionContext
  role: Role
  citations: Citation[]
  mail: QualityMail[]
}

export type PracticeQueueItem = {
  id: string
  citation_id: string
  title: string | null
  situation: string
  action_taken: string
  status: 'pending_review' | 'approved' | 'dismissed'
}

export type Page<T> = { items: T[]; next_cursor?: string | number | null }

const problem: Record<string, string> = {
  invalid_request: 'Проверьте обязательные поля и длину текста.',
  session_required: 'Сеанс завершился. Обновите страницу.',
  invalid_access_code: 'Код доступа не подошёл.',
  mail_thread_changed: 'В цепочке появилось новое письмо. Обновите страницу перед ответом.',
  login_throttled: 'Слишком много попыток. Повторите через минуту.',
  empty_corpus: 'В этом сеансе нет доступных документов.',
  queue_full: 'Агент уже обрабатывает несколько вопросов. Повторите чуть позже.',
  source_access_changed: 'Изменились редакции или доступ к источникам. Задайте вопрос заново.',
  model_timeout: 'Модель не успела ответить. Попробуйте сократить вопрос или повторить запрос.',
  model_unavailable: 'Локальная модель временно недоступна.',
  invalid_model_response: 'Ответ модели не удалось обработать. Попробуйте задать вопрос иначе.',
  unverified_evidence: 'Модель предложила фрагмент, который не удалось сверить с источником. Попробуйте уточнить вопрос.',
  question_not_found: 'В этом сеансе вопрос недоступен.',
  citation_not_found: 'Цитата недоступна.',
  document_not_found: 'Документ недоступен.',
  quality_access_required: 'Для этого действия нужен доступ службы качества.',
  quality_answer_conflict: 'Уже опубликован другой ответ с той же формулировкой и областью действия.',
  quality_source_conflict: 'Пункты регламентов расходятся. Согласуйте их применимость с владельцем документа. Публикация приостановлена.',
  quality_corpus_changed: 'После разбора запроса документы обновились. Для публикации нужен новый вопрос по текущему корпусу.',
  quality_deadline_changed: 'В ответе изменён срок из найденного пункта. Откройте цитату и сверьте срок с регламентом.',
  invalid_effective_date: 'Дата окончания не может предшествовать дате запроса.',
  practice_already_submitted: 'По этой цитате вы уже отправили пример.',
}

export function describeError(error: unknown): string {
  const code = error instanceof ApiError ? error.code : typeof error === 'string' ? error : 'network'
  return problem[code] ?? 'Не удалось выполнить запрос. Попробуйте ещё раз.'
}

class ApiError extends Error {
  constructor(readonly code: string) {
    super(code)
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null) as { detail?: string } | null
    throw new ApiError(error?.detail ?? 'request_failed')
  }
  return response.json() as Promise<T>
}

export function post<T>(path: string, data: unknown, idempotencyKey?: string): Promise<T> {
  return api<T>(path, {
    method: 'POST', body: JSON.stringify(data),
    headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
  })
}

export function formatLocator(locator: Locator): string {
  if (locator.type === 'pdf') return 'Страница ' + locator.page_number
  if (locator.type === 'text') {
    return locator.line_start === locator.line_end
      ? 'Строка ' + locator.line_start
      : `Строки ${locator.line_start}–${locator.line_end}`
  }
  const clause = locator.clause_number ? `Пункт ${locator.clause_number} · ` : ''
  if (locator.type === 'docx_paragraph') return clause + 'Абзац ' + locator.paragraph_index
  const table = locator.table_path.map((step) => step.table_index
    ? 'Таблица ' + step.table_index
    : `строка ${step.row_index}, столбец ${step.column_index}`,
  ).concat('абзац ' + locator.paragraph_index).join(', ')
  return clause + table + (locator.column_header ? ` · «${locator.column_header}»` : '')
}

export function formatRevision(source: SourceVersion): string {
  return source.revision_label ?? `Редакция ${source.version_label}`
}

export function formatDate(date: string): string {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'Europe/Moscow' }).format(new Date(date))
}
