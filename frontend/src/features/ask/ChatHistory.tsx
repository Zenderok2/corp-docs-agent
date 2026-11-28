import { useState } from 'react'
import type { FormEvent } from 'react'
import { Ellipsis, History, MessageCircle, Pencil, Trash2 } from 'lucide-react'
import { api, describeError } from '../../shared/api'
import type { ChatSummary } from '../../shared/api'
import { Dialog } from '../../shared/Dialog'

type ChatAction = { chat: ChatSummary; kind: 'rename' | 'delete' }

export function ChatHistory({ chats, activeId, onOpen, onRename, onDelete }: {
  chats: ChatSummary[]
  activeId: string | null
  onOpen: (id: string) => void
  onRename: (chat: ChatSummary) => void
  onDelete: (id: string) => void
}) {
  const [menuId, setMenuId] = useState<string | null>(null)
  const [action, setAction] = useState<ChatAction | null>(null)

  function choose(chat: ChatSummary, kind: ChatAction['kind']) {
    setMenuId(null)
    setAction({ chat, kind })
  }

  return <>
    <nav className="recent" aria-labelledby="chat-history">
      <h2 id="chat-history"><History size={15} /> История чатов</h2>
      {chats.length === 0 && <p>Здесь появятся ваши чаты</p>}
      {chats.map((chat) => <div className="history-row" key={chat.id}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setMenuId((id) => id === chat.id ? null : id)
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Escape' || menuId !== chat.id) return
          event.stopPropagation()
          event.currentTarget.querySelector<HTMLButtonElement>('.history-more')?.focus()
          setMenuId(null)
        }}>
        <button type="button" className="history-open" aria-current={activeId === chat.id ? 'true' : undefined}
          onClick={() => { setMenuId(null); onOpen(chat.id) }} title={chat.title}><MessageCircle size={15} /><span>{chat.title}</span></button>
        <button type="button" className="history-more" aria-label={`Действия для чата «${chat.title}»`}
          title="Действия с чатом" aria-expanded={menuId === chat.id} aria-controls={'chat-actions-' + chat.id}
          onClick={() => setMenuId(menuId === chat.id ? null : chat.id)}><Ellipsis size={17} /></button>
        {menuId === chat.id && <div className="history-actions card" id={'chat-actions-' + chat.id}>
          <button type="button" onClick={() => choose(chat, 'rename')}><Pencil size={15} /> Переименовать</button>
          <button type="button" className="history-delete" onClick={() => choose(chat, 'delete')}><Trash2 size={15} /> Удалить</button>
        </div>}
      </div>)}
    </nav>
    {action && <ChatActionDialog action={action} onClose={() => setAction(null)} onRename={onRename} onDelete={onDelete} />}
  </>
}

function ChatActionDialog({ action, onClose, onRename, onDelete }: {
  action: ChatAction
  onClose: () => void
  onRename: (chat: ChatSummary) => void
  onDelete: (id: string) => void
}) {
  const { chat, kind } = action
  const deleting = kind === 'delete'
  const [title, setTitle] = useState(chat.title)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || (!deleting && !title.trim())) return
    setBusy(true)
    setFailure('')
    try {
      const path = '/chats/' + chat.id
      if (deleting) {
        await api<void>(path, { method: 'DELETE' })
        onDelete(chat.id)
      } else {
        onRename(await api<ChatSummary>(path, { method: 'PATCH', body: JSON.stringify({ title: title.trim() }) }))
      }
      onClose()
    } catch (error) {
      setFailure(describeError(error))
      setBusy(false)
    }
  }

  return <Dialog title={deleting ? 'Удалить чат' : 'Переименовать чат'} onClose={() => { if (!busy) onClose() }}>
    <form className="stack" onSubmit={submit}>
      {deleting ? <p>Удалить «{chat.title}» из истории?</p> : <label>Название чата
        <input type="text" value={title} required maxLength={160} autoFocus disabled={busy}
          onChange={(event) => setTitle(event.target.value)} />
      </label>}
      {failure && <p className="alert error" role="alert">{failure}</p>}
      <div className="row-actions">
        <button type="button" className="button secondary" disabled={busy} onClick={onClose}>Отмена</button>
        <button type="submit" className={'button ' + (deleting ? 'danger' : 'primary')}
          disabled={busy || (!deleting && !title.trim())}>{deleting ? 'Удалить' : 'Сохранить'}</button>
      </div>
    </form>
  </Dialog>
}
