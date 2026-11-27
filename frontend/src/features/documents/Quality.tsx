import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, ClipboardCheck, CornerUpLeft, FileCheck2, LoaderCircle, Mail } from 'lucide-react'
import { api, describeError, formatDate, formatLocator, scopeFields } from '../../shared/api'
import type { Page, PracticeQueueItem, QualityCase } from '../../shared/api'
import { SourceDialog } from '../sources/SourceDialog'
import { MailThread } from './MailThread'

const caseStatuses = { pending: 'На проверке', published: 'Опубликован', dismissed: 'Закрыт', withdrawn: 'Отозван' }
const practiceStatuses = { pending_review: 'На проверке', approved: 'Одобрен', dismissed: 'Отклонён' }

export function Quality() {
  const [cases, setCases] = useState<QualityCase[]>([])
  const [practices, setPractices] = useState<PracticeQueueItem[]>([])
  const [tab, setTab] = useState<'cases' | 'practices'>('cases')
  const [loading, setLoading] = useState(true)
  const [failure, setFailure] = useState('')
  const [selectedCase, setSelectedCase] = useState<string | null>(null)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  async function load(signal?: AbortSignal) {
    try {
      const [casePage, practicePage] = await Promise.all([
        api<Page<QualityCase>>('/quality/cases', { signal }),
        api<Page<PracticeQueueItem>>('/quality/practices', { signal }),
      ])
      setCases(casePage.items)
      setPractices(practicePage.items)
    } catch (error) {
      if (!signal?.aborted) setFailure(describeError(error))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }

  useEffect(() => {
    const abort = new AbortController()
    void load(abort.signal)
    return () => abort.abort()
  }, [])

  async function review(path: string, payload: object) {
    setWorking(true)
    setFailure('')
    try {
      await api(path, { method: 'PUT', body: JSON.stringify(payload) })
      await load()
      setSelectedCase(null)
    } catch (error) {
      setFailure(describeError(error))
    } finally {
      setWorking(false)
    }
  }

  const pending = cases.filter((item) => item.status === 'pending').length
  const reports = practices.filter((item) => item.status === 'pending_review').length
  return <section className="content-page">
    <div className="page-heading"><span className="square-icon"><ClipboardCheck size={22} /></span><div>
      <h1>Служба качества</h1><p>Отвечайте на письма сотрудников и проверяйте примеры применения регламентов.</p>
    </div></div>
    <div className="tabs" aria-label="Очереди проверки">
      <button type="button" aria-pressed={tab === 'cases'} onClick={() => setTab('cases')}>Входящие письма <span>{pending}</span></button>
      <button type="button" aria-pressed={tab === 'practices'} onClick={() => setTab('practices')}>Примеры применения <span>{reports}</span></button>
    </div>
    {failure && (tab !== 'cases' || !selectedCase) && <p className="alert error" role="alert">{failure}</p>}
    {loading && <p className="working" role="status"><LoaderCircle size={18} className="spin" /> Загружаем очередь</p>}
    {tab === 'cases' && <>
      {!loading && !cases.length && <p className="card list-empty">Вопросов на проверку пока нет.</p>}
      {cases.map((item) => <article key={item.id} className="review-card card">
        <header><div><span className="speaker">{formatDate(item.as_of)}{item.role === 'demo' ? ' · Демо' : ''}</span><h3>{item.question}</h3></div>
          <span className={item.status === 'pending' ? 'tag amber' : 'tag green'}>{caseStatuses[item.status]}</span>
        </header>
        {item.turns.map((turn, index) => <p key={index} className="soft">Уточнение · {turn}</p>)}
        {scopeFields.filter(({ key }) => item.context[key]).map(({ key, label }) => (
          <p className="soft" key={key}>{label} · {item.context[key]}</p>
        ))}
        {Boolean(item.context.departments?.length) && <p className="soft">Подразделения · {item.context.departments?.join(', ')}</p>}
        {item.citations.map((citation) => <button type="button" className="button text-button review-citation" key={citation.id} onClick={() => setSourceId(citation.id)}>
          <FileCheck2 size={16} />{citation.title} · {formatLocator(citation.locator)}
        </button>)}
        {item.publication_block && (item.status === 'pending' || item.status === 'published') &&
          <p className="alert neutral">{describeError(item.publication_block)}</p>}
        {item.reason === 'unknown_publication' && !item.publication_block && item.status === 'pending' &&
          <p className="alert neutral">Уточните действие редакции на дату вопроса перед публикацией.</p>}
        <MailThread mail={item.mail} expanded={item.status === 'pending'} />
        {!item.mail.length && item.answer && item.status === 'published' && !item.publication_block && <p className="published-text">{item.answer}</p>}
        {!item.publication_block && (item.status === 'pending' || (item.status === 'published' && item.mail.length > 0)) && (selectedCase === item.id
          ? <ReviewForm item={item} busy={working} failure={failure} onCancel={() => { setSelectedCase(null); setFailure('') }} onReview={(payload) => void review(
            '/quality/cases/' + item.id, { ...payload, in_reply_to: item.mail.at(-1)?.id },
          )} />
          : <button type="button" className="button secondary" disabled={working} onClick={() => { setSelectedCase(item.id); setFailure('') }}><Mail size={16} />
            {item.status === 'pending' ? 'Ответить в цепочке' : 'Отправить уточнённый ответ'}</button>
        )}
        {item.publication_block && item.status === 'pending' && <button type="button" className="button text-button" disabled={working}
          onClick={() => void review('/quality/cases/' + item.id, { decision: 'dismiss', in_reply_to: item.mail.at(-1)?.id })}>Закрыть обращение</button>}
        {item.status === 'published' && <button type="button" className="button text-button" disabled={working}
          onClick={() => void review('/quality/cases/' + item.id, { decision: 'withdraw', in_reply_to: item.mail.at(-1)?.id })}><CornerUpLeft size={16} /> Отозвать ответ</button>}
      </article>)}
    </>}
    {tab === 'practices' && <>
      {!loading && !practices.length && <p className="card list-empty">Примеров на проверку пока нет.</p>}
      {practices.map((item) => <article key={item.id} className="review-card card">
        <header><div><span className="speaker">Практика применения</span><h3>{item.title ?? 'Источник недоступен'}</h3></div>
          <span className={item.status === 'pending_review' ? 'tag amber' : 'tag green'}>{practiceStatuses[item.status]}</span>
        </header>
        <p><strong>Ситуация</strong> {item.situation}</p>
        <p><strong>Действие</strong> {item.action_taken}</p>
        {item.title && <button type="button" className="button text-button" onClick={() => setSourceId(item.citation_id)}>Открыть исходную цитату</button>}
        {item.status === 'pending_review' && <div className="row-actions">
          <button type="button" className="button primary" disabled={working || !item.title} onClick={() => void review('/quality/practices/' + item.id, { decision: 'approve' })}><Check size={17} /> Одобрить</button>
          <button type="button" className="button secondary" disabled={working} onClick={() => void review('/quality/practices/' + item.id, { decision: 'dismiss' })}>Отклонить</button>
        </div>}
      </article>)}
    </>}
    {sourceId && <SourceDialog key={sourceId} id={sourceId} onClose={() => setSourceId(null)} />}
  </section>
}

function ReviewForm({ item, busy, failure, onReview, onCancel }: {
  item: QualityCase; busy: boolean; failure: string; onReview: (payload: object) => void; onCancel: () => void
}) {
  const [answer, setAnswer] = useState(item.answer ?? '')
  const [question, setQuestion] = useState(item.canonical_question ?? item.question)
  const [aliases, setAliases] = useState(item.aliases.join('\n'))
  const [ends, setEnds] = useState(item.effective_to ?? '')

  function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onReview({
      decision: 'publish', answer, question, aliases: aliases.split('\n').map((value) => value.trim()).filter(Boolean).slice(0, 5),
      effective_to: ends || null,
    })
  }

  return <form className="stack review-form" onSubmit={publish}>
    <label>Вопрос для базы знаний<input required minLength={4} maxLength={800} value={question} onChange={(event) => setQuestion(event.target.value)} /></label>
    <label>Проверенный ответ<textarea required minLength={5} maxLength={2000} rows={4} value={answer} onChange={(event) => setAnswer(event.target.value)} /></label>
    <p className="soft">Действующий регламент имеет приоритет. Опишите, как применить его в этой ситуации, или дополните сведения по пробелу.</p>
    {failure && <p className="alert error" role="alert">{failure}</p>}
    <details className="quiet-details">
      <summary>Формулировки и срок действия</summary>
      <div className="stack">
        <label title="До 5 формулировок, по одной в строке">Другие формулировки<textarea rows={3} maxLength={4000} value={aliases} onChange={(event) => setAliases(event.target.value)} /></label>
        <label>Действует по<input type="date" min={item.as_of} value={ends} onChange={(event) => setEnds(event.target.value)} /></label>
      </div>
    </details>
    <div className="row-actions">
      <button className="button primary" type="submit" disabled={busy}>{busy ? 'Сохраняем…' : 'Ответить и обновить базу знаний'}</button>
      <button className="button secondary" type="button" disabled={busy} onClick={onCancel}>Отмена</button>
      {item.status === 'pending' && <button className="button text-button" type="button" disabled={busy} onClick={() => onReview({ decision: 'dismiss' })}>Закрыть запрос</button>}
    </div>
  </form>
}
