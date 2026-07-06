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

export default function Project() {
  const { pid } = useParams()
  const navigate = useNavigate()
  const me = getUser()
  const [project, setProject] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!getToken()) navigate('/')
  }, [navigate])

  const loadProject = useCallback(() => {
    api(`/api/projects/${pid}`)
      .then(setProject)
      .catch((err) => setError(err.message))
  }, [pid])

  useEffect(() => {
    loadProject()
  }, [loadProject])

  const isAdmin = project && me && project.admin_id === me.user_id

  async function removeMember(uid) {
    if (!window.confirm('Remove this member?')) return
    try {
      await api(`/api/projects/${pid}/members/${uid}`, { method: 'DELETE' })
      loadProject()
    } catch (err) {
      setError(err.message)
    }
  }

  async function transferAdmin(uid) {
    if (!window.confirm('Transfer admin to this member?')) return
    try {
      await api(`/api/projects/${pid}/transfer-admin`, { method: 'POST', body: { user_id: uid } })
      loadProject()
    } catch (err) {
      setError(err.message)
    }
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
      <div className="container">
        <div className="alert error">{error}</div>
        <Link to="/">← Back</Link>
      </div>
    )
  }
  if (!project) return <div className="container muted">Loading…</div>

  return (
    <div className="container">
      <div className="row spread" style={{ marginBottom: 'var(--sp-5)' }}>
        <div className="row">
          <h1>{project.name}</h1>
          <span className="badge">{project.members.length} member{project.members.length === 1 ? '' : 's'}</span>
        </div>
        <div className="row">
          <ThemeToggle />
          <Link to="/">← My projects</Link>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}

      {isAdmin && (
        <div className="card invite-card">
          <div className="card-head" style={{ padding: 0, border: 'none', marginBottom: 'var(--sp-3)' }}>
            <h2 style={{ margin: 0 }}>Invite teammates</h2>
            <button className="btn secondary sm" onClick={copyInvite}>
              {copied ? 'Copied!' : 'Copy invite link'}
            </button>
          </div>
          <p className="muted">
            Share this link so others can join:<br />
            <code>{window.location.origin}/?join={project.invite_code}</code>
          </p>
        </div>
      )}

      <div className="card flush">
        <div className="card-head">
          <h2>Members</h2>
        </div>
        <div className="card-body">
          <ul className="plain">
            {project.members.map((m) => (
              <li key={m.user_id} className="row spread">
                <span className="row">
                  <span className="avatar" aria-hidden>{initials(m.name)}</span>
                  <span>
                    <span style={{ fontWeight: 550 }}>{m.name}</span>{' '}
                    <span className={`badge ${m.role}`}>{m.role}</span>
                    <br />
                    <span className="faint">{m.email}</span>
                  </span>
                </span>
                {isAdmin && m.user_id !== me.user_id && (
                  <span className="row">
                    <button className="btn secondary sm" onClick={() => transferAdmin(m.user_id)}>Make admin</button>
                    <button className="btn danger sm" onClick={() => removeMember(m.user_id)}>Remove</button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <ChatPanel pid={pid} me={me} />

      <GitPanel />
    </div>
  )
}
