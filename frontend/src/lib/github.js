// GitHub integration client (Phase 1: identity + repo link).
// Thin wrappers over the /api/github/* endpoints in app/platform.py.
import { api } from '../api'

// --- Identity (per-user GitHub account) ---
export const githubStatus = () => api('/api/github/status')

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

// --- Write (Phase 3): create branches / PRs / issues, comment ---
export const ghCreateBranch = (pid, name, fromRef) =>
  api(`/api/projects/${pid}/github/branches`, { method: 'POST', body: { name, from_ref: fromRef } })

export const ghCreatePull = (pid, data) =>
  api(`/api/projects/${pid}/github/pulls`, { method: 'POST', body: data })

export const ghCreateIssue = (pid, data) =>
  api(`/api/projects/${pid}/github/issues`, { method: 'POST', body: data })

export const ghComment = (pid, number, body) =>
  api(`/api/projects/${pid}/github/issues/${number}/comments`, { method: 'POST', body: { body } })

// --- Webhooks (Phase 4): setup info + recent deliveries ---
export const ghWebhookInfo = (pid) => api(`/api/projects/${pid}/github/webhook`)
