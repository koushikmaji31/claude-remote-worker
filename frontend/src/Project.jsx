import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { api, getToken, getUser } from './api.js'
import ThemeToggle from './ui/ThemeToggle.jsx'
import GitPanel from './components/GitPanel.jsx'
import ChatPanel from './components/ChatPanel.jsx'

function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

const NAV = [
  { id: 'overview', label: 'Overview' },
  { id: 'discussion', label: 'Discussion' },
  { id: 'branches', label: 'Branches' },
  { id: 'members', label: 'Members' },
]

function NavIcon({ id }) {
  const common = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  if (id === 'overview') return <svg {...common}><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>
  if (id === 'discussion') return <svg {...common}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
  if (id === 'branches') return <svg {...common}><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="8" r="2.5" /><path d="M6 8.5v7M8.5 6H14a4 4 0 0 1 4 4v.5M18 10.5V13" /></svg>
  return <svg {...common}><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /></svg>
}

function Section({ title, action, children }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {action}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  )
}

function GroupJoinPanel({ pid }) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api(`/api/projects/${pid}/bus`).then(setInfo).catch((err) => setError(err.message))
  }, [pid])

  function copy() {
    if (!info) return
    navigator.clipboard.writeText(info.command).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <Section
      title="Connect a Claude session"
      action={<span className="tag">bus · this group</span>}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Run this in your project repo, then start <code>claude</code>. That window joins
        this group only — it talks to the other Claude sessions here and no one else.
      </p>
      {error && <div className="alert error">Could not load join command: {error}</div>}
      {!info && !error && <div className="skeleton" style={{ height: 46 }} />}
      {info && (
        <div className="cmd-row">
          <code className="cmd-box">{info.command}</code>
          <button className="btn sm" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
        </div>
      )}
    </Section>
  )
}

export default function Project() {
  const { pid } = useParams()
  const navigate = useNavigate()
  const me = getUser()
  const [project, setProject] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [view, setView] = useState('overview')

  useEffect(() => {
    if (!getToken()) navigate('/')
  }, [navigate])

  const loadProject = useCallback(() => {
    api(`/api/projects/${pid}`).then(setProject).catch((err) => setError(err.message))
  }, [pid])

  useEffect(() => { loadProject() }, [loadProject])

  const isAdmin = project && me && project.admin_id === me.user_id

  async function removeMember(uid) {
    if (!window.confirm('Remove this member?')) return
    try {
      await api(`/api/projects/${pid}/members/${uid}`, { method: 'DELETE' })
      loadProject()
    } catch (err) { setError(err.message) }
  }

  async function transferAdmin(uid) {
    if (!window.confirm('Transfer admin to this member?')) return
    try {
      await api(`/api/projects/${pid}/transfer-admin`, { method: 'POST', body: { user_id: uid } })
      loadProject()
    } catch (err) { setError(err.message) }
  }

  function copyInvite() {
    const link = `${window.location.origin}/?join=${project.invite_code}`
    navigator.clipboard.writeText(link).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  if (error && !project) {
    return (
      <div className="app-fallback">
        <div className="alert error">{error}</div>
        <Link className="link" to="/">Back to projects</Link>
      </div>
    )
  }
  if (!project) return <div className="app-fallback muted">Loading workspace…</div>

  const memberCount = project.members.length
  const activeLabel = NAV.find((n) => n.id === view)?.label

  return (
    <div className="app">
      <aside className="side">
        <div className="side-top">
          <span className="side-avatar" aria-hidden>{initials(project.name)}</span>
          <div className="side-id">
            <div className="side-name" title={project.name}>{project.name}</div>
            <div className="side-sub">{memberCount} member{memberCount === 1 ? '' : 's'}</div>
          </div>
        </div>

        <nav className="side-nav">
          <div className="side-label">Workspace</div>
          {NAV.map((n) => (
            <button
              key={n.id}
              className={`nav-item ${view === n.id ? 'on' : ''}`}
              onClick={() => setView(n.id)}
            >
              <NavIcon id={n.id} />
              <span>{n.label}</span>
              {n.id === 'members' && <span className="nav-count">{memberCount}</span>}
            </button>
          ))}
        </nav>

        <div className="side-foot">
          <Link className="nav-item" to="/">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M15 18l-6-6 6-6" />
            </svg>
            <span>All projects</span>
          </Link>
          <div className="side-foot-row">
            <span className="side-user">{me?.name}</span>
            <ThemeToggle />
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="main-head">
          <div>
            <div className="crumb">{project.name}</div>
            <h1>{activeLabel}</h1>
          </div>
          <div className="main-actions">
            {isAdmin && (
              <button className="btn sm" onClick={copyInvite}>
                {copied ? 'Invite copied' : 'Copy invite link'}
              </button>
            )}
          </div>
        </header>

        <div className="main-body">
          {error && <div className="alert error">{error}</div>}

          {view === 'overview' && (
            <div className="stack-4">
              <div className="stat-row">
                <div className="stat"><div className="stat-n">{memberCount}</div><div className="stat-l">Members</div></div>
                <div className="stat"><div className="stat-n">{isAdmin ? 'Admin' : 'Member'}</div><div className="stat-l">Your role</div></div>
                <div className="stat"><div className="stat-n mono">{project.invite_code.slice(0, 8)}</div><div className="stat-l">Group id</div></div>
              </div>
              <GroupJoinPanel pid={pid} />
              {isAdmin && (
                <Section title="Invite teammates">
                  <p className="muted" style={{ marginTop: 0 }}>Share this link so others can join the workspace.</p>
                  <div className="cmd-row">
                    <code className="cmd-box">{window.location.origin}/?join={project.invite_code}</code>
                    <button className="btn sm" onClick={copyInvite}>{copied ? 'Copied' : 'Copy'}</button>
                  </div>
                </Section>
              )}
            </div>
          )}

          {view === 'discussion' && <ChatPanel pid={pid} me={me} />}

          {view === 'branches' && <GitPanel />}

          {view === 'members' && (
            <Section title="Members" action={<span className="tag">{memberCount}</span>}>
              <ul className="member-list">
                {project.members.map((m) => (
                  <li key={m.user_id} className="member-row">
                    <span className="row">
                      <span className="avatar" aria-hidden>{initials(m.name)}</span>
                      <span className="member-meta">
                        <span className="member-name">
                          {m.name} <span className={`badge ${m.role}`}>{m.role}</span>
                        </span>
                        <span className="faint">{m.email}</span>
                      </span>
                    </span>
                    {isAdmin && m.user_id !== me.user_id && (
                      <span className="row">
                        <button className="btn ghost sm" onClick={() => transferAdmin(m.user_id)}>Make admin</button>
                        <button className="btn danger sm" onClick={() => removeMember(m.user_id)}>Remove</button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      </main>
    </div>
  )
}
