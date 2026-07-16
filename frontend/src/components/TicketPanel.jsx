// Ticket space — a Jira-like board. Each card IS a ticket: title + description
// (its own context) + status, moved across To Do / In Progress / Done. Humans
// and agents both drive it, but only a human (this web UI) may move a card to
// Done. Below the board, a live strip shows each agent's auto-published todos.
// Polls every 3s. Shared design tokens + .board-*/.ticket-* rules; no emoji.
import { useEffect, useState, useCallback } from 'react'
import { getTicket, createCard, updateCard, deleteCard } from '../lib/ticket.js'

const STATUSES = ['todo', 'doing', 'done']
const COLUMNS = [
  { id: 'todo', label: 'To Do' },
  { id: 'doing', label: 'In Progress' },
  { id: 'done', label: 'Done' },
]
const STATUS_LABEL = { todo: 'To Do', doing: 'In Progress', done: 'Done' }
const MOVE_LABEL = { todo: 'To Do', doing: 'Start', done: 'Done' }

function relTime(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function Card({ card, onMove, onSave, onDelete, colId }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(card.title)
  const [body, setBody] = useState(card.body || '')
  const dirty = title !== card.title || body !== (card.body || '')

  return (
    <div className={`board-card ${card.status === 'done' ? 'is-done' : ''}`}>
      <button className="board-card-titlebtn" onClick={() => setOpen((o) => !o)} title="Open ticket">
        {card.title}
      </button>
      {!open && card.body && <div className="board-card-body">{card.body}</div>}
      {open && (
        <div className="board-card-edit">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" />
          <textarea value={body} rows={4} onChange={(e) => setBody(e.target.value)}
            placeholder="Description / context for this ticket…" />
          <div className="board-card-editrow">
            <button className="board-move" onClick={() => setOpen(false)}>Close</button>
            <button className="btn sm" disabled={!dirty || !title.trim()}
              onClick={() => { onSave({ title: title.trim(), body }); setOpen(false) }}>Save</button>
          </div>
        </div>
      )}
      <div className="board-card-foot">
        <span className="faint board-card-by">{card.updated_by || card.created_by || ''}</span>
        <div className="board-card-moves">
          {COLUMNS.filter((t) => t.id !== colId).map((t) => (
            <button key={t.id} className="board-move" title={`Move to ${t.label}`}
              onClick={() => onMove(t.id)}>{MOVE_LABEL[t.id]}</button>
          ))}
          <button className="board-move board-del" title="Delete ticket" onClick={onDelete}>Del</button>
        </div>
      </div>
    </div>
  )
}

export default function TicketPanel({ pid }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [newBody, setNewBody] = useState('')
  const [adding, setAdding] = useState(false)

  const poll = useCallback(() => {
    getTicket(pid).then(setData).catch(() => {})
  }, [pid])

  useEffect(() => {
    poll()
    const iv = setInterval(poll, 3000)
    return () => clearInterval(iv)
  }, [poll])

  async function act(fn) {
    setError('')
    try { setData(await fn()) } catch (err) { setError(err.message); poll() }
  }
  async function addCard(e) {
    e.preventDefault()
    if (!newTitle.trim()) return
    const title = newTitle.trim(); const body = newBody
    setNewTitle(''); setNewBody(''); setAdding(false)
    await act(() => createCard(pid, title, body))
  }

  const agents = data?.agents || []
  const cards = data?.cards || []

  return (
    <div className="stack-4">
      <section className="panel">
        <header className="panel-head">
          <h2>Board</h2>
          {!adding && <button className="btn sm" onClick={() => setAdding(true)}>New ticket</button>}
        </header>
        <div className="panel-body">
          {error && <div className="alert error">{error}</div>}
          {adding && (
            <form className="board-new" onSubmit={addCard}>
              <input autoFocus value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="Ticket title" />
              <textarea value={newBody} rows={3} onChange={(e) => setNewBody(e.target.value)}
                placeholder="Description / context (optional)…" />
              <div className="board-card-editrow">
                <button type="button" className="board-move" onClick={() => { setAdding(false); setNewTitle(''); setNewBody('') }}>Cancel</button>
                <button className="btn sm" type="submit" disabled={!newTitle.trim()}>Add ticket</button>
              </div>
            </form>
          )}
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
                      <Card key={c.id} card={c} colId={col.id}
                        onMove={(status) => act(() => updateCard(pid, c.id, { status }))}
                        onSave={(patch) => act(() => updateCard(pid, c.id, patch))}
                        onDelete={() => act(() => deleteCard(pid, c.id))} />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          <p className="faint" style={{ marginTop: 'var(--sp-3)' }}>
            Open a ticket to read or edit its description. Agents can create tickets and move them to
            To Do / In Progress; only a human can mark a ticket Done.
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
