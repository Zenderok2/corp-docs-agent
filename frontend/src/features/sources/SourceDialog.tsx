import { useEffect, useState } from 'react'
import { FileSearch, LoaderCircle } from 'lucide-react'
import { api, describeError, formatDate, formatLocator, scopeFields } from '../../shared/api'
import type { Citation } from '../../shared/api'
import { Dialog } from '../../shared/Dialog'

export function SourceDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [source, setSource] = useState<Citation | null>(null)
  const [failure, setFailure] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    api<Citation>('/citations/' + encodeURIComponent(id), { signal: abort.signal }).then(setSource).catch((error: unknown) => {
      if (!abort.signal.aborted) setFailure(describeError(error))
    })
    return () => abort.abort()
  }, [id])

  return (
    <Dialog title="Цитата из источника" onClose={onClose}>
      {failure && <p className="alert error" role="alert">{failure}</p>}
      {!failure && !source && <p className="working"><LoaderCircle className="spin" size={18} /> Загружаем цитату</p>}
      {source && <>
        <div className="source-meta">
          <span className="square-icon"><FileSearch size={20} /></span>
          <div><h3>{source.title}</h3><p>{formatLocator(source.locator)} · Редакция {source.version_label}</p></div>
        </div>
        {'section_path' in source.locator && source.locator.section_path.length > 0 && (
          <p className="soft">{source.locator.section_path.join(' / ')}</p>
        )}
        <div className="source-excerpt">
          {source.context?.before}<mark>{source.quote}</mark>{source.context?.after}
        </div>
        <dl className="metadata-grid">
          <dt>Действует с</dt><dd>{source.effective_from ? formatDate(source.effective_from) : 'Дата не указана'}</dd>
          {source.effective_to && <><dt>Действует по</dt><dd>{formatDate(source.effective_to)}</dd></>}
          {scopeFields.filter(({ key }) => source.scope[key]).map(({ key, label }) => (
            <div className="metadata-row" key={key}><dt>{label}</dt><dd>{source.scope[key]}</dd></div>
          ))}
        </dl>
        <details className="quiet-details">
          <summary>Реквизиты для сверки</summary>
          <dl className="metadata-grid">
            <dt>Редакция</dt><dd>{source.document_version_id}</dd>
            <dt>Блок</dt><dd>{source.block_id}</dd>
            <dt>SHA-256 файла</dt><dd className="hash">{source.source_hash}</dd>
          </dl>
        </details>
      </>}
    </Dialog>
  )
}
