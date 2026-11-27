import { StrictMode, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import {
  BookOpen, ClipboardCheck, Files, History, KeyRound, LoaderCircle, LogOut, Menu,
  MessageSquareText, Plus, X,
} from 'lucide-react'
import { AskWorkspace } from '../features/ask/AskWorkspace'
import { Documents } from '../features/documents/Documents'
import { Quality } from '../features/documents/Quality'
import { api, describeError, post } from '../shared/api'
import type { Page, QuestionSummary, Role, Session } from '../shared/api'
import { Dialog } from '../shared/Dialog'
import './styles.css'

type Section = 'ask' | 'documents' | 'quality'
const labels: Record<Section, string> = { ask: 'Вопрос агенту', documents: 'Документы', quality: 'Служба качества' }
const roles: Record<Role, string> = { demo: 'Гость', staff: 'Участник', quality: 'Служба качества' }

function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [ready, setReady] = useState<boolean | null>(null)
  const [failure, setFailure] = useState('')
  const [section, setSection] = useState<Section>('ask')
  const [history, setHistory] = useState<QuestionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [workspace, setWorkspace] = useState(0)
  const [login, setLogin] = useState<'staff' | 'quality' | null>(null)
  const [navOpen, setNavOpen] = useState(false)

  useEffect(() => {
    const abort = new AbortController()
    api<Session>('/session', { signal: abort.signal }).then(setSession).catch((error: unknown) => {
      if (!abort.signal.aborted) setFailure(describeError(error))
    })
    const health = () => {
      fetch('/health/ready', { signal: abort.signal, cache: 'no-store' }).then((response) => setReady(response.ok)).catch(() => {
        if (!abort.signal.aborted) setReady(false)
      })
    }
    health()
    const timer = window.setInterval(health, 30_000)
    return () => { abort.abort(); window.clearInterval(timer) }
  }, [])

  useEffect(() => {
    if (!session) return
    const abort = new AbortController()
    api<Page<QuestionSummary>>('/questions?limit=7', { signal: abort.signal }).then((page) => setHistory(page.items)).catch(() => {
      if (!abort.signal.aborted) setHistory([])
    })
    return () => abort.abort()
  }, [session, activeId])

  function navigate(next: Section) {
    if (next === 'quality' && session?.role !== 'quality') {
      setLogin('quality')
    } else {
      setSection(next)
    }
    setNavOpen(false)
  }

  function startQuestion() {
    setSection('ask')
    setActiveId(null)
    setWorkspace((value) => value + 1)
    setNavOpen(false)
  }

  function applySession(value: Session) {
    setSession(value)
    setHistory([])
    setActiveId(null)
    setWorkspace((version) => version + 1)
    setSection(value.role === 'quality' ? 'quality' : 'ask')
    setFailure('')
    setLogin(null)
  }

  return <div className="app-shell">
    {navOpen && <button type="button" className="navigation-scrim" aria-label="Закрыть навигацию" onClick={() => setNavOpen(false)} />}
    <aside className={'sidebar' + (navOpen ? ' open' : '')}>
      <button type="button" className="brand" onClick={startQuestion}>
        <span><BookOpen size={21} strokeWidth={1.65} /></span>
        <strong>Агент документов</strong>
      </button>
      <button type="button" className="new-question" onClick={startQuestion}><Plus size={17} /> Новый вопрос</button>
      <nav aria-label="Основная навигация">
        <button type="button" aria-current={section === 'ask' ? 'page' : undefined} onClick={() => navigate('ask')}><MessageSquareText size={18} />{labels.ask}</button>
        <button type="button" aria-current={section === 'documents' ? 'page' : undefined} onClick={() => navigate('documents')}><Files size={18} />{labels.documents}</button>
        <button type="button" aria-current={section === 'quality' ? 'page' : undefined} onClick={() => navigate('quality')} disabled={!session?.quality_login_available}><ClipboardCheck size={18} />{labels.quality}</button>
      </nav>
      {history.length > 0 && <div className="recent">
        <h2><History size={15} /> Недавние вопросы</h2>
        {history.map((item) => <button type="button" key={item.id} className={activeId === item.id && section === 'ask' ? 'recent-active' : ''}
          onClick={() => {
            setActiveId(item.id)
            setSection('ask')
            setWorkspace((version) => version + 1)
            setNavOpen(false)
          }} title={item.question}>{item.question}</button>)}
      </div>}
      {session && <footer className="sidebar-footer">
        <span className="person-symbol">{session.role === 'quality' ? <ClipboardCheck size={19} /> : <BookOpen size={18} />}</span>
        <div><strong>{roles[session.role]}</strong><small>{session.role === 'demo' ? 'Демонстрационный корпус' : 'Рабочий контур'}</small></div>
        {session.role !== 'demo' && <button type="button" className="icon-button" title="Выйти" aria-label="Выйти"
          onClick={() => void api<Session>('/session', { method: 'DELETE' }).then(applySession).catch((error: unknown) => setFailure(describeError(error)))}><LogOut size={17} /></button>}
      </footer>}
    </aside>

    <main className="main-shell">
      <header className="topbar">
        <div className="topbar-title">
          <button type="button" className="icon-button mobile-menu" aria-label={navOpen ? 'Закрыть меню' : 'Открыть меню'} aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>
            {navOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
          <strong>{labels[section]}</strong>
        </div>
        <div className="topbar-actions">
          {ready !== null && <span className={'status-pill ' + (ready ? 'status-ready' : 'status-waiting')}><i />
            {ready ? 'Агент доступен' : 'Агент недоступен'}
          </span>}
          {session?.role === 'demo' && session.staff_login_available && <button type="button" className="button topbar-login" onClick={() => setLogin('staff')}>
            <KeyRound size={16} /> Войти
          </button>}
        </div>
      </header>
      {failure && <p className="alert error app-error" role="alert">{failure}</p>}
      {!session && !failure && <p className="working app-error" role="status"><LoaderCircle size={19} className="spin" /> Открываем рабочую область</p>}
      {session && section === 'ask' && <AskWorkspace key={workspace} session={session} activeId={activeId} onSelect={setActiveId}
        onLogin={() => setLogin('staff')} onLibrary={() => navigate('documents')} />}
      {session && section === 'documents' && <Documents key={session.role} />}
      {session?.role === 'quality' && section === 'quality' && <Quality />}
    </main>
    {login && <LoginDialog key={login} role={login} onClose={() => setLogin(null)} onLogin={applySession} />}
  </div>
}

function LoginDialog({ role, onLogin, onClose }: {
  role: 'staff' | 'quality'; onLogin: (session: Session) => void; onClose: () => void
}) {
  const [code, setCode] = useState('')
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setFailure('')
    try {
      onLogin(await post<Session>('/session', { role, code }))
    } catch (error) {
      setFailure(describeError(error))
      setBusy(false)
    }
  }
  return <Dialog title={role === 'quality' ? 'Вход для службы качества' : 'Вход участника'} onClose={onClose}>
    <form className="stack" onSubmit={submit}>
      <label>Код доступа<input type="password" autoComplete="current-password" required maxLength={256} value={code} onChange={(event) => setCode(event.target.value)} /></label>
      {failure && <p className="alert error" role="alert">{failure}</p>}
      <button type="submit" className="button primary" disabled={busy || !code}>{busy ? 'Проверяем…' : 'Войти'}</button>
    </form>
  </Dialog>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
