# Instructions for shantanu — Branches graph (koushik_2 took these over while you were out of tokens)

Two asks from the product owner, both in your Branches area. I implemented them so
you weren't blocked; here's exactly what changed so you can review / build on it.

## 1. Graph is instant now (stale-while-revalidate + disk cache)
`app/platform.py` — the `github_graph` endpoint was rebuilt:
- Extracted the GitHub-fetching body into `_build_graph_result(conn, pid, user_id)` (unchanged logic).
- The endpoint now: memory cache hit → return; else **disk cache hit → return instantly AND refresh in a background thread** (`_graph_refresh_bg`); else cold-build once.
- Built graphs persist to `.graph-cache/{pid}.json` (gitignored), so a **server restart is no longer a cold start**.
- Response gains `cached: true` + `stale_age` when served from cache (the UI shows "refreshing").
- Tunables unchanged: `GRAPH_CACHE_TTL=60`, `GRAPH_MAX_BRANCHES=12`, `GRAPH_COMMITS_PER_BRANCH=30`.
- If you want a manual refresh button later: add `?refresh=1` that skips the cache and calls `_build_graph_result` sync.

## 2. Squashed commits (expandable) in the graph
`frontend/src/components/BranchGraph.jsx` — rewritten so SVG dots, edges and rows are
all positioned by one **display-row index** (they were index-locked to the raw commit
list, so you can't collapse rows without desyncing).
- `findGroups()` detects runs of **minor linear commits** (single parent = the next row,
  same branch, not a branch tip/merge) of length ≥ `MIN_GROUP` (3).
- A collapsed group renders as ONE clickable row ("N commits — click to expand") with a
  square marker in the lane; `expanded` (a Set of group ids) toggles it open to reveal
  the member commits (shown with a `↳` and `.gh-graph-member` style).
- Edges are recomputed over rendered-row indices via `rowOfSha`, dropping edges internal
  to a collapsed group — so the DAG stays correct as you expand/collapse.
- New CSS: `.gh-graph-group`, `.gh-group-caret`, `.gh-graph-member` in `styles.css`.

## What I did NOT touch
Only `app/platform.py` (the graph endpoint + cache helpers), `BranchGraph.jsx`,
`styles.css` (graph rules), `.gitignore`. Your GitHub/GitLab/Jira identity, link, and
data endpoints are untouched.

## Verified
`py_compile` clean; `npm run build` clean (51 modules). Not browser-tested against a live
linked repo — please eyeball the squash expand/collapse and the cached-graph render when
you're back, and tweak `MIN_GROUP` if the grouping is too aggressive/shy.
