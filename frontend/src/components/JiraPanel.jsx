// Jira integration panel (Phase 1: identity + project link), shown in the Ticket
// section. Connect an Atlassian account (OAuth "Continue with Atlassian" or an
// API token), then a manager links a Jira Cloud project. Reads land in Phase 2.
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api.js'
import {
  jiraStatus, jiraConnect, jiraDisconnect, jiraOAuthStart,
  getJiraLink, linkJiraProject, unlinkJiraProject, syncJira, jiraCreateIssue,
} from '../lib/jira.js'

const JIRA_TYPES = ['Task', 'Story', 'Bug', 'Epic']

// Pull the ?jira=connected|error the OAuth callback appended, then scrub it.
function consumeJiraResult() {
  const params = new URLSearchParams(window.location.search)
  const result = params.get('jira')
  if (!result) return null
  const reason = params.get('jira_reason') || ''
  params.delete('jira'); params.delete('jira_reason')
  const qs = params.toString()
  window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
  return { result, reason }
}

export default function JiraPanel({ pid }) {
  const [status, setStatus] = useState(null)   // null = loading
  const [link, setLink] = useState(null)
  const [canManage, setCanManage] = useState(false)
  const [site, setSite] = useState('')
  const [email, setEmail] = useState('')
  const [token, setToken] = useState('')
  const [projectKey, setProjectKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [banner, setBanner] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncInfo, setSyncInfo] = useState(null)  // { synced, at } after a sync
  const [newSummary, setNewSummary] = useState('')
  const [newType, setNewType] = useState('Task')
  const [creating, setCreating] = useState(false)

  const refresh = useCallback(async () => {
    setError('')
    try {
      const [s, l, proj] = await Promise.all([
        jiraStatus(), getJiraLink(pid), api(`/api/projects/${pid}`),
      ])
      setStatus(s); setLink(l); setCanManage(!!proj.can_manage)
    } catch (err) { setError(err.message) }
  }, [pid])

  useEffect(() => { setBanner(consumeJiraResult()) }, [])
  useEffect(() => { refresh() }, [refresh])

  async function run(fn) {
    setBusy(true); setError('')
    try { await fn(); await refresh() }
    catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const oauthLogin = () => run(async () => {
    const { authorize_url } = await jiraOAuthStart(`/project/${pid}?tab=ticket`)
    window.location.href = authorize_url
  })
  const connect = () => run(async () => {
    await jiraConnect(site.trim(), email.trim(), token.trim())
    setSite(''); setEmail(''); setToken('')
  })
  const disconnect = () => run(() => jiraDisconnect())
  const linkProject = () => run(async () => { await linkJiraProject(pid, projectKey.trim()); setProjectKey('') })
  const unlink = () => run(() => unlinkJiraProject(pid))

  // Pull the linked project's Jira issues into the board (upsert into ticket_cards).
  const doSync = useCallback(async () => {
    setSyncing(true); setError('')
    try { setSyncInfo(await syncJira(pid)) }
    catch (err) { setError(err.message) }
    finally { setSyncing(false) }
  }, [pid])

  // Auto-sync once a Jira project is linked so the board fills without a click.
  useEffect(() => { if (link?.linked) doSync() }, [link?.linked, doSync])

  // Create a Jira issue (lands in the board via the mirror + next sync).
  async function createIssue(e) {
    e?.preventDefault()
    const summary = newSummary.trim()
    if (!summary) return
    setCreating(true); setError('')
    try {
      await jiraCreateIssue(pid, { summary, issue_type: newType })
      setNewSummary('')
      await doSync()
    } catch (err) { setError(err.message) }
    finally { setCreating(false) }
  }

  return (
    <section className="panel">
      <header className="panel-head gh-acct-head">
        <h2>Jira</h2>
        <div className="gh-acct-actions">
          {status?.connected && (
            <>
              <span className="gh-conn-badge">
                <span className="gh-conn-dot" />
                {status.account} · {status.site}
              </span>
              {canManage && <button className="gh-disconnect" onClick={disconnect} disabled={busy}>Disconnect</button>}
            </>
          )}
          {status && !status.connected && canManage && (
            <div className="gh-oauth-btns">
              {status.oauth_available && (
                <button className="btn jira-oauth-btn" onClick={oauthLogin} disabled={busy}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                    <path d="M11.53 2 22 12.47a1.5 1.5 0 0 1 0 2.12l-4.6 4.6-6.35-6.36a2.62 2.62 0 0 1 0-3.7L11.53 2ZM6.6 6.93l3.17 3.17a4.12 4.12 0 0 0 0 5.82l3.17 3.16-2.4 2.41a1.5 1.5 0 0 1-2.13 0L2 15.6a1.5 1.5 0 0 1 0-2.12l4.6-6.55Z" />
                  </svg>
                  {busy ? 'Redirecting…' : 'Continue with Atlassian'}
                </button>
              )}
            </div>
          )}
        </div>
      </header>

      <div className="panel-body">
        {banner?.result === 'error' && (
          <div className="alert error">Jira sign-in failed{banner.reason ? `: ${banner.reason}` : ''}.</div>
        )}
        {banner?.result === 'connected' && <div className="alert success">Jira account connected.</div>}
        {error && <div className="alert error">{error}</div>}
        {status === null && <div className="skeleton" style={{ height: 44 }} />}

        {/* Not connected: API-token form (managers) or a prompt for others */}
        {status && !status.connected && (
          canManage ? (
            <>
              <p className="muted" style={{ marginTop: 0 }}>
                Connect your Atlassian account to link a Jira project.
                {status.oauth_available
                  ? ' Use “Continue with Atlassian” above, or an API token below.'
                  : ' Paste your site, email and an API token (id.atlassian.com → Security → API tokens).'}
              </p>
              <div className="jira-token-form">
                <input placeholder="yoursite.atlassian.net" value={site} onChange={(e) => setSite(e.target.value)} />
                <input placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
                <input type="password" placeholder="API token" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
                <button className="btn" onClick={connect}
                  disabled={busy || !site.trim() || !email.trim() || !token.trim()}>
                  {busy ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            </>
          ) : (
            <p className="muted" style={{ marginTop: 0 }}>No Jira account is connected. Ask a project manager to connect one.</p>
          )
        )}

        {/* Connected: link a Jira project */}
        {status?.connected && (
          <div className="jira-link">
            {link === null && <div className="skeleton" style={{ height: 40 }} />}
            {link && !link.linked && (
              canManage ? (
                <div className="gh-row">
                  <input placeholder="Project key (e.g. PROJ)" value={projectKey}
                    onChange={(e) => setProjectKey(e.target.value.toUpperCase())} />
                  <button className="btn" onClick={linkProject} disabled={busy || !projectKey.trim()}>
                    {busy ? 'Linking…' : 'Link project'}
                  </button>
                </div>
              ) : (
                <p className="muted" style={{ margin: 0 }}>No Jira project linked yet. Ask a project manager to link one.</p>
              )
            )}
            {link?.linked && (
              <>
                <div className="gh-connected">
                  <div>
                    <a className="gh-login" href={link.url} target="_blank" rel="noreferrer">
                      {link.project_name || link.project_key} ({link.project_key})
                    </a>
                    <div className="faint">
                      {link.site}
                      {syncInfo && ` · ${syncInfo.synced} issue${syncInfo.synced === 1 ? '' : 's'} synced into the board`}
                      {syncing && ' · syncing…'}
                    </div>
                  </div>
                  <div className="row" style={{ gap: 'var(--sp-2)' }}>
                    <button className="btn ghost sm" onClick={doSync} disabled={syncing}>
                      {syncing ? 'Syncing…' : 'Sync from Jira'}
                    </button>
                    {canManage && <button className="btn ghost sm" onClick={unlink} disabled={busy}>Unlink</button>}
                  </div>
                </div>
                <form className="jira-new" onSubmit={createIssue}>
                  <select value={newType} onChange={(e) => setNewType(e.target.value)} aria-label="Issue type">
                    {JIRA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <input placeholder={`New ${newType.toLowerCase()} in ${link.project_key}…`}
                    value={newSummary} onChange={(e) => setNewSummary(e.target.value)} />
                  <button className="btn sm" type="submit" disabled={creating || !newSummary.trim()}>
                    {creating ? 'Creating…' : 'Create issue'}
                  </button>
                </form>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
