// Ticket space:
//  1. a shared ticket/context pasted at the top,
//  2. a Jira-like board of cards (To Do / In Progress / Done) driven by humans
//     AND agents — but only a human (this web UI) may move a card to Done,
//  3. a live strip of each agent's task list (auto-published from their todos).
// Polls getTicket every 3s so the board stays live. Shared design tokens +
// .ticket-*/.board-* rules; no hardcoded colors, no emoji.
import { useEffect, useState, useCallback } from 'react'
import { getTicket, setTicket, createCard, updateCard, deleteCard } from '../lib/ticket.js'

const STATUSES = ['todo', 'doing', 'done']
const COLUMNS = [
  { id: 'todo', label: 'To Do' },
  { id: 'doing', label: 'In Progress' },
  { id: 'done', label: 'Done' },
]
const STATUS_LABEL = { todo: 'To Do', doing: 'In Progress', done: 'Done' }

function relTime(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export default function TicketPanel({ pid }) {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [newTitle, setNewTitle] = useState('')

  const poll = useCallback(() => {
    getTicket(pid)
      .then((d) => {
        setData(d)
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
    setSaving(true); setError('')
    try {
      await setTicket(pid, draft); setEditing(false); poll()
    } catch (err) { setError(err.message) } finally { setSaving(false) }
  }

  // Optimistic: apply the server's returned state immediately.
  async function act(fn) {
    setError('')
    try { setData(await fn()) } catch (err) { setError(err.message); poll() }
  }
  async function addCard(e) {
    e.preventDefault()
    if (!newTitle.trim()) return
    const title = newTitle.trim(); setNewTitle('')
    await act(() => createCard(pid, title))
  }

  const ticket = data?.ticket
  const agents = data?.agents || []
  const cards = data?.cards || []

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
            <div className="ticket-editor">
              <textarea className="ticket-textarea" value={draft} rows={8}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Paste the ticket / shared context here…" />
              <div className="ticket-editor-actions">
                {ticket && editing && (
                  <button className="btn ghost sm" onClick={() => { setEditing(false); setDraft(ticket.body || '') }}>Cancel</button>
                )}
                <button className="btn sm" onClick={save} disabled={saving || !draft.trim()}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          ) : (
            <div className="ticket-ticket">
              <pre className="ticket-body">{ticket.body}</pre>
              <div className="ticket-meta faint">Set by {ticket.set_by || 'unknown'} · {relTime(ticket.ts)}</div>
            </div>
          )}
        </div>
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2>Board</h2>
          <form className="board-add" onSubmit={addCard}>
            <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="New card title…" />
            <button className="btn sm" type="submit" disabled={!newTitle.trim()}>Add</button>
          </form>
        </header>
        <div className="panel-body">
          <div className="board">
            {COLUMNS.map((col) => {
              const colCards = cards.filter((c) => (STATUSES.includes(c.status) ? c.status : 'todo') === col.id)
              return (
                <div key={col.id} className="board-col">
                  <div className="board-col-head">
                    <span>{col.label}</span>
                    <span className="board-count">{colCards.length}</span>
                  </div>
                  <div className="board-col-body">
                    {colCards.length === 0 && <div className="board-empty faint">—</div>}
                    {colCards.map((c) => (
                      <div key={c.id} className={`board-card ${c.status === 'done' ? 'is-done' : ''}`}>
                        <div className="board-card-title">{c.title}</div>
                        {c.body && <div className="board-card-body">{c.body}</div>}
                        <div className="board-card-foot">
                          <span className="faint board-card-by">{c.updated_by || c.created_by || ''}</span>
                          <div className="board-card-moves">
                            {COLUMNS.filter((t) => t.id !== col.id).map((t) => (
                              <button key={t.id} className="board-move" title={`Move to ${t.label}`}
                                onClick={() => act(() => updateCard(pid, c.id, { status: t.id }))}>
                                {t.id === 'done' ? 'Done' : t.id === 'doing' ? 'Start' : 'To Do'}
                              </button>
                            ))}
                            <button className="board-move board-del" title="Delete card"
                              onClick={() => act(() => deleteCard(pid, c.id))}>Del</button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          <p className="faint" style={{ marginTop: 'var(--sp-3)' }}>
            Agents can create cards and move them to To Do / In Progress. Only a human can mark a card Done.
          </p>
        </div>
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2>Agent activity</h2>
          <span className="tag">{agents.length} agent{agents.length === 1 ? '' : 's'}</span>
        </header>
        <div className="panel-body">
          {agents.length === 0 ? (
            <div className="muted">No agents have published tasks yet.</div>
          ) : (
            <div className="ticket-board">
              {agents.map((a) => (
                <div key={a.agent} className="ticket-col card">
                  <div className="ticket-col-head">
                    <span className="ticket-agent">{a.agent}</span>
                    <span className="faint">{relTime(a.ts)}</span>
                  </div>
                  <ul className="ticket-tasks">
                    {(a.tasks || []).length === 0 && <li className="muted ticket-empty">No tasks</li>}
                    {(a.tasks || []).map((t, i) => (
                      <li key={i} className="ticket-task">
                        <span className={`badge ticket-pill ${STATUSES.includes(t.status) ? t.status : 'todo'}`}>
                          {STATUS_LABEL[STATUSES.includes(t.status) ? t.status : 'todo']}
                        </span>
                        <span className="ticket-task-text">{t.text}</span>
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
