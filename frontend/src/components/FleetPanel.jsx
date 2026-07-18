// Fleet — mission control. Two panes: a scannable agent roster (left) and a live
// narration log (right), with a fleet summary on top. Each roster card shows
// identity (initials avatar) · health · current task · STATE/TOOL/QUEUE · metrics,
// plus inline actions (answer a decision, ping a blocked agent) — the control the
// passive tools lack. Data is one /fleet poll driving both panes.
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'
import { avatarColor } from '../ui/avatarColor.js'
import PeerActivity from './PeerActivity.jsx'

function initials(n) {
  return (n || '?').split(/[\s_-]+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}
function ago(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}
function fmt(n) {
  if (n == null) return null
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}
const HEALTH = {
  blocked: { cls: 'crit', label: 'Blocked' },
  needs_decision: { cls: 'crit', label: 'Needs you' },
  stuck: { cls: 'crit', label: 'Stuck' },
  working: { cls: 'ok', label: 'Active' },
  idle: { cls: 'idle', label: 'Idle' },
  offline: { cls: 'off', label: 'Offline' },
}

function Avatar({ name, size = 40 }) {
  return (
    <span className="fm-avatar" style={{ background: avatarColor(name), width: size, height: size, fontSize: size * 0.4 }}>
      {initials(name)}
    </span>
  )
}

export default function FleetPanel({ pid }) {
  const [data, setData] = useState(null)
  const [bus, setBus] = useState(null)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [nName, setNName] = useState('')
  const [nRole, setNRole] = useState('')
  const [editing, setEditing] = useState(null)
  const [editName, setEditName] = useState('')
  const [copied, setCopied] = useState(null)
  const [rightView, setRightView] = useState('narration') // narration | interactions
  const [inter, setInter] = useState(null)

  const load = useCallback(() => {
    api(`/api/projects/${pid}/fleet`).then((d) => { setData(d); setError('') }).catch((e) => setError(e.message))
  }, [pid])
  useEffect(() => {
    load(); const iv = setInterval(load, 4000)
    api(`/api/projects/${pid}/bus`).then(setBus).catch(() => {})
    return () => clearInterval(iv)
  }, [load, pid])

  // Interactions poll only while that view is open (keeps the default poll lean).
  useEffect(() => {
    if (rightView !== 'interactions') return
    const tick = () => api(`/api/projects/${pid}/interactions`).then(setInter).catch(() => {})
    tick(); const iv = setInterval(tick, 5000)
    return () => clearInterval(iv)
  }, [rightView, pid])

  const createAgent = async () => {
    if (!nName.trim()) return
    await api(`/api/projects/${pid}/agents`, { method: 'POST', body: { name: nName.trim(), role: nRole.trim() } }).catch((e) => setError(e.message))
    setNName(''); setNRole(''); setCreating(false); load()
  }
  const rename = async (a) => {
    if (editName.trim() && editName.trim() !== a.name)
      await api(`/api/projects/${pid}/agents/${a.id}`, { method: 'POST', body: { name: editName.trim(), role: a.role || '' } }).catch((e) => setError(e.message))
    setEditing(null); load()
  }
  const removeAgent = async (a) => { if (window.confirm(`Delete "${a.name}"?`)) { await api(`/api/projects/${pid}/agents/${a.id}`, { method: 'DELETE' }).catch(() => {}); load() } }
  const addToRoster = async (name) => { await api(`/api/projects/${pid}/agents`, { method: 'POST', body: { name, role: '' } }).catch((e) => setError(e.message)); load() }
  const answer = async (did, ans) => { await api(`/api/projects/${pid}/decisions/${did}/answer`, { method: 'POST', body: { answer: ans } }).catch(() => {}); load() }
  const ping = async (a) => {
    const t = a.health === 'blocked'
      ? `⚠ ${a.name}: you're in an edit conflict — coordinate before pushing.`
      : `◴ ${a.name}: looks stuck — need help? (${a.current || 'no recent progress'})`
    await api(`/api/projects/${pid}/messages`, { method: 'POST', body: { text: t } }).catch(() => {})
  }
  const copy = (id, text) => navigator.clipboard.writeText(text).then(() => { setCopied(id); setTimeout(() => setCopied(null), 1400) })

  if (error && !data) return <div className="alert error">Could not load fleet: {error}</div>
  if (!data) return <div className="app-fallback muted">Loading fleet…</div>

  return (
    <div className="fm">
      {/* summary strip */}
      <div className="fm-summary">
        <span className="fm-title">Fleet</span>
        <span className={`fm-pill ${data.needs_you > 0 ? 'crit' : ''}`}>{data.needs_you} need you</span>
        <span className="fm-pill">{data.working} active</span>
        <span className="fm-pill">{data.online} online</span>
        <span className="fm-pill">{data.agents.length} agents</span>
        <button className="btn sm" style={{ marginLeft: 'auto' }} onClick={() => setCreating((v) => !v)}>+ New agent</button>
      </div>

      {creating && (
        <div className="fm-new">
          <input className="coord-search-input" placeholder="Name (e.g. Backend-Bot)" value={nName} onChange={(e) => setNName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && createAgent()} />
          <input className="coord-search-input" placeholder="Role (optional)" value={nRole} onChange={(e) => setNRole(e.target.value)} />
          <button className="btn sm" onClick={createAgent}>Create</button>
        </div>
      )}

      <div className="fm-panes">
        {/* LEFT: roster */}
        <div className="fm-roster">
          {data.agents.length === 0 && <p className="muted">No agents yet. Create one, then bind a terminal with its launch command.</p>}
          {data.agents.map((a) => {
            const h = HEALTH[a.health] || HEALTH.idle
            const m = a.metrics
            return (
              <section key={a.name} className={`fm-card ${h.cls}`}>
                <div className="fm-card-top">
                  <Avatar name={a.name} />
                  <div className="fm-id">
                    {editing === a.name ? (
                      <input className="fm-rename" autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && rename(a)} onBlur={() => rename(a)} />
                    ) : (
                      <div className="fm-name" onClick={() => a.id && (setEditing(a.name), setEditName(a.name))}>{a.name}</div>
                    )}
                    <div className="fm-task">{a.current || (a.live === 'offline' ? 'not running' : 'idle — no active task')}</div>
                    {a.role && <div className="fm-role">{a.role}</div>}
                  </div>
                  <span className={`fm-status ${h.cls}`}>{h.label}</span>
                </div>

                <div className="fm-rows">
                  <div className="fm-row"><span className="fm-k">STATE</span><span className={`fm-v ${h.cls}`}>{h.label}</span></div>
                  <div className="fm-row"><span className="fm-k">TOOL</span><span className="fm-v">{a.tool || '—'}</span></div>
                  <div className="fm-row"><span className="fm-k">QUEUE</span><span className="fm-v">{a.queue.length ? a.queue.map((c) => `#${c.id}`).join(' ') : '—'}</span></div>
                </div>

                {a.files && a.files.length > 0 && (
                  <div className="fm-files">
                    <div className="fm-files-head">Changing {a.files.length} file{a.files.length === 1 ? '' : 's'}</div>
                    {a.files.map((f) => (
                      <div key={f.path} className="fm-file">
                        <span className="mono fm-file-path" title={f.path}>{f.path}</span>
                        <span className="fm-file-counts">
                          {f.added ? <span className="peer-add">+{f.added}</span> : null}
                          {f.removed ? <span className="peer-del">−{f.removed}</span> : null}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="fm-foot">
                  <span>{a.tasks_done}/{a.tasks_total} tasks</span>
                  {m && fmt(m.tokens_in) && <span>· {fmt(m.tokens_in)}↓ {fmt(m.tokens_out)}↑</span>}
                  {m && m.tool_errors > 0 && <span className="crit">· {m.tool_errors} errs</span>}
                </div>

                {/* inline actions */}
                {a.decisions && a.decisions.length > 0 && (
                  <div className="fm-decide">
                    <div className="fm-q">{a.decisions[0].question}</div>
                    <div className="fm-opts">
                      {a.decisions[0].options.map((o) => (
                        <button key={o} className="btn sm" onClick={() => answer(a.decisions[0].id, o)}>{o}</button>
                      ))}
                    </div>
                  </div>
                )}
                {(a.health === 'blocked' || a.health === 'stuck') && (
                  <div className="fm-actions"><button className="btn ghost sm" onClick={() => ping(a)}>Ping in chat</button></div>
                )}

                {(a.planned || a.live === 'offline') && bus && (
                  <div className="fm-bind">
                    <code title={`${bus.command} && CLAUDE_BUS_NAME=${a.name} claude`}>{`${bus.command} && CLAUDE_BUS_NAME=${a.name} claude`}</code>
                    <button className="btn ghost sm" onClick={() => copy(a.name, `${bus.command} && CLAUDE_BUS_NAME=${a.name} claude`)}>{copied === a.name ? 'Copied' : 'Copy launch'}</button>
                  </div>
                )}

                <div className="fm-manage">
                  {a.id ? <button className="fm-x" onClick={() => removeAgent(a)}>Delete</button>
                    : <button className="fm-x" onClick={() => addToRoster(a.name)}>+ Add to roster</button>}
                </div>
              </section>
            )
          })}
          {/* Unassigned-tasks list removed — the backlog lives in Jira; no need to duplicate it here. */}
        </div>

        {/* RIGHT: toggle between narration log and agent-to-agent interactions */}
        <div className="fm-log">
          <div className="fm-log-tabs">
            <button className={`fm-log-tab ${rightView === 'narration' ? 'on' : ''}`} onClick={() => setRightView('narration')}>Narration log</button>
            <button className={`fm-log-tab ${rightView === 'interactions' ? 'on' : ''}`} onClick={() => setRightView('interactions')}>Interactions</button>
          </div>

          {rightView === 'narration' && (
            <>
              {(!data.log || data.log.length === 0) && <p className="muted" style={{ padding: '0 4px' }}>No updates yet. Agent signals appear here live.</p>}
              {(data.log || []).map((s, i) => (
                <div key={i} className="fm-log-row">
                  <Avatar name={s.agent} size={26} />
                  <div className="fm-log-body">
                    <div className="fm-log-meta"><span className="fm-log-name">{s.agent}</span><span className="ts">{ago(s.ts)} ago</span></div>
                    <div className="fm-log-text"><span className={`fm-log-kind ${s.severity}`}>{s.kind}</span> {s.text}</div>
                  </div>
                </div>
              ))}
            </>
          )}

          {rightView === 'interactions' && (
            <>
              {/* Team activity (peer diffs) — who's touching which files right now,
                  with click-to-view live diffs. Moved here from Branches. */}
              <PeerActivity pid={pid} />
              {!inter && <p className="muted" style={{ padding: '0 4px' }}>Loading…</p>}
              {inter && inter.messages.length === 0 && <p className="muted" style={{ padding: '0 4px' }}>No agent-to-agent messages yet. When agents DM each other on the bus, their exchange shows here.</p>}
              {inter && inter.pairs.length > 0 && (
                <div className="fm-pairs">
                  {inter.pairs.map((p, i) => (
                    <div key={i} className="fm-pair" title={p.last_text}>
                      <Avatar name={p.a} size={20} /><span className="fm-pair-x">⇄</span><Avatar name={p.b} size={20} />
                      <span className="fm-pair-c">{p.count}</span>
                    </div>
                  ))}
                </div>
              )}
              {inter && inter.messages.slice().reverse().map((m, i) => (
                <div key={i} className="fm-msg">
                  <Avatar name={m.from} size={22} />
                  <div className="fm-msg-body">
                    <div className="fm-msg-meta">
                      <span className="fm-log-name">{m.from}</span>
                      <span className="fm-arrow">→</span>
                      <span className="fm-log-name">{m.to}</span>
                      <span className="ts">{ago(m.ts)} ago</span>
                    </div>
                    <div className="fm-log-text">{m.text}</div>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
