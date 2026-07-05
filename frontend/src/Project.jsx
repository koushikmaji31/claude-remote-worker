import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { api, getToken, getUser } from './api.js'

export default function Project() {
  const { pid } = useParams()
  const navigate = useNavigate()
  const me = getUser()
  const [project, setProject] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const sinceId = useRef(0)
  const logRef = useRef(null)

  useEffect(() => {
    if (!getToken()) navigate('/')
  }, [navigate])

  const loadProject = useCallback(() => {
    api(`/api/projects/${pid}`)
      .then(setProject)
      .catch((err) => setError(err.message))
  }, [pid])

  const pollMessages = useCallback(() => {
    api(`/api/projects/${pid}/messages?since_id=${sinceId.current}`)
      .then((d) => {
        if (d.messages.length) {
          sinceId.current = d.messages[d.messages.length - 1].id
          setMessages((prev) => [...prev, ...d.messages])
        }
      })
      .catch(() => {})
  }, [pid])

  useEffect(() => {
    loadProject()
    pollMessages()
    const iv = setInterval(pollMessages, 3000)
    return () => clearInterval(iv)
  }, [loadProject, pollMessages])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [messages])

  const isAdmin = project && me && project.admin_id === me.user_id

  async function post(e) {
    e.preventDefault()
    if (!text.trim()) return
    try {
      await api(`/api/projects/${pid}/messages`, { method: 'POST', body: { text } })
      setText('')
      pollMessages()
    } catch (err) {
      setError(err.message)
    }
  }

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
    const link = `http://localhost:5173/?join=${project.invite_code}`
    navigator.clipboard.writeText(link).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  if (error && !project) {
    return (
      <div className="container">
        <div className="error">{error}</div>
        <Link to="/">← Back</Link>
      </div>
    )
  }
  if (!project) return <div className="container muted">Loading…</div>

  return (
    <div className="container">
      <div className="row spread">
        <h1>{project.name}</h1>
        <Link to="/">← My projects</Link>
      </div>
      {error && <div className="error">{error}</div>}

      {isAdmin && (
        <div className="card">
          <h2>Invite</h2>
          <p className="muted">
            Backend link: <code>{project.invite_link}</code>
            <br />
            Frontend link: <code>http://localhost:5173/?join={project.invite_code}</code>
          </p>
          <button className="small" onClick={copyInvite}>{copied ? 'Copied!' : 'Copy invite link'}</button>
        </div>
      )}

      <div className="card">
        <h2>Members</h2>
        <ul className="plain">
          {project.members.map((m) => (
            <li key={m.user_id} className="row spread">
              <span>
                {m.name} <span className="muted">({m.email})</span>{' '}
                <span className={`badge ${m.role}`}>{m.role}</span>
              </span>
              {isAdmin && m.user_id !== me.user_id && (
                <span className="row">
                  <button className="secondary small" onClick={() => transferAdmin(m.user_id)}>Make admin</button>
                  <button className="danger small" onClick={() => removeMember(m.user_id)}>Remove</button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>Message log</h2>
        <div className="msglog" ref={logRef}>
          {messages.length === 0 && <p className="muted">No messages yet.</p>}
          {messages.map((m) => (
            <div className="msg" key={m.id}>
              <span className="sender">{m.sender}</span>
              <span className="ts">{new Date(m.ts * 1000).toLocaleTimeString()}</span>
              <div>{m.text}</div>
            </div>
          ))}
        </div>
        <form onSubmit={post}>
          <textarea placeholder="Write a message…" value={text} onChange={(e) => setText(e.target.value)} />
          <button type="submit">Post</button>
        </form>
      </div>
    </div>
  )
}
