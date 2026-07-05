// Project chat/message panel (owner: agent-d). Self-contained: polls the
// project message log, renders it, and posts new messages. Styling uses the
// shared design tokens/classes owned by agent-e (.card, .msglog, .msg,
// .composer, .avatar, .btn) — no hardcoded colors here.
import { useEffect, useRef, useState, useCallback } from 'react'
import { api } from '../api.js'

function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

export default function ChatPanel({ pid, me }) {
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const sinceId = useRef(0)
  const logRef = useRef(null)

  const pollMessages = useCallback(() => {
    api(`/api/projects/${pid}/messages?since_id=${sinceId.current}`)
      .then((d) => {
        if (d.messages.length) {
          sinceId.current = d.messages[d.messages.length - 1].id
          setMessages((prev) => [...prev, ...d.messages])
        }
      })
      .catch(() => {})
  }, [pid])

  useEffect(() => {
    pollMessages()
    const iv = setInterval(pollMessages, 3000)
    return () => clearInterval(iv)
  }, [pollMessages])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [messages])

  async function post(e) {
    e.preventDefault()
    if (!text.trim()) return
    try {
      await api(`/api/projects/${pid}/messages`, { method: 'POST', body: { text } })
      setText('')
      pollMessages()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section className="card flush">
      <div className="card-head">
        <h2>Discussion</h2>
        <span className="faint">{messages.length} message{messages.length === 1 ? '' : 's'}</span>
      </div>

      <div className="card-body">
        {error && <div className="alert error">{error}</div>}

        <div className="msglog" ref={logRef}>
          {messages.length === 0 && <p className="muted">No messages yet. Start the conversation.</p>}
          {messages.map((m) => {
            const mine = me && m.sender === me.name
            return (
              <div className={`msg${mine ? ' me' : ''}`} key={m.id}>
                <span className="avatar" aria-hidden>{initials(m.sender)}</span>
                <div className="meta">
                  <div>
                    <span className="sender">{m.sender}</span>{' '}
                    <span className="ts">{new Date(m.ts * 1000).toLocaleTimeString()}</span>
                  </div>
                  <div className="body">{m.text}</div>
                </div>
              </div>
            )
          })}
        </div>

        <form className="composer" onSubmit={post}>
          <textarea
            placeholder="Write a message…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) post(e)
            }}
          />
          <button type="submit" className="btn" disabled={!text.trim()}>Post</button>
        </form>
      </div>
    </section>
  )
}
