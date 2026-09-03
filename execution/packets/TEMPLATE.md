# P0N — <name>

**Owner:** <person>   **Agent:** <claude session / none>   **Status:** READY | RUNNING | BLOCKED | DONE

## Objective

_One sentence. What exists at the end that doesn't now._

## Read first

- `problem/statement.md`
- `product/thesis.md`
- `product/decisions.md`

## Boundary

**Owns** (may create and modify freely):
- `path/**`

**May modify** (shared — announce in `product/decisions.md` first):
- `web/lib/contracts/*.ts`

**Must not modify:**
- everything else

## Contracts

**Consumes:** _type / endpoint, and where it is defined_

**Produces:** _type / endpoint, and where it is defined_

## Acceptance

Observable checks. Someone else must be able to run these without asking you.

1.
2.

## Definition of done

- [ ] acceptance checks pass
- [ ] `git diff --name-only main` touches only owned paths
- [ ] demo beat it serves still works end to end
- [ ] result written to `execution/packets/P0N-result.md`

## Fallback

_If this isn't working by <clock>, we ship this instead._
