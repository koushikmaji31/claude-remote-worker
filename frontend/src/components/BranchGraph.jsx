// Branch-history graph: commit DAG for the linked repo, colored lanes (one per
// branch) with a synced HTML row list. Runs of minor linear commits are SQUASHED
// into one expandable node (click to reveal the individual commits). SVG dots,
// edges and rows are all positioned by the same DISPLAY-row index so they stay
// aligned as groups expand/collapse. Data: /api/projects/{pid}/github/graph.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ghGraph } from '../lib/github'

const LANE_W = 18
const ROW_H = 30
const PAD_Y = 12
const MAX_COLOR_LANES = 8
const MIN_GROUP = 3 // only collapse runs of at least this many minor commits

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

// Lanes + which commits are "significant" (branch tips or merges) vs minor.
function baseModel({ branches, commits, pulls, default_branch }) {
  const rowIndex = new Map(commits.map((c, i) => [c.sha, i]))
  const ordered = [...branches].sort((a, b) => {
    if (a.name === default_branch) return -1
    if (b.name === default_branch) return 1
    return (rowIndex.get(a.tip) ?? Infinity) - (rowIndex.get(b.tip) ?? Infinity)
  })
  const laneOf = new Map(ordered.map((b, i) => [b.name, i]))
  const tipsAt = new Map()
  for (const b of ordered) {
    if (!tipsAt.has(b.tip)) tipsAt.set(b.tip, [])
    tipsAt.get(b.tip).push(b.name)
  }
  const prByHead = new Map(pulls.map((p) => [p.head, p]))
  return { rowIndex, ordered, laneOf, tipsAt, prByHead }
}

// Squash consecutive minor commits (single parent that is the next commit, same
// lane, not a tip/merge) into groups of >= MIN_GROUP. Returns the group list.
function findGroups(commits, rowIndex, tipsAt) {
  const minor = (c, i) =>
    c.parents.length === 1 &&
    !tipsAt.has(c.sha) &&
    rowIndex.get(c.parents[0]) === i + 1 // parent is the very next row -> linear
  const groups = []
  let i = 0
  while (i < commits.length) {
    if (!minor(commits[i], i)) { i++; continue }
    let j = i
    while (j < commits.length && minor(commits[j], j) && commits[j].branch === commits[i].branch) j++
    // j is exclusive; the run i..j-1 is linear + minor on one branch
    if (j - i >= MIN_GROUP) groups.push({ id: commits[i].sha, start: i, end: j, branch: commits[i].branch })
    i = j
  }
  return groups
}

export default function BranchGraph({ pid }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [hover, setHover] = useState(-1)
  const [expanded, setExpanded] = useState(() => new Set()) // group ids the user opened

  const load = useCallback(async () => {
    setError('')
    try { setData(await ghGraph(pid)) } catch (err) { setError(err.message) }
  }, [pid])
  useEffect(() => { load() }, [load])

  const base = useMemo(() => (data ? baseModel(data) : null), [data])

  // Build the display rows (commits + collapsed group placeholders), a sha->row
  // map, and edges over row indices. Recomputed when data or expansion changes.
  const view = useMemo(() => {
    if (!data || !base) return null
    const { commits } = data
    const { rowIndex, laneOf, tipsAt } = base
    const groups = findGroups(commits, rowIndex, tipsAt)
    const groupAt = new Map(groups.map((g) => [g.start, g]))
    const inGroup = new Map() // commit index -> group (only for collapsed groups)
    for (const g of groups) if (!expanded.has(g.id)) for (let k = g.start; k < g.end; k++) inGroup.set(k, g)

    const rows = []            // {kind:'commit'|'group', ...}
    const rowOfSha = new Map() // every sha -> its rendered row index
    for (let i = 0; i < commits.length; i++) {
      const g = groupAt.get(i)
      if (g && !expanded.has(g.id)) {
        const idx = rows.length
        rows.push({ kind: 'group', group: g, lane: laneOf.get(g.branch) ?? 0,
                    count: g.end - g.start, sha: commits[i].sha })
        for (let k = g.start; k < g.end; k++) rowOfSha.set(commits[k].sha, idx)
        i = g.end - 1
      } else {
        const c = commits[i]
        rowOfSha.set(c.sha, rows.length)
        rows.push({ kind: 'commit', c, ci: i, lane: laneOf.get(c.branch) ?? 0,
                    grouped: !!(inGroup.get(i)) })
      }
    }

    const edges = []
    commits.forEach((c) => {
      const ri = rowOfSha.get(c.sha)
      const lane = laneOf.get(c.branch) ?? 0
      c.parents.forEach((sha, k) => {
        const rj = rowOfSha.get(sha)
        if (rj === undefined) { edges.push({ stub: true, i: ri, lane }); return }
        if (rj === ri) return // collapsed into the same group row
        const pLane = laneOf.get((commits.find((x) => x.sha === sha) || {}).branch) ?? 0
        edges.push({ i: ri, j: rj, a: lane, b: pLane, color: k === 0 ? lane : pLane })
      })
    })
    return { rows, edges, groups }
  }, [data, base, expanded])

  if (error) return <div className="alert error">{error}</div>
  if (!data || !base || !view) return <div className="skeleton" style={{ height: 220 }} />

  const { repo, truncated, commits_per_branch } = data
  const { ordered, laneOf, tipsAt, prByHead } = base
  const { rows, edges } = view

  const laneCount = Math.max(ordered.length, 1)
  const gW = laneCount * LANE_W + 10
  const gH = PAD_Y * 2 + rows.length * ROW_H
  const x = (lane) => lane * LANE_W + LANE_W / 2 + 4
  const y = (i) => PAD_Y + i * ROW_H + ROW_H / 2

  const toggle = (id) => setExpanded((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  return (
    <div className="gh-graph-wrap">
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
                <line key={k} x1={x(e.lane)} y1={y(e.i)} x2={x(e.lane)} y2={y(e.i) + ROW_H * 0.6}
                  stroke={laneColor(e.lane)} strokeWidth="2" strokeDasharray="2 4" strokeLinecap="round" opacity="0.45" />
              ) : e.a === e.b ? (
                <line key={k} x1={x(e.a)} y1={y(e.i)} x2={x(e.b)} y2={y(e.j)}
                  stroke={laneColor(e.color)} strokeWidth="2" strokeLinecap="round" />
              ) : (
                <path key={k}
                  d={`M ${x(e.a)} ${y(e.i)} C ${x(e.a)} ${(y(e.i) + y(e.j)) / 2}, ${x(e.b)} ${(y(e.i) + y(e.j)) / 2}, ${x(e.b)} ${y(e.j)}`}
                  fill="none" stroke={laneColor(e.color)} strokeWidth="2" strokeLinecap="round" />
              )
            )}
            {rows.map((r, i) => r.kind === 'group' ? (
              <rect key={i} x={x(r.lane) - 4} y={y(i) - 5} width="8" height="10" rx="2"
                fill={laneColor(r.lane)} stroke="var(--surface)" strokeWidth="2" />
            ) : (
              <circle key={i} cx={x(r.lane)} cy={y(i)} r={hover === i ? 6 : 4.5}
                fill={laneColor(r.lane)} stroke="var(--surface)" strokeWidth="2" />
            ))}
          </svg>

          <div className="gh-graph-rows" style={{ paddingTop: PAD_Y }} onMouseLeave={() => setHover(-1)}>
            {rows.map((r, i) => r.kind === 'group' ? (
              <button key={i} className="gh-graph-row gh-graph-group" style={{ height: ROW_H }}
                      onClick={() => toggle(r.group.id)} onMouseEnter={() => setHover(i)}>
                <span className="gh-group-caret" aria-hidden>{'▸'}</span>
                <span className="gh-graph-msg">{r.count} commits</span>
                <span className="faint">squashed — click to expand</span>
              </button>
            ) : (
              <a key={i}
                 className={`gh-graph-row ${hover === i ? 'hover' : ''} ${r.grouped ? 'gh-graph-member' : ''}`}
                 style={{ height: ROW_H }}
                 href={`https://github.com/${repo}/commit/${r.c.sha}`} target="_blank" rel="noreferrer"
                 onMouseEnter={() => setHover(i)}>
                {tipsAt.get(r.c.sha)?.map((name) => {
                  const pr = prByHead.get(name)
                  return (
                    <span key={name} className="gh-lane-chip tip" style={{ '--lane': laneColor(laneOf.get(name)) }}>
                      <span className="gh-lane-dot" aria-hidden />
                      <span className="mono">{name}</span>
                      {pr && <span className="gh-pr-flag">PR #{pr.number}</span>}
                    </span>
                  )
                })}
                <span className="gh-graph-msg">{r.c.message}</span>
                <span className="faint">{r.c.author}</span>
                <span className="faint mono">{r.c.sha.slice(0, 7)}</span>
                <span className="faint gh-graph-when">{relTime(r.c.date)}</span>
              </a>
            ))}
          </div>
        </div>
      </div>

      <p className="faint" style={{ margin: '8px 0 0' }}>
        Last {commits_per_branch} commits per branch across {ordered.length} branch{ordered.length === 1 ? '' : 'es'}.
        Runs of minor commits are squashed — click a group to expand.
        {data.cached ? ` Served from cache${data.stale_age != null ? ` (${data.stale_age}s old, refreshing)` : ''}.` : ''}
      </p>
    </div>
  )
}
