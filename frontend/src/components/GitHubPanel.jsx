// GitHub integration panel (Phase 1: identity + repo link).
// - Connect a personal access token (validated server-side against GitHub).
// - Link the project to a GitHub repo (admin only).
// Uses shared design-system classes; GitHub-specific bits are in styles.css (.gh-*).
import { useEffect, useState, useCallback } from 'react'
import {
  githubStatus, githubConnect, githubDisconnect,
  getRepoLink, linkRepo, unlinkRepo,
  ghBranches, ghPulls, ghIssues, ghPullDetail,
  ghCreateBranch, ghCreatePull, ghCreateIssue, ghComment,
  ghWebhookInfo,
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

const NEW_LABEL = { branches: 'New branch', pulls: 'New pull request', issues: 'New issue' }

function GitHubRepoData({ pid, defaultBranch, connected }) {
  const [tab, setTab] = useState('branches')
  const [cache, setCache] = useState({}) // { branches: [...], pulls: [...], issues: [...] }
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [openPr, setOpenPr] = useState(null) // number
  const [prDetail, setPrDetail] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const [openIssue, setOpenIssue] = useState(null) // number

  const load = useCallback(async (which, force = false) => {
    if (cache[which] && !force) return
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
  useEffect(() => { setShowNew(false) }, [tab]) // collapse the create form when switching tabs

  async function togglePr(number) {
    if (openPr === number) { setOpenPr(null); setPrDetail(null); return }
    setOpenPr(number); setPrDetail(null); setError('')
    try {
      setPrDetail(await ghPullDetail(pid, number))
    } catch (err) {
      setError(err.message)
    }
  }

  // Refresh the current tab after a write, and jump to where the new item shows up.
  function afterCreate() {
    setShowNew(false)
    load(tab, true)
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

        <div className="gh-new-bar">
          <button
            className="btn ghost sm"
            onClick={() => setShowNew((v) => !v)}
            disabled={!connected}
            title={connected ? '' : 'Connect your GitHub account first'}
          >
            {showNew ? 'Cancel' : `+ ${NEW_LABEL[tab]}`}
          </button>
        </div>
        {showNew && connected && (
          <NewItemForm
            tab={tab}
            pid={pid}
            defaultBranch={defaultBranch}
            branches={cache.branches}
            onCreated={afterCreate}
          />
        )}

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
                          <CommentBox pid={pid} number={p.number} connected={connected} />
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
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <a className="gh-issue-link" href={i.html_url} target="_blank" rel="noreferrer">
                      <span className="gh-num">#{i.number}</span> {i.title}
                    </a>
                    <button
                      className="btn ghost sm"
                      onClick={() => setOpenIssue(openIssue === i.number ? null : i.number)}
                      disabled={!connected}
                      title={connected ? '' : 'Connect your GitHub account first'}
                    >
                      {openIssue === i.number ? 'Close' : 'Comment'}
                    </button>
                  </div>
                  <div className="faint">
                    @{i.user}{i.comments ? ` · ${i.comments} comments` : ''}
                    {i.labels.length > 0 && ' · '}
                    {i.labels.map((l) => <span key={l} className="badge">{l}</span>)}
                  </div>
                  {openIssue === i.number && connected && (
                    <CommentBox pid={pid} number={i.number} connected={connected} onDone={() => load(tab, true)} />
                  )}
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

// Post a comment on a PR or issue (same GitHub endpoint for both).
function CommentBox({ pid, number, connected, onDone }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  async function submit() {
    setBusy(true); setError(''); setDone(false)
    try {
      await ghComment(pid, number, text.trim())
      setText(''); setDone(true)
      onDone?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="gh-comment" onClick={(e) => e.stopPropagation()}>
      {error && <div className="alert error">{error}</div>}
      <textarea
        className="gh-textarea"
        rows={2}
        placeholder={`Comment on #${number}…`}
        value={text}
        onChange={(e) => { setText(e.target.value); setDone(false) }}
      />
      <div className="row" style={{ justifyContent: 'flex-end', gap: 8 }}>
        {done && <span className="faint">Comment posted</span>}
        <button className="btn sm" onClick={submit} disabled={busy || !connected || !text.trim()}>
          {busy ? 'Posting…' : 'Comment'}
        </button>
      </div>
    </div>
  )
}

// Create a branch, pull request, or issue depending on the active tab.
function NewItemForm({ tab, pid, defaultBranch, branches, onCreated }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // branch
  const [name, setName] = useState('')
  const [fromRef, setFromRef] = useState(defaultBranch || '')
  // pull request
  const [prTitle, setPrTitle] = useState('')
  const [head, setHead] = useState('')
  const [base, setBase] = useState(defaultBranch || '')
  const [prBody, setPrBody] = useState('')
  const [draft, setDraft] = useState(false)
  // issue
  const [issueTitle, setIssueTitle] = useState('')
  const [issueBody, setIssueBody] = useState('')
  const [labels, setLabels] = useState('')

  const branchNames = (branches || []).map((b) => b.name)

  async function run(fn) {
    setBusy(true); setError('')
    try { await fn(); onCreated() }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const createBranch = () => run(() => ghCreateBranch(pid, name.trim(), fromRef.trim()))
  const createPull = () => run(() => ghCreatePull(pid, {
    title: prTitle.trim(), head: head.trim(), base: base.trim(), body: prBody, draft,
  }))
  const createIssue = () => run(() => ghCreateIssue(pid, {
    title: issueTitle.trim(), body: issueBody,
    labels: labels.split(',').map((l) => l.trim()).filter(Boolean),
  }))

  return (
    <div className="gh-form">
      {error && <div className="alert error">{error}</div>}

      {tab === 'branches' && (
        <>
          <input placeholder="new-branch-name" value={name} onChange={(e) => setName(e.target.value)} />
          <label className="gh-field-label">from
            <BranchSelect value={fromRef} onChange={setFromRef} options={branchNames} fallback={defaultBranch} />
          </label>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn sm" onClick={createBranch} disabled={busy || !name.trim()}>
              {busy ? 'Creating…' : 'Create branch'}
            </button>
          </div>
        </>
      )}

      {tab === 'pulls' && (
        <>
          <input placeholder="Pull request title" value={prTitle} onChange={(e) => setPrTitle(e.target.value)} />
          <div className="gh-form-row">
            <label className="gh-field-label">from
              <BranchSelect value={head} onChange={setHead} options={branchNames} placeholder="head branch" />
            </label>
            <label className="gh-field-label">into
              <BranchSelect value={base} onChange={setBase} options={branchNames} fallback={defaultBranch} />
            </label>
          </div>
          <textarea className="gh-textarea" rows={3} placeholder="Description (optional)"
            value={prBody} onChange={(e) => setPrBody(e.target.value)} />
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <label className="gh-check">
              <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> Draft
            </label>
            <button className="btn sm" onClick={createPull} disabled={busy || !prTitle.trim() || !head.trim()}>
              {busy ? 'Opening…' : 'Open pull request'}
            </button>
          </div>
        </>
      )}

      {tab === 'issues' && (
        <>
          <input placeholder="Issue title" value={issueTitle} onChange={(e) => setIssueTitle(e.target.value)} />
          <textarea className="gh-textarea" rows={3} placeholder="Description (optional)"
            value={issueBody} onChange={(e) => setIssueBody(e.target.value)} />
          <input placeholder="labels, comma, separated (optional)" value={labels} onChange={(e) => setLabels(e.target.value)} />
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn sm" onClick={createIssue} disabled={busy || !issueTitle.trim()}>
              {busy ? 'Creating…' : 'Create issue'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// Webhook setup + live delivery log (Phase 4). Admins copy the URL into the repo's
// webhook settings; incoming events post into Discussion as a "github" bot.
function WebhookSetup({ pid, isAdmin }) {
  const [info, setInfo] = useState(null)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const refresh = useCallback(async () => {
    setError('')
    try { setInfo(await ghWebhookInfo(pid)) }
    catch (err) { setError(err.message) }
  }, [pid])

  useEffect(() => { refresh() }, [refresh])

  function copyUrl() {
    if (!info?.webhook_url) return
    navigator.clipboard?.writeText(info.webhook_url)
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Webhooks</h2>
        {info && (
          <span className={`badge dot ${info.secret_configured ? 'success' : 'admin'}`}>
            {info.secret_configured ? 'secret set' : 'unsigned (dev)'}
          </span>
        )}
      </header>
      <div className="panel-body">
        {error && <div className="alert error">{error}</div>}
        {!info && !error && <div className="skeleton" style={{ height: 44 }} />}

        {info && (
          <>
            <p className="muted" style={{ marginTop: 0 }}>
              {isAdmin
                ? 'Add this as a webhook on the GitHub repo (Settings → Webhooks). Events post into Discussion as @github.'
                : 'Repo events post into Discussion as @github once an admin adds the webhook.'}
            </p>
            {isAdmin && (
              <>
                <div className="gh-hook-url">
                  <code className="mono">{info.webhook_url}</code>
                  <button className="btn ghost sm" onClick={copyUrl}>{copied ? 'Copied' : 'Copy'}</button>
                </div>
                <ul className="gh-hook-meta faint">
                  <li>Content type: <span className="mono">{info.content_type}</span></li>
                  <li>Secret: {info.secret_configured
                    ? 'configured — signatures verified'
                    : <span>not set — <span className="mono">GITHUB_WEBHOOK_SECRET</span> on the server enables HMAC verification</span>}</li>
                  <li>Subscribe to: <span className="mono">{info.subscribe}</span></li>
                </ul>
              </>
            )}

            <div className="row" style={{ justifyContent: 'space-between', marginTop: 8 }}>
              <h3 className="gh-hook-h3">Recent deliveries</h3>
              <button className="btn ghost sm" onClick={refresh}>Refresh</button>
            </div>
            {info.recent.length === 0 && (
              <p className="muted" style={{ margin: 0 }}>No events received yet.</p>
            )}
            {info.recent.length > 0 && (
              <ul className="gh-list">
                {info.recent.map((e, i) => (
                  <li key={i} className="gh-item">
                    <div className="gh-item-main">
                      <span className="badge">{e.event_type}{e.action ? `.${e.action}` : ''}</span>
                      {' '}{e.summary || <span className="faint">(no message posted)</span>}
                      <div className="faint mono">{new Date(e.received_at * 1000).toLocaleString()}</div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  )
}

// Branch picker: a datalist-backed input so users can pick a known branch or type a new one.
function BranchSelect({ value, onChange, options, placeholder, fallback }) {
  const listId = `br-${placeholder || fallback || 'list'}-${options.length}`
  return (
    <>
      <input
        list={listId}
        className="gh-branch-input"
        placeholder={placeholder || fallback || 'branch'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {options.map((o) => <option key={o} value={o} />)}
      </datalist>
    </>
  )
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

      {/* --- Live repo data (Phase 2) + writes (Phase 3) --- */}
      {repo?.linked && (
        <GitHubRepoData pid={pid} defaultBranch={repo.default_branch} connected={status?.connected} />
      )}

      {/* --- Webhooks -> Discussion (Phase 4) --- */}
      {repo?.linked && <WebhookSetup pid={pid} isAdmin={isAdmin} />}
    </div>
  )
}
