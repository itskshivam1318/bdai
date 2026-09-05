"""Bounded exploration: walk an app and build a behavioural model of it.

Six modules build the map, and none of them calls a model:

    observer.py   live page   -> Observation (a11y tree + network + elements)
    statekey.py   Observation -> a stable identity for "which state is this?"
    noise.py      what is in the page but is not the application
    forms.py      what an explorer can DO to a page, and what to type doing it
    worldmap.py   Observations + actions -> states, transitions, gaps
    crawler.py    an entry URL -> a WorldMap. The loop that drives the rest.

and two sit at the edges, each crossing exactly one boundary:

    synth.py      the model seam. Invalid input, so error states are reachable.
    store.py      the database seam. WorldMap <-> SQLModel tables.

Two more stand outside the loop entirely, and neither is imported by it:

    snapshot.py   a WorldMap to a file and back, and the diff of two of them
    probe.py      the observable check for observer + statekey

`worldmap.py` is the artifact the rest of the pipeline reads and writes: the
Planner's test plan is its transitions, the Generator compiles paths out of it,
and the Healer classifies a failure by re-observing into it. The brief names a
pipeline; this is the shared model that pipeline operates on.

**Why the loop calls no model.** The measured failure mode of agentic
exploration is looping -- 44.4% of WebVoyager's failures are "navigation stuck".
The architecture that wins in the literature (Temac, AutoDroid) is a cheap
deterministic crawler that builds the graph, with the expensive model invoked
only at the edges. So the spine is code, and the same app produces the same
graph twice; any variation the judges see is attributable to a seam.

**But "deterministic everywhere" is the wrong rule, and was tried.** The test is
what happens when a component is wrong. A model choosing the *next action* can
loop, and a loop is unrecoverable -- keep that deterministic. A model choosing
what to *type* cannot corrupt anything: bad input gets rejected, the rejection
is observed, and that rejection is a state we wanted. Self-correcting and
observable, so `synth.py` is a model call and should be.

The seams still unbuilt, in the order they are worth building: ranking
`gaps()`, naming states for humans, classifying a `nondeterministic()` edge, and
choosing where to explore when the frontier stalls.

**Where the decisions live.** State identity is decided once, cheaply, at
observation time (`state_key` -- match or create, no confidence score). Whether
that decision was *right* is decided later, over the accumulated graph:
`WorldMap.nondeterministic()` reports every state whose projection collapsed two
behaviours, and the two observations behind it name the variable that should
have been identity. Measuring which differences matter beats asserting it, and
costs nothing.

Prior art and the numbers behind these choices: `../../../docs/research/
exploration-landscape.md`.
"""

# `crawler` is deliberately absent. It is an entry point (`python -m
# agents.explorer.crawler`), and importing it here makes the package import it
# before runpy executes it, which Python warns about. Import it by module.
from .forms import Credentials
from .observer import Element, NetworkEvent, Observation, Observer
from .statekey import normalize, state_key
from .worldmap import StateNode, Transition, WorldMap

__all__ = [
    "Credentials",
    "Element",
    "NetworkEvent",
    "Observation",
    "Observer",
    "StateNode",
    "Transition",
    "WorldMap",
    "normalize",
    "state_key",
]
