// GitHub integration panel.
// - Connect via GitHub OAuth (preferred) or a personal access token (fallback).
// - Link the project to a GitHub repo (admin only).
// - Live repo views: branch graph, branches, PRs, issues.
// Uses shared design-system classes; GitHub-specific bits are in styles.css (.gh-*).
import { useEffect, useState, useCallback } from 'react'
import {
  githubStatus, githubConnect, githubDisconnect, ghOAuthStart, glOAuthStart,
  getRepoLink, linkRepo, unlinkRepo, listRepos,
  ghBranches, ghPulls, ghIssues, ghPullDetail, ghConflicts,
} from '../lib/github'
import BranchGraph from './BranchGraph.jsx'
import PeerActivity from './PeerActivity.jsx'

// Merge-conflict preview: pick two branches and ask the server whether merging
// head into base would conflict. Exact — the backend runs `git merge-tree` on a
// bare mirror, so this is what git would actually do, not a heuristic.
function MergeCheck({ pid, branches }) {
  const [base, setBase] = useState('')
  const [head, setHead] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!branches?.length) return
    setBase((b) => b || branches[0].name)
    setHead((h) => h || (branches.find((x) => x.name !== branches[0].name)?.name ?? ''))
  }, [branches])

  async function check(e) {
    e?.preventDefault()
    setBusy(true); setError(''); setResult(null)
    try {
      setResult(await ghConflicts(pid, base, head))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!branches?.length) return null

  return (
    <form className="merge-check" onSubmit={check}>
      <div className="merge-row">
        <label className="merge-field">
          <span className="label">Merge into (base)</span>
          <select value={base} onChange={(e) => setBase(e.target.value)}>
            {branches.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
        </label>
        <label className="merge-field">
          <span className="label">From (head)</span>
          <select value={head} onChange={(e) => setHead(e.target.value)}>
            {branches.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}
          </select>
        </label>
        <button className="btn sm" type="submit" disabled={busy || !base || !head || base === head}>
          {busy ? 'Checking…' : 'Check for conflicts'}
        </button>
      </div>

      {busy && <p className="faint" style={{ margin: 0 }}>First check on a repo clones it — this can take a moment.</p>}
      {error && <div className="alert error">{error}</div>}

      {result && (
        <div className={`merge-result ${result.clean ? 'clean' : 'conflict'}`}>
          <div className="merge-verdict">
            <span className={`badge ${result.clean ? 'success' : 'admin'}`}>
              {result.clean ? 'Merges cleanly' : `${result.conflicts.length} conflicting file${result.conflicts.length === 1 ? '' : 's'}`}
            </span>
            <span className="faint">
              {result.head} is {result.ahead} commit{result.ahead === 1 ? '' : 's'} ahead,{' '}
              {result.behind} behind {result.base}
            </span>
          </div>
          {!result.clean && (
            <ul className="merge-files">
              {result.conflicts.map((f) => <li key={f} className="mono">{f}</li>)}
            </ul>
          )}
        </div>
      )}
    </form>
  )
}

// Class a unified-diff line so styles.css (.git-diff .add/.del/.hunk) colorizes it.
function diffLineClass(line) {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'del'
  return ''
}

function DiffPatch({ patch }) {
  if (!patch) return <p className="faint" style={{ margin: '4px 0 0' }}>(no textual diff)</p>
  return (
    <pre className="git-diff" aria-label="file diff">
      {patch.split('\n').map((line, i) => (
        <span key={i} className={diffLineClass(line)}>{line + '\n'}</span>
      ))}
    </pre>
  )
}

// Tabs differ by provider: GitLab uses "Merge Requests" and (for now) has no
// commit-graph / exact-conflict-check, which are GitHub-only server features.
function repoTabs(provider) {
  if (provider === 'gitlab') {
    return [
      { id: 'branches', label: 'Branches' },
      { id: 'pulls', label: 'Merge Requests' },
      { id: 'issues', label: 'Issues' },
    ]
  }
  return [
    { id: 'graph', label: 'Branch graph' },
    { id: 'branches', label: 'Branches' },
    { id: 'pulls', label: 'Pull Requests' },
    { id: 'issues', label: 'Issues' },
  ]
}

function GitHubRepoData({ pid, provider = 'github' }) {
  const isGitlab = provider === 'gitlab'
  const TABS = repoTabs(provider)
  const [tab, setTab] = useState(isGitlab ? 'branches' : 'graph')
  const [cache, setCache] = useState({}) // { branches: [...], pulls: [...], issues: [...] }
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openPr, setOpenPr] = useState(null) // number
  const [prDetail, setPrDetail] = useState(null)

  const load = useCallback(async (which) => {
    if (which === 'graph' || cache[which]) return // graph fetches its own data
    setLoading(true); setError('')
    try {
      const fn = which === 'branches' ? ghBranches : which === 'pulls' ? ghPulls : ghIssues
      const d = await fn(pid)
      setCache((c) => ({ ...c, [which]: d[which] }))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [pid, cache])

  useEffect(() => { load(tab) }, [tab, load])

  async function togglePr(number) {
    if (openPr === number) { setOpenPr(null); setPrDetail(null); return }
    setOpenPr(number); setPrDetail(null); setError('')
    try {
      setPrDetail(await ghPullDetail(pid, number))
    } catch (err) {
      setError(err.message)
    }
  }

  const rows = cache[tab]

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Repository</h2>
        <div className="gh-tabs">
          {TABS.map((t) => (
            <button key={t.id} className={`gh-tab ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </header>
      <div className="panel-body">
        {error && <div className="alert error">{error}</div>}
        {tab !== 'graph' && loading && !rows && <div className="skeleton" style={{ height: 60 }} />}
        {tab !== 'graph' && rows && rows.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing open here.</p>}

        {tab === 'graph' && <BranchGraph pid={pid} />}

        {tab === 'branches' && rows && (
          <>
            {!isGitlab && <MergeCheck pid={pid} branches={rows} />}
            <ul className="gh-list">
              {rows.map((b) => (
                <li key={b.name} className="gh-item">
                  <span className="mono">{b.name}</span>
                  <span className="row">
                    {b.protected && <span className="badge">protected</span>}
                    <span className="faint mono">{b.sha.slice(0, 7)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        {tab === 'pulls' && rows && (
          <ul className="gh-list">
            {rows.map((p) => (
              <li key={p.number} className="gh-item gh-item-btn" onClick={() => togglePr(p.number)}>
                <div className="gh-item-main">
                  <span className="gh-num">#{p.number}</span> {p.title}
                  {p.draft && <span className="badge">draft</span>}
                  <div className="faint mono">{p.head} → {p.base} · @{p.user}</div>
                  {openPr === p.number && (
                    <div className="gh-pr-detail" onClick={(e) => e.stopPropagation()}>
                      {!prDetail && <div className="skeleton" style={{ height: 40 }} />}
                      {prDetail && prDetail.number === p.number && (
                        <>
                          <div className="row" style={{ marginBottom: 8 }}>
                            <MergeBadge state={prDetail.mergeable_state} mergeable={prDetail.mergeable} />
                            <span className="faint">
                              +{prDetail.additions} −{prDetail.deletions} · {prDetail.changed_files} files
                            </span>
                            <a className="link" href={prDetail.html_url} target="_blank" rel="noreferrer">open on {isGitlab ? 'GitLab' : 'GitHub'}</a>
                          </div>
                          {prDetail.files.map((f) => (
                            <details key={f.filename} className="gh-file">
                              <summary>
                                <span className="mono">{f.filename}</span>
                                <span className="faint mono"> +{f.additions} −{f.deletions}</span>
                              </summary>
                              <DiffPatch patch={f.patch} />
                            </details>
                          ))}
                        </>
                      )}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        {tab === 'issues' && rows && (
          <ul className="gh-list">
            {rows.map((i) => (
              <li key={i.number} className="gh-item">
                <div className="gh-item-main">
                  <a className="gh-issue-link" href={i.html_url} target="_blank" rel="noreferrer">
                    <span className="gh-num">#{i.number}</span> {i.title}
                  </a>
                  <div className="faint">
                    @{i.user}{i.comments ? ` · ${i.comments} comments` : ''}
                    {i.labels.length > 0 && ' · '}
                    {i.labels.map((l) => <span key={l} className="badge">{l}</span>)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function MergeBadge({ state, mergeable }) {
  // mergeable_state: clean/dirty/blocked/behind/unstable/unknown; mergeable: true/false/null
  const clean = mergeable === true && (state === 'clean' || state === 'unstable')
  const conflict = state === 'dirty' || mergeable === false
  const cls = clean ? 'success' : conflict ? 'danger' : 'admin'
  const label = conflict ? 'conflicts' : clean ? 'mergeable' : (state || 'checking…')
  return <span className={`badge ${cls} dot`}>{label}</span>
}

// Pull the ?github=… or ?gitlab=… result the OAuth callback appended, then
// scrub it from the URL so refreshes don't re-show the banner.
function consumeOAuthResult() {
  const params = new URLSearchParams(window.location.search)
  const provider = params.get('github') ? 'GitHub' : params.get('gitlab') ? 'GitLab' : null
  const result = params.get('github') || params.get('gitlab')
  if (!result) return null
  const reason = params.get('github_reason') || params.get('gitlab_reason') || ''
  ;['github', 'github_reason', 'gitlab', 'gitlab_reason'].forEach((k) => params.delete(k))
  const qs = params.toString()
  window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
  return { result, reason, provider }
}

// Case-insensitive subsequence match: every char of `q` appears in `text`, in order.
function subsequenceMatch(q, text) {
  if (!q) return true
  const needle = q.toLowerCase()
  const hay = text.toLowerCase()
  let i = 0
  for (let j = 0; j < hay.length && i < needle.length; j++) {
    if (hay[j] === needle[i]) i++
  }
  return i === needle.length
}

export default function GitHubPanel({ pid, canManage }) {
  const [status, setStatus] = useState(null) // null = loading
  const [repo, setRepo] = useState(null)
  const [token, setToken] = useState('')
  const [fullName, setFullName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [banner, setBanner] = useState(null) // {result, reason} from the OAuth callback
  const [toasts, setToasts] = useState([]) // [{id, msg}]

  // Repo combobox state.
  const [repoList, setRepoList] = useState(null) // null = not loaded yet
  const [reposLoading, setReposLoading] = useState(false)
  const [repoOpen, setRepoOpen] = useState(false)
  const [repoActive, setRepoActive] = useState(-1)

  const showToast = useCallback((msg) => {
    const id = Date.now() + Math.random()
    setToasts((ts) => [...ts, { id, msg }])
    setTimeout(() => setToasts((ts) => ts.filter((t) => t.id !== id)), 10000)
  }, [])

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [s, r] = await Promise.all([githubStatus(), getRepoLink(pid)])
      setStatus(s)
      setRepo(r)
    } catch (err) {
      setError(err.message)
    }
  }, [pid])

  useEffect(() => {
    const r = consumeOAuthResult()
    setBanner(r)
    if (r?.result === 'connected') showToast(`${r.provider || 'GitHub'} account connected`)
  }, [showToast])
  useEffect(() => { refresh() }, [refresh])

  const oauthLogin = () => run(async () => {
    const { authorize_url } = await ghOAuthStart(`/project/${pid}?tab=branches`)
    window.location.href = authorize_url // leaves the app; GitHub redirects back here
  })
  const gitlabLogin = () => run(async () => {
    const { authorize_url } = await glOAuthStart(`/project/${pid}?tab=branches`)
    window.location.href = authorize_url // leaves the app; GitLab redirects back here
  })

  async function run(fn) {
    setBusy(true); setError('')
    try { await fn(); await refresh() }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const connect = () => run(async () => {
    await githubConnect(token.trim())
    setToken('')
    showToast(`${status?.provider === 'gitlab' ? 'GitLab' : 'GitHub'} account connected`)
  })
  const disconnect = () => run(() => githubDisconnect())
  const link = () => run(async () => {
    await linkRepo(pid, fullName.trim())
    setFullName('')
    setRepoOpen(false)
  })
  const unlink = () => run(() => unlinkRepo(pid))

  // Lazily load the account's repos the first time the combobox opens.
  const ensureRepos = useCallback(async () => {
    if (repoList !== null || reposLoading) return
    setReposLoading(true); setError('')
    try {
      const d = await listRepos()
      setRepoList(d.repos || [])
    } catch (err) {
      setError(err.message)
      setRepoList([])
    } finally {
      setReposLoading(false)
    }
  }, [repoList, reposLoading])

  const openRepoDropdown = () => { setRepoOpen(true); setRepoActive(-1); ensureRepos() }

  const repoMatches = (repoList || [])
    .filter((r) => subsequenceMatch(fullName.trim(), r.full_name))
    .slice(0, 50)

  const selectRepo = (name) => {
    setFullName(name)
    setRepoOpen(false)
    setRepoActive(-1)
    run(async () => {
      await linkRepo(pid, name.trim())
      setFullName('')
    })
  }

  function onRepoKeyDown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!repoOpen) { openRepoDropdown(); return }
      setRepoActive((i) => Math.min(i + 1, repoMatches.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setRepoActive((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (repoOpen && repoActive >= 0 && repoMatches[repoActive]) {
        selectRepo(repoMatches[repoActive].full_name)
      } else if (fullName.includes('/')) {
        link()
      }
    } else if (e.key === 'Escape') {
      setRepoOpen(false)
    }
  }

  return (
    <div className="stack-4">
      <div className="toast-stack">
        {toasts.map((t) => <div key={t.id} className="toast">{t.msg}</div>)}
      </div>
      <PeerActivity pid={pid} />{/* #15: live peer diffs; renders only when peers report */}
      {banner?.result === 'error' && (
        <div className="alert error">{banner.provider || 'GitHub'} sign-in failed{banner.reason ? `: ${banner.reason}` : ''}.</div>
      )}
      {error && <div className="alert error">{error}</div>}

      {/* --- Identity (only integration managers connect a GitHub account) --- */}
      {canManage && (
      <section className="panel">
        <header className="panel-head gh-acct-head">
          <h2>Git account</h2>
          <div className="gh-acct-actions">
            {status?.connected && (
              <>
                <span className="gh-conn-badge">
                  <span className="gh-conn-dot" /> @{status.login} · {status.provider === 'gitlab' ? 'GitLab' : 'GitHub'}
                </span>
                <button className="gh-disconnect" onClick={disconnect} disabled={busy}>Disconnect</button>
              </>
            )}

            {status && !status.connected && (status.oauth_available || status.gitlab_oauth_available) && (
              <>
                <div className="gh-oauth-btns">
                  {status.oauth_available && (
                    <button className="btn gh-oauth-btn" onClick={oauthLogin} disabled={busy}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                        <path d="M12 .5A11.5 11.5 0 0 0 .5 12a11.5 11.5 0 0 0 7.86 10.91c.58.1.79-.25.79-.55v-2.17c-3.2.7-3.87-1.36-3.87-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.03 1.75 2.69 1.25 3.34.95.1-.74.4-1.25.72-1.53-2.55-.29-5.23-1.28-5.23-5.68 0-1.26.45-2.28 1.18-3.09-.12-.29-.51-1.46.11-3.05 0 0 .96-.31 3.16 1.18a11 11 0 0 1 5.76 0c2.2-1.49 3.16-1.18 3.16-1.18.62 1.59.23 2.76.11 3.05.73.81 1.18 1.83 1.18 3.09 0 4.41-2.69 5.38-5.25 5.67.41.35.77 1.04.77 2.1v3.12c0 .3.2.66.8.55A11.5 11.5 0 0 0 23.5 12 11.5 11.5 0 0 0 12 .5Z" />
                      </svg>
                      {busy ? 'Redirecting…' : 'Continue with GitHub'}
                    </button>
                  )}
                  {status.gitlab_oauth_available && (
                    <button className="btn gl-oauth-btn" onClick={gitlabLogin} disabled={busy}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                        <path d="M22.65 9.6 21.4 5.79a.55.55 0 0 0-1.05-.02L18.2 12H5.8L3.65 5.77a.55.55 0 0 0-1.05.02L1.35 9.6a3.6 3.6 0 0 0 1.3 4.03l9.35 6.8 9.35-6.8a3.6 3.6 0 0 0 1.3-4.03Z" />
                      </svg>
                      {busy ? 'Redirecting…' : 'Continue with GitLab'}
                    </button>
                  )}
                </div>
                <details className="gh-pat-fallback">
                  <summary className="faint">Use a personal access token instead</summary>
                  <div className="gh-row" style={{ marginTop: 8 }}>
                    <input
                      type="password"
                      placeholder="ghp_… or github_pat_…"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      autoComplete="off"
                    />
                    <button className="btn" onClick={connect} disabled={busy || !token.trim()}>
                      {busy ? 'Connecting…' : 'Connect'}
                    </button>
                  </div>
                </details>
              </>
            )}

            {status && !status.connected && !(status.oauth_available || status.gitlab_oauth_available) && (
              <div className="gh-row">
                <input
                  type="password"
                  placeholder="ghp_… or github_pat_…"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  autoComplete="off"
                />
                <button className="btn" onClick={connect} disabled={busy || !token.trim()}>
                  {busy ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            )}
          </div>
        </header>
        <div className="panel-body">
          {status === null && <div className="skeleton" style={{ height: 44 }} />}

          {status && !status.connected && !(status.oauth_available || status.gitlab_oauth_available) && (
            <p className="muted" style={{ marginTop: 0 }}>
              GitHub/GitLab sign-in isn't configured on this server (set <code>GITHUB_CLIENT_ID</code>/
              <code>GITHUB_CLIENT_SECRET</code> or <code>GITLAB_CLIENT_ID</code>/<code>GITLAB_CLIENT_SECRET</code>).
              Meanwhile you can paste a fine-grained personal access token with read access.
            </p>
          )}

          {status && !status.encrypted && (
            <div className="alert warn gh-warn">
              Tokens are stored <strong>unencrypted</strong> — set <code>TOKEN_ENCRYPTION_KEY</code>
              {' '}on the server to encrypt them at rest.
            </div>
          )}
        </div>
      </section>
      )}

      {/* --- Repo link --- */}
      <section className="panel">
        <header className="panel-head">
          <h2>Linked repository</h2>
          {repo?.linked && <span className="tag mono">{repo.full_name}</span>}
        </header>
        <div className="panel-body">
          {repo === null && <div className="skeleton" style={{ height: 44 }} />}

          {repo && !repo.linked && (
            <>
              <p className="muted" style={{ marginTop: 0 }}>
                {canManage
                  ? `Link a ${status?.provider === 'gitlab' ? 'GitLab project' : 'GitHub repo'} to this project. Validated against your connected account.`
                  : 'No repository is linked yet. Ask a project admin to link one.'}
              </p>
              {canManage && (
                <div className="gh-row">
                  <div className="gh-repo-combo">
                    <input
                      type="text"
                      placeholder={status?.provider === 'gitlab' ? 'group/project' : 'owner/repo'}
                      value={fullName}
                      onChange={(e) => { setFullName(e.target.value); setRepoOpen(true); setRepoActive(-1) }}
                      onFocus={openRepoDropdown}
                      onClick={openRepoDropdown}
                      onKeyDown={onRepoKeyDown}
                      onBlur={() => setTimeout(() => setRepoOpen(false), 150)}
                      autoComplete="off"
                    />
                    {repoOpen && (
                      <div className="gh-repo-dropdown">
                        {reposLoading && <div className="gh-repo-empty">Loading repositories…</div>}
                        {!reposLoading && repoList !== null && repoMatches.length === 0 && (
                          <div className="gh-repo-empty">No matching repositories</div>
                        )}
                        {repoMatches.map((r, idx) => (
                          <div
                            key={r.full_name}
                            className={`gh-repo-option ${idx === repoActive ? 'active' : ''}`}
                            onMouseDown={(e) => e.preventDefault()}
                            onMouseEnter={() => setRepoActive(idx)}
                            onClick={() => selectRepo(r.full_name)}
                          >
                            <span className="mono">{r.full_name}</span>
                            {r.private && <span className="badge">private</span>}
                            {r.description && <span className="faint">{r.description}</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <button
                    className="btn"
                    onClick={link}
                    disabled={busy || !fullName.includes('/') || !status?.connected}
                    title={!status?.connected ? 'Connect your GitHub account first' : ''}
                  >
                    {busy ? 'Linking…' : 'Link repo'}
                  </button>
                </div>
              )}
              {canManage && !status?.connected && (
                <p className="faint" style={{ marginTop: 8 }}>Connect your GitHub account above first.</p>
              )}
            </>
          )}

          {repo?.linked && (
            <div className="gh-connected">
              <div>
                <a className="gh-login" href={`https://github.com/${repo.full_name}`} target="_blank" rel="noreferrer">
                  {repo.full_name}
                </a>
                <div className="faint">default branch: {repo.default_branch || 'unknown'}</div>
              </div>
              {canManage && (
                <button className="btn ghost sm" onClick={unlink} disabled={busy}>Unlink</button>
              )}
            </div>
          )}
        </div>
      </section>

      {/* --- Live repo data (Phase 2) --- */}
      {repo?.linked && <GitHubRepoData pid={pid} provider={repo.provider || 'github'} />}
    </div>
  )
}
