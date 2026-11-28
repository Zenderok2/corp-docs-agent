import { useState } from 'react'
import type { FormEvent } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { describeError, post } from '../../shared/api'
import type { Citation } from '../../shared/api'
import { Dialog } from '../../shared/Dialog'

export function PracticeDialog({ citation, questionId, onClose }: {
  citation: Citation; questionId: string; onClose: () => void
}) {
  const [situation, setSituation] = useState('')
  const [action, setAction] = useState('')
  const [state, setState] = useState<'edit' | 'submitting' | 'done'>('edit')
  const [failure, setFailure] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setState('submitting')
    setFailure('')
    try {
      await post('/questions/' + questionId + '/practice-reports', {
        citation_id: citation.id, situation, action_taken: action,
      })
      setState('done')
    } catch (error) {
      setFailure(describeError(error))
      setState('edit')
    }
  }

  return <Dialog title="Добавить случай из практики" onClose={onClose}>
    {state === 'done' ? <>
      <p className="alert success"><CheckCircle2 size={20} /> Случай передан на проверку в службу качества.</p>
      <button className="button primary" onClick={onClose}>Готово</button>
    </> : <form className="stack" onSubmit={submit}>
      <blockquote className="form-quote">{citation.quote}</blockquote>
      <p className="soft">После одобрения службой качества случай появится рядом с этой цитатой.</p>
      <label>Ситуация<textarea required minLength={5} maxLength={2000} rows={3} value={situation} onChange={(event) => setSituation(event.target.value)} placeholder="Когда вы применили этот пункт?" /></label>
      <label>Действие<textarea required minLength={5} maxLength={2000} rows={3} value={action} onChange={(event) => setAction(event.target.value)} placeholder="Что вы сделали?" /></label>
      {failure && <p className="alert error" role="alert">{failure}</p>}
      <button className="button primary" disabled={state === 'submitting'} type="submit">
        {state === 'submitting' ? 'Отправляем…' : 'Передать на проверку'}
      </button>
    </form>}
  </Dialog>
}
