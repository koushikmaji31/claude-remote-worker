// Branch-history graph (Phase 3): commit DAG for the linked repo, laid out as
// colored lanes (one per branch) with a synced HTML row list for messages.
// Data comes from /api/projects/{pid}/github/graph; lane palette lives in
// styles.css (--lane-1..8, validated for both themes).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ghGraph } from '../lib/github'

const LANE_W = 18
const ROW_H = 30
const PAD_Y = 12
const MAX_COLOR_LANES = 8 // fixed palette slots; extra lanes fall back to gray + label

const laneColor = (lane) =>
  lane < MAX_COLOR_LANES ? `var(--lane-${lane + 1})` : 'var(--text-faint)'

function relTime(iso) {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`
  return new Date(iso).toLocaleDateString()
}

// Lay the DAG out: lane per branch (default branch first, then most recently
// updated), row per commit (newest first, as delivered by the API).
function buildModel({ branches, commits, pulls, default_branch }) {
  const rowIndex = new Map(commits.map((c, i) => [c.sha, i]))
  const ordered = [...branches].sort((a, b) => {
    if (a.name === default_branch) return -1
    if (b.name === default_branch) return 1
    return (rowIndex.get(a.tip) ?? Infinity) - (rowIndex.get(b.tip) ?? Infinity)
  })
  const laneOf = new Map(ordered.map((b, i) => [b.name, i]))
  const tipsAt = new Map() // sha -> branch names whose tip is this commit
  for (const b of ordered) {
    if (!tipsAt.has(b.tip)) tipsAt.set(b.tip, [])
    tipsAt.get(b.tip).push(b.name)
  }
  const prByHead = new Map(pulls.map((p) => [p.head, p]))

  const edges = []
  commits.forEach((c, i) => {
    const lane = laneOf.get(c.branch) ?? 0
    c.parents.forEach((sha, k) => {
      const j = rowIndex.get(sha)
      if (j === undefined) {
        // Parent beyond the fetch window: draw a fading stub instead of an edge.
        edges.push({ stub: true, i, lane })
        return
      }
      const pLane = laneOf.get(commits[j].branch) ?? 0
      // First-parent edges carry the child's lane color (a branch growing);
      // extra parents carry the parent's (the branch being merged in).
      edges.push({ i, j, a: lane, b: pLane, color: k === 0 ? lane : pLane })
    })
  })
  return { ordered, laneOf, tipsAt, prByHead, edges }
}

export default function BranchGraph({ pid }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hover, setHover] = useState(-1)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await ghGraph(pid))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [pid])

  useEffect(() => { load() }, [load])

  const model = useMemo(() => (data ? buildModel(data) : null), [data])

  if (error) return <div className="alert error">{error}</div>
  if (!data || !model) return <div className="skeleton" style={{ height: 220 }} />

  const { commits, repo, truncated, commits_per_branch } = data
  const { ordered, laneOf, tipsAt, prByHead, edges } = model

  const laneCount = Math.max(ordered.length, 1)
  const gW = laneCount * LANE_W + 10
  const gH = PAD_Y * 2 + commits.length * ROW_H
  const x = (lane) => lane * LANE_W + LANE_W / 2 + 4
  const y = (i) => PAD_Y + i * ROW_H + ROW_H / 2

  const hovered = hover >= 0 ? commits[hover] : null

  return (
    <div className="gh-graph-wrap">
      {/* Legend doubles as direct labels: every lane is named, color is never alone. */}
      <div className="gh-graph-legend" role="list" aria-label="branches">
        {ordered.map((b) => (
          <span key={b.name} className="gh-lane-chip" role="listitem"
                style={{ '--lane': laneColor(laneOf.get(b.name)) }}>
            <span className="gh-lane-dot" aria-hidden />
            <span className="mono">{b.name}</span>
            {b.name === data.default_branch && <span className="faint">default</span>}
            {b.protected && <span className="faint">protected</span>}
          </span>
        ))}
        {truncated && <span className="faint">…more branches not shown</span>}
      </div>

      <div className="gh-graph-scroll">
        <div className="gh-graph" style={{ minHeight: gH }}>
          <svg width={gW} height={gH} className="gh-graph-svg" aria-hidden>
            {edges.map((e, k) =>
              e.stub ? (
                <line key={k}
                  x1={x(e.lane)} y1={y(e.i)} x2={x(e.lane)} y2={y(e.i) + ROW_H * 0.6}
                  stroke={laneColor(e.lane)} strokeWidth="2" strokeDasharray="2 4"
                  strokeLinecap="round" opacity="0.45"
                />
              ) : e.a === e.b ? (
                <line key={k}
                  x1={x(e.a)} y1={y(e.i)} x2={x(e.b)} y2={y(e.j)}
                  stroke={laneColor(e.color)} strokeWidth="2" strokeLinecap="round"
                />
              ) : (
                <path key={k}
                  d={`M ${x(e.a)} ${y(e.i)} C ${x(e.a)} ${(y(e.i) + y(e.j)) / 2}, ${x(e.b)} ${(y(e.i) + y(e.j)) / 2}, ${x(e.b)} ${y(e.j)}`}
                  fill="none" stroke={laneColor(e.color)} strokeWidth="2" strokeLinecap="round"
                />
              )
            )}
            {commits.map((c, i) => {
              const lane = laneOf.get(c.branch) ?? 0
              return (
                <circle key={c.sha}
                  cx={x(lane)} cy={y(i)} r={hover === i ? 6 : 4.5}
                  fill={laneColor(lane)} stroke="var(--surface)" strokeWidth="2"
                />
              )
            })}
          </svg>

          <div className="gh-graph-rows" style={{ paddingTop: PAD_Y }} onMouseLeave={() => setHover(-1)}>
            {commits.map((c, i) => {
              const tips = tipsAt.get(c.sha)
              return (
                <a key={c.sha}
                   className={`gh-graph-row ${hover === i ? 'hover' : ''}`}
                   style={{ height: ROW_H }}
                   href={`https://github.com/${repo}/commit/${c.sha}`}
                   target="_blank" rel="noreferrer"
                   onMouseEnter={() => setHover(i)}>
                  {tips?.map((name) => {
                    const pr = prByHead.get(name)
                    return (
                      <span key={name} className="gh-lane-chip tip"
                            style={{ '--lane': laneColor(laneOf.get(name)) }}>
                        <span className="gh-lane-dot" aria-hidden />
                        <span className="mono">{name}</span>
                        {pr && <span className="gh-pr-flag">PR #{pr.number}</span>}
                      </span>
                    )
                  })}
                  <span className="gh-graph-msg">{c.message}</span>
                  <span className="faint">{c.author}</span>
                  <span className="faint mono">{c.sha.slice(0, 7)}</span>
                  <span className="faint gh-graph-when">{relTime(c.date)}</span>
                </a>
              )
            })}
          </div>

          {hovered && (
            <div className="gh-graph-tip"
                 style={{ top: PAD_Y + (hover + 1) * ROW_H + 2, left: gW + 8 }}>
              <div className="gh-graph-tip-msg">{hovered.message}</div>
              <div className="faint">
                {hovered.author} · <span className="mono">{hovered.sha.slice(0, 7)}</span>
                {' · '}{hovered.date ? new Date(hovered.date).toLocaleString() : ''}
                {' · '}on <span className="mono">{hovered.branch}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <p className="faint" style={{ margin: '8px 0 0' }}>
        Showing the last {commits_per_branch} commits per branch across {ordered.length} branch{ordered.length === 1 ? '' : 'es'}.
        Rows link to the commit on GitHub.
      </p>
    </div>
  )
}
