// Fleet — mission control. Left: a scannable roster of agent cards (click a card
// to open its detail view). Right: the live narration log. Each card is a compact
// status glance; the detail modal holds the heavy stuff — the agent's actual file
// edits (with diffs), its interactions, metrics, launch command and management.
// Data: one /fleet poll drives the roster; the modal lazy-loads diffs + interactions.
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'
import { avatarColor } from '../ui/avatarColor.js'
import { getPeerDiff } from '../lib/peers.js'

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
function diffClass(line) {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'del'
  return ''
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
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [copied, setCopied] = useState(false)
  const [selected, setSelected] = useState(null)  // agent name -> detail modal open
  const [inter, setInter] = useState(null)
  const [openFile, setOpenFile] = useState(null)  // path whose diff is expanded
  const [fileDiff, setFileDiff] = useState(null)
  const [fileErr, setFileErr] = useState('')

  const load = useCallback(() => {
    api(`/api/projects/${pid}/fleet`).then((d) => { setData(d); setError('') }).catch((e) => setError(e.message))
  }, [pid])
  useEffect(() => {
    load(); const iv = setInterval(load, 4000)
    api(`/api/projects/${pid}/bus`).then(setBus).catch(() => {})
    return () => clearInterval(iv)
  }, [load, pid])

  // Interactions poll only while a detail modal is open.
  useEffect(() => {
    if (!selected) return
    const tick = () => api(`/api/projects/${pid}/interactions`).then(setInter).catch(() => {})
    tick(); const iv = setInterval(tick, 5000)
    return () => clearInterval(iv)
  }, [selected, pid])

  // Reset the per-file diff + rename state whenever the selected agent changes.
  useEffect(() => { setOpenFile(null); setFileDiff(null); setFileErr(''); setEditing(false) }, [selected])

  const createAgent = async () => {
    if (!nName.trim()) return
    await api(`/api/projects/${pid}/agents`, { method: 'POST', body: { name: nName.trim(), role: nRole.trim() } }).catch((e) => setError(e.message))
    setNName(''); setNRole(''); setCreating(false); load()
  }
  const rename = async (a) => {
    if (editName.trim() && editName.trim() !== a.name)
      await api(`/api/projects/${pid}/agents/${a.id}`, { method: 'POST', body: { name: editName.trim(), role: a.role || '' } }).catch((e) => setError(e.message))
    setEditing(false); setSelected(editName.trim() || a.name); load()
  }
  const removeAgent = async (a) => { if (window.confirm(`Delete "${a.name}"?`)) { await api(`/api/projects/${pid}/agents/${a.id}`, { method: 'DELETE' }).catch(() => {}); setSelected(null); load() } }
  const addToRoster = async (name) => { await api(`/api/projects/${pid}/agents`, { method: 'POST', body: { name, role: '' } }).catch((e) => setError(e.message)); load() }
  const answer = async (did, ans) => { await api(`/api/projects/${pid}/decisions/${did}/answer`, { method: 'POST', body: { answer: ans } }).catch(() => {}); load() }
  const ping = async (a) => {
    const t = a.health === 'blocked'
      ? `⚠ ${a.name}: you're in an edit conflict — coordinate before pushing.`
      : `◴ ${a.name}: looks stuck — need help? (${a.current || 'no recent progress'})`
    await api(`/api/projects/${pid}/messages`, { method: 'POST', body: { text: t } }).catch(() => {})
  }
  const copyCmd = (text) => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1400) })

  const viewFile = async (machine, path) => {
    if (openFile === path) { setOpenFile(null); setFileDiff(null); return }
    setOpenFile(path); setFileDiff(null); setFileErr('')
    if (!machine) { setFileErr('No live diff source for this agent.'); return }
    try { setFileDiff((await getPeerDiff(pid, machine, path)).diff || '') }
    catch (e) { setFileErr(e.message) }
  }

  if (error && !data) return <div className="alert error">Could not load fleet: {error}</div>
  if (!data) return <div className="app-fallback muted">Loading fleet…</div>

  const sel = selected ? (data.agents || []).find((a) => a.name === selected) : null

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
        {/* LEFT: roster of clickable status cards */}
        <div className="fm-roster">
          {data.agents.length === 0 && <p className="muted">No agents yet. Create one, then bind a terminal with its launch command.</p>}
          {data.agents.map((a) => {
            const h = HEALTH[a.health] || HEALTH.idle
            const m = a.metrics
            const nfiles = (a.files || []).length
            const adds = (a.files || []).reduce((s, f) => s + (f.added || 0), 0)
            const dels = (a.files || []).reduce((s, f) => s + (f.removed || 0), 0)
            return (
              <section key={a.name} className={`fm-card clickable ${h.cls}`} role="button" tabIndex={0}
                       onClick={() => setSelected(a.name)}
                       onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setSelected(a.name)}>
                <div className="fm-card-top">
                  <Avatar name={a.name} />
                  <div className="fm-id">
                    <div className="fm-name">{a.name}</div>
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

                <div className="fm-foot">
                  <span>{a.tasks_done}/{a.tasks_total} tasks</span>
                  {m && fmt(m.tokens_in) && <span>· {fmt(m.tokens_in)}↓ {fmt(m.tokens_out)}↑</span>}
                  {m && m.tool_errors > 0 && <span className="crit">· {m.tool_errors} errs</span>}
                  {nfiles > 0 && (
                    <span className="fm-files-hint">· {nfiles} file{nfiles === 1 ? '' : 's'}
                      {adds ? <span className="peer-add"> +{adds}</span> : null}
                      {dels ? <span className="peer-del"> −{dels}</span> : null}
                    </span>
                  )}
                </div>

                {/* time-sensitive actions stay on the card (stop click-through) */}
                {a.decisions && a.decisions.length > 0 && (
                  <div className="fm-decide" onClick={(e) => e.stopPropagation()}>
                    <div className="fm-q">{a.decisions[0].question}</div>
                    <div className="fm-opts">
                      {a.decisions[0].options.map((o) => (
                        <button key={o} className="btn sm" onClick={() => answer(a.decisions[0].id, o)}>{o}</button>
                      ))}
                    </div>
                  </div>
                )}
                {(a.health === 'blocked' || a.health === 'stuck') && (
                  <div className="fm-actions" onClick={(e) => e.stopPropagation()}><button className="btn ghost sm" onClick={() => ping(a)}>Ping in chat</button></div>
                )}
              </section>
            )
          })}
        </div>

        {/* RIGHT: narration log only */}
        <div className="fm-log">
          <div className="fm-log-head">Narration log</div>
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
        </div>
      </div>

      {/* DETAIL MODAL — the big view for one agent */}
      {sel && (() => {
        const h = HEALTH[sel.health] || HEALTH.idle
        const m = sel.metrics
        const cmd = bus ? `${bus.command} && CLAUDE_BUS_NAME=${sel.name} claude` : ''
        const myMsgs = (inter?.messages || []).filter((x) => x.from === sel.name || x.to === sel.name)
        return (
          <div className="fm-modal-backdrop" onClick={() => setSelected(null)}>
            <div className="fm-modal" onClick={(e) => e.stopPropagation()}>
              <header className="fm-modal-head">
                <Avatar name={sel.name} size={44} />
                <div className="fm-modal-id">
                  {editing ? (
                    <input className="fm-rename" autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
                           onKeyDown={(e) => e.key === 'Enter' && rename(sel)} onBlur={() => rename(sel)} />
                  ) : (
                    <div className="fm-modal-name">
                      {sel.name}
                      {sel.id ? <button className="fm-edit-x" title="Rename" onClick={() => { setEditing(true); setEditName(sel.name) }}>edit</button> : null}
                    </div>
                  )}
                  <div className="fm-modal-sub">
                    <span className={`fm-status ${h.cls}`}>{h.label}</span>
                    {sel.role && <span className="fm-role">{sel.role}</span>}
                    <span className="faint">{sel.live}</span>
                  </div>
                </div>
                <button className="fm-modal-close" onClick={() => setSelected(null)} aria-label="Close">×</button>
              </header>

              <div className="fm-modal-body">
                <div className="fm-rows">
                  <div className="fm-row"><span className="fm-k">STATE</span><span className={`fm-v ${h.cls}`}>{h.label}</span></div>
                  <div className="fm-row"><span className="fm-k">TOOL</span><span className="fm-v">{sel.tool || '—'}</span></div>
                  <div className="fm-row"><span className="fm-k">QUEUE</span><span className="fm-v">{sel.queue.length ? sel.queue.map((c) => `#${c.id} ${c.title}`).join(', ') : '—'}</span></div>
                  <div className="fm-row"><span className="fm-k">TASKS</span><span className="fm-v">{sel.tasks_done}/{sel.tasks_total} done</span></div>
                  {m && fmt(m.tokens_in) && <div className="fm-row"><span className="fm-k">TOKENS</span><span className="fm-v">{fmt(m.tokens_in)}↓ {fmt(m.tokens_out)}↑ · {m.tool_calls || 0} calls{m.tool_errors ? ` · ${m.tool_errors} errs` : ''}</span></div>}
                </div>

                {sel.current && <p className="fm-modal-current">{sel.current}</p>}

                {/* Edits — the agent's live uncommitted changes, click a file for its diff */}
                <div className="fm-modal-section">
                  <div className="fm-section-title">Edits{sel.files.length ? ` · ${sel.files.length} file${sel.files.length === 1 ? '' : 's'}` : ''}</div>
                  {sel.files.length === 0 && <p className="muted" style={{ margin: 0 }}>No uncommitted changes right now.</p>}
                  {sel.files.map((f) => (
                    <div key={f.path} className="fm-editfile">
                      <button className="fm-editfile-btn" onClick={() => viewFile(sel.machine, f.path)}>
                        <span className="mono fm-file-path" title={f.path}>{f.path}</span>
                        <span className="fm-file-counts">
                          {f.added ? <span className="peer-add">+{f.added}</span> : null}
                          {f.removed ? <span className="peer-del">−{f.removed}</span> : null}
                        </span>
                      </button>
                      {openFile === f.path && (
                        <div className="fm-editdiff">
                          {fileErr && <div className="alert error">{fileErr}</div>}
                          {fileDiff === null && !fileErr && <div className="skeleton" style={{ height: 40 }} />}
                          {fileDiff !== null && fileDiff !== '' && (
                            <pre className="git-diff" aria-label={`diff of ${f.path}`}>
                              {fileDiff.split('\n').map((l, i) => <span key={i} className={diffClass(l)}>{l + '\n'}</span>)}
                            </pre>
                          )}
                          {fileDiff === '' && <p className="faint" style={{ margin: 0 }}>(no textual diff)</p>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {/* Interactions — bus messages to/from this agent */}
                <div className="fm-modal-section">
                  <div className="fm-section-title">Interactions</div>
                  {!inter && <p className="muted" style={{ margin: 0 }}>Loading…</p>}
                  {inter && myMsgs.length === 0 && <p className="muted" style={{ margin: 0 }}>No agent-to-agent messages involving {sel.name} yet.</p>}
                  {myMsgs.map((msg, i) => (
                    <div key={i} className="fm-msg">
                      <Avatar name={msg.from} size={22} />
                      <div className="fm-msg-body">
                        <div className="fm-msg-meta">
                          <span className="fm-log-name">{msg.from}</span>
                          <span className="fm-arrow">→</span>
                          <span className="fm-log-name">{msg.to}</span>
                          <span className="ts">{ago(msg.ts)} ago</span>
                        </div>
                        <div className="fm-log-text">{msg.text}</div>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Launch command for planned/offline agents */}
                {(sel.planned || sel.live === 'offline') && cmd && (
                  <div className="fm-modal-section">
                    <div className="fm-section-title">Launch</div>
                    <div className="fm-bind">
                      <code title={cmd}>{cmd}</code>
                      <button className="btn ghost sm" onClick={() => copyCmd(cmd)}>{copied ? 'Copied' : 'Copy launch'}</button>
                    </div>
                  </div>
                )}
              </div>

              <footer className="fm-modal-foot">
                {sel.id
                  ? <button className="btn danger sm" onClick={() => removeAgent(sel)}>Delete agent</button>
                  : <button className="btn sm" onClick={() => addToRoster(sel.name)}>+ Add to roster</button>}
              </footer>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
