// Activity feed — one attention-ranked stream of what's happening across a
// project's agents. Blockers and stuck agents float to the top; progress and
// claims/pushes sit below as ambient context. The `needs_you` count (rendered
// as a tab badge by Project) is the product: 0 = keep working, >0 = look.
// Presentational: data is polled by Project and passed in; onPing posts a
// heads-up to the project discussion.
import { useState } from 'react'

// Structural events (derived by the platform).
const META = {
  decision: { icon: '❓', label: 'Decision needed', cls: 'high' },
  blocker: { icon: '⚠', label: 'Conflict', cls: 'high' },
  stuck: { icon: '◴', label: 'Stuck', cls: 'high' },
  progress: { icon: '✓', label: 'Progress', cls: 'low' },
  claim: { icon: '🔒', label: 'Claim', cls: 'low' },
  push: { icon: '⬆', label: 'Push', cls: 'low' },
}

// AI-distilled / agent signals carry a DYNAMIC category (any label the AI picks).
// We don't map to a fixed list — we render the label as-is and color by severity.
function metaFor(e) {
  if (e.type === 'signal') {
    const label = (e.kind || 'update').replace(/(^|\s)\S/g, (c) => c.toUpperCase())
    return { icon: e.severity === 'high' ? '⚠' : '▸', label, cls: e.severity === 'high' ? 'high' : 'low' }
  }
  return META[e.type] || { icon: '•', label: e.type, cls: 'low' }
}

function ago(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

const FILTERS = [
  { id: 'attention', label: 'Needs you' },
  { id: 'all', label: 'All' },
]

export default function ActivityPanel({ data, onPing, onDecide }) {
  const [filter, setFilter] = useState('attention')
  const [pinged, setPinged] = useState({})
  const [deciding, setDeciding] = useState({})

  if (!data) return <div className="app-fallback muted">Loading activity…</div>

  const { needs_you = 0, events = [] } = data
  const shown = filter === 'attention'
    ? events.filter((e) => e.severity === 'high')
    : events

  async function ping(e, idx) {
    if (!onPing) return
    await onPing(e)
    setPinged((p) => ({ ...p, [idx]: true }))
    setTimeout(() => setPinged((p) => ({ ...p, [idx]: false })), 2000)
  }

  async function decide(e, option) {
    if (!onDecide) return
    setDeciding((d) => ({ ...d, [e.decision_id]: option }))
    await onDecide(e, option)
    // the next poll drops the answered decision from the feed
  }

  return (
    <section className="card flush">
      <div className="card-head">
        <h2>Activity</h2>
        <span className={`act-count ${needs_you > 0 ? 'on' : ''}`}>
          {needs_you > 0 ? `${needs_you} need${needs_you === 1 ? 's' : ''} you` : 'all clear'}
        </span>
      </div>

      <div className="card-body">
        <div className="act-filters">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              className={`act-filter ${filter === f.id ? 'on' : ''}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
              {f.id === 'attention' && needs_you > 0 && <span className="act-dot">{needs_you}</span>}
            </button>
          ))}
        </div>

        {shown.length === 0 ? (
          <p className="muted">
            {filter === 'attention'
              ? 'Nothing needs you right now — agents are working cleanly.'
              : 'No activity yet. Agent progress, claims, and conflicts will appear here live.'}
          </p>
        ) : (
          <ul className="act-list">
            {shown.map((e, i) => {
              const m = metaFor(e)
              const actionable = e.severity === 'high'
              const agent = e.agents && e.agents[0]
              return (
                <li key={i} className={`act-row ${m.cls}`}>
                  <span className="act-icon" aria-hidden>{m.icon}</span>
                  <div className="act-body">
                    <div className="act-top">
                      <span className="act-tag">{m.label}</span>
                      {agent && <span className="act-agent">{agent}</span>}
                      <span className="ts">{ago(e.ts)}</span>
                    </div>
                    <div className="act-title">{e.title}</div>
                    {e.detail && <div className="act-detail">{e.detail}</div>}
                    {e.type === 'decision' && (
                      <div className="act-options">
                        {(e.options || []).map((opt) => (
                          <button
                            key={opt}
                            className={`btn sm act-opt ${deciding[e.decision_id] === opt ? 'chosen' : ''}`}
                            onClick={() => decide(e, opt)}
                            disabled={!!deciding[e.decision_id]}
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  {actionable && e.type !== 'decision' && (
                    <button
                      className="btn ghost sm act-action"
                      onClick={() => ping(e, i)}
                      disabled={pinged[i]}
                    >
                      {pinged[i] ? 'Pinged' : 'Ping in chat'}
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}
