// Project chat/message panel. Self-contained: polls the project message log,
// renders it, and posts new messages. Messages may carry an optional `image`
// (a data-URL string) per the team image contract; images are downscaled and
// re-encoded client-side to stay under the 2MB cap the server also enforces.
// Styling uses the shared design tokens/classes — no hardcoded colors here.
import { useEffect, useRef, useState, useCallback } from 'react'
import { api } from '../api.js'

const MAX_EDGE = 1600 // px, longest edge after downscale
const MAX_BYTES = 2 * 1024 * 1024 // 2MB hard cap on the data URL (matches server backstop)

function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

// Approximate decoded byte size of a data URL's base64 payload.
function dataUrlBytes(dataUrl) {
  const i = dataUrl.indexOf(',')
  const b64 = i >= 0 ? dataUrl.slice(i + 1) : dataUrl
  return Math.floor((b64.length * 3) / 4)
}

// Downscale + re-encode an image File to a data URL under MAX_BYTES.
// Keeps PNG for transparency when it fits, otherwise falls back to JPEG and
// steps quality down until it's under the cap.
async function fileToDataUrl(file) {
  if (!file.type.startsWith('image/')) throw new Error('That file is not an image')
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const width = Math.round(bitmap.width * scale)
  const height = Math.round(bitmap.height * scale)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  canvas.getContext('2d').drawImage(bitmap, 0, 0, width, height)
  bitmap.close?.()

  let dataUrl = file.type === 'image/png' ? canvas.toDataURL('image/png') : ''
  if (!dataUrl || dataUrlBytes(dataUrl) > MAX_BYTES) {
    let q = 0.85
    dataUrl = canvas.toDataURL('image/jpeg', q)
    while (dataUrlBytes(dataUrl) > MAX_BYTES && q > 0.4) {
      q -= 0.15
      dataUrl = canvas.toDataURL('image/jpeg', q)
    }
  }
  if (dataUrlBytes(dataUrl) > MAX_BYTES) {
    throw new Error('Image is too large even after compression (max 2MB)')
  }
  return dataUrl
}

export default function ChatPanel({ pid, me }) {
  const [messages, setMessages] = useState([])
  const [text, setText] = useState('')
  const [image, setImage] = useState(null) // pending data URL
  const [error, setError] = useState('')
  const [attaching, setAttaching] = useState(false)
  const sinceId = useRef(0)
  const logRef = useRef(null)
  const fileRef = useRef(null)

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

  async function attach(file) {
    if (!file) return
    setError('')
    setAttaching(true)
    try {
      setImage(await fileToDataUrl(file))
    } catch (err) {
      setError(err.message)
    } finally {
      setAttaching(false)
    }
  }

  function onPaste(e) {
    const item = [...(e.clipboardData?.items || [])].find((it) => it.type.startsWith('image/'))
    if (item) {
      e.preventDefault()
      attach(item.getAsFile())
    }
  }

  async function post(e) {
    e.preventDefault()
    if (!text.trim() && !image) return
    try {
      await api(`/api/projects/${pid}/messages`, {
        method: 'POST',
        body: { text, image: image || undefined },
      })
      setText('')
      setImage(null)
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
                  {m.image && (
                    <a href={m.image} target="_blank" rel="noreferrer" className="msg-img-link">
                      <img className="msg-img" src={m.image} alt="attachment" />
                    </a>
                  )}
                  {m.text && <div className="body">{m.text}</div>}
                </div>
              </div>
            )
          })}
        </div>

        {image && (
          <div className="composer-preview">
            <img src={image} alt="attachment preview" />
            <button type="button" className="composer-preview-remove" onClick={() => setImage(null)} aria-label="Remove image">
              ×
            </button>
          </div>
        )}

        <form className="composer" onSubmit={post}>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              attach(e.target.files?.[0])
              e.target.value = '' // allow re-picking the same file
            }}
          />
          <button
            type="button"
            className="btn ghost btn-attach"
            onClick={() => fileRef.current?.click()}
            disabled={attaching}
            title="Attach image"
            aria-label="Attach image"
          >
            {attaching ? '…' : '📎'}
          </button>
          <textarea
            placeholder="Write a message…  (paste an image to attach)"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onPaste={onPaste}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) post(e)
            }}
          />
          <button type="submit" className="btn" disabled={(!text.trim() && !image) || attaching}>Post</button>
        </form>
      </div>
    </section>
  )
}
