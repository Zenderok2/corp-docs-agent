import { useEffect, useState } from 'react'
import { ArrowRight, FileText, FolderOpen, LoaderCircle } from 'lucide-react'
import { api, describeError, formatDate, formatLocator, scopeFields } from '../../shared/api'
import type { Locator, Page, SourceVersion } from '../../shared/api'
import { Dialog } from '../../shared/Dialog'

type DocumentBlock = { id: string; ordinal: number; text: string; locator: Locator }

export function Documents() {
  const [items, setItems] = useState<SourceVersion[]>([])
  const [cursor, setCursor] = useState<string | number | null>(null)
  const [open, setOpen] = useState<SourceVersion | null>(null)
  const [loading, setLoading] = useState(true)
  const [failure, setFailure] = useState('')

  async function load(after?: string | number, signal?: AbortSignal) {
    setLoading(true)
    setFailure('')
    try {
      const page = await api<Page<SourceVersion>>('/documents?limit=30' + (after ? '&after=' + encodeURIComponent(after) : ''), { signal })
      setItems((previous) => after ? [...previous, ...page.items] : page.items)
      setCursor(page.next_cursor ?? null)
    } catch (error) {
      if (!signal?.aborted) setFailure(describeError(error))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }

  useEffect(() => {
    const abort = new AbortController()
    void load(undefined, abort.signal)
    return () => abort.abort()
  }, [])

  return <section className="content-page">
    <div className="page-heading"><span className="square-icon"><FolderOpen size={22} /></span><div>
      <h1>Документы</h1><p>Редакции, даты применения и исходные пункты доступного корпуса.</p>
    </div></div>
    {failure && <p className="alert error" role="alert">{failure}</p>}
    {loading && !items.length && <p className="working"><LoaderCircle size={18} className="spin" /> Загружаем документы</p>}
    {!loading && !failure && !items.length && <p className="card list-empty">В этом сеансе нет доступных документов.</p>}
    {items.length > 0 && <div className="document-list card">
      {items.map((item) => <button type="button" className="document-row" key={item.id} onClick={() => setOpen(item)}>
        <span className="square-icon"><FileText size={19} /></span>
        <span className="document-name"><strong>{item.title}</strong>
          <small>{item.kind === 'regulations' ? 'Регламенты' : 'Продукты и лицензирование'} · Редакция {item.version_label}</small>
        </span>
        <span className={item.publication_status === 'published' ? 'tag green' : 'tag amber'}>
          {item.publication_status === 'published' ? (item.effective_from ? 'С ' + formatDate(item.effective_from) : 'Опубликован') : 'Статус уточняется'}
        </span>
        <ArrowRight size={17} />
      </button>)}
    </div>}
    {cursor !== null && <button type="button" className="button secondary" disabled={loading} onClick={() => void load(cursor)}>Ещё документы</button>}
    {open && <DocumentDialog key={open.id} version={open} onClose={() => setOpen(null)} />}
  </section>
}

function DocumentDialog({ version, onClose }: { version: SourceVersion; onClose: () => void }) {
  const [blocks, setBlocks] = useState<DocumentBlock[]>([])
  const [after, setAfter] = useState<string | number | null>(0)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState('')

  async function load(cursor: number | string, signal?: AbortSignal) {
    setLoading(true)
    setFailure('')
    try {
      const page = await api<Page<DocumentBlock>>('/document-versions/' + encodeURIComponent(version.id) + '/content?after=' + cursor, { signal })
      setBlocks((previous) => cursor === 0 ? page.items : [...previous, ...page.items])
      setAfter(page.next_cursor ?? null)
    } catch (error) {
      if (!signal?.aborted) setFailure(describeError(error))
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }

  useEffect(() => {
    const abort = new AbortController()
    void load(0, abort.signal)
    return () => abort.abort()
  // The dialog remounts when the selected version changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <Dialog title={version.title} onClose={onClose}>
    <p className="soft">Редакция {version.version_label}
      {version.effective_from && ' · С ' + formatDate(version.effective_from)}
      {version.effective_to && ' по ' + formatDate(version.effective_to)}
    </p>
    {scopeFields.filter(({ key }) => version.scope[key]).map(({ key, label }) => (
      <p className="soft" key={key}>{label} · {version.scope[key]}</p>
    ))}
    {failure && <p className="alert error" role="alert">{failure}</p>}
    {blocks.map((block) => <div key={block.id} className="document-block">
      <small>{formatLocator(block.locator)}</small><p>{block.text}</p>
    </div>)}
    {loading && <p className="working"><LoaderCircle size={18} className="spin" /> Загружаем текст</p>}
    {after !== null && !loading && <button type="button" className="button secondary" onClick={() => void load(after)}>Показать следующие пункты</button>}
  </Dialog>
}
