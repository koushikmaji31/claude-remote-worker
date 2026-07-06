import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, getToken, getUser, setAuth, clearAuth } from './api.js'
import ThemeToggle from './ui/ThemeToggle.jsx'

function initials(name = '') {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || '').join('') || '?'
}

function AuthForm({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const body = mode === 'register' ? { name, email } : { email }
      const data = await api(`/api/${mode}`, { method: 'POST', body, auth: false })
      setAuth(data)
      onAuthed()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card auth-card">
      <div className="auth-tabs">
        <button
          className={`auth-tab ${mode === 'login' ? 'on' : ''}`}
          onClick={() => { setMode('login'); setError('') }}
        >Sign in</button>
        <button
          className={`auth-tab ${mode === 'register' ? 'on' : ''}`}
          onClick={() => { setMode('register'); setError('') }}
        >Register</button>
      </div>
      <form onSubmit={submit} className="stack-3">
        {mode === 'register' && (
          <label className="field">
            <span className="label">Name</span>
            <input placeholder="Ada Lovelace" value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
        )}
        <label className="field">
          <span className="label">Email</span>
          <input type="email" placeholder="you@team.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button className="btn block" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'register' ? 'Create account' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

function JoinPanel({ code, authed, onJoined }) {
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    setPreview(null)
    api(`/api/join/${code}`, { auth: false })
      .then(setPreview)
      .catch((err) => setError(`Invite invalid: ${err.message}`))
  }, [code])

  async function join() {
    setError('')
    try {
      const data = await api(`/api/join/${code}`, { method: 'POST' })
      onJoined(data.project_id)
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="card invite-card">
      <div className="badge member dot">Invitation</div>
      {error && <div className="alert error">{error}</div>}
      {preview && (
        <>
          <h2 style={{ marginTop: 12 }}>{preview.name}</h2>
          <p className="muted">Admin {preview.admin_name} · {preview.member_count} member(s)</p>
          {authed
            ? <button className="btn" onClick={join}>Join project</button>
            : <p className="muted">Sign in or register to accept this invite.</p>}
        </>
      )}
    </div>
  )
}

export default function Landing() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const joinCode = params.get('join')
  const [authed, setAuthed] = useState(!!getToken())
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [newName, setNewName] = useState('')
  const [error, setError] = useState('')
  const user = getUser()

  const loadProjects = useCallback(() => {
    setLoading(true)
    api('/api/projects')
      .then((d) => setProjects(d.projects))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (authed) loadProjects()
  }, [authed, loadProjects])

  async function createProject(e) {
    e.preventDefault()
    setError('')
    try {
      const data = await api('/api/projects', { method: 'POST', body: { name: newName } })
      setNewName('')
      navigate(`/project/${data.project_id}`)
    } catch (err) {
      setError(err.message)
    }
  }

  function logout() {
    clearAuth()
    setAuthed(false)
    setProjects([])
  }

  return (
    <div className="container">
      <header className="row spread" style={{ marginBottom: 'var(--sp-6)' }}>
        <div className="row">
          <div className="brand-mark">TC</div>
          <div>
            <h1>Team Collab</h1>
            <div className="faint">Projects · Members · Chat · Git</div>
          </div>
        </div>
        <div className="row" style={{ gap: 'var(--sp-5)' }}>
          <ThemeToggle />
          {authed && user && (
            <>
              <div className="row" style={{ gap: 8 }}>
                <div className="avatar">{initials(user.name)}</div>
                <div className="faint" style={{ lineHeight: 1.2 }}>
                  <div style={{ color: 'var(--text)', fontWeight: 600, fontSize: 13 }}>{user.name}</div>
                  {user.email}
                </div>
              </div>
              <button className="btn ghost sm" onClick={logout}>Logout</button>
            </>
          )}
        </div>
      </header>

      {!authed && (
        <div className="hero-wrap">
          <div className="hero-glow" aria-hidden />
          <div className="hero">
            <div>
              <h1 className="hero-title">
                Where your team ships <span className="text-gradient">together</span>.
              </h1>
              <p className="muted" style={{ fontSize: 15.5, maxWidth: 420, marginTop: 'var(--sp-3)' }}>
                Create a project, invite your teammates, chat in real time, and review git branches
                and diffs — all in one place.
              </p>
              <div className="feature-strip">
                <span className="feature-item"><span className="check">✓</span>Real-time chat</span>
                <span className="feature-item"><span className="check">✓</span>Git diffs &amp; branches</span>
                <span className="feature-item"><span className="check">✓</span>Instant invites</span>
              </div>
            </div>
            <AuthForm onAuthed={() => setAuthed(true)} />
          </div>
        </div>
      )}

      {joinCode && (
        <JoinPanel code={joinCode} authed={authed} onJoined={(pid) => navigate(`/project/${pid}`)} />
      )}

      {authed && (
        <>
          {error && <div className="alert error">{error}</div>}
          <div className="card flush">
            <div className="card-head">
              <h2 style={{ margin: 0 }}>My projects</h2>
              <span className="badge">{projects.length}</span>
            </div>
            <div className="card-body">
              {loading && (
                <div className="stack-3">
                  {[0, 1].map((i) => <div key={i} className="skeleton" style={{ height: 44 }} />)}
                </div>
              )}
              {!loading && projects.length === 0 && (
                <p className="muted">No projects yet — create your first one below.</p>
              )}
              {!loading && projects.length > 0 && (
                <ul className="plain">
                  {projects.map((p) => (
                    <li key={p.project_id} className="row spread project-row"
                        onClick={() => navigate(`/project/${p.project_id}`)}>
                      <span className="row">
                        <div className="avatar" style={{ background: 'var(--brand-600)' }}>
                          {initials(p.name)}
                        </div>
                        <span>
                          <span style={{ fontWeight: 600 }}>{p.name}</span>{' '}
                          <span className={`badge ${p.role}`}>{p.role}</span>
                        </span>
                      </span>
                      <span className="faint">admin {p.admin_name} · {p.member_count} member(s)</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="card">
            <h2>Create a project</h2>
            <form onSubmit={createProject} className="row" style={{ gap: 8 }}>
              <input className="grow" placeholder="Project name" value={newName}
                     onChange={(e) => setNewName(e.target.value)} required style={{ width: 'auto' }} />
              <button className="btn" type="submit">Create</button>
            </form>
          </div>
        </>
      )}
    </div>
  )
}
