// GitHub integration client (Phase 1: identity + repo link).
// Thin wrappers over the /api/github/* endpoints in app/platform.py.
import { api } from '../api'

// --- Identity (per-user GitHub account) ---
export const githubStatus = () => api('/api/github/status')

// --- OAuth (Phase 3): preferred over PATs ---
export const ghOAuthConfig = () => api('/api/github/oauth/config')

// Returns { authorize_url }; caller navigates the browser there.
export const ghOAuthStart = (returnTo) =>
  api('/api/github/oauth/start', { method: 'POST', body: { return_to: returnTo } })

export const githubConnect = (token) =>
  api('/api/github/connect', { method: 'POST', body: { token } })

export const githubDisconnect = () =>
  api('/api/github/disconnect', { method: 'DELETE' })

// --- Repo link (per-project) ---
export const getRepoLink = (pid) => api(`/api/projects/${pid}/github`)

export const linkRepo = (pid, fullName) =>
  api(`/api/projects/${pid}/github/link`, { method: 'POST', body: { full_name: fullName } })

export const unlinkRepo = (pid) =>
  api(`/api/projects/${pid}/github/link`, { method: 'DELETE' })

// --- Read (Phase 2): live repo data ---
export const ghBranches = (pid) => api(`/api/projects/${pid}/github/branches`)
export const ghPulls = (pid) => api(`/api/projects/${pid}/github/pulls`)
export const ghIssues = (pid) => api(`/api/projects/${pid}/github/issues`)
export const ghPullDetail = (pid, number) => api(`/api/projects/${pid}/github/pulls/${number}`)

// --- Branch-history graph (Phase 3) ---
export const ghGraph = (pid) => api(`/api/projects/${pid}/github/graph`)

// Exact merge-conflict preview between two branches of the linked repo.
// Server clones/fetches a bare mirror and runs `git merge-tree`.
export const ghConflicts = (pid, base, head) =>
  api(`/api/projects/${pid}/github/conflicts?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`)
