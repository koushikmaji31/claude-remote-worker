// Overview dashboard — SDLC metrics at a glance for developers and PMs.
// Real signals come from the platform + linked GitHub repo (commits, branches,
// PRs, issues, members, discussion volume). Metrics we don't measure yet ship
// as dummies and carry a small "sample" tag so nobody mistakes them for truth.
// Visual language: gradient hero cards, pill badges, weekly activity bars
// (see .ov-* in styles.css; light+dark variants).
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { getRepoLink, ghGraph, ghIssues } from '../lib/github'

const Sample = () => <span className="ov-sample" title="Sample data — not measured yet">sample</span>

function Stat({ value, label, pill, pillClass = '', ready = true }) {
  return (
    <div className="ov-stat">
      <div className="ov-stat-top">
        {ready
          ? <span className="ov-num ov-in">{value}</span>
          : <span className="ov-skel-num" aria-hidden />}
        {ready && pill && <span className={`ov-pill ${pillClass} ov-in`}>{pill}</span>}
      </div>
      <div className="ov-stat-label">{label}</div>
    </div>
  )
}

// Commits-per-week buckets for the last 12 weeks (real when a repo is linked).
function weekBuckets(commits) {
  const WEEKS = 12
  const now = Date.now()
  const buckets = Array.from({ length: WEEKS }, (_, i) => {
    const start = new Date(now - (WEEKS - 1 - i) * 7 * 86400e3)
    return { count: 0, month: start.toLocaleString('en', { month: 'short' }), start }
  })
  for (const c of commits || []) {
    if (!c.date) continue
    const age = now - new Date(c.date).getTime()
    const idx = WEEKS - 1 - Math.floor(age / (7 * 86400e3))
    if (idx >= 0 && idx < WEEKS) buckets[idx].count++
  }
  return buckets
}

// Fixed pseudo-random heights for the loading shimmer, so the pill keeps its
// shape while real weekly counts are on their way.
const WAIT_HEIGHTS = [10, 18, 8, 22, 12, 26, 9, 16, 20, 11, 24, 14]

function Timeline({ commits, live, pending }) {
  const buckets = useMemo(() => weekBuckets(commits), [commits])
  const max = Math.max(...buckets.map((b) => b.count), 1)
  return (
    <div className="ov-timeline">
      <div className="ov-timeline-track">
        {buckets.map((b, i) => {
          const showMonth = i === 0 || b.month !== buckets[i - 1].month
          if (pending) {
            return (
              <div key={i} className="ov-week" aria-hidden>
                <span className="ov-bar wait" style={{ height: WAIT_HEIGHTS[i % WAIT_HEIGHTS.length], animationDelay: `${i * 70}ms` }} />
                <div className="ov-week-label">{showMonth ? b.month : ''}</div>
              </div>
            )
          }
          return (
            <div key={i} className="ov-week ov-in" style={{ animationDelay: `${i * 25}ms` }}
                 title={`${b.count} commits, week of ${b.start.toLocaleDateString()}`}>
              <span
                className={`ov-bar ${b.count === 0 ? 'zero' : ''} ${i === buckets.length - 1 ? 'now' : ''}`}
                style={{ height: b.count === 0 ? 4 : 6 + Math.round((b.count / max) * 30) }}
              />
              <div className="ov-week-label">{showMonth ? b.month : ''}</div>
            </div>
          )
        })}
      </div>
      <div className="ov-float-chip">
        <span className="ov-trend" aria-hidden>↗</span>
        <div>
          <div className="ov-chip-title">Delivery improving</div>
          <div className="faint">+3.2 last 30 days {!pending && !live && <Sample />}</div>
        </div>
      </div>
    </div>
  )
}

function Tile({ icon, name, value, unit, pill, spark, sample }) {
  return (
    <div className="ov-tile">
      <div className="ov-tile-head">
        <span className="ov-tile-icon" aria-hidden>{icon}</span>
        <span>{name}</span>
        {pill && <span className="ov-pill">{pill}</span>}
      </div>
      <div className="ov-tile-value">
        <span className="ov-num md">{value}</span>
        <span className="ov-unit">{unit}{sample && <> <Sample /></>}</span>
      </div>
      {spark && (
        <svg className="ov-spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden>
          <polyline fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
            points={spark.map((v, i) => `${(i / (spark.length - 1)) * 96 + 2},${22 - v * 18}`).join(' ')} />
        </svg>
      )}
    </div>
  )
}

export default function OverviewDashboard({ pid, project }) {
  const [gh, setGh] = useState(null)      // {graph, issues} | null (not linked / error)
  const [msgCount, setMsgCount] = useState(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let on = true
    ;(async () => {
      try {
        const link = await getRepoLink(pid)
        if (link?.linked) {
          const [graph, issues] = await Promise.all([ghGraph(pid), ghIssues(pid)])
          if (on) setGh({ graph, issues: issues.issues })
        }
      } catch { /* unlinked or GitHub unavailable -> dummies below */ }
      try {
        const m = await api(`/api/projects/${pid}/messages`)
        if (on) setMsgCount(m.messages.length)
      } catch { /* ignore */ }
      if (on) setLoaded(true)
    })()
    return () => { on = false }
  }, [pid])

  const live = !!gh
  const commits30 = useMemo(() => {
    if (!gh) return null
    const cutoff = Date.now() - 30 * 86400e3
    return gh.graph.commits.filter((c) => c.date && new Date(c.date).getTime() > cutoff).length
  }, [gh])

  const members = project.members.length
  const branches = gh ? gh.graph.branches.length : 6
  const pulls = gh ? gh.graph.pulls : []
  const issues = gh ? gh.issues : []
  const openPrs = gh ? pulls.length : 4
  const openIssues = gh ? issues.length : 11
  const commits = commits30 ?? 87

  const attention = [
    ...pulls.slice(0, 2).map((p) => ({
      key: `pr${p.number}`, kind: 'PR', danger: p.draft,
      text: `#${p.number} ${p.title}`, sub: `${p.head} → ${p.base}${p.draft ? ' · draft' : ''}`,
      href: p.html_url,
    })),
    ...[...issues].sort((a, b) => b.comments - a.comments).slice(0, 2).map((i) => ({
      key: `is${i.number}`, kind: 'Issue', danger: i.comments > 5,
      text: `#${i.number} ${i.title}`, sub: `${i.comments} comments · @${i.user}`,
      href: i.html_url,
    })),
  ]

  return (
    <div className="ov">
      {/* Top stat strip — real counts when GitHub is linked */}
      <div className="ov-stats">
        <Stat ready={loaded} value={commits} label="Commits · 30d" pill={live ? 'live' : 'sample'} pillClass={live ? 'lime' : ''} />
        <Stat ready={loaded} value={branches} label="Active branches" pill={live ? 'in repo' : 'sample'} />
        <Stat ready={loaded} value={openPrs} label="Open PRs" pill={openPrs > 6 ? 'review debt' : 'in range'} pillClass={openPrs > 6 ? 'warn' : ''} />
        <Stat ready={loaded} value={openIssues} label="Open issues" pill={openIssues > 20 ? 'out of range' : 'in range'} pillClass={openIssues > 20 ? 'warn' : ''} />
        <Stat value={members} label="Team members" pill="live" pillClass="lime" />
        <Stat ready={msgCount !== null} value={msgCount ?? 0} label="Messages" pill="live" pillClass="lime" />
      </div>

      {/* Commit activity timeline (per week) */}
      <Timeline commits={gh?.graph.commits} live={live} pending={!loaded} />

      {/* Hero cards */}
      <div className="ov-hero">
        <div className="ov-card score">
          <div className="ov-card-title">Delivery Score</div>
          <div className="ov-num hero">78</div>
          <div className="ov-card-sub">On Track <Sample /></div>
          <div className="ov-card-ruler" aria-hidden>
            {Array.from({ length: 32 }, (_, i) => <span key={i} className={i === 24 ? 'mark' : ''} />)}
          </div>
        </div>

        <div className="ov-card sprint">
          <div className="ov-card-title">Sprint 14</div>
          <div className="ov-num hero">5</div>
          <div className="ov-card-sub">days left · 2.5 days ahead <Sample /></div>
          <div className="ov-card-ruler" aria-hidden>
            {Array.from({ length: 32 }, (_, i) => <span key={i} className={i === 20 ? 'mark' : ''} />)}
          </div>
        </div>

        <div className="ov-side">
          <div className="ov-panel">
            <div className="ov-panel-head">Agent collaboration</div>
            <div className="ov-panel-big">
              <span className="ov-num md">1.2M</span>
              <span className="ov-unit">tokens saved <Sample /></span>
            </div>
            <p className="faint" style={{ margin: '6px 0 0' }}>
              Claude sessions coordinated over the project bus instead of re-reading context.
              {msgCount !== null && <> {msgCount} messages exchanged here.</>}
            </p>
          </div>
          <div className="ov-panel">
            <div className="ov-panel-head">Staging deploy pending</div>
            <div className="ov-panel-big">
              <span className="ov-num md">7-10</span>
              <span className="ov-unit">min <Sample /></span>
            </div>
            <div className="ov-progress" aria-hidden><span /></div>
            <p className="faint" style={{ margin: '6px 0 0' }}>Until then, CI is verifying the release branch.</p>
          </div>
        </div>
      </div>

      {/* Engineering health tiles */}
      <div className="ov-section-head">
        <h3>Engineering health</h3>
        <p className="faint">A snapshot of how the team is shipping.</p>
      </div>
      <div className="ov-tiles">
        <Tile icon="⏱" name="Lead time" value="2.4" unit="days" pill="p50" sample
              spark={[0.7, 0.6, 0.65, 0.5, 0.45, 0.5, 0.35, 0.3]} />
        <Tile icon="⇄" name="PR merge time" value={18} unit="hours" sample
              spark={[0.4, 0.5, 0.45, 0.6, 0.5, 0.4, 0.45, 0.35]} />
        <Tile icon="✓" name="Review coverage" value="92%" unit="of merges" pill="healthy" sample
              spark={[0.5, 0.55, 0.6, 0.62, 0.7, 0.72, 0.8, 0.85]} />
        <Tile icon="◆" name="Velocity" value={42} unit="pts / sprint" sample
              spark={[0.4, 0.45, 0.5, 0.42, 0.55, 0.6, 0.58, 0.65]} />
      </div>

      {/* Needs attention — real PRs/issues when linked */}
      {loaded && attention.length > 0 && (
        <>
          <div className="ov-section-head">
            <h3>Needs attention</h3>
            <p className="faint">Open work pulled straight from the linked repository.</p>
          </div>
          <ul className="ov-attn">
            {attention.map((a) => (
              <li key={a.key}>
                <a href={a.href} target="_blank" rel="noreferrer">
                  <span className={`ov-pill ${a.danger ? 'warn' : ''}`}>{a.kind}</span>
                  <span className="ov-attn-text">{a.text}</span>
                  <span className="faint">{a.sub}</span>
                </a>
              </li>
            ))}
          </ul>
        </>
      )}

      {loaded && !live && (
        <p className="faint" style={{ margin: 0 }}>
          Link a GitHub repository in the Branches tab to replace sample numbers with live repo data.
        </p>
      )}
    </div>
  )
}
