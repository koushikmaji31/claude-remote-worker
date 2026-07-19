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

// Per-conversation read state (client-side): the ts of the last message the user
// has seen. Used for unread dots in the list and the unread count in the sidebar.
const readKey = (pid, cid) => `chat-read-${pid}-${cid}`
export const getRead = (pid, cid) => { try { return Number(localStorage.getItem(readKey(pid, cid)) || 0) } catch { return 0 } }
export const setRead = (pid, cid, ts) => { try { localStorage.setItem(readKey(pid, cid), String(ts)) } catch { /* ignore */ } }

// Count of conversations with a message newer than the user's last-read mark.
export const unreadCount = (pid, conversations) =>
  (conversations || []).filter((c) => c.last && c.last.ts > getRead(pid, c.id)).length
