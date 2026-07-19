// Overview dashboard — a real snapshot of the workspace. Numbers come from live
// sources: the linked GitHub repo (commits, branches, PRs, issues), the project
// bus/fleet (agents online, active, tokens), the ticket board (done / in
// progress / todo), members, and discussion volume. Repo-dependent stats show a
// "link repo" hint until a repo is linked; nothing here is faked.
// Visual language: gradient hero cards, pill badges, weekly activity bars (.ov-*).
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { getRepoLink, ghGraph, ghIssues } from '../lib/github'
import { getTicket } from '../lib/ticket'

function fmt(n) {
  if (n == null) return '—'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k'
  return String(n)
}

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

const WAIT_HEIGHTS = [10, 18, 8, 22, 12, 26, 9, 16, 20, 11, 24, 14]

function Timeline({ commits, live, pending, delta }) {
  const buckets = useMemo(() => weekBuckets(commits), [commits])
  const max = Math.max(...buckets.map((b) => b.count), 1)
  const up = (delta ?? 0) >= 0
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
      {live && (
        <div className="ov-float-chip">
          <span className="ov-trend" aria-hidden>{up ? '↗' : '↘'}</span>
          <div>
            <div className="ov-chip-title">{up ? 'More commits' : 'Fewer commits'} lately</div>
            <div className="faint">{up ? '+' : ''}{delta} vs prior 30 days</div>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ icon, name, value, unit, pill, spark }) {
  return (
    <div className="ov-tile">
      <div className="ov-tile-head">
        <span className="ov-tile-icon" aria-hidden>{icon}</span>
        <span>{name}</span>
        {pill && <span className="ov-pill">{pill}</span>}
      </div>
      <div className="ov-tile-value">
        <span className="ov-num md">{value}</span>
        <span className="ov-unit">{unit}</span>
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
  const [fleet, setFleet] = useState(null)   // /fleet summary + agents
  const [board, setBoard] = useState(null)   // {total, todo, doing, done}
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
      } catch { /* unlinked or GitHub unavailable */ }
      try { const m = await api(`/api/projects/${pid}/messages`); if (on) setMsgCount(m.messages.length) } catch { /* ignore */ }
      try { const f = await api(`/api/projects/${pid}/fleet`); if (on) setFleet(f) } catch { /* ignore */ }
      try {
        const t = await getTicket(pid)
        const cards = t.cards || []
        const c = { total: cards.length, todo: 0, doing: 0, done: 0 }
        for (const card of cards) {
          const s = ['todo', 'doing', 'done'].includes(card.status) ? card.status : 'todo'
          c[s]++
        }
        if (on) setBoard(c)
      } catch { /* ignore */ }
      if (on) setLoaded(true)
    })()
    return () => { on = false }
  }, [pid])

  const live = !!gh
  const commits30 = useMemo(() => {
    if (!gh) return null
    const cut = Date.now() - 30 * 86400e3
    return gh.graph.commits.filter((c) => c.date && new Date(c.date).getTime() > cut).length
  }, [gh])
  const commitDelta = useMemo(() => {
    if (!gh) return null
    const now = Date.now(), d30 = 30 * 86400e3
    const inRange = (c, a, b) => c.date && new Date(c.date).getTime() > now - a && new Date(c.date).getTime() <= now - b
    const cur = gh.graph.commits.filter((c) => inRange(c, d30, 0)).length
    const prev = gh.graph.commits.filter((c) => inRange(c, 2 * d30, d30)).length
    return cur - prev
  }, [gh])

  const members = project.members.length
  const pulls = gh ? gh.graph.pulls : []
  const issues = gh ? gh.issues : []
  const openPrs = gh ? pulls.length : null
  const openIssues = gh ? issues.length : null
  const branches = gh ? gh.graph.branches.length : null

  // Fleet (real)
  const online = fleet?.online ?? null
  const working = fleet?.working ?? null
  const needsYou = fleet?.needs_you ?? 0
  const agentCount = fleet?.agents?.length ?? null
  const tokens = useMemo(() => {
    if (!fleet?.agents) return null
    return fleet.agents.reduce((s, a) => s + ((a.metrics?.tokens_in || 0) + (a.metrics?.tokens_out || 0)), 0)
  }, [fleet])
  const editing = fleet?.agents ? fleet.agents.filter((a) => (a.files || []).length > 0).length : 0

  // Board (real)
  const bTotal = board?.total ?? 0
  const bDone = board?.done ?? 0
  const bDoing = board?.doing ?? 0
  const bPct = bTotal ? Math.round((bDone / bTotal) * 100) : 0

  const commitSpark = useMemo(() => {
    const b = weekBuckets(gh?.graph.commits).map((x) => x.count)
    const max = Math.max(...b, 1)
    return b.slice(-8).map((v) => v / max)
  }, [gh])

  const attention = [
    ...pulls.slice(0, 2).map((p) => ({
      key: `pr${p.number}`, kind: 'PR', danger: p.draft,
      text: `#${p.number} ${p.title}`, sub: `${p.head} → ${p.base}${p.draft ? ' · draft' : ''}`, href: p.html_url,
    })),
    ...[...issues].sort((a, b) => b.comments - a.comments).slice(0, 2).map((i) => ({
      key: `is${i.number}`, kind: 'Issue', danger: i.comments > 5,
      text: `#${i.number} ${i.title}`, sub: `${i.comments} comments · @${i.user}`, href: i.html_url,
    })),
  ]

  return (
    <div className="ov">
      {/* Top stat strip */}
      <div className="ov-stats">
        <Stat ready={loaded} value={live ? commits30 : '—'} label="Commits · 30d" pill={live ? undefined : 'link repo'} pillClass="warn" />
        <Stat ready={loaded} value={live ? branches : '—'} label="Active branches" pill={live ? undefined : 'link repo'} pillClass="warn" />
        <Stat ready={loaded} value={live ? openPrs : '—'} label="Open PRs"
              pill={live ? (openPrs > 6 ? 'review debt' : undefined) : 'link repo'} pillClass="warn" />
        <Stat ready={loaded} value={live ? openIssues : '—'} label="Open issues"
              pill={live ? (openIssues > 20 ? 'out of range' : undefined) : 'link repo'} pillClass="warn" />
        <Stat ready={fleet !== null} value={online ?? 0} label="Agents online" />
        <Stat ready={fleet !== null} value={working ?? 0} label="Active agents" pill={needsYou > 0 ? `${needsYou} need you` : undefined} pillClass="warn" />
        <Stat value={members} label="Team members" />
        <Stat ready={msgCount !== null} value={msgCount ?? 0} label="Messages" />
      </div>

      {/* Commit activity timeline (per week) */}
      <Timeline commits={gh?.graph.commits} live={live} pending={!loaded} delta={commitDelta} />

      {/* Hero cards — real board progress + real fleet */}
      <div className="ov-hero">
        <div className="ov-card score">
          <div className="ov-card-title">Board progress</div>
          <div className="ov-num hero">{bPct}%</div>
          <div className="ov-card-sub">{bDone} of {bTotal} tickets done{bDoing ? ` · ${bDoing} in progress` : ''}</div>
          <div className="ov-card-ruler" aria-hidden>
            {Array.from({ length: 32 }, (_, i) => <span key={i} className={i === Math.round((bPct / 100) * 31) ? 'mark' : ''} />)}
          </div>
        </div>

        <div className="ov-card sprint">
          <div className="ov-card-title">Fleet</div>
          <div className="ov-num hero">{working ?? 0}</div>
          <div className="ov-card-sub">active · {online ?? 0} online{needsYou > 0 ? ` · ${needsYou} need you` : ''}</div>
          <div className="ov-card-ruler" aria-hidden>
            {Array.from({ length: 32 }, (_, i) => <span key={i} className={agentCount && i === Math.min(31, Math.round(((working ?? 0) / Math.max(agentCount, 1)) * 31)) ? 'mark' : ''} />)}
          </div>
        </div>

        <div className="ov-side">
          <div className="ov-panel">
            <div className="ov-panel-head">Agent collaboration</div>
            <div className="ov-panel-big">
              <span className="ov-num md">{fmt(tokens)}</span>
              <span className="ov-unit">tokens processed</span>
            </div>
            <p className="faint" style={{ margin: '6px 0 0' }}>
              Across {agentCount ?? 0} agent{agentCount === 1 ? '' : 's'} coordinating over the project bus
              {msgCount !== null && <> · {msgCount} messages exchanged</>}.
            </p>
          </div>
          <div className="ov-panel">
            <div className="ov-panel-head">Work in progress</div>
            <div className="ov-panel-big">
              <span className="ov-num md">{bDoing}</span>
              <span className="ov-unit">ticket{bDoing === 1 ? '' : 's'} in progress</span>
            </div>
            <p className="faint" style={{ margin: '6px 0 0' }}>
              {editing > 0 ? `${editing} agent${editing === 1 ? '' : 's'} currently editing files.` : 'No agents are editing files right now.'}
            </p>
          </div>
        </div>
      </div>

      {/* Live metric tiles */}
      <div className="ov-section-head">
        <h3>Metrics</h3>
        <p className="faint">Pulled straight from the repo, the bus, and the board.</p>
      </div>
      <div className="ov-tiles">
        <Tile icon="◷" name="Commits" value={live ? commits30 : '—'} unit="last 30 days" pill={live ? undefined : 'link repo'}
              spark={live ? commitSpark : undefined} />
        <Tile icon="✓" name="Tickets done" value={bDone} unit={`of ${bTotal}`} pill={bTotal && bDone === bTotal ? 'all done' : undefined} />
        <Tile icon="⇄" name="Open PRs" value={live ? openPrs : '—'} unit="to review" pill={live ? undefined : 'link repo'} />
        <Tile icon="◎" name="Active agents" value={working ?? 0} unit={`of ${agentCount ?? 0} online`} />
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
          Link a GitHub repository in the Branches tab to fill in the repo metrics above.
        </p>
      )}
    </div>
  )
}
