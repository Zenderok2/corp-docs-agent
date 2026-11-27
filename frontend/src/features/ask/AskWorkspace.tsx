import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ArrowRight, ArrowUp, Check, ChevronDown, CircleHelp, ClipboardCheck,
  FileText, FolderOpen, LibraryBig, ListFilter, LoaderCircle, MessageSquarePlus, ScanText, Search, Sparkles,
} from 'lucide-react'
import { api, describeError, formatDate, formatLocator, formatRevision, post, scopeFields } from '../../shared/api'
import type { Answer, Citation, Question, QuestionContext, Scope, Session } from '../../shared/api'
import { MailThread } from '../documents/MailThread'
import { SourceDialog } from '../sources/SourceDialog'
import { DepartmentComparison } from './DepartmentComparison'
import { PracticeDialog } from './PracticeDialog'

const sampleQuestions = [
  { label: 'Согласование плана проекта', question: 'В какой срок проджект-менеджер согласует план проекта?' },
  { label: 'Подписание договора', question: 'Кто подписывает агентский договор на 400 000 рублей и что проверяет инициатор?' },
  { label: 'Лицензия подрядчика', question: 'Какую лицензию выдают внешнему подрядчику и на какой срок?' },
  { label: 'Передача в службу качества', question: 'Как оформить гостевой пропуск в офис на выходные?' },
]

const outcomeLabels = {
  answered: 'Ответ по регламентам',
  partial: 'Ответ подтверждён частично',
  needs_clarification: 'Нужно уточнение',
  not_found: 'Ответ в регламентах не найден',
  conflict: 'В источниках есть противоречие',
  quality_answer: 'Ответ службы качества',
  out_of_scope: 'Вопрос вне корпоративных документов',
}

const explanations: Record<string, string> = {
  unknown_publication: 'Найдены подходящие положения. Действие редакции на выбранную дату не подтверждено.',
  partial: 'Доступные источники подтверждают часть ответа.',
  conflict: 'Доступные положения дают разные ответы. Для однозначного решения нужна проверка службы качества.',
  clarification_exhausted: 'После уточнений подтверждённого ответа всё ещё недостаточно.',
  no_evidence: 'В доступных источниках не нашлось пункта, который прямо отвечает на вопрос.',
  out_of_scope: 'Агент ищет правила в корпоративных документах. За оперативными данными обратитесь к владельцу процесса.',
}

type Interaction = { id: string }

export function AskWorkspace({ session, activeId, onSelect, onLogin, onLibrary }: {
  session: Session
  activeId: string | null
  onSelect: (id: string) => void
  onLogin: () => void
  onLibrary: () => void
}) {
  const [draft, setDraft] = useState('')
  const [context, setContext] = useState<Scope>({})
  const [asOf, setAsOf] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [current, setCurrent] = useState<Question | null>(null)
  const [followUp, setFollowUp] = useState('')
  const [departmentSelection, setDepartmentSelection] = useState<{ id: string; names: string[] } | null>(null)
  const [sending, setSending] = useState(false)
  const [failure, setFailure] = useState('')
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [practice, setPractice] = useState<Citation | null>(null)

  useEffect(() => {
    if (!activeId) return
    const abort = new AbortController()
    let timer = 0
    let retries = 0
    const poll = async () => {
      try {
        const value = await api<Question>('/questions/' + activeId, { signal: abort.signal })
        setCurrent(value)
        retries = 0
        if (value.status === 'queued' || value.status === 'running') {
          timer = window.setTimeout(poll, 900)
        } else if (value.result && 'outcome' in value.result && value.result.quality_case?.status === 'pending') {
          timer = window.setTimeout(poll, 5000)
        }
      } catch (error) {
        if (!abort.signal.aborted) {
          if (retries++ < 2) timer = window.setTimeout(poll, 1200)
          else setFailure(describeError(error))
        }
      }
    }
    void poll()
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [activeId])

  const displayed = current?.id === activeId ? current : null
  const result = displayed?.result && 'outcome' in displayed.result ? displayed.result : null
  const awaiting = Boolean(activeId && !failure && (!displayed || ['queued', 'running'].includes(displayed.status)))
  const selectedDepartments = departmentSelection?.id === activeId ? departmentSelection?.names ?? [] : []

  async function send(question: string, parent?: string, detail?: QuestionContext) {
    if (!question.trim() || sending || !session.visible_versions) return
    setFailure('')
    setSending(true)
    try {
      const path = parent ? '/questions/' + parent + '/follow-ups' : '/questions'
      const departmentNames = context.department?.split(';').map((value) => value.trim()).filter(Boolean) ?? []
      const request = parent
        ? { answer: question, context: detail ?? {} }
        : { question, context: departmentNames.length > 1
          ? { ...context, department: null, departments: departmentNames }
          : { ...context, department: departmentNames[0] ?? null }, as_of: asOf || undefined }
      const response = await post<Interaction>(path, request, crypto.randomUUID())
      setDraft('')
      setFollowUp('')
      setDepartmentSelection(null)
      onSelect(response.id)
    } catch (error) {
      setFailure(describeError(error))
    } finally {
      setSending(false)
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void send(draft.trim())
  }

  return <div className="ask-layout">
    <section className="ask-main" aria-label="Диалог с агентом">
      {!activeId && <header className="welcome">
        <h1>Поиск по регламентам</h1>
        <p>Задайте вопрос о рабочей ситуации. В ответе будут ссылки на пункты, по которым можно сверить формулировку.</p>
      </header>}

      {displayed && <div className="conversation">
        <div className="question-bubble">
          <span className="speaker">Ваш вопрос</span>
          <p>{displayed.question}</p>
          <span className="tiny">На {formatDate(displayed.as_of)}</span>
        </div>
        {displayed.turns.map((turn, index) => <div className="question-bubble" key={index}>
          <span className="speaker">Уточнение</span><p>{turn}</p>
        </div>)}
        {displayed.status === 'failed' && displayed.result && 'error_code' in displayed.result && (
          <p className="alert error" role="alert">{describeError(displayed.result.error_code)}</p>
        )}
        {result && <AnswerPanel answer={result} openSource={setSourceId} />}
        {Boolean(result?.citations.length) && <a className="mobile-evidence-link" href="#answer-sources">
          <LibraryBig size={17} /> Источники ответа <ArrowRight size={16} />
        </a>}
        {result?.clarification && <div className="clarification">
          <div className="clarification-title"><CircleHelp size={19} /><h3>{result.clarification.question}</h3></div>
          {result.clarification.field === 'department' && result.clarification.options.length > 0 ? <div className="department-options">
            <fieldset>
              <legend>Выберите одно или несколько подразделений</legend>
              <div className="option-list">{result.clarification.options.map((option) => <label className="pill-button" key={option}>
                <input type="checkbox" checked={selectedDepartments.includes(option)}
                  disabled={sending || (selectedDepartments.length === 5 && !selectedDepartments.includes(option))}
                  onChange={(event) => setDepartmentSelection({ id: displayed.id, names: event.target.checked
                    ? [...selectedDepartments, option] : selectedDepartments.filter((name) => name !== option) })} />{option}
              </label>)}</div>
            </fieldset>
            <div className="option-list">
              <button type="button" className="button primary" disabled={sending || !selectedDepartments.length} onClick={() => void send(
                selectedDepartments.join(', '), displayed.id, { departments: selectedDepartments },
              )}>Продолжить <ArrowRight size={15} /></button>
              <button type="button" className="button text-button" disabled={sending} onClick={() => void send(
                'Подразделение пока неизвестно', displayed.id,
              )}>Не знаю подразделение</button>
            </div>
          </div> : result.clarification.options.length > 0 && <div className="option-list">
            {result.clarification.options.map((option) => {
              const field = result.clarification?.field
              return <button type="button" className="pill-button" key={option} disabled={sending} onClick={() => void send(
                option, displayed.id, field ? { [field]: option } : {},
              )}>{option}<ArrowRight size={15} /></button>
            })}
          </div>}
          <form className="follow-up-form" onSubmit={(event) => {
            event.preventDefault()
            const field = result.clarification?.field
            const value = followUp.trim()
            void send(value, displayed.id, field && field !== 'department' ? { [field]: value } : {})
          }}>
            <input aria-label="Ответ на уточняющий вопрос" placeholder="Введите уточнение" required maxLength={result.clarification.field ? 160 : 800} value={followUp} onChange={(event) => setFollowUp(event.target.value)} />
            <button className="button primary" type="submit" disabled={sending || !followUp.trim()} aria-label="Отправить уточнение"><ArrowUp size={19} /></button>
          </form>
        </div>}
      </div>}

      {awaiting && <div className="pending-box" role="status">
        <span className="square-icon"><LoaderCircle className="spin" size={19} /></span>
        <div><strong>{displayed?.status === 'running' ? 'Агент сверяет источники' : 'Вопрос принят'}</strong>
          <p>{displayed?.status === 'running' ? 'Ищем основания и проверяем цитаты.' : 'Вопрос появится здесь, когда начнётся обработка.'}</p>
        </div>
      </div>}
      {failure && <p className="alert error" role="alert">{failure}</p>}

      {!session.visible_versions ? (
        <div className="card no-access">
          <FileText size={20} />
          <div><h3>В этом сеансе нет доступных документов</h3>
            <p>{session.staff_login_available ? 'Войдите с кодом участника, чтобы открыть рабочий корпус.' : 'Корпус появится после импорта.'}</p>
          </div>
          {session.staff_login_available && <button className="button primary" onClick={onLogin}>Войти</button>}
        </div>
      ) : !awaiting && !result?.clarification && (
        <form className="composer card" onSubmit={submit}>
          <label htmlFor="question-box">{activeId ? 'Следующий вопрос' : 'Ваш вопрос'}</label>
          <textarea id="question-box" rows={3} value={draft} placeholder="Например, кто подписывает агентский договор?" required minLength={4} maxLength={800}
            aria-keyshortcuts="Control+Enter Meta+Enter"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && draft.trim().length >= 4) {
                event.preventDefault()
                void send(draft.trim())
              }
            }}
          />
          {expanded && <div className="context-fields">
            {scopeFields.map(({ key, label }) => <label key={key}>{label}<input type="text" value={context[key] ?? ''} maxLength={160}
              placeholder={key === 'department' ? 'Можно через точку с запятой' : undefined}
              onChange={(event) => setContext({ ...context, [key]: event.target.value || null })} /></label>)}
            <label>Дата применимости<input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label>
          </div>}
          <div className="composer-actions">
            <button type="button" className="button text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
              <ListFilter size={17} /> Указать контекст <ChevronDown size={14} className={expanded ? 'flipped' : ''} />
            </button>
            <div className="composer-submit">
              <kbd>Ctrl + Enter</kbd>
              <button type="submit" className="button primary" disabled={sending || draft.trim().length < 4}>
                {sending ? 'Отправляем…' : 'Спросить'}<ArrowUp size={17} />
              </button>
            </div>
          </div>
        </form>
      )}
      {!activeId && session.role === 'demo' && session.visible_versions > 0 && <div className="examples">
        <h2>Примеры вопросов</h2>
        <div className="example-grid">
          {sampleQuestions.map((example) => <button type="button" className="example" key={example.label} onClick={() => {
            setDraft(example.question)
            document.getElementById('question-box')?.focus()
          }}><span>{example.label}</span><ArrowRight size={16} /></button>)}
        </div>
      </div>}
    </section>

    <aside className="evidence-rail" id="answer-sources" aria-label="Основания ответа">
      <div className="aside-heading"><div><LibraryBig size={19} /><h2>Источники</h2></div>
        {result && result.citations.length > 0 && <span className="number-label">{result.citations.length}</span>}
      </div>
      {result && result.citations.length > 0 ? <div className="evidence-list">
        {result.citations.map((citation, index) => <div className="evidence-card card" key={citation.id}>
          <button type="button" className="source-link" onClick={() => setSourceId(citation.id)}>
            <span className="source-number">{index + 1}</span>
            <span><strong>{citation.title}</strong><small>{formatLocator(citation.locator)} · {formatRevision(citation)}</small></span>
            <ArrowRight size={16} className="source-arrow" />
          </button>
          <blockquote>{citation.quote}</blockquote>
          <button type="button" className="button text-button practice-link" onClick={() => setPractice(citation)}>
            <MessageSquarePlus size={15} /> Добавить пример применения
          </button>
        </div>)}
      </div> : <>
        <div className="evidence-empty">
          <span className="evidence-illustration"><ScanText size={23} strokeWidth={1.55} /></span>
          <p>{result ? 'К этому ответу нет цитат.' : 'Здесь появятся цитаты из документов, которые подтверждают ответ.'}</p>
        </div>
        {session.visible_versions > 0 && <button className="catalog-link" type="button" onClick={onLibrary}>
          <FolderOpen size={18} />
          <span><strong>Открыть каталог</strong><small>Доступно редакций · {session.visible_versions}</small></span>
          <ArrowRight size={16} />
        </button>}
      </>}
      {result && <Trace answer={result} />}
    </aside>
    {sourceId && <SourceDialog key={sourceId} id={sourceId} onClose={() => setSourceId(null)} />}
    {practice && displayed && <PracticeDialog citation={practice} questionId={displayed.id} onClose={() => setPractice(null)} />}
  </div>
}

function AnswerPanel({ answer, openSource }: { answer: Answer; openSource: (id: string) => void }) {
  const positive = answer.outcome === 'answered' || answer.outcome === 'quality_answer'
  const comparison = answer.department_comparison ?? []
  const comparedIds = new Set(comparison.map((row) => row.citation_id))
  const groupedIds = new Set(answer.departments.flatMap((department) => department.citation_ids))
  const ungrouped = answer.citations.filter((citation) => !groupedIds.has(citation.id))
  const departmentAnswers = answer.departments.map(({ name, citation_ids }) => ({
    name,
    citations: answer.citations.filter(({ id }) => citation_ids.includes(id) && !comparedIds.has(id)),
  })).filter(({ citations }) => comparison.length === 0 || citations.length > 0)
  function evidenceLines(citations: Citation[]) {
    if (!citations.length) return null
    const available = new Set(citations.map((citation) => citation.id))
    const findings = answer.findings.filter((finding) => finding.citation_ids.every((id) => available.has(id)))
    const summarized = new Set(findings.flatMap((finding) => finding.citation_ids))
    const lines = [
      ...findings,
      ...citations.filter((citation) => !summarized.has(citation.id)).map((citation) => ({
        text: citation.locator.type === 'docx_table_cell' && citation.locator.column_header
          ? `${citation.locator.column_header} — ${citation.quote}` : citation.quote,
        citation_ids: [citation.id],
      })),
    ]
    return <ol className="answer-quotes">{lines.map(({ text, citation_ids }, index) => <li key={index}>
      <p>{text}</p>
      <span className="inline-sources">{citation_ids.map((id) => {
        const number = answer.citations.findIndex((citation) => citation.id === id) + 1
        return <button key={id} type="button" className="inline-source" onClick={() => openSource(id)}
          aria-label={'Открыть источник ' + number}>{number}</button>
      })}</span>
    </li>)}</ol>
  }
  return <article className="answer-panel card">
    <div className="answer-heading">
      <span className={positive ? 'square-icon' : 'square-icon amber'}>{positive ? <Check size={21} /> : <CircleHelp size={20} />}</span>
      <div><span className="speaker">Агент документов</span><h2>{outcomeLabels[answer.outcome]}</h2></div>
    </div>
    {explanations[answer.message_code] && <p className="answer-explanation">{explanations[answer.message_code]}</p>}
    {answer.corpus_changed && <p className="alert neutral">После ответа корпус обновился. Проверьте актуальность новым вопросом.</p>}
    {answer.outcome === 'answered' && comparison.length === 0 && <p className="answer-explanation">
      {answer.findings.length ? 'Краткий ответ' : 'Выдержки из источников'}
    </p>}
    {comparison.length > 0 && <DepartmentComparison rows={comparison} citations={answer.citations} openSource={openSource} />}
    {departmentAnswers.length > 0 && <div className="department-answers">
      {answer.clarification?.field === 'department' && <p className="answer-explanation">Найдены возможные положения для разных подразделений.</p>}
      {departmentAnswers.map(({ name, citations }) => <section key={name}>
        <h3>{name}</h3>
        {evidenceLines(citations) ?? <p>Подтверждённые положения не найдены.</p>}
      </section>)}
    </div>}
    {evidenceLines(ungrouped)}
    {answer.quality_case?.status === 'pending' && <p className="review-status"><ClipboardCheck size={17} /> Запрос отправлен службе качества. Копия в тестовой почте.</p>}
    {answer.quality_case?.mail && <MailThread mail={answer.quality_case.mail} />}
    {answer.quality_answer && <div className="knowledge-answer">
      {answer.outcome !== 'quality_answer' && <strong>Ответ службы качества</strong>}
      <p>{answer.quality_answer.answer}</p>
      <small>Проверено службой качества · {formatDate(answer.quality_answer.reviewed_at)}</small>
    </div>}
    {answer.practices.length > 0 && <div className="approved-practices">
      <h3>Практика применения</h3>
      {answer.practices.map((entry) => <div key={entry.id}>
        <strong>{entry.situation}</strong><p>{entry.action_taken}</p>
      </div>)}
    </div>}
  </article>
}

function Trace({ answer }: { answer: Answer }) {
  const search = answer.trace.find((entry) => entry.step === 'retrieval')
  return <details className="trace card">
    <summary><Search size={16} /> Как искал агент <ChevronDown size={15} /></summary>
    <ul>
      <li>Поиск по словам · {search?.lexical_hits ?? 0} фрагментов</li>
      {search?.vector_state === 'ready' && <li>Поиск по смыслу · {search.vector_hits} фрагментов</li>}
      {search?.vector_state && search.vector_state !== 'ready' && <li>Семантический индекс недоступен</li>}
      {Boolean(search?.related_hits) && <li>Связанные пункты · {search?.related_hits}</li>}
      {answer.usage.chat_model && <li><Sparkles size={13} /> Локальная модель {answer.usage.chat_model}</li>}
      <li>Проверено цитат · {answer.citations.length}</li>
      <li>Время обработки · {(answer.usage.latency_ms / 1000).toFixed(1)} с</li>
    </ul>
  </details>
}
