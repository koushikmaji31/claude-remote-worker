import { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, getToken, getUser, setAuth, clearAuth } from './api.js'
import ThemeToggle from './ui/ThemeToggle.jsx'
import { avatarColor } from './ui/avatarColor.js'

const GIS_SRC = 'https://accounts.google.com/gsi/client'

// Load Google Identity Services once; resolves when the oauth2 API is ready.
function loadGis() {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.oauth2) return resolve()
    const existing = document.querySelector(`script[src="${GIS_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', () => resolve())
      existing.addEventListener('error', reject)
      return
    }
    const s = document.createElement('script')
    s.src = GIS_SRC
    s.async = true
    s.defer = true
    s.onload = () => resolve()
    s.onerror = reject
    document.head.appendChild(s)
  })
}

function initials(name = '') {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || '').join('') || '?'
}

const LOGOS = ['Apple', 'Meta', 'Siemens', 'Disney+', 'Adobe', 'Mercedes']

const TESTIMONIALS = [
  { by: '@ada', text: 'Mark my words. The next big team will ship on Team Collab — and it might be mine.' },
  { by: '@lin', text: 'I have been using Team Collab for a while now and it is so much easier than juggling five tabs. Fast too!' },
  { by: '@sven', text: 'Slack + a git viewer once led the way, but this is the leader now. Well engineered, more intuitive, faster.' },
  { by: '@mira', text: 'Over the holidays I moved my whole squad onto Team Collab to keep our reviews in one place.' },
]

function GoogleMark() {
  return (
    <svg className="gauth-ico" width="18" height="18" viewBox="0 0 48 48" aria-hidden>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 16 19 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.6 6.1 29.6 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 10-2 13.6-5.2l-6.3-5.3C29.2 35 26.7 36 24 36c-5.3 0-9.7-3.1-11.3-7.6l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4 5.5l6.3 5.3C41.4 36 44 30.6 44 24c0-1.3-.1-2.3-.4-3.5z" />
    </svg>
  )
}

function AuthForm({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [googleClientId, setGoogleClientId] = useState('')
  const [gisReady, setGisReady] = useState(false)
  const tokenClient = useRef(null)

  // Fetch the Google client id from the backend; if set, load GIS and wire an
  // OAuth token client whose callback verifies with our /api/auth/google.
  useEffect(() => {
    let cancelled = false
    api('/api/config', { auth: false })
      .then((cfg) => {
        if (cancelled || !cfg?.google_client_id) return
        setGoogleClientId(cfg.google_client_id)
        return loadGis().then(() => {
          if (cancelled) return
          tokenClient.current = window.google.accounts.oauth2.initTokenClient({
            client_id: cfg.google_client_id,
            scope: 'openid email profile',
            callback: async (resp) => {
              if (resp.error || !resp.access_token) {
                setBusy(false)
                setError('Google sign-in was cancelled.')
                return
              }
              try {
                const data = await api('/api/auth/google', {
                  method: 'POST', auth: false,
                  body: { access_token: resp.access_token },
                })
                setAuth(data)
                onAuthed()
              } catch (err) {
                setBusy(false)
                setError(err.message)
              }
            },
          })
          setGisReady(true)
        })
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [onAuthed])

  async function submit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const body = mode === 'register' ? { name, email, password } : { email, password }
      const data = await api(`/api/${mode}`, { method: 'POST', body, auth: false })
      setAuth(data)
      onAuthed()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function signInWithGoogle() {
    setError('')
    if (!tokenClient.current) return
    setBusy(true)
    tokenClient.current.requestAccessToken()
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
        <label className="field">
          <span className="label">Password</span>
          <input type="password" placeholder={mode === 'register' ? 'At least 8 characters' : 'Your password'}
                 value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                 minLength={8} required />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button className="btn block" type="submit" disabled={busy}>
          {busy ? 'Please wait…' : mode === 'register' ? 'Create account' : 'Sign in'}
        </button>
      </form>

      {googleClientId && (
        <>
          <div className="auth-divider"><span>or</span></div>
          <button type="button" className="gauth-btn" onClick={signInWithGoogle}
                  disabled={busy || !gisReady}>
            <GoogleMark />
            {mode === 'register' ? 'Sign up with Google' : 'Continue with Google'}
          </button>
        </>
      )}
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

function TopNav({ authed, user, onLogout }) {
  return (
    <nav className="ag-nav">
      <div className="ag-nav-inner">
        <div className="row" style={{ gap: 10 }}>
          <div className="brand-mark">TC</div>
          <span className="ag-wordmark">Team&nbsp;Collab</span>
        </div>
        {!authed && (
          <div className="ag-nav-links">
            <a href="#features">Product</a>
            <a href="#loved">Teams</a>
            <a href="#testimonials">Stories</a>
          </div>
        )}
        <div className="row" style={{ gap: 'var(--sp-3)' }}>
          <ThemeToggle />
          {authed && user ? (
            <>
              <div className="nav-user">
                <div className="avatar" style={{ background: avatarColor(user.name) }}>{initials(user.name)}</div>
                <span className="nav-user-name">{user.name}</span>
              </div>
              <button className="btn ghost sm" onClick={onLogout}>Logout</button>
            </>
          ) : (
            <a className="btn pill sm" href="#get-started">Get started</a>
          )}
        </div>
      </div>
    </nav>
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
    <div className="ag-page">
      <TopNav authed={authed} user={user} onLogout={logout} />

      {!authed && (
        <>
          <section className="ag-hero">
            <div className="hero-glow" aria-hidden />
            <div className="ag-hero-inner">
              <div className="ag-eyebrow">Projects · Members · Chat · Git</div>
              <h1 className="ag-hero-title">
                Where your team ships <span className="text-gradient">together</span><span className="ag-cursor" aria-hidden />
              </h1>
              <p className="ag-hero-sub">
                Create a project, invite your teammates, chat in real time, and review git
                branches and diffs — all in one private, secure place.
              </p>
              <div className="ag-hero-cta" id="get-started">
                <a className="btn pill lg" href="#auth">+ Get started</a>
                <a className="btn secondary pill lg" href="#features">See how it works</a>
              </div>
            </div>
          </section>

          <section className="ag-features" id="features">
            {[
              ['Real-time chat', 'Talk to your team inline, no context switching.'],
              ['Git diffs & branches', 'Review branches and unified diffs in the browser.'],
              ['Instant invites', 'Share one link and your teammates are in.'],
            ].map(([t, d]) => (
              <div className="ag-feature" key={t}>
                <span className="check" aria-hidden>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6L9 17l-5-5" />
                  </svg>
                </span>
                <div>
                  <div className="ag-feature-t">{t}</div>
                  <div className="ag-feature-d">{d}</div>
                </div>
              </div>
            ))}
          </section>

          <section className="ag-loved" id="loved">
            <span className="ag-loved-label">Loved by teams at</span>
            <div className="ag-logos">
              {LOGOS.map((l) => <span className="ag-logo" key={l}>{l}</span>)}
            </div>
          </section>

          <section className="ag-testimonials" id="testimonials">
            {TESTIMONIALS.map((t) => (
              <figure className="ag-quote" key={t.by}>
                <blockquote>{t.text}</blockquote>
                <figcaption><span className="ag-brand-tag">{t.by}</span></figcaption>
              </figure>
            ))}
          </section>

          <section className="ag-auth" id="auth">
            <div className="ag-auth-copy">
              <h2 className="ag-section-title">Get started with your team</h2>
              <p className="muted">Sign in or create an account. It takes seconds.</p>
            </div>
            <AuthForm onAuthed={() => setAuthed(true)} />
          </section>
        </>
      )}

      <div className="container">
        {joinCode && (
          <JoinPanel code={joinCode} authed={authed} onJoined={(pid) => navigate(`/project/${pid}`)} />
        )}

        {authed && (
          <div className="dash">
            <div className="dash-head">
              <div>
                <h1 className="dash-title">Projects</h1>
                <p className="muted" style={{ margin: '4px 0 0' }}>
                  {user ? `${user.name.split(' ')[0]}'s workspaces` : 'Your workspaces'}
                </p>
              </div>
              <form onSubmit={createProject} className="dash-create">
                <input placeholder="New project name" value={newName}
                       onChange={(e) => setNewName(e.target.value)} required />
                <button className="btn" type="submit">Create</button>
              </form>
            </div>

            {error && <div className="alert error">{error}</div>}

            {loading && (
              <div className="proj-grid">
                {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 116, borderRadius: 'var(--r-lg)' }} />)}
              </div>
            )}

            {!loading && projects.length === 0 && (
              <div className="empty">
                <div className="empty-title">No projects yet</div>
                <div className="muted">Create your first workspace using the field above.</div>
              </div>
            )}

            {!loading && projects.length > 0 && (
              <div className="proj-grid">
                {projects.map((p) => (
                  <button key={p.project_id} className="proj-card"
                          onClick={() => navigate(`/project/${p.project_id}`)}>
                    <div className="proj-card-top">
                      <span className="avatar" aria-hidden style={{ background: avatarColor(p.name) }}>{initials(p.name)}</span>
                      <span className={`badge ${p.role}`}>{p.role}</span>
                    </div>
                    <div className="proj-name">{p.name}</div>
                    <div className="proj-meta">
                      <span>{p.member_count} member{p.member_count === 1 ? '' : 's'}</span>
                      <span className="proj-dot" aria-hidden />
                      <span className="proj-admin">{p.admin_name}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
