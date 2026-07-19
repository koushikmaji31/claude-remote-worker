// Discussion — Google-Chat-style. Left: a list of EVERY conversation you could
// have — the project-wide "Everyone" channel, a row for every teammate (a DM,
// empty until you've messaged), and any named groups. A search box filters it;
// rows sort latest-message-first. Right: the selected thread + composer.
// Group creation is a lightweight mode: tap people in the same list to add them.
// Messages may carry an optional downscaled `image` (2MB cap). Shared tokens.
import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { avatarColor } from '../ui/avatarColor.js'
import { getConversations, createConversation, getConvMessages, postConvMessage } from '../lib/chat.js'

const MAX_EDGE = 1600
const MAX_BYTES = 2 * 1024 * 1024

function initials(name) {
  return (name || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}
function dataUrlBytes(dataUrl) {
  const i = dataUrl.indexOf(',')
  const b64 = i >= 0 ? dataUrl.slice(i + 1) : dataUrl
  return Math.floor((b64.length * 3) / 4)
}
async function fileToDataUrl(file) {
  if (!file.type.startsWith('image/')) throw new Error('That file is not an image')
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const width = Math.round(bitmap.width * scale)
  const height = Math.round(bitmap.height * scale)
  const canvas = document.createElement('canvas')
  canvas.width = width; canvas.height = height
  canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height)
  bitmap.close?.()
  let dataUrl = file.type === 'image/png' ? canvas.toDataURL('image/png') : ''
  if (!dataUrl || dataUrlBytes(dataUrl) > MAX_BYTES) {
    let q = 0.85
    dataUrl = canvas.toDataURL('image/jpeg', q)
    while (dataUrlBytes(dataUrl) > MAX_BYTES && q > 0.4) { q -= 0.15; dataUrl = canvas.toDataURL('image/jpeg', q) }
  }
  if (dataUrlBytes(dataUrl) > MAX_BYTES) throw new Error('Image is too large even after compression (max 2MB)')
  return dataUrl
}

function relTime(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

const readKey = (pid, cid) => `chat-read-${pid}-${cid}`
const getRead = (pid, cid) => { try { return Number(localStorage.getItem(readKey(pid, cid)) || 0) } catch { return 0 } }
const setRead = (pid, cid, ts) => { try { localStorage.setItem(readKey(pid, cid), String(ts)) } catch { /* ignore */ } }

function ConvIcon({ row }) {
  if (row.type === 'everyone') return <span className="conv-icon everyone" aria-hidden>#</span>
  return <span className={`conv-icon ${row.type === 'group' ? 'group' : ''}`} style={{ background: avatarColor(row.title) }} aria-hidden>{initials(row.title)}</span>
}

export default function ChatPanel({ pid, me, members = [] }) {
  const [convs, setConvs] = useState([])
  const [selected, setSelected] = useState(null)
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [image, setImage] = useState(null)
  const [error, setError] = useState('')
  const [attaching, setAttaching] = useState(false)
  const [query, setQuery] = useState('')
  const [groupMode, setGroupMode] = useState(false)
  const [groupPick, setGroupPick] = useState([])   // user ids
  const [groupName, setGroupName] = useState('')
  const sinceId = useRef(0)
  const logRef = useRef(null)
  const fileRef = useRef(null)

  const others = useMemo(() => members.filter((m) => m.user_id !== me?.user_id), [members, me])

  const loadConvs = useCallback(() => {
    getConversations(pid).then((d) => {
      setConvs(d.conversations)
      setSelected((s) => s ?? (d.conversations.find((c) => c.type === 'everyone')?.id ?? d.conversations[0]?.id ?? null))
    }).catch(() => {})
  }, [pid])
  useEffect(() => { loadConvs(); const iv = setInterval(loadConvs, 4000); return () => clearInterval(iv) }, [loadConvs])

  const pollMessages = useCallback(() => {
    if (!selected) return
    getConvMessages(pid, selected, sinceId.current).then((d) => {
      if (d.messages.length) {
        sinceId.current = d.messages[d.messages.length - 1].id
        setMessages((prev) => [...prev, ...d.messages])
        setRead(pid, selected, d.messages[d.messages.length - 1].ts)
      }
    }).catch(() => {})
  }, [pid, selected])
  useEffect(() => {
    sinceId.current = 0; setMessages([])
    if (!selected) return
    setRead(pid, selected, Date.now() / 1000)
    pollMessages()
    const iv = setInterval(pollMessages, 3000)
    return () => clearInterval(iv)
  }, [selected, pid, pollMessages])
  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [messages])

  // Build the full list: Everyone + a row per teammate (real DM or empty
  // placeholder) + groups. Filter by search, sort latest-first (empties last).
  const rows = useMemo(() => {
    const everyone = convs.find((c) => c.type === 'everyone')
    const groups = convs.filter((c) => c.type === 'group')
    const dmByUser = new Map()
    convs.filter((c) => c.type === 'dm').forEach((c) => {
      const o = c.members.find((m) => m.id !== me?.user_id)
      if (o) dmByUser.set(o.id, c)
    })
    const memberRows = others.map((m) => dmByUser.get(m.user_id) || {
      id: `new-${m.user_id}`, type: 'dm', title: m.name,
      members: [{ id: m.user_id, name: m.name }], last: null, newUser: m.user_id,
    })
    let all = [...(everyone ? [everyone] : []), ...groups, ...memberRows]
    const q = query.trim().toLowerCase()
    if (q) all = all.filter((r) => r.title.toLowerCase().includes(q))
    const rank = (r) => (r.type === 'everyone' ? 0 : r.last?.ts ? 1 : 2)
    all.sort((a, b) => {
      const ra = rank(a), rb = rank(b)
      if (ra !== rb) return ra - rb
      if (ra === 1) return (b.last?.ts || 0) - (a.last?.ts || 0)
      return a.title.localeCompare(b.title)
    })
    return all
  }, [convs, others, query, me])

  async function attach(file) {
    if (!file) return
    setError(''); setAttaching(true)
    try { setImage(await fileToDataUrl(file)) } catch (err) { setError(err.message) } finally { setAttaching(false) }
  }
  function onPaste(e) {
    const item = [...(e.clipboardData?.items || [])].find((it) => it.type.startsWith('image/'))
    if (item) { e.preventDefault(); attach(item.getAsFile()) }
  }
  async function post(e) {
    e.preventDefault()
    if ((!text.trim() && !image) || !selected) return
    try { await postConvMessage(pid, selected, { text, image: image || undefined }); setText(''); setImage(null); pollMessages() }
    catch (err) { setError(err.message) }
  }

  const otherIdOf = (r) => r.newUser ?? r.members.find((m) => m.id !== me?.user_id)?.id
  const toggleGroupPick = (id) => setGroupPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  async function openRow(r) {
    if (groupMode) { if (r.type === 'dm') toggleGroupPick(otherIdOf(r)); return }
    if (r.newUser) {
      try { const c = await createConversation(pid, { type: 'dm', member_ids: [r.newUser] }); await loadConvs(); setSelected(c.id) }
      catch (err) { setError(err.message) }
    } else setSelected(r.id)
  }
  async function createGroup() {
    if (!groupName.trim() || groupPick.length === 0) return
    try {
      const c = await createConversation(pid, { type: 'group', name: groupName.trim(), member_ids: groupPick })
      setGroupMode(false); setGroupPick([]); setGroupName(''); await loadConvs(); setSelected(c.id)
    } catch (err) { setError(err.message) }
  }

  const active = convs.find((c) => c.id === selected)

  return (
    <div className="chat-wrap">
      {/* LEFT: conversation list */}
      <aside className="conv-list">
        <div className="conv-list-head">
          <span>Messages</span>
          <button className="conv-newgroup" onClick={() => { setGroupMode((v) => !v); setGroupPick([]); setGroupName('') }}>
            {groupMode ? 'Cancel' : 'New group'}
          </button>
        </div>

        <div className="conv-search">
          {groupMode ? (
            <input autoFocus placeholder="Group name" value={groupName} onChange={(e) => setGroupName(e.target.value)} />
          ) : (
            <input placeholder="Search people and groups" value={query} onChange={(e) => setQuery(e.target.value)} />
          )}
        </div>
        {groupMode && (
          <div className="conv-group-bar">
            <span className="faint">{groupPick.length} selected · tap people below</span>
            <button className="btn sm" disabled={!groupName.trim() || groupPick.length === 0} onClick={createGroup}>Create</button>
          </div>
        )}

        <div className="conv-items">
          {rows.map((r) => {
            const picked = groupMode && r.type === 'dm' && groupPick.includes(otherIdOf(r))
            const disabled = groupMode && r.type !== 'dm'
            const unread = !groupMode && r.last && r.id !== selected && r.last.ts > getRead(pid, r.id)
            return (
              <button key={r.id} className={`conv-item ${r.id === selected && !groupMode ? 'on' : ''} ${picked ? 'picked' : ''} ${disabled ? 'muted-row' : ''}`}
                      disabled={disabled} onClick={() => openRow(r)}>
                <ConvIcon row={r} />
                <div className="conv-item-main">
                  <div className="conv-item-top">
                    <span className="conv-item-title">{r.title}{r.type === 'group' && <span className="faint conv-count"> · {r.members.length}</span>}</span>
                    {r.last && !groupMode && <span className="conv-item-time faint">{relTime(r.last.ts)}</span>}
                  </div>
                  <div className="conv-item-last faint">
                    {r.last ? <>{r.last.sender.split(/\s+/)[0]}: {r.last.text}</> : (r.type === 'dm' ? 'No messages yet' : '—')}
                  </div>
                </div>
                {picked && <span className="conv-check" aria-hidden>✓</span>}
                {unread && <span className="conv-unread" aria-label="unread" />}
              </button>
            )
          })}
          {rows.length === 0 && <p className="faint" style={{ padding: '8px 10px' }}>No matches.</p>}
        </div>
      </aside>

      {/* RIGHT: thread */}
      <section className="chat">
        {active && (
          <div className="chat-head">
            <ConvIcon row={active} />
            <div>
              <div className="chat-head-title">{active.title}</div>
              <div className="faint chat-head-sub">
                {active.type === 'everyone' ? 'Everyone in this workspace'
                  : active.type === 'group' ? active.members.map((m) => m.name).join(', ')
                  : 'Direct message'}
              </div>
            </div>
          </div>
        )}

        {error && <div className="alert error" style={{ margin: 'var(--sp-2) 0' }}>{error}</div>}

        <div className="chat-log" ref={logRef}>
          {messages.length === 0 && <p className="muted chat-empty">No messages yet. Start the conversation.</p>}
          {messages.map((m, i) => {
            const mine = me && m.sender === me.name
            const prev = messages[i - 1]
            const grouped = prev && prev.sender === m.sender && (m.ts - prev.ts) < 300
            return (
              <div className={`crow${mine ? ' me' : ''}${grouped ? ' grouped' : ''}`} key={m.id}>
                {!mine && <div className="crow-gutter">{!grouped && <span className="avatar sm" aria-hidden style={{ background: avatarColor(m.sender) }}>{initials(m.sender)}</span>}</div>}
                <div className="crow-main">
                  {!grouped && (
                    <div className="crow-meta">
                      <span className="crow-sender">{mine ? 'You' : m.sender}</span>
                      <span className="crow-ts">{new Date(m.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                    </div>
                  )}
                  <div className="bubble">
                    {m.image && (
                      <a href={m.image} target="_blank" rel="noreferrer" className="bubble-img-link">
                        <img className="bubble-img" src={m.image} alt="attachment" />
                      </a>
                    )}
                    {m.text && <span className="bubble-text">{m.text}</span>}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {image && (
          <div className="composer-preview">
            <img src={image} alt="attachment preview" />
            <button type="button" className="composer-preview-remove" onClick={() => setImage(null)} aria-label="Remove image">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
            </button>
          </div>
        )}

        <form className="chat-composer" onSubmit={post}>
          <input ref={fileRef} type="file" accept="image/*" hidden
            onChange={(e) => { attach(e.target.files?.[0]); e.target.value = '' }} />
          <button type="button" className="chat-attach" onClick={() => fileRef.current?.click()}
            disabled={attaching || !selected} title="Attach image" aria-label="Attach image">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <textarea
            placeholder={active ? `Message ${active.title}…  (paste an image to attach)` : 'Select a conversation'}
            value={text} disabled={!selected}
            onChange={(e) => setText(e.target.value)}
            onPaste={onPaste}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) post(e) }}
          />
          <button type="submit" className="chat-send" aria-label="Send" title="Send  (Cmd/Ctrl + Enter)"
                  disabled={(!text.trim() && !image) || attaching || !selected}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4 20-7z" />
            </svg>
          </button>
        </form>
      </section>
    </div>
  )
}
