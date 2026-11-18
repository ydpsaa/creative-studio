import { createRoot } from 'react-dom/client'
import { useEffect, useState } from 'react'
import './styles.css'
import { useLoad } from './ui'
import Inbox from './pages/Inbox'
import Library from './pages/Library'
import Source from './pages/Source'
import Knowledge from './pages/Knowledge'
import Assets from './pages/Assets'
import Brands from './pages/Brands'
import Projects from './pages/Projects'
import Settings from './pages/Settings'
import Review from './pages/Review'

const NAV: [string, string][] = [
  ['inbox', 'Входящие'], ['library', 'Библиотека'], ['knowledge', 'Методы и стили'], ['assets', 'Звуки и ассеты'], ['review', 'Ревью'],
  ['brands', 'Бренды'], ['projects', 'Проекты'], ['settings', 'Настройки'],
]

function useRoute() {
  const [h, setH] = useState(window.location.hash || '#/inbox')
  useEffect(() => {
    const f = () => { setH(window.location.hash || '#/inbox'); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  const [path, query] = h.replace(/^#\//, '').split('?')
  const parts = path.split('/')
  return { parts, query: new URLSearchParams(query || '') }
}

function App() {
  const { parts, query } = useRoute()
  const { data: tools } = useLoad('/api/tools')
  const { data: links } = useLoad('/api/links')
  const page = parts[0] || 'inbox'
  let body
  switch (page) {
    case 'library': body = <Library />; break
    case 'source': body = <Source id={parts[1]} t={Number(query.get('t') || 0)} />; break
    case 'knowledge': body = <Knowledge id={parts[1]} />; break
    case 'assets': body = <Assets />; break
    case 'review': body = <Review />; break
    case 'brands': body = <Brands />; break
    case 'projects': body = <Projects id={parts[1]} />; break
    case 'settings': body = <Settings />; break
    default: body = <Inbox />
  }
  const nav = page === 'source' ? 'library' : page
  return (
    <div className="app">
      <nav className="side">
        <div className="logo">Creative <span>Studio</span></div>
        {NAV.map(([k, l]) => <a key={k} className={nav === k ? 'on' : ''} href={`#/${k}`}>{l}</a>)}
        <div className="foot">
          worker: {tools ? (tools.worker.alive ? <span style={{ color: 'var(--accent)' }}>работает</span> : <span style={{ color: 'var(--bad)' }}>остановлен</span>) : '…'}<br />
          AI-провайдер: не подключён
          {links?.site?.url && <div style={{ marginTop: 8 }}><a href={links.site.url} target="_blank" rel="noreferrer">{links.site.label} →</a></div>}
          {(links?.socials || []).length > 0 && <div>{links.socials.map((s: { label: string; url: string }) =>
            <a key={s.url} href={s.url} target="_blank" rel="noreferrer" style={{ marginRight: 8 }}>{s.label}</a>)}</div>}
        </div>
      </nav>
      <main>{body}</main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
