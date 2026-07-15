// Tickit space — a shared ticket/context pasted at the top, and a live board of
// each agent's task list. Polls getTickit every 3s like ChatPanel so the board
// stays live as agents publish progress. Styling uses shared design tokens plus
// .tickit-* rules; no hardcoded colors, no emoji.
import { useEffect, useState, useCallback } from 'react'
import { getTickit, setTicket } from '../lib/tickit.js'

const STATUSES = ['todo', 'doing', 'done']

function relTime(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export default function TickitPanel({ pid, me }) {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const poll = useCallback(() => {
    getTickit(pid)
      .then((d) => {
        setData(d)
        // Only seed the draft while not actively editing, so live polls don't
        // clobber what the user is typing.
        setDraft((prev) => (editing ? prev : d.ticket?.body || ''))
      })
      .catch(() => {})
  }, [pid, editing])

  useEffect(() => {
    poll()
    const iv = setInterval(poll, 3000)
    return () => clearInterval(iv)
  }, [poll])

  async function save() {
    setSaving(true)
    setError('')
    try {
      await setTicket(pid, draft)
      setEditing(false)
      poll()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const ticket = data?.ticket
  const agents = data?.agents || []

  return (
    <div className="stack-4">
      <section className="panel">
        <header className="panel-head">
          <h2>Ticket</h2>
          {ticket && !editing && (
            <button className="btn ghost sm" onClick={() => { setDraft(ticket.body || ''); setEditing(true) }}>Edit</button>
          )}
        </header>
        <div className="panel-body">
          {error && <div className="alert error">{error}</div>}
          {editing || !ticket ? (
            <div className="tickit-editor">
              <textarea
                className="tickit-textarea"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Paste the ticket / shared context here…"
                rows={8}
              />
              <div className="tickit-editor-actions">
                {ticket && editing && (
                  <button className="btn ghost sm" onClick={() => { setEditing(false); setDraft(ticket.body || '') }}>Cancel</button>
                )}
                <button className="btn sm" onClick={save} disabled={saving || !draft.trim()}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <div className="tickit-ticket">
              <pre className="tickit-body">{ticket.body}</pre>
              <div className="tickit-meta faint">
                Set by {ticket.set_by || 'unknown'} · {relTime(ticket.ts)}
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2>Board</h2>
          <span className="tag">{agents.length} agent{agents.length === 1 ? '' : 's'}</span>
        </header>
        <div className="panel-body">
          {agents.length === 0 ? (
            <div className="muted">No agents have published tasks yet.</div>
          ) : (
            <div className="tickit-board">
              {agents.map((a) => (
                <div key={a.agent} className="tickit-col card">
                  <div className="tickit-col-head">
                    <span className="tickit-agent">{a.agent}</span>
                    <span className="faint">{relTime(a.ts)}</span>
                  </div>
                  <ul className="tickit-tasks">
                    {(a.tasks || []).length === 0 && <li className="muted tickit-empty">No tasks</li>}
                    {(a.tasks || []).map((t, i) => (
                      <li key={i} className="tickit-task">
                        <span className={`badge tickit-pill ${STATUSES.includes(t.status) ? t.status : 'todo'}`}>
                          {STATUSES.includes(t.status) ? t.status : 'todo'}
                        </span>
                        <span className="tickit-task-text">{t.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
