import { useEffect, useState, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, getToken, getUser, setAuth, clearAuth } from './api.js'

function AuthForm({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault()
    setError('')
    try {
      const body = mode === 'register' ? { name, email } : { email }
      const data = await api(`/api/${mode}`, { method: 'POST', body, auth: false })
      setAuth(data)
      onAuthed()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="card">
      <h2>{mode === 'register' ? 'Register' : 'Login'}</h2>
      <form onSubmit={submit}>
        {mode === 'register' && (
          <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        )}
        <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        {error && <div className="error">{error}</div>}
        <div className="row">
          <button type="submit">{mode === 'register' ? 'Register' : 'Login'}</button>
          <button type="button" className="secondary" onClick={() => { setMode(mode === 'register' ? 'login' : 'register'); setError('') }}>
            {mode === 'register' ? 'Have an account? Login' : 'New here? Register'}
          </button>
        </div>
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
    <div className="card">
      <h2>Join a project</h2>
      {error && <div className="error">{error}</div>}
      {preview && (
        <div>
          <p>
            <strong>{preview.name}</strong>
            <br />
            <span className="muted">Admin: {preview.admin_name} · {preview.member_count} member(s)</span>
          </p>
          {authed
            ? <button onClick={join}>Join project</button>
            : <p className="muted">Log in or register above to join.</p>}
        </div>
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
  const [newName, setNewName] = useState('')
  const [error, setError] = useState('')
  const user = getUser()

  const loadProjects = useCallback(() => {
    api('/api/projects')
      .then((d) => setProjects(d.projects))
      .catch((err) => setError(err.message))
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
      <div className="row spread">
        <h1>Team Collab Platform</h1>
        {authed && user && (
          <div className="row">
            <span className="muted">{user.name} ({user.email})</span>
            <button className="secondary small" onClick={logout}>Logout</button>
          </div>
        )}
      </div>

      {!authed && <AuthForm onAuthed={() => setAuthed(true)} />}

      {joinCode && (
        <JoinPanel code={joinCode} authed={authed} onJoined={(pid) => navigate(`/project/${pid}`)} />
      )}

      {authed && (
        <>
          <div className="card">
            <h2>My projects</h2>
            {error && <div className="error">{error}</div>}
            {projects.length === 0 && <p className="muted">No projects yet — create one below.</p>}
            <ul className="plain">
              {projects.map((p) => (
                <li key={p.project_id} className="row spread">
                  <span>
                    <a href={`/project/${p.project_id}`} onClick={(e) => { e.preventDefault(); navigate(`/project/${p.project_id}`) }}>
                      {p.name}
                    </a>{' '}
                    <span className={`badge ${p.role}`}>{p.role}</span>
                  </span>
                  <span className="muted">admin: {p.admin_name} · {p.member_count} member(s)</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="card">
            <h2>Create project</h2>
            <form onSubmit={createProject}>
              <input placeholder="Project name" value={newName} onChange={(e) => setNewName(e.target.value)} required />
              <button type="submit">Create</button>
            </form>
          </div>
        </>
      )}
    </div>
  )
}
