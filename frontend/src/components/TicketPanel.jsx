// Ticket space — a Jira-like board. Each card IS a ticket: title + description
// (its own context) + status, moved across To Do / In Progress / Done. Humans
// and agents both drive it, but only a human (this web UI) may move a card to
// Done. Below the board, a live strip shows each agent's auto-published todos.
// Polls every 3s. Shared design tokens + .board-*/.ticket-* rules; no emoji.
import { useEffect, useState, useCallback } from 'react'
import { getTicket, createCard, updateCard, deleteCard } from '../lib/ticket.js'
import { jiraTransition, jiraComments, jiraAddComment, jiraAssignable, jiraEditIssue } from '../lib/jira.js'
import JiraPanel from './JiraPanel.jsx'
import JiraSprint from './JiraSprint.jsx'
import { avatarColor } from '../ui/avatarColor.js'

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

const JIRA_PRIO = { Highest: 'highest', High: 'high', Medium: 'medium', Low: 'low', Lowest: 'lowest' }
const initials = (name) => (name || '?').trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('').toUpperCase()

// Atlassian-style issue-type icon: a colored rounded square with a white glyph.
const TYPE_BG = { Story: '#63ba3c', Bug: '#e5493a', Task: '#4bade8', Epic: '#8777d9', 'Sub-task': '#4bade8', Subtask: '#4bade8' }
function JiraTypeIcon({ type }) {
  const t = type || 'Task'
  const glyph = {
    Story: <path d="M5 4h6v8l-3-2.2L5 12z" fill="#fff" />,
    Bug: <circle cx="8" cy="8" r="3.1" fill="#fff" />,
    Epic: <path d="M9 3.4L4.8 9H7.4l-.7 3.6L11.2 7H8.5z" fill="#fff" />,
    Task: <path d="M5 8.2l2 2 4-4.3" stroke="#fff" strokeWidth="1.7" fill="none" strokeLinecap="round" strokeLinejoin="round" />,
  }[t] || <path d="M5.5 8h5M8 5.5v5" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" />
  return <svg className="jira-type-ic" width="15" height="15" viewBox="0 0 16 16" style={{ background: TYPE_BG[t] || '#4bade8' }} title={t} aria-label={t}>{glyph}</svg>
}

// Atlassian-style priority icon: colored chevron(s).
function JiraPrioIcon({ priority }) {
  const p = priority || ''
  const color = { Highest: '#cd1317', High: '#e9494a', Medium: '#e97f33', Low: '#2a8735', Lowest: '#0b7a3b' }[p] || '#8993a4'
  const up = 'M3 8l4-3.5L11 8'
  const dn = 'M3 6l4 3.5L11 6'
  const paths = {
    Highest: [up, 'M3 11l4-3.5L11 11'], High: [up], Low: [dn], Lowest: [dn, 'M3 9l4 3.5L11 9'],
    Medium: ['M3 6h8', 'M3 9h8'],
  }[p] || [up]
  return (
    <svg className="jira-prio-ic" width="13" height="13" viewBox="0 0 14 14" title={`Priority: ${p}`} aria-label={p}>
      {paths.map((d, i) => <path key={i} d={d} stroke={color} strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />)}
    </svg>
  )
}

const JIRA_PRIORITIES = ['Highest', 'High', 'Medium', 'Low', 'Lowest']

// Expandable detail for a Jira card (Phase 4c): edit assignee/priority + comments.
function JiraDetail({ pid, card, onChanged }) {
  const m = card.meta || {}
  const key = card.external_id
  const [comments, setComments] = useState(null)
  const [users, setUsers] = useState(null)
  const [asgId, setAsgId] = useState('')
  const [prio, setPrio] = useState(m.priority || '')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const loadComments = useCallback(() => {
    jiraComments(pid, key).then((d) => setComments(d.comments)).catch((e) => setErr(e.message))
  }, [pid, key])
  useEffect(() => {
    loadComments()
    jiraAssignable(pid).then((d) => {
      setUsers(d.users)
      const cur = d.users.find((u) => u.name === m.assignee)
      setAsgId(cur ? cur.account_id : '')
    }).catch(() => setUsers([]))
  }, [pid, key, loadComments, m.assignee])

  async function run(fn) {
    setBusy(true); setErr('')
    try { await fn(); onChanged?.() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  const changeAssignee = (e) => {
    const id = e.target.value, name = id ? e.target.selectedOptions[0].text : null
    setAsgId(id)
    run(() => jiraEditIssue(pid, key, { assignee_account_id: id, assignee_name: name }))
  }
  const changePriority = (e) => { setPrio(e.target.value); run(() => jiraEditIssue(pid, key, { priority: e.target.value })) }
  async function addComment() {
    if (!text.trim()) return
    await run(() => jiraAddComment(pid, key, text.trim()))
    setText(''); loadComments()
  }

  return (
    <div className="jira-detail" onClick={(e) => e.stopPropagation()}>
      {err && <div className="alert error">{err}</div>}
      <div className="jira-edit-row">
        <label className="jira-edit-field">Assignee
          {users
            ? (
              <select disabled={busy} value={asgId} onChange={changeAssignee}>
                <option value="">Unassigned</option>
                {users.map((u) => <option key={u.account_id} value={u.account_id}>{u.name}</option>)}
              </select>
            ) : <select disabled><option>Loading…</option></select>}
        </label>
        <label className="jira-edit-field">Priority
          <select disabled={busy} value={prio} onChange={changePriority}>
            {!JIRA_PRIORITIES.includes(prio) && <option value="">{prio || '—'}</option>}
            {JIRA_PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </div>
      <div className="jira-comments">
        {comments === null && <div className="skeleton" style={{ height: 28 }} />}
        {comments && comments.length === 0 && <p className="faint" style={{ margin: 0 }}>No comments yet.</p>}
        {(comments || []).map((c, i) => (
          <div key={i} className="jira-comment">
            <span className="jira-comment-author">{c.author}</span>
            <span className="jira-comment-body">{c.body}</span>
          </div>
        ))}
      </div>
      <div className="jira-comment-add">
        <textarea rows={2} placeholder="Add a comment…" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn sm" disabled={busy || !text.trim()} onClick={addComment}>Comment</button>
      </div>
    </div>
  )
}

// Mirror of a Jira issue (source='jira'), styled like an Atlassian board card: epic
// tag, summary, then a footer with type icon + key + priority + story points +
// assignee. Title/desc are edited in Jira; moving runs the real workflow transition.
function JiraCard({ card, onMove, moving, pid, onChanged }) {
  const m = card.meta || {}
  const prioSlug = JIRA_PRIO[m.priority] || 'none'
  const [open, setOpen] = useState(false)
  return (
    <div className={`board-card jira-card prio-${prioSlug} ${card.status === 'done' ? 'is-done' : ''}`}>
      {m.epic_key && (
        <a className="jira-epic" href={card.external_url} target="_blank" rel="noreferrer"
          style={{ background: avatarColor(m.epic_name || m.epic_key) }}>{m.epic_name || m.epic_key}</a>
      )}
      <a className="jira-card-title" href={card.external_url} target="_blank" rel="noreferrer">{card.title}</a>
      {m.labels?.length > 0 && (
        <div className="jira-labels">{m.labels.map((l) => <span key={l} className="badge">{l}</span>)}</div>
      )}
      <div className="jira-card-foot">
        <span className="jira-foot-left">
          <JiraTypeIcon type={m.type} />
          <a className="jira-key" href={card.external_url} target="_blank" rel="noreferrer">{card.external_id}</a>
        </span>
        <span className="jira-foot-right">
          {m.priority && <JiraPrioIcon priority={m.priority} />}
          {m.points != null && <span className="jira-points" title={`${m.points} story points`}>{m.points}</span>}
          {m.assignee
            ? <span className="jira-avatar" title={m.assignee} style={{ background: avatarColor(m.assignee) }}>{initials(m.assignee)}</span>
            : <span className="jira-avatar unassigned" title="Unassigned">?</span>}
        </span>
      </div>
      <div className="jira-moves-row">
        <button className="board-move" onClick={() => setOpen((o) => !o)}
          title="Comments, assignee & priority">{open ? 'Hide' : 'Details'}</button>
        <div className="board-card-moves">
          {moving && <span className="faint jira-move-hint">Moving…</span>}
          {COLUMNS.filter((t) => t.id !== card.status).map((t) => (
            <button key={t.id} className="board-move" title={`Transition ${card.external_id} to ${t.label} in Jira`}
              disabled={moving} onClick={() => onMove(t.id)}>{MOVE_LABEL[t.id]}</button>
          ))}
        </div>
      </div>
      {open && <JiraDetail pid={pid} card={card} onChanged={onChanged} />}
    </div>
  )
}

function Card({ card, onMove, onSave, onDelete, onJiraMove, jiraMoving, onJiraChanged, pid, colId }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(card.title)
  const [body, setBody] = useState(card.body || '')
  const dirty = title !== card.title || body !== (card.body || '')

  if (card.source === 'jira') return <JiraCard card={card} onMove={onJiraMove} moving={jiraMoving} pid={pid} onChanged={onJiraChanged} />

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
  const [movingKey, setMovingKey] = useState(null)  // Jira key mid-transition
  const [assigneeFilter, setAssigneeFilter] = useState(null)  // filter Jira cards by assignee

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
  // Move a Jira card = run the real Jira transition, then refresh the board.
  async function jiraMove(key, to) {
    setError(''); setMovingKey(key)
    try { await jiraTransition(pid, key, to); poll() }
    catch (err) { setError(err.message); poll() }
    finally { setMovingKey(null) }
  }

  const agents = data?.agents || []
  const cards = data?.cards || []
  // Distinct Jira assignees, for the filter chips.
  const assignees = [...new Set(cards.filter((c) => c.source === 'jira').map((c) => c.meta?.assignee).filter(Boolean))].sort()
  const visible = assigneeFilter
    ? cards.filter((c) => c.source === 'jira' && c.meta?.assignee === assigneeFilter)
    : cards

  return (
    <div className="stack-4">
      <JiraPanel pid={pid} />
      {cards.some((c) => c.source === 'jira') && <JiraSprint pid={pid} />}
      <section className="panel">
        <header className="panel-head">
          <h2>Board</h2>
          {!adding && <button className="btn sm" onClick={() => setAdding(true)}>New ticket</button>}
        </header>
        <div className="panel-body">
          {error && <div className="alert error">{error}</div>}
          {assignees.length > 0 && (
            <div className="jira-filter">
              <span className="faint jira-filter-label">Assignee</span>
              <button className={`jira-chip ${!assigneeFilter ? 'on' : ''}`} onClick={() => setAssigneeFilter(null)}>All</button>
              {assignees.map((a) => (
                <button key={a} className={`jira-chip ${assigneeFilter === a ? 'on' : ''}`} onClick={() => setAssigneeFilter(a)}>
                  <span className="jira-avatar sm" style={{ background: avatarColor(a) }}>{initials(a)}</span>{a}
                </button>
              ))}
            </div>
          )}
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
              const colCards = visible.filter((c) => (STATUSES.includes(c.status) ? c.status : 'todo') === col.id)
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
                        onDelete={() => act(() => deleteCard(pid, c.id))}
                        onJiraMove={(to) => jiraMove(c.external_id, to)}
                        jiraMoving={movingKey === c.external_id}
                        pid={pid} onJiraChanged={poll} />
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
