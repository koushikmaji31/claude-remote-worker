// Ticket API helpers — the shared per-project ticket context plus each agent's
// live task list. Auth is the platform user bearer token (handled by api()).
import { api } from '../api.js'

// GET the ticket + all agents' task lists for a project.
// -> { ticket: {body,set_by,ts}|null, agents: [{agent,tasks:[{text,status}],ts}] }
export function getTicket(pid) {
  return api(`/api/projects/${pid}/ticket`)
}

// Set/replace the ticket context; set_by is the calling user server-side.
export function setTicket(pid, body) {
  return api(`/api/projects/${pid}/ticket/ticket`, { method: 'POST', body: { body } })
}
