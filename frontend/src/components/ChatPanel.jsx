// Discussion — Google-Chat-style. Left: a conversation list (the project-wide
// "Everyone" channel + 1:1 DMs + named groups, all human members). Right: the
// selected conversation's thread + composer. Messages may carry an optional
// `image` (a data-URL); images are downscaled client-side under the 2MB cap.
// Styling uses shared design tokens/classes.
import { useEffect, useRef, useState, useCallback } from 'react'
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

function ConvIcon({ conv }) {
  if (conv.type === 'everyone') return <span className="conv-icon everyone" aria-hidden>#</span>
  if (conv.type === 'group') return <span className="conv-icon group" style={{ background: avatarColor(conv.title) }} aria-hidden>{initials(conv.title)}</span>
  return <span className="conv-icon" style={{ background: avatarColor(conv.title) }} aria-hidden>{initials(conv.title)}</span>
}

export default function ChatPanel({ pid, me, members = [] }) {
  const [convs, setConvs] = useState([])
  const [selected, setSelected] = useState(null)   // conversation id
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [image, setImage] = useState(null)
  const [error, setError] = useState('')
  const [attaching, setAttaching] = useState(false)
  const [composing, setComposing] = useState(false)  // new-conversation panel open
  const [pick, setPick] = useState([])               // selected member ids
  const [groupName, setGroupName] = useState('')
  const sinceId = useRef(0)
  const logRef = useRef(null)
  const fileRef = useRef(null)

  const others = members.filter((m) => m.user_id !== me?.user_id)

  const loadConvs = useCallback(() => {
    getConversations(pid).then((d) => {
      setConvs(d.conversations)
      setSelected((s) => s ?? (d.conversations[0]?.id ?? null))
    }).catch(() => {})
  }, [pid])
  useEffect(() => { loadConvs(); const iv = setInterval(loadConvs, 4000); return () => clearInterval(iv) }, [loadConvs])

  // Messages for the selected conversation.
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
    try {
      await postConvMessage(pid, selected, { text, image: image || undefined })
      setText(''); setImage(null); pollMessages()
    } catch (err) { setError(err.message) }
  }

  const togglePick = (id) => setPick((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  async function startDm() {
    if (pick.length !== 1) return
    try {
      const c = await createConversation(pid, { type: 'dm', member_ids: pick })
      resetCompose(); loadConvs(); setSelected(c.id)
    } catch (err) { setError(err.message) }
  }
  async function createGroup() {
    if (!groupName.trim() || pick.length === 0) return
    try {
      const c = await createConversation(pid, { type: 'group', name: groupName.trim(), member_ids: pick })
      resetCompose(); loadConvs(); setSelected(c.id)
    } catch (err) { setError(err.message) }
  }
  function resetCompose() { setComposing(false); setPick([]); setGroupName('') }

  const active = convs.find((c) => c.id === selected)

  return (
    <div className="chat-wrap">
      {/* LEFT: conversation list */}
      <aside className="conv-list">
        <div className="conv-list-head">
          <span>Messages</span>
          <button className="btn ghost sm" onClick={() => setComposing((v) => !v)} title="New conversation">{composing ? 'Cancel' : '+ New'}</button>
        </div>

        {composing && (
          <div className="conv-new">
            <input className="conv-group-name" placeholder="Group name (optional for DM)"
                   value={groupName} onChange={(e) => setGroupName(e.target.value)} />
            <div className="conv-pick">
              {others.length === 0 && <p className="faint" style={{ margin: 0 }}>No other members yet.</p>}
              {others.map((m) => (
                <label key={m.user_id} className={`conv-pick-row ${pick.includes(m.user_id) ? 'on' : ''}`}>
                  <input type="checkbox" checked={pick.includes(m.user_id)} onChange={() => togglePick(m.user_id)} />
                  <span className="avatar sm" aria-hidden style={{ background: avatarColor(m.name) }}>{initials(m.name)}</span>
                  <span>{m.name}</span>
                </label>
              ))}
            </div>
            <div className="conv-new-actions">
              <button className="btn sm" disabled={pick.length !== 1 || !!groupName.trim()} onClick={startDm} title="1:1 direct message">Start DM</button>
              <button className="btn sm" disabled={!groupName.trim() || pick.length === 0} onClick={createGroup}>Create group</button>
            </div>
          </div>
        )}

        <div className="conv-items">
          {convs.map((c) => {
            const unread = c.last && c.id !== selected && c.last.ts > getRead(pid, c.id)
            return (
              <button key={c.id} className={`conv-item ${c.id === selected ? 'on' : ''}`} onClick={() => setSelected(c.id)}>
                <ConvIcon conv={c} />
                <div className="conv-item-main">
                  <div className="conv-item-top">
                    <span className="conv-item-title">{c.title}{c.type === 'group' && <span className="faint conv-count"> · {c.members.length}</span>}</span>
                    {c.last && <span className="conv-item-time faint">{relTime(c.last.ts)}</span>}
                  </div>
                  <div className="conv-item-last faint">
                    {c.last ? <>{c.last.sender.split(/\s+/)[0]}: {c.last.text}</> : 'No messages yet'}
                  </div>
                </div>
                {unread && <span className="conv-unread" aria-label="unread" />}
              </button>
            )
          })}
        </div>
      </aside>

      {/* RIGHT: thread */}
      <section className="chat">
        {active && (
          <div className="chat-head">
            <ConvIcon conv={active} />
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
          <button type="submit" className="btn sm chat-send" disabled={(!text.trim() && !image) || attaching || !selected}>Post</button>
        </form>
      </section>
    </div>
  )
}
