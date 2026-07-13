// GitHub integration panel (Phase 1: identity + repo link).
// - Connect a personal access token (validated server-side against GitHub).
// - Link the project to a GitHub repo (admin only).
// Uses shared design-system classes; GitHub-specific bits are in styles.css (.gh-*).
import { useEffect, useState, useCallback } from 'react'
import {
  githubStatus, githubConnect, githubDisconnect,
  getRepoLink, linkRepo, unlinkRepo,
  ghBranches, ghPulls, ghIssues, ghPullDetail,
} from '../lib/github'

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

const REPO_TABS = [
  { id: 'branches', label: 'Branches' },
  { id: 'pulls', label: 'Pull Requests' },
  { id: 'issues', label: 'Issues' },
]

function GitHubRepoData({ pid }) {
  const [tab, setTab] = useState('branches')
  const [cache, setCache] = useState({}) // { branches: [...], pulls: [...], issues: [...] }
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openPr, setOpenPr] = useState(null) // number
  const [prDetail, setPrDetail] = useState(null)

  const load = useCallback(async (which) => {
    if (cache[which]) return
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
          {REPO_TABS.map((t) => (
            <button key={t.id} className={`gh-tab ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </header>
      <div className="panel-body">
        {error && <div className="alert error">{error}</div>}
        {loading && !rows && <div className="skeleton" style={{ height: 60 }} />}
        {rows && rows.length === 0 && <p className="muted" style={{ margin: 0 }}>Nothing open here.</p>}

        {tab === 'branches' && rows && (
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
                            <a className="link" href={prDetail.html_url} target="_blank" rel="noreferrer">open on GitHub</a>
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

export default function GitHubPanel({ pid, isAdmin }) {
  const [status, setStatus] = useState(null) // null = loading
  const [repo, setRepo] = useState(null)
  const [token, setToken] = useState('')
  const [fullName, setFullName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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

  useEffect(() => { refresh() }, [refresh])

  async function run(fn) {
    setBusy(true); setError('')
    try { await fn(); await refresh() }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const connect = () => run(async () => {
    await githubConnect(token.trim())
    setToken('')
  })
  const disconnect = () => run(() => githubDisconnect())
  const link = () => run(async () => {
    await linkRepo(pid, fullName.trim())
    setFullName('')
  })
  const unlink = () => run(() => unlinkRepo(pid))

  return (
    <div className="stack-4">
      {error && <div className="alert error">{error}</div>}

      {/* --- Identity --- */}
      <section className="panel">
        <header className="panel-head">
          <h2>GitHub account</h2>
          {status?.connected && <span className="badge success dot">Connected</span>}
        </header>
        <div className="panel-body">
          {status === null && <div className="skeleton" style={{ height: 44 }} />}

          {status && !status.connected && (
            <>
              <p className="muted" style={{ marginTop: 0 }}>
                Paste a GitHub personal access token to connect your account. Use a
                fine-grained token with read access to the repos you want to link.
              </p>
              {!status.encrypted && (
                <div className="alert warn gh-warn">
                  Tokens are stored <strong>unencrypted</strong> — set <code>TOKEN_ENCRYPTION_KEY</code>
                  {' '}on the server to encrypt them at rest.
                </div>
              )}
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
            </>
          )}

          {status?.connected && (
            <div className="gh-connected">
              <div>
                <div className="gh-login">@{status.login}</div>
                <div className="faint">
                  {status.scopes ? `scopes: ${status.scopes}` : 'fine-grained token'}
                  {' · '}{status.encrypted ? 'encrypted at rest' : 'stored unencrypted'}
                </div>
              </div>
              <button className="btn ghost sm" onClick={disconnect} disabled={busy}>Disconnect</button>
            </div>
          )}
        </div>
      </section>

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
                {isAdmin
                  ? 'Link a GitHub repo to this project. Validated against your connected account.'
                  : 'No repository is linked yet. Ask a project admin to link one.'}
              </p>
              {isAdmin && (
                <div className="gh-row">
                  <input
                    type="text"
                    placeholder="owner/repo"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
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
              {isAdmin && !status?.connected && (
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
              {isAdmin && (
                <button className="btn ghost sm" onClick={unlink} disabled={busy}>Unlink</button>
              )}
            </div>
          )}
        </div>
      </section>

      {/* --- Live repo data (Phase 2) --- */}
      {repo?.linked && <GitHubRepoData pid={pid} />}
    </div>
  )
}
