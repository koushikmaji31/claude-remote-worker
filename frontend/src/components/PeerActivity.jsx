// "Team activity" (ticket #15): live view of who is touching which files across the
// team's machines/agents, with click-to-view their actual unified diff. Read-only,
// polls every 5s. Renders nothing until the peer-diff endpoints exist and report
// peers, so it's invisible on servers that don't have the feature yet.
import { useEffect, useState, useCallback } from 'react'
import { getPeers, getPeerDiff } from '../lib/peers.js'

function diffClass(line) {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'del'
  return ''
}

function relTime(ts) {
  if (!ts) return ''
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

export default function PeerActivity({ pid }) {
  const [peers, setPeers] = useState(null)
  const [open, setOpen] = useState(null)   // `${machine}::${file}`
  const [diff, setDiff] = useState(null)
  const [diffErr, setDiffErr] = useState('')

  const poll = useCallback(() => {
    getPeers(pid).then((d) => setPeers(d.peers || [])).catch(() => setPeers([]))
  }, [pid])
  useEffect(() => { poll(); const iv = setInterval(poll, 5000); return () => clearInterval(iv) }, [poll])

  async function view(machine, file) {
    const id = `${machine}::${file}`
    if (open === id) { setOpen(null); setDiff(null); return }
    setOpen(id); setDiff(null); setDiffErr('')
    try { setDiff((await getPeerDiff(pid, machine, file)).diff || '') }
    catch (err) { setDiffErr(err.message) }
  }

  // Invisible until the feature is live and someone is actually touching files.
  if (!peers || peers.length === 0) return null

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Team activity</h2>
        <span className="tag">{peers.length} active</span>
      </header>
      <div className="panel-body peer-activity">
        <p className="muted" style={{ marginTop: 0 }}>
          Who's touching what right now — read a teammate's live diff to reconcile before you push.
        </p>
        {peers.map((p) => (
          <div key={p.machine} className="peer">
            <div className="peer-head">
              <span className="peer-who">{p.agent || p.machine}</span>
              {p.agent && <span className="faint mono">{p.machine}</span>}
              <span className="faint">{relTime(p.updated)}</span>
            </div>
            <ul className="peer-files">
              {(p.files || []).map((f) => {
                const id = `${p.machine}::${f.path}`
                return (
                  <li key={f.path} className="peer-file">
                    <button className="peer-file-btn" onClick={() => view(p.machine, f.path)}>
                      <span className="mono peer-path">{f.path}</span>
                      <span className="peer-counts">
                        {f.added ? <span className="peer-add">+{f.added}</span> : null}
                        {f.removed ? <span className="peer-del">−{f.removed}</span> : null}
                      </span>
                    </button>
                    {open === id && (
                      <div className="peer-diff">
                        {diffErr && <div className="alert error">{diffErr}</div>}
                        {diff === null && !diffErr && <div className="skeleton" style={{ height: 40 }} />}
                        {diff !== null && diff !== '' && (
                          <pre className="git-diff" aria-label={`diff of ${f.path}`}>
                            {diff.split('\n').map((l, i) => <span key={i} className={diffClass(l)}>{l + '\n'}</span>)}
                          </pre>
                        )}
                        {diff === '' && <p className="faint" style={{ margin: 0 }}>(no textual diff)</p>}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}
