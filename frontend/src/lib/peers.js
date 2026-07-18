// Peer-diff sharing (ticket #15): read what teammates/agents are touching + their
// live diff, so you can reconcile without the "what did you change?" back-and-forth.
// Thin wrappers over platform.py proxy endpoints (which pass through to the bus).
import { api } from '../api.js'

// -> { peers: [{ machine, agent, files: [{path, added, removed}], updated }] }
export const getPeers = (pid) => api(`/api/projects/${pid}/peers`)

// -> { machine, file, diff }  (omit file to get the machine's whole diff)
export const getPeerDiff = (pid, machine, file) =>
  api(`/api/projects/${pid}/peers/diff?machine=${encodeURIComponent(machine)}` +
    (file ? `&file=${encodeURIComponent(file)}` : ''))
