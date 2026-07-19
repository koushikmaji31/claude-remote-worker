// Discussion conversations (Google-Chat style): the project-wide "Everyone"
// channel plus 1:1 DMs and named groups. Thin wrappers over the platform API.
import { api } from '../api.js'

// -> { conversations: [{ id, type, title, members:[{id,name}], last:{sender,text,ts}|null }] }
export const getConversations = (pid) => api(`/api/projects/${pid}/conversations`)

// type: 'dm' (member_ids=[one]) | 'group' (name + member_ids). Returns the conversation view.
export const createConversation = (pid, body) =>
  api(`/api/projects/${pid}/conversations`, { method: 'POST', body })

export const getConvMessages = (pid, cid, sinceId = 0) =>
  api(`/api/projects/${pid}/conversations/${cid}/messages?since_id=${sinceId}`)

export const postConvMessage = (pid, cid, body) =>
  api(`/api/projects/${pid}/conversations/${cid}/messages`, { method: 'POST', body })
