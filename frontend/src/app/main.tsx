import { StrictMode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import {
  ChevronDown, ClipboardCheck, Files, History, KeyRound, LoaderCircle, LogOut, Menu,
  MessageCircle, MessageSquareText, PanelLeft, PanelsTopLeft, Plus, UserRound, X,
} from 'lucide-react'
import { AskWorkspace } from '../features/ask/AskWorkspace'
import { Documents } from '../features/documents/Documents'
import { Quality } from '../features/documents/Quality'
import { api, describeError, post } from '../shared/api'
import type { Page, QuestionSummary, Role, Session } from '../shared/api'
import { Dialog } from '../shared/Dialog'
import { ThemeToggle, updateThemeColor } from './ThemeToggle'
import './styles.css'
import './layouts.css'
import './workspace.css'

type Section = 'ask' | 'documents' | 'quality'
const labels: Record<Section, string> = { ask: 'Чат с агентом', documents: 'Документы', quality: 'Служба качества' }
const roles: Record<Role, string> = { demo: 'Гость', staff: 'Участник', quality: 'Служба качества' }
const layouts = [
  { id: 'workspace', name: 'Классический' },
  { id: 'focus', name: 'Современный' },
] as const

function findLayout(id: string | null) {
  return layouts.find((layout) => layout.id === id)
}

const initialLayout = findLayout(new URLSearchParams(location.search).get('layout'))?.id ?? 'workspace'

function App() {
  const [layout, setLayout] = useState(initialLayout)
  const [session, setSession] = useState<Session | null>(null)
  const [ready, setReady] = useState<boolean | null>(null)
  const [failure, setFailure] = useState('')
  const [section, setSection] = useState<Section>('ask')
  const [history, setHistory] = useState<QuestionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [workspace, setWorkspace] = useState(0)
  const [login, setLogin] = useState<'staff' | 'quality' | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  const mainRef = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    document.documentElement.dataset.layout = layout
    updateThemeColor()
  }, [layout])

  useEffect(() => {
    mainRef.current?.scrollTo(0, 0)
  }, [section, workspace])

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

  useEffect(() => {
    if (!navOpen) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [navOpen])

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
    setNavOpen(false)
  }

  function changeLayout(id: string) {
    const next = findLayout(id)
    if (!next) return
    const url = new URL(location.href)
    url.searchParams.set('layout', next.id)
    window.history.replaceState(window.history.state, '', url)
    setLayout(next.id)
    setNavOpen(false)
  }

  const navigation = <nav className="primary-nav" aria-label="Основная навигация">
    <button type="button" aria-label={labels.ask} title={labels.ask} aria-current={section === 'ask' ? 'page' : undefined} onClick={() => navigate('ask')}><MessageSquareText size={18} /><span>{labels.ask}</span></button>
    <button type="button" aria-label={labels.documents} title={labels.documents} aria-current={section === 'documents' ? 'page' : undefined} onClick={() => navigate('documents')}><Files size={18} /><span>{labels.documents}</span></button>
    {session?.role === 'quality' && <button type="button" aria-label={labels.quality} title={labels.quality} aria-current={section === 'quality' ? 'page' : undefined} onClick={() => navigate('quality')}><ClipboardCheck size={18} /><span>{labels.quality}</span></button>}
  </nav>
  const brand = <button type="button" className="brand" onClick={startQuestion}>
    <img src="/agent-mark.png" width="36" height="36" alt="" />
    <strong>Агент документов</strong>
  </button>
  const sidebarLabel = layout === 'focus' ? 'Открыть историю чатов' : 'Открыть меню'

  return <div className="app-shell" data-layout={layout}>
    {layout !== 'workspace' && <div className="app-background" aria-hidden="true">
      <span className="scene-curve scene-curve-top" />
      <span className="scene-curve scene-curve-bottom" />
    </div>}
    {navOpen && <button type="button" className="navigation-scrim" aria-label="Закрыть навигацию" onClick={() => setNavOpen(false)} />}
    <header className="topbar">
      <div className="topbar-title">
        <button type="button" className="icon-button sidebar-toggle" aria-label={sidebarLabel} title={sidebarLabel} aria-controls="app-sidebar" aria-expanded={navOpen} onClick={() => setNavOpen(!navOpen)}>
          {layout === 'focus' ? <><PanelLeft size={19} /><span>История</span></> : <Menu size={21} />}
        </button>
        {layout === 'workspace' ? <strong>{labels[section]}</strong> : brand}
        {layout === 'focus' && <button type="button" className="button topbar-action focus-new-chat" aria-label="Новый чат" title="Новый чат" onClick={startQuestion}><Plus size={18} /><span>Новый чат</span></button>}
      </div>
      <div className="topbar-actions">
        {ready !== null && <span className={'status-pill ' + (ready ? 'status-ready' : 'status-waiting')}
          title={ready ? 'Агент доступен' : 'Агент недоступен'}><i /><span>{ready ? 'Агент доступен' : 'Агент недоступен'}</span>
        </span>}
        <label className="layout-control">
          <PanelsTopLeft size={16} aria-hidden="true" />
          <select aria-label="Компоновка сайта" value={layout} onChange={(event) => changeLayout(event.target.value)}>
            {layouts.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </label>
        <ThemeToggle />
        {session && <div className="topbar-account">
          <span className="topbar-role" title={session.role === 'demo' ? 'Демонстрационный корпус' : 'Рабочий контур'}>
            {session.role === 'quality' ? <ClipboardCheck size={16} aria-hidden="true" /> : <UserRound size={16} aria-hidden="true" />}
            <strong>{roles[session.role]}</strong>
          </span>
          {session.role === 'demo'
            ? (session.staff_login_available || session.quality_login_available) && <button type="button" className="button topbar-action topbar-login"
              aria-label="Войти" title="Войти" onClick={() => setLogin(session.staff_login_available ? 'staff' : 'quality')}>
              <KeyRound size={16} aria-hidden="true" /><span>Войти</span>
            </button>
            : <button type="button" className="button topbar-action topbar-logout" title="Выйти" aria-label="Выйти"
              onClick={() => void api<Session>('/session', { method: 'DELETE' }).then(applySession).catch((error: unknown) => setFailure(describeError(error)))}>
              <LogOut size={17} aria-hidden="true" />
            </button>}
        </div>}
      </div>
    </header>

    <aside id="app-sidebar" className={'sidebar' + (navOpen ? ' open' : '')}>
      <div className="sidebar-heading">
        {brand}
        <button type="button" className="icon-button sidebar-close" aria-label="Закрыть меню" onClick={() => setNavOpen(false)}>
          <X size={22} />
        </button>
      </div>
      <button type="button" className="new-question" onClick={startQuestion}><Plus size={19} /> Новый чат</button>
      {layout !== 'focus' && navigation}
      {session && <nav className="recent" aria-labelledby="chat-history">
        <h2 id="chat-history"><History size={15} /> История чатов</h2>
        {history.length === 0 && <p>Здесь появятся ваши чаты</p>}
        {history.map((item) => <button type="button" key={item.id} aria-current={activeId === item.id && section === 'ask' ? 'true' : undefined}
          onClick={() => {
            setActiveId(item.id)
            setSection('ask')
            setWorkspace((version) => version + 1)
            setNavOpen(false)
          }} title={item.question}><MessageCircle size={15} /><span>{item.question}</span></button>)}
      </nav>}
    </aside>

    <main className="main-shell" data-section={section} ref={mainRef}>
      {failure && <p className="alert error app-error" role="alert">{failure}</p>}
      {!session && !failure && <p className="working app-error" role="status"><LoaderCircle size={19} className="spin" /> Открываем рабочую область</p>}
      {session && section === 'ask' && <AskWorkspace key={workspace} session={session} activeId={activeId} onSelect={setActiveId}
        onLogin={() => setLogin('staff')} variant={layout} />}
      {session && section === 'documents' && <Documents key={session.role} />}
      {session?.role === 'quality' && section === 'quality' && <Quality />}
    </main>
    {layout === 'focus' && <div className="navigation-dock">{navigation}</div>}
    {login && session && <LoginDialog key={login} initialRole={login} session={session}
      onClose={() => setLogin(null)} onLogin={applySession} />}
  </div>
}

function LoginDialog({ initialRole, session, onLogin, onClose }: {
  initialRole: 'staff' | 'quality'; session: Session; onLogin: (session: Session) => void; onClose: () => void
}) {
  const [role, setRole] = useState(initialRole)
  const [code, setCode] = useState('')
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const accessCode = code.trim()
    if (!accessCode || busy) return
    setBusy(true)
    setFailure('')
    try {
      onLogin(await post<Session>('/session', { role, code: accessCode }))
    } catch (error) {
      setFailure(describeError(error))
      setBusy(false)
    }
  }
  return <Dialog title={role === 'quality' ? 'Вход для службы качества' : 'Вход участника'} onClose={onClose}>
    <form className="stack" onSubmit={submit}>
      {session.staff_login_available && session.quality_login_available && <label className="login-role">
        Войти как
        <select value={role} disabled={busy} onChange={(event) => {
          setRole(event.target.value === 'quality' ? 'quality' : 'staff')
          setCode('')
          setFailure('')
        }}>
          <option value="staff">Участник / жюри</option>
          <option value="quality">Служба качества</option>
        </select>
      </label>}
      <label>{role === 'quality' ? 'Код службы качества' : 'Код участника / жюри'}
        <input type="password" autoComplete="off" required maxLength={256} value={code} onChange={(event) => setCode(event.target.value)} />
      </label>
      {failure && <p className="alert error" role="alert">{failure}</p>}
      <button type="submit" className="button primary" disabled={busy || !code.trim()}>{busy ? 'Проверяем…' : 'Войти'}</button>
    </form>
  </Dialog>
}

document.documentElement.dataset.layout = initialLayout
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
