// JSON-RPC client for the agent-coordination endpoints (see docs/API_CONTRACT.md).
import { api } from '../api'

let nextId = 1

export async function rpc(method, params) {
  const data = await api('/rpc', {
    method: 'POST',
    body: { method, params, id: nextId++ },
  })
  if (data?.error) {
    const err = new Error(data.error.message || 'RPC error')
    err.code = data.error.code
    throw err
  }
  return data.result
}

export const gitBranches = (repoPath) =>
  rpc('git.branches', { repo_path: repoPath })

export const gitDiff = (repoPath, base, head) =>
  rpc('git.diff', { repo_path: repoPath, base, head })

export const gitConflicts = (repoPath, base, head) =>
  rpc('git.conflicts', { repo_path: repoPath, base, head })
