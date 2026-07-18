// Sprint progress + burndown (Phase 4d). Data from /api/projects/{pid}/jira/sprint,
// computed from the mirrored Jira cards. Charts follow the dataviz method: status
// colors (labeled, never color-alone) for the distribution; a single "remaining"
// series + a gray dashed "ideal" reference for the burndown.
import { useEffect, useState, useCallback } from 'react'
import { jiraSprint } from '../lib/jira.js'

const STATUS = [
  { id: 'todo', label: 'To Do', color: 'var(--text-faint)' },
  { id: 'doing', label: 'In Progress', color: 'var(--info)' },
  { id: 'done', label: 'Done', color: 'var(--success)' },
]

function fmtDay(iso) {
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// Inline SVG burndown: ideal (gray dashed reference) vs remaining (brand line).
function Burndown({ series, scope }) {
  if (!series?.length || scope <= 0) {
    return <p className="faint" style={{ margin: 0 }}>No story points to chart yet.</p>
  }
  const W = 560, H = 200, padL = 34, padR = 12, padT = 12, padB = 26
  const n = series.length
  const x = (i) => padL + (i / (n - 1)) * (W - padL - padR)
  const y = (v) => padT + (1 - v / scope) * (H - padT - padB)
  const line = (key) => series.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(' ')
  const last = series[n - 1]
  const ticks = [0, 0.5, 1].map((f) => Math.round(scope * f))
  return (
    <figure className="jira-burndown">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Sprint burndown chart">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="bd-grid" />
            <text x={padL - 6} y={y(t) + 3} className="bd-axis" textAnchor="end">{t}</text>
          </g>
        ))}
        <text x={padL} y={H - 8} className="bd-axis" textAnchor="start">{fmtDay(series[0].day)}</text>
        <text x={W - padR} y={H - 8} className="bd-axis" textAnchor="end">{fmtDay(last.day)}</text>
        <path d={line('ideal')} className="bd-ideal" />
        <path d={line('remaining')} className="bd-remaining" />
        <circle cx={x(n - 1)} cy={y(last.remaining)} r="3.5" className="bd-endpoint" />
      </svg>
      <figcaption className="jira-legend">
        <span className="jira-legend-item"><span className="jira-legend-swatch remaining" /> Remaining</span>
        <span className="jira-legend-item"><span className="jira-legend-swatch ideal" /> Ideal</span>
      </figcaption>
    </figure>
  )
}

export default function JiraSprint({ pid }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => { jiraSprint(pid).then(setData).catch((e) => setError(e.message)) }, [pid])
  useEffect(() => { load(); const iv = setInterval(load, 10000); return () => clearInterval(iv) }, [load])

  if (error) return null
  if (!data) return <section className="panel"><div className="panel-body"><div className="skeleton" style={{ height: 120 }} /></div></section>

  const { scope, completed, remaining, by_status, counts } = data
  const pct = scope > 0 ? Math.round((completed / scope) * 100) : 0

  return (
    <section className="panel">
      <header className="panel-head"><h2>Sprint</h2><span className="tag">{pct}% complete</span></header>
      <div className="panel-body jira-sprint">
        <div className="jira-stats">
          <div className="jira-stat"><div className="jira-stat-n">{scope}</div><div className="jira-stat-l">Scope (pts)</div></div>
          <div className="jira-stat"><div className="jira-stat-n">{completed}</div><div className="jira-stat-l">Done</div></div>
          <div className="jira-stat"><div className="jira-stat-n">{remaining}</div><div className="jira-stat-l">Remaining</div></div>
          <div className="jira-stat"><div className="jira-stat-n">{data.total}</div><div className="jira-stat-l">Issues</div></div>
        </div>

        {/* Status distribution — points per status (labeled status colors) */}
        <div className="jira-dist">
          <div className="jira-dist-bar" role="img" aria-label="Story points by status">
            {STATUS.map((s) => {
              const v = by_status[s.id] || 0
              return v > 0 ? <span key={s.id} className="jira-dist-seg" style={{ flexGrow: v, background: s.color }} title={`${s.label}: ${v} pts`} /> : null
            })}
          </div>
          <div className="jira-legend">
            {STATUS.map((s) => (
              <span key={s.id} className="jira-legend-item">
                <span className="jira-legend-swatch" style={{ background: s.color }} />
                {s.label} <span className="faint">· {by_status[s.id] || 0} pts · {counts[s.id] || 0}</span>
              </span>
            ))}
          </div>
        </div>

        <div className="jira-burndown-wrap">
          <div className="jira-burndown-title">Burndown · last {data.window_days} days</div>
          <Burndown series={data.burndown} scope={scope} />
        </div>
      </div>
    </section>
  )
}
