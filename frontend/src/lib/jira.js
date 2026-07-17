// Jira integration client (Phase 1: identity + project link).
// Thin wrappers over the /api/jira/* endpoints in app/platform.py.
import { api } from '../api.js'

// --- Identity (per-user Atlassian account) ---
export const jiraStatus = () => api('/api/jira/status')

// API-token connect (email + token against a site) — the fast path.
export const jiraConnect = (site, email, token) =>
  api('/api/jira/connect', { method: 'POST', body: { site, email, token } })

export const jiraDisconnect = () => api('/api/jira/disconnect', { method: 'DELETE' })

// OAuth ("Continue with Atlassian"). Returns { authorize_url }; caller navigates there.
export const jiraOAuthConfig = () => api('/api/jira/oauth/config')
export const jiraOAuthStart = (returnTo) =>
  api('/api/jira/oauth/start', { method: 'POST', body: { return_to: returnTo } })

// --- Project link (per-project) ---
export const getJiraLink = (pid) => api(`/api/projects/${pid}/jira`)
export const linkJiraProject = (pid, projectKey) =>
  api(`/api/projects/${pid}/jira/link`, { method: 'POST', body: { project_key: projectKey } })
export const unlinkJiraProject = (pid) =>
  api(`/api/projects/${pid}/jira/link`, { method: 'DELETE' })

// --- Read (Phase 2): mirror linked Jira issues into the ticket_cards board ---
export const syncJira = (pid) => api(`/api/projects/${pid}/jira/sync`, { method: 'POST' })

// --- Write (Phase 3): create an issue, transition status back to Jira ---
export const jiraCreateIssue = (pid, data) =>
  api(`/api/projects/${pid}/jira/issues`, { method: 'POST', body: data })
export const jiraTransition = (pid, key, to) =>
  api(`/api/projects/${pid}/jira/issues/${encodeURIComponent(key)}/transition`, { method: 'POST', body: { to } })
