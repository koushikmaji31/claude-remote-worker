// GitHub integration panel (Phase 1: identity + repo link).
// - Connect a personal access token (validated server-side against GitHub).
// - Link the project to a GitHub repo (admin only).
// Uses shared design-system classes; GitHub-specific bits are in styles.css (.gh-*).
import { useEffect, useState, useCallback } from 'react'
import {
  githubStatus, githubConnect, githubDisconnect,
  getRepoLink, linkRepo, unlinkRepo,
} from '../lib/github'

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
    </div>
  )
}
