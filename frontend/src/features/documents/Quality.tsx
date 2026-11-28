import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, CornerUpLeft, FileCheck2, LoaderCircle, Mail } from 'lucide-react'
import { api, describeError, formatDate, formatLocator, formatTimestamp, scopeFields } from '../../shared/api'
import type { QualityCase } from '../../shared/api'
import { SourceDialog } from '../sources/SourceDialog'
import { MailThread } from './MailThread'
import type { QualityNotice, QualityTab, useQualityQueue } from './useQualityQueue'

const caseStatuses = { pending: 'На проверке', published: 'Ответ опубликован', dismissed: 'Закрыт', withdrawn: 'Отозван' }
const caseColors = { pending: 'amber', published: 'published', dismissed: 'withdrawn', withdrawn: 'withdrawn' }
const practiceStatuses = { pending_review: 'На проверке', approved: 'Одобрен', dismissed: 'Отклонён' }
const caseTabs = [
  { id: 'pending', label: 'Входящие письма', empty: 'Вопросов на проверку пока нет.' },
  { id: 'reviewed', label: 'Проверенные', empty: 'Проверенных обращений пока нет.' },
  { id: 'withdrawn', label: 'Отозванные', empty: 'Отозванных ответов пока нет.' },
] as const

export function Quality({ queue, target }: { queue: ReturnType<typeof useQualityQueue>; target: QualityNotice | null }) {
  const { cases, practices, loading, notices, refresh } = queue
  const [tab, setTab] = useState<QualityTab>(target?.tab ?? 'pending')
  const [failure, setFailure] = useState('')
  const [selectedCase, setSelectedCase] = useState<string | null>(null)
  const [sourceId, setSourceId] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  useEffect(() => {
    if (!target) return
    setTab(target.tab)
    setSelectedCase(null)
    setFailure('')
  }, [target])

  useEffect(() => {
    if (!target || tab !== target.tab) return
    const frame = requestAnimationFrame(() => {
      document.getElementById('quality-item-' + target.id)?.scrollIntoView({ block: 'nearest' })
    })
    return () => cancelAnimationFrame(frame)
  }, [tab, target])

  const updates = new Map(notices.map((notice) => [notice.id, notice]))
  if (target && !updates.has(target.id)) updates.set(target.id, target)

  async function review(id: string, kind: 'cases' | 'practices', payload: object) {
    setWorking(true)
    setFailure('')
    try {
      await api('/quality/' + kind + '/' + id, { method: 'PUT', body: JSON.stringify(payload) })
      refresh(id)
      setSelectedCase(null)
    } catch (error) {
      setFailure(describeError(error))
      refresh()
    } finally {
      setWorking(false)
    }
  }

  const grouped = {
    pending: cases.filter((item) => item.status === 'pending'),
    reviewed: cases.filter((item) => item.status === 'published' || item.status === 'dismissed'),
    withdrawn: cases.filter((item) => item.status === 'withdrawn'),
  }
  const reports = practices.filter((item) => item.status === 'pending_review').length
  return <section className="content-page">
    <header className="page-heading">
      <h1>Служба качества</h1><p>Отвечайте на письма сотрудников и проверяйте, как они применили регламенты.</p>
    </header>
    <div className="tabs" aria-label="Очереди проверки">
      {caseTabs.map(({ id, label }) => <button key={id} type="button" aria-pressed={tab === id}
        onClick={() => { setTab(id); setSelectedCase(null); setFailure('') }}>{label} <span>{grouped[id].length}</span></button>)}
      <button type="button" aria-pressed={tab === 'practices'} onClick={() => setTab('practices')}>Случаи из практики <span>{reports}</span></button>
    </div>
    {queue.failure && <p className="alert error" role="alert">{queue.failure}</p>}
    {failure && (tab === 'practices' || !selectedCase) && <p className="alert error" role="alert">{failure}</p>}
    {loading && <p className="working" role="status"><LoaderCircle size={18} className="spin" /> Загружаем очередь</p>}
    {tab !== 'practices' && <>
      {!loading && !grouped[tab].length && <p className="card list-empty">{caseTabs.find(({ id }) => id === tab)?.empty}</p>}
      {grouped[tab].map((item) => <article key={item.id} id={'quality-item-' + item.id} className="review-card card"
        data-updated={updates.has(item.id) || undefined}>
        <header><div><span className="speaker">{item.created_at
          ? <>Получено <time dateTime={item.created_at}>{formatTimestamp(item.created_at)} МСК</time></>
          : <>На {formatDate(item.as_of)}</>}{item.role === 'demo' ? ' · Демо' : ''}</span>
          {updates.has(item.id) && <span className="review-update">{updates.get(item.id)?.isNew ? 'Новое' : 'Обновлено'}</span>}
          <h3>{item.question}</h3></div>
          <span className={'tag ' + caseColors[item.status]}>{caseStatuses[item.status]}</span>
        </header>
        {item.created_at && <p className="soft">Дата применимости · {formatDate(item.as_of)}</p>}
        {item.reviewed_at && <p className="soft">{caseStatuses[item.status]} <time dateTime={item.reviewed_at}>{formatTimestamp(item.reviewed_at)} МСК</time></p>}
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
            item.id, 'cases', { ...payload, in_reply_to: item.mail.at(-1)?.id },
          )} />
          : <button type="button" className="button secondary" disabled={working} onClick={() => { setSelectedCase(item.id); setFailure('') }}><Mail size={16} />
            {item.status === 'pending' ? 'Ответить в цепочке' : 'Отправить уточнённый ответ'}</button>
        )}
        {item.publication_block && item.status === 'pending' && <button type="button" className="button text-button" disabled={working}
          onClick={() => void review(item.id, 'cases', { decision: 'dismiss', in_reply_to: item.mail.at(-1)?.id })}>Закрыть обращение</button>}
        {item.status === 'published' && <button type="button" className="button text-button" disabled={working}
          onClick={() => void review(item.id, 'cases', { decision: 'withdraw', in_reply_to: item.mail.at(-1)?.id })}><CornerUpLeft size={16} /> Отозвать ответ</button>}
      </article>)}
    </>}
    {tab === 'practices' && <>
      {!loading && !practices.length && <p className="card list-empty">Случаев из практики пока нет.</p>}
      {practices.map((item) => <article key={item.id} id={'quality-item-' + item.id} className="review-card card"
        data-updated={updates.has(item.id) || undefined}>
        <header><div><span className="speaker">Случай из практики</span>
          {updates.has(item.id) && <span className="review-update">{updates.get(item.id)?.isNew ? 'Новое' : 'Обновлено'}</span>}
          <h3>{item.title ?? 'Источник недоступен'}</h3></div>
          <span className={'tag ' + (item.status === 'pending_review' ? 'amber' : item.status === 'approved' ? 'published' : 'withdrawn')}>{practiceStatuses[item.status]}</span>
        </header>
        {item.created_at && <p className="soft">Получено <time dateTime={item.created_at}>{formatTimestamp(item.created_at)} МСК</time></p>}
        {item.reviewed_at && <p className="soft">Проверено <time dateTime={item.reviewed_at}>{formatTimestamp(item.reviewed_at)} МСК</time></p>}
        <p><strong>Ситуация</strong> {item.situation}</p>
        <p><strong>Действие</strong> {item.action_taken}</p>
        {item.title && <button type="button" className="button text-button" onClick={() => setSourceId(item.citation_id)}>Открыть исходную цитату</button>}
        {item.status === 'pending_review' && <div className="row-actions">
          <button type="button" className="button primary" disabled={working || !item.title} onClick={() => void review(item.id, 'practices', { decision: 'approve' })}><Check size={17} /> Одобрить</button>
          <button type="button" className="button secondary" disabled={working} onClick={() => void review(item.id, 'practices', { decision: 'dismiss' })}>Отклонить</button>
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
