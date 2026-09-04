# Decisions

Append-only. Newest last. One entry per decision that another person or agent
could otherwise re-litigate or contradict.

Format: `## YYYY-MM-DD HH:MM — <decision>` then **Why**, **Alternatives
rejected**, **Who**.

---

## 2026-09-03 22:30 — Stack: Next.js + FastAPI + SQLite, canvas via xyflow

**Why:** Fastest path to a widget canvas we can extend during the event;
FastAPI keeps Playwright and the Anthropic SDK in the same process as the data.

**Alternatives rejected:** dashboard grid (react-grid-layout) — less aligned
with agent/flow visuals; Postgres — needless ops cost for a 30-hour build.

**Who:** shivam + Claude, pre-event setup.

---

## 2026-09-03 22:30 — One worktree per person, each running its own stack

**Why:** Three people running `npm run dev` in one checkout collide on ports
and on `app.db`. Per-worktree port pairs let all three stacks run at once so
changes can be compared side by side.

**Alternatives rejected:** shared dev server (serialises the team); Docker
Compose (slower to start, more to debug at 2am).

**Who:** shivam + Claude, pre-event setup.
