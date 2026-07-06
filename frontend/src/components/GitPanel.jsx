// Git coordination panel: lets teammates inspect branches, diffs, and merge
// conflicts of a shared repo (via the backend's /rpc git.* methods) so they can
// zero down merge conflicts before they happen.
import { useState } from 'react'
import { gitBranches, gitDiff, gitConflicts } from '../lib/rpc'

// Class a single unified-diff line so styles.css can colorize it.
function diffLineClass(line) {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'add'
  if (line.startsWith('-') && !line.startsWith('---')) return 'del'
  return ''
}

export default function GitPanel() {
  const [repoPath, setRepoPath] = useState('')
  const [branches, setBranches] = useState(null)
  const [current, setCurrent] = useState('')
  const [base, setBase] = useState('')
  const [head, setHead] = useState('')
  const [diff, setDiff] = useState('')
  const [conflicts, setConflicts] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function loadBranches(e) {
    e?.preventDefault()
    setError(''); setDiff(''); setConflicts(null); setBusy(true)
    try {
      const res = await gitBranches(repoPath)
      setBranches(res.branches || [])
      setCurrent(res.current || '')
      if (res.branches?.length >= 2) {
        setBase(res.current || res.branches[0])
        setHead(res.branches.find((b) => b !== (res.current || res.branches[0])) || res.branches[0])
      }
    } catch (err) {
      setError(err.message)
      setBranches(null)
    } finally {
      setBusy(false)
    }
  }

  async function compare() {
    setError(''); setBusy(true)
    try {
      const [d, c] = await Promise.all([
        gitDiff(repoPath, base, head),
        gitConflicts(repoPath, base, head),
      ])
      setDiff(d.diff || '(no differences)')
      setConflicts(c.conflicts || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="git-panel card">
      <h2>Git coordination</h2>
      <p className="muted">
        Inspect a shared repo's branches and check for merge conflicts before merging.
      </p>

      <form onSubmit={loadBranches} className="git-panel-row">
        <input
          type="text"
          placeholder="Absolute repo path on the server, e.g. /path/to/repo"
          value={repoPath}
          onChange={(e) => setRepoPath(e.target.value)}
          required
        />
        <button type="submit" className="btn" disabled={busy || !repoPath}>
          {busy ? 'Loading…' : 'Load branches'}
        </button>
      </form>

      {error && <p className="alert error">{error}</p>}

      {branches && (
        <>
          <div className="git-panel-row">
            <label>
              Base{' '}
              <select value={base} onChange={(e) => setBase(e.target.value)}>
                {branches.map((b) => (
                  <option key={b} value={b}>{b}{b === current ? ' (current)' : ''}</option>
                ))}
              </select>
            </label>
            <label>
              Compare{' '}
              <select value={head} onChange={(e) => setHead(e.target.value)}>
                {branches.map((b) => (
                  <option key={b} value={b}>{b}{b === current ? ' (current)' : ''}</option>
                ))}
              </select>
            </label>
            <button className="btn secondary" onClick={compare} disabled={busy || !base || !head || base === head}>
              Diff + conflict check
            </button>
          </div>

          {conflicts !== null && (
            <p className={`git-status ${conflicts.length ? 'error' : 'ok'}`}>
              {conflicts.length
                ? `Merge conflicts in ${conflicts.length} file(s): ${conflicts.join(', ')}`
                : 'No merge conflicts between these branches.'}
            </p>
          )}

          {diff && (
            <pre className="git-diff" aria-label="unified diff">
              {diff.split('\n').map((line, i) => (
                <span key={i} className={diffLineClass(line)}>{line + '\n'}</span>
              ))}
            </pre>
          )}
        </>
      )}
    </section>
  )
}
