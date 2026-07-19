// Profile — a small modal off the sidebar user name. Shows identity + sign-in
// method and lets the user SET or CHANGE a password. A Google-only account can
// set a password here to also log in with email+password (the backend flips it
// to 'both'); an account that already has one must confirm the current password.
import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { avatarColor } from '../ui/avatarColor.js'

function initials(name) {
  return (name || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
}
const PROVIDER_LABEL = { google: 'Google', password: 'Email & password', both: 'Google + password' }

export default function ProfileModal({ onClose }) {
  const [me, setMe] = useState(null)
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const load = () => api('/api/me').then(setMe).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  async function submit(e) {
    e.preventDefault()
    setError(''); setDone(false)
    if (next.length < 8) return setError('Password must be at least 8 characters')
    if (next !== confirm) return setError('Passwords do not match')
    setBusy(true)
    try {
      await api('/api/account/password', { method: 'POST', body: { new_password: next, current_password: cur || undefined } })
      setCur(''); setNext(''); setConfirm(''); setDone(true)
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const hasPw = me?.has_password
  return (
    <div className="pf-backdrop" onClick={onClose}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <header className="pf-head">
          <span className="avatar" aria-hidden style={{ background: avatarColor(me?.name || '') }}>{initials(me?.name)}</span>
          <div className="pf-id">
            <div className="pf-name">{me?.name || '—'}</div>
            <div className="faint">{me?.email}</div>
          </div>
          <button className="pf-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="pf-body">
          <div className="pf-row">
            <span className="pf-k">Sign-in method</span>
            <span className="pf-v">{me ? (PROVIDER_LABEL[me.auth_provider] || me.auth_provider || 'Email & password') : '…'}</span>
          </div>

          <form className="pf-form" onSubmit={submit}>
            <div className="pf-section-title">{hasPw ? 'Change password' : 'Set a password'}</div>
            {!hasPw && me?.auth_provider === 'google' && (
              <p className="faint" style={{ margin: 0 }}>You signed in with Google. Set a password to also log in with your email.</p>
            )}
            {hasPw && (
              <label className="field">
                <span className="label">Current password</span>
                <input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required />
              </label>
            )}
            <label className="field">
              <span className="label">New password</span>
              <input type="password" value={next} onChange={(e) => setNext(e.target.value)} minLength={8} autoComplete="new-password" placeholder="At least 8 characters" required />
            </label>
            <label className="field">
              <span className="label">Confirm new password</span>
              <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} minLength={8} autoComplete="new-password" required />
            </label>
            {error && <div className="alert error">{error}</div>}
            {done && <div className="pf-ok">Password {hasPw ? 'changed' : 'set'}. You can now sign in with your email and password.</div>}
            <button className="btn block" type="submit" disabled={busy || !next || !confirm}>{busy ? 'Saving…' : hasPw ? 'Change password' : 'Set password'}</button>
          </form>
        </div>
      </div>
    </div>
  )
}
