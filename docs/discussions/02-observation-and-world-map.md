# What the explorer returns, and how the world map absorbs it

> **External input, not a decision.** This is a captured ChatGPT conversation,
> kept verbatim for provenance. Nothing here is settled until it appears in
> `../product/decisions.md`. Treat the reasoning as an argument to weigh, and
> the citations as leads to check rather than established facts.

| | |
|---|---|
| Source | [Model explorer observations](https://chatgpt.com/share/6a9a90ed-9c94-83e8-9a12-2dda421f2780) |
| Held | 2026-09-04, ~1 PM IST |
| Captured | 2026-09-04, rendered with Playwright (the page is JS-only; a plain fetch returns an empty shell) |

## What it covers

The data contract between the explorer and the persistent application model.

- The explorer returns an **ObservationChunk** (bounded, evidence-backed), never
  "a state". The world map is the belief built from many chunks. Framed as a POMDP.
- **`Finding`** triples (subject / predicate / value / evidence) as the primitive,
  rather than having an LLM emit a whole world map each turn.
- **Behavioral state abstraction** as the missing layer, with the rule that
  carries it: *a variable belongs in state identity only when changing it can
  change observable actions, transitions, constraints or outcomes.*
- **Contradictions as first-class and unresolved** — new evidence that conflicts
  with a belief produces a record for a verifier, not an overwrite.
- State template vs. state instance, delta-based observation, and an explicit
  list of what does **not** belong in the world map.

---

from what we discuss what does the explorer agent return which is an observed state... using these multiple chunks of observation this agent will either append or update the application world map so i would like you to research and think about it and how we will model the data for input and output

The key distinction I would make is:

The Explorer should not return an “application state.” It should return an ObservationChunk: a bounded, evidence-backed description of what it observed and what it can reasonably infer from that observation.

The World Map is the persistent model built from many such chunks.

That distinction matters because the explorer only sees a partial projection of the application. This is essentially a POMDP problem: the underlying application state is not directly observable, and an agent maintains a belief about it from observations and actions.
pomdp.org

Existing browser environments expose roughly the raw material we need: URL, tabs, screenshot, DOM, accessibility tree, element properties, focused element, previous action and errors.
GitHub
 But our system needs another layer above this: semantic state extracted from those observations.

1. The architecture I think we want
                    BROWSER
                       │
                       ▼
              ┌─────────────────┐
              │ Explorer Agent  │
              │                 │
              │ observe         │
              │ act             │
              │ compare        │
              │ infer           │
              └────────┬────────┘
                       │
                       ▼
             ┌───────────────────┐
             │ ObservationChunk  │
             │                   │
             │ raw evidence      │
             │ semantic findings │
             │ possible state    │
             │ transitions      │
             │ uncertainty       │
             └─────────┬─────────┘
                       │
                       ▼
             ┌───────────────────┐
             │ World Map Updater │
             │                   │
             │ match             │
             │ merge             │
             │ create            │
             │ contradict       │
             └─────────┬─────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ Application     │
              │ World Map       │
              │                 │
              │ states          │
              │ entities        │
              │ actions         │
              │ transitions     │
              │ invariants      │
              │ uncertainty     │
              └─────────────────┘

The important thing is that the Explorer doesn't directly mutate the map.

It produces evidence.

The Map Updater decides what that evidence means relative to everything already known.

This gives you a clean separation:

Explorer = perception + local inference

World Map = persistent belief/model

Verifier = challenges belief/model

That is much closer to the research direction around web agents with world models. One recent approach explicitly uses a transition-focused abstraction containing only important differences between observations rather than trying to model the entire HTML observation.
arXiv

2. What exactly is an ObservationChunk?

I would model it at roughly four levels.

ObservationChunk
│
├── context
│   ├── URL
│   ├── route
│   ├── viewport
│   ├── timestamp
│   └── session
│
├── evidence
│   ├── DOM
│   ├── screenshot
│   ├── accessibility tree
│   └── interaction result
│
├── observations
│   ├── UI elements
│   ├── visible entities
│   ├── values
│   ├── states
│   └── relationships
│
└── inferences
    ├── semantic state
    ├── available actions
    ├── transition
    ├── constraints
    └── confidence

I would not put all of the raw DOM into the semantic object.

The raw DOM is evidence.

The semantic representation is what the rest of the system consumes.

3. Concrete schema

Something like:

JSON
{
  "observation_id": "obs_0182",
  "session_id": "explore_01",
  "timestamp": "...",

  "context": {
    "url": "https://app.example.com/checkout",
    "route": "/checkout",
    "title": "Checkout",
    "viewport": {
      "width": 1440,
      "height": 900,
      "scroll_y": 420
    }
  },

  "trigger": {
    "action_id": "act_0181",
    "action": {
      "type": "click",
      "target": "continue"
    }
  },

  "evidence": {
    "screenshot_ref": "s3://...",
    "dom_snapshot_ref": "...",
    "accessibility_tree_ref": "...",
    "visible_element_ids": [
      "el_email",
      "el_address",
      "el_continue"
    ]
  },

  "ui": {
    "elements": [
      {
        "id": "el_email",
        "role": "textbox",
        "name": "Email",
        "value": "alice@example.com",
        "visible": true,
        "enabled": true,
        "required": true
      },
      {
        "id": "el_continue",
        "role": "button",
        "name": "Continue",
        "visible": true,
        "enabled": true
      }
    ]
  },

  "semantic_observations": [
    {
      "subject": "checkout_form",
      "predicate": "email_required",
      "value": true,
      "confidence": 0.98,
      "evidence": ["el_email"]
    }
  ],

  "state_hypothesis": {
    "state_type": "checkout",
    "identity": {
      "route": "/checkout",
      "semantic_key": "checkout"
    },
    "properties": {
      "email": "filled",
      "shipping_address": "unknown",
      "payment": "unknown"
    },
    "confidence": 0.91
  },

  "available_actions": [
    {
      "type": "fill",
      "target": "email"
    },
    {
      "type": "fill",
      "target": "shipping_address"
    },
    {
      "type": "click",
      "target": "continue"
    }
  ],

  "transition": {
    "from_observation": "obs_0181",
    "action": "click(continue)",
    "result": "checkout_remained",
    "changes": [
      {
        "property": "error.email",
        "from": null,
        "to": "invalid"
      }
    ]
  }
}

But there is an important refinement.

Don't make state_hypothesis a single state

The Explorer should be allowed to say:

JSON
{
  "state_hypothesis": {
    "candidates": [
      {
        "state": "checkout",
        "confidence": 0.91
      },
      {
        "state": "checkout_validation_error",
        "confidence": 0.76
      }
    ]
  }
}

because sometimes the observation isn't enough to distinguish states.

That is directly aligned with the belief-state formulation of partially observable environments: the agent maintains uncertainty rather than pretending that its inferred state is ground truth.
pomdp.org

For the hackathon, though, you don't need a mathematically precise probability distribution over every possible application state.

A practical approximation is:

confidence: 0.0 → 1.0
evidence: [...]
alternatives: [...]
4. The most important object: the Observation Finding

I think this is actually the primitive your system should revolve around.

Instead of:

"The current page is checkout."

produce:

JSON
{
  "finding_id": "f_123",
  "subject": "checkout",
  "type": "state_property",

  "predicate": "continue_button_enabled",
  "value": true,

  "confidence": 0.99,

  "evidence": {
    "observation_id": "obs_182",
    "element_ids": ["el_continue"]
  }
}

Or:

JSON
{
  "subject": "checkout",
  "type": "transition",

  "predicate": "submit_without_email",
  "result": "validation_error"
}

Or:

JSON
{
  "subject": "cart",
  "type": "relationship",

  "predicate": "contains",
  "object": "product_123"
}

Or:

JSON
{
  "subject": "user_session",
  "type": "state",

  "predicate": "authenticated",
  "value": true
}

This gives you a very powerful abstraction:

Observation
      ↓
Findings
      ↓
World Map

rather than:

Observation
      ↓
LLM guesses entire application state
      ↓
overwrite world map

The latter will become extremely brittle.

5. Then what is the World Map?

I would model the application as a typed graph.

Something like:

                         ┌─────────────┐
                         │ Login Page  │
                         └──────┬──────┘
                                │
                         submit credentials
                                │
                                ▼
                       ┌────────────────┐
                       │ Dashboard      │
                       └───────┬────────┘
                               │
                         click Projects
                               │
                               ▼
                       ┌────────────────┐
                       │ Project List   │
                       └───────┬────────┘
                               │
                         click Project
                               │
                               ▼
                       ┌────────────────┐
                       │ Project Detail │
                       └───────┬────────┘
                               │
                         click Settings
                               │
                               ▼
                       ┌────────────────┐
                       │ Settings       │
                       └────────────────┘

But nodes shouldn't only be pages.

There are several useful node types:

State
Entity
UIComponent
Action
Constraint
Invariant

For example:

JSON
{
  "states": {
    "s_dashboard": {
      "type": "page",
      "route": "/dashboard",
      "identity": {
        "semantic_key": "dashboard"
      }
    },

    "s_project_detail": {
      "type": "page",
      "route": "/projects/:id",
      "identity": {
        "semantic_key": "project_detail"
      }
    }
  }
}

Then:

JSON
{
  "transitions": [
    {
      "from": "s_dashboard",
      "action": "open_project",
      "to": "s_project_detail",

      "conditions": [
        "project_exists"
      ],

      "confidence": 0.96,

      "evidence": [
        "obs_182",
        "obs_194"
      ]
    }
  ]
}
6. But state identity is the hard part

This is where I would spend engineering effort.

You don't want:

URL = /products/123

to automatically mean:

state = products_123

because:

/products/123
/products/123?edit=true
/products/123#reviews

may represent different behavioral states.

Conversely:

/products/123
/products/456

may be the same state template with a different entity.

So distinguish:

State Template
        +
State Instance

For example:

JSON
{
  "state_template": "product_detail",

  "instance": {
    "product_id": "123"
  }
}

That gives:

ProductDetail(product=123)
ProductDetail(product=456)

rather than creating two unrelated page nodes.

7. I would therefore define the World Map around 5 primitives
State

A behavioral/UI configuration.

JSON
{
  "id": "state_checkout",
  "kind": "page",

  "identity": {
    "route_pattern": "/checkout",
    "semantic_signature": [
      "checkout_form",
      "order_summary",
      "payment"
    ]
  },

  "variables": {
    "email": "unknown",
    "shipping_address": "unknown",
    "payment_method": "unknown"
  }
}
Entity

Something the application represents.

JSON
{
  "id": "entity_order",
  "kind": "order",

  "properties": {
    "status": "pending",
    "total": 1299
  }
}
Action

Something the user/agent can do.

JSON
{
  "id": "action_submit_order",
  "type": "click",
  "target": "submit_order"
}
Transition

The behavioral relationship.

JSON
{
  "from": "state_checkout",
  "action": "action_submit_order",
  "to": "state_order_confirmation",

  "conditions": [
    "payment_valid",
    "shipping_address_valid"
  ]
}
Finding / Evidence

Why we believe any of this.

JSON
{
  "claim": "submit_order_requires_payment",
  "confidence": 0.87,

  "evidence": [
    "obs_412",
    "obs_417"
  ]
}

That last one is crucial for self-healing and verification.

8. Now the append/update operation becomes much cleaner

Suppose exploration produces:

Observation #100

URL: /cart

Visible:
- Cart item
- Quantity = 2
- Remove
- Checkout

World Map currently has:

/cart
  └── contains CartItem

The updater doesn't say:

replace /cart

Instead:

1. identify candidate state
2. match entities
3. extract findings
4. compare findings against existing beliefs
5. merge compatible information
6. record contradictions
7. add newly discovered transitions

Conceptually:

Python
patch = reconcile(
    world_map,
    observation_chunk
)

Output:

JSON
{
  "operation": "update",

  "state": "cart",

  "changes": [
    {
      "type": "add_property",
      "path": "cart.items[].quantity",
      "value": 2
    },
    {
      "type": "add_action",
      "action": "checkout"
    }
  ],

  "contradictions": [],

  "confidence": 0.94
}
9. And sometimes the correct result is NOT update

This is important for your contradiction-handling idea.

Imagine the map says:

checkout.submit
    requires authentication = true

Explorer later discovers:

logged-out user
→ checkout
→ submit
→ successful order

The updater should not silently overwrite the old belief.

It should produce:

JSON
{
  "type": "contradiction",

  "claim": {
    "subject": "checkout.submit",
    "predicate": "requires_authentication",
    "value": true
  },

  "counter_evidence": {
    "value": false,
    "observation_id": "obs_823"
  },

  "resolution": "unresolved"
}

Now you have an explicit thing for the verifier to investigate.

That is much more interesting than ordinary RAG-style memory.

10. This gives us a natural Explorer → Map contract

I would make the Explorer's output roughly:

TypeScript
interface ObservationChunk {
  id: string;
  sessionId: string;
  timestamp: number;

  context: ObservationContext;

  trigger?: ActionReference;

  evidence: Evidence;

  ui: UIObservation;

  findings: Finding[];

  stateHypotheses: StateHypothesis[];

  availableActions: ActionObservation[];

  transition?: TransitionObservation;

  uncertainty: Uncertainty;
}

And the Map Updater returns:

TypeScript
interface WorldMapPatch {
  observationId: string;

  operations: WorldMapOperation[];

  newEntities: Entity[];

  newStates: State[];

  newActions: Action[];

  newTransitions: Transition[];

  contradictions: Contradiction[];

  unresolvedQuestions: Question[];

  confidence: number;
}

Where:

TypeScript
type WorldMapOperation =
  | AddState
  | UpdateState
  | AddEntity
  | UpdateEntity
  | AddAction
  | UpdateAction
  | AddTransition
  | UpdateTransition
  | AddEvidence
  | AddContradiction;

This is significantly safer than having the LLM return an entire serialized World Map every time.

11. There's another thing I would add: delta

The Explorer should know the previous observation.

So don't make it describe everything again.

Give it:

previous observation
        +
action
        +
new observation

and ask:

What changed?

For example:

JSON
{
  "action": {
    "type": "click",
    "target": "login"
  },

  "delta": [
    {
      "type": "navigation",
      "from": "/",
      "to": "/login"
    },
    {
      "type": "element_appeared",
      "element": "email"
    },
    {
      "type": "element_appeared",
      "element": "password"
    },
    {
      "type": "element_appeared",
      "element": "submit"
    }
  ]
}

This is strongly supported by the direction of recent web-agent world-model research: transition-focused observation abstractions specifically try to capture important state differences rather than repeatedly representing huge HTML observations.
arXiv

12. The Explorer therefore has two jobs

This distinction is useful:

Perception
What can I directly observe?

Example:

button "Submit"
disabled = true
Behavioral inference
What does this imply?

Example:

The form cannot currently be submitted.

But we should mark the second as inference:

JSON
{
  "observation": {
    "button.disabled": true
  },

  "inference": {
    "form.submittable": false,
    "confidence": 0.96
  }
}

This allows the verifier to challenge the inference later.

13. I would NOT put these things into the World Map

Don't make the World Map:

DOM tree
HTML
CSS
pixel coordinates
every visible text node
every screenshot
LLM reasoning
full observation history

Those belong in the evidence/trace store.

The World Map should contain the compressed behavioral model.

Think:

Evidence Store
     │
     │ lots of observations
     ▼
Observation Chunks
     │
     │ extracted claims
     ▼
World Map
     │
     │ compact behavioral model
     ▼
Verifier / Planner

This also solves the context-window problem.

BrowserGym itself demonstrates why raw observations become enormous: its environment can expose DOM, AXTree, screenshots, element properties, URLs, actions and errors.
GitHub

You don't want your planner carrying all of that.

14. The resulting mental model

I think the cleanest conceptual model for AIVAR is:

                  REAL APPLICATION
                         │
                         │ partial observation
                         ▼
                 ┌───────────────┐
                 │    Explorer   │
                 └───────┬───────┘
                         │
                         ▼
               ┌──────────────────┐
               │ ObservationChunk │
               │                  │
               │ evidence         │
               │ findings         │
               │ delta            │
               │ hypotheses       │
               │ uncertainty      │
               └────────┬─────────┘
                        │
                        ▼
               ┌──────────────────┐
               │  Map Reconciler  │
               └────────┬─────────┘
                        │
             ┌──────────┼──────────┐
             ▼          ▼          ▼
           ADD        UPDATE   CONTRADICTION
             │          │          │
             └──────────┼──────────┘
                        ▼
               ┌──────────────────┐
               │    WORLD MAP     │
               │                  │
               │ States           │
               │ Entities         │
               │ Actions          │
               │ Transitions      │
               │ Constraints      │
               │ Beliefs          │
               │ Evidence refs    │
               └────────┬─────────┘
                        │
                        ▼
                    VERIFIER
                        │
                        ▼
                New experiments
                        │
                        └──────────► Explorer

And this is where I think your earlier active experiment selection idea becomes important.

The World Map should not just answer:

"What do we know?"

It should also expose:

"What do we currently believe but haven't verified?"

For example:

JSON
{
  "question": "Does checkout require authentication?",

  "current_belief": {
    "value": true,
    "confidence": 0.61
  },

  "best_experiment": {
    "action_sequence": [
      "logout",
      "navigate:/checkout",
      "click:submit"
    ]
  }
}

Now the loop becomes:

Explore
  ↓
Observe
  ↓
Extract findings
  ↓
Update belief map
  ↓
Find uncertainty / contradictions
  ↓
Select highest-value experiment
  ↓
Explore again

That is a much stronger architecture than "LLM crawls website and stores pages."

The research supports the broader framing: BrowserGym/WebArena treat browser interaction as a partially observable sequential environment, while recent world-model work explicitly focuses on predicting or abstracting state transitions to improve web-agent decision making.
WebArena
arXiv

My recommendation for the hackathon implementation: don't build the full probabilistic world model yet. Build these four concrete objects first:

ObservationChunk
Finding
WorldMap
WorldMapPatch

Everything else—beliefs, contradiction resolution, active exploration, verifier—can operate on those primitives.

The next useful step is to turn this into the actual Pydantic/TypeScript schemas + example JSON for a 5–10 step exploration, because that will expose whether our abstraction is genuinely sufficient or whether we're missing a critical concept such as session state, entity identity, permissions, or temporal state.

does above answer solves for these this Every observation becomes part of an evolving application model.

Something conceptually like:

                    APPLICATION
                         │
                    ┌────┴────┐
                    │         │
                 STATE       STATE
                    │
              available actions
                    │
          ┌─────────┼─────────┐
          ↓         ↓         ↓
        click     input     submit
          │
          ▼
      new state

Behavioral model of an unknown application.

State abstraction

You cannot have:

S1 = dashboard with 1 project
S2 = dashboard with 2 projects
S3 = dashboard with 3 projects
S4 = dashboard with 4 projects
...

That becomes useless state explosion.

You need to discover what differences actually matter behaviorally.

For example:

Dashboard
  logged-in: admin
  projects: N

rather than storing every concrete database value as a different state.

But:

Dashboard
  role = admin

and

Dashboard
  role = normal_user

may need to be separate behavioral states because available actions differ.

Similarly:

Create Project
  form = empty

Create Project
  form = invalid

Create Project
  form = valid

may matter because the available transitions differ.

So Level 2 asks:

What information defines a meaningful behavioral state?

Behavioral model → test hypotheses

Only now do we introduce testing.

The model contains:

states
actions
transitions
observations
uncertainties

From this, the system asks:

What behaviors should become tests?

For example:

Create Project
 ├─ valid input → project created
 └─ invalid input → validation error

naturally suggests:

TEST:
valid project creation

TEST:
invalid project creation

And:

create
→ project exists
→ refresh
→ project exists

suggests a persistence test.

This is important:

Tests emerge from the behavioral model.

You're no longer asking an LLM:

“Write me 20 Playwright tests.”

You're asking:

“Given what we know about this application's behavior, which behavioral claims should we verify?”

Yes, but only partially. The previous answer gives you the right data architecture, but it does not yet fully solve the specific problem you are describing.

The missing piece is behavioral state abstraction: deciding when two observations belong to the same state, and when a difference creates a genuinely different behavioral state.

Your overall pipeline should be:

                    APPLICATION
                         │
                    ┌────┴────┐
                    │         │
                  STATE      STATE
                    │
             available actions
                    │
          ┌─────────┼─────────┐
          ↓         ↓         ↓
        click      input    submit
          │
          ▼
       new STATE
          │
          ▼
    ObservationChunk
          │
          ▼
   State Abstraction
          │
          ▼
    World Map Update
          │
          ▼
 Behavioral Model
          │
          ▼
  Behavioral Hypotheses
          │
          ▼
        TESTS

The important correction is that ObservationChunk ≠ State.

An observation is evidence from one concrete execution. A state is an abstraction inferred from potentially many observations.

1. The hierarchy should be this

I would model four layers:

Concrete Observation
        ↓
Observation / Findings
        ↓
Behavioral State
        ↓
Application World Model

For example, the Explorer sees:

URL: /dashboard
User: admin
Projects visible: 17
Buttons:
  Create Project
  Delete Project

That's an observation.

It should NOT create:

State #18372
dashboard + 17 projects

Instead, it produces findings:

JSON
[
  {
    "subject": "dashboard",
    "predicate": "route",
    "value": "/dashboard"
  },
  {
    "subject": "session",
    "predicate": "role",
    "value": "admin"
  },
  {
    "subject": "dashboard",
    "predicate": "project_count",
    "value": 17
  },
  {
    "subject": "dashboard",
    "predicate": "available_action",
    "value": "create_project"
  },
  {
    "subject": "dashboard",
    "predicate": "available_action",
    "value": "delete_project"
  }
]

The State Abstraction layer then says:

dashboard
role = admin
project_count = variable

Therefore:

State:
Dashboard[role=admin]

not:

Dashboard[projects=17]

That is the critical missing layer from my previous answer.

2. State should contain behavioral variables, not arbitrary variables

This is the core design problem.

Suppose Explorer encounters:

Dashboard
projects = 17

and later:

Dashboard
projects = 18

The system asks:

Did the application's behavior change?

If:

available actions = same
transitions = same
constraints = same

then:

Dashboard(projects=17)
≈
Dashboard(projects=18)

and both map to:

Dashboard
  role = admin
  projects = N

But suppose:

projects = 0

causes:

"Create your first project"

while:

projects > 0

causes:

"Manage projects"

Now project_count matters at the boundary.

So the model might become:

Dashboard
 ├── empty
 │    projects = 0
 │
 └── populated
      projects > 0

Not:

17
18
19
20
...

This is essentially discovering a behaviorally relevant partition of the application's state space.

3. Your role example works exactly this way

You gave:

Dashboard
  role = admin

versus:

Dashboard
  role = normal_user

The reason these become different states is not because role is interesting data.

It's because:

admin
  ↓
available actions
  ├── delete project
  ├── manage users
  └── settings

normal_user
  ↓
available actions
  ├── view project
  └── edit project

Therefore:

Dashboard[admin]

and

Dashboard[user]

have different transition structures.

That's the rule I would use:

A variable belongs in state identity when changing that variable can change the application's observable behavior.

This is much stronger than simply asking the LLM what fields "look important."

4. Forms make this even clearer

Take:

Create Project

Initially:

name = ""

Then:

name = "My Project"

Then:

name = ""
error = "Name required"

Then:

name = "My Project"
valid = true

We don't want four states merely because the input values differ.

We want:

CreateProject
       │
       ├── EMPTY
       │
       ├── INVALID
       │
       └── VALID

because these affect transitions:

EMPTY
 └── submit → validation error

INVALID
 └── submit → validation error

VALID
 └── submit → project created

Now the state abstraction is doing real work.

5. Therefore the Explorer should NOT decide the final state

This is another refinement I'd make to the previous answer.

Explorer:

"I observed these facts."

State abstraction:

"These facts correspond to this behavioral state."

World Map:

"This state is connected to these other states through these actions."

So:

Explorer
   │
   │ ObservationChunk
   ▼
State Abstraction
   │
   │ StateCandidate
   ▼
World Map Reconciler
   │
   ▼
World Model

This lets the same observation contribute to multiple things.

6. I'd change the ObservationChunk schema accordingly

Instead of making state_hypothesis too prominent, I'd make it:

TypeScript
interface ObservationChunk {
  id: string;

  context: {
    url: string;
    route: string;
    session: string;
  };

  previousObservation?: string;

  action?: ActionObservation;

  evidence: Evidence[];

  findings: Finding[];

  behavioralChanges: BehavioralChange[];

  candidateState?: StateCandidate;
}

And:

TypeScript
interface StateCandidate {
  template: string;

  variables: {
    name: string;
    value: unknown;
    behavioralRelevance: number;
  }[];

  availableActions: Action[];

  confidence: number;
}

The interesting field is:

behavioralRelevance

because now you can eventually learn:

project_count → low relevance
role → high relevance
form_validity → high relevance
csrf_token → irrelevant
timestamp → irrelevant
7. The World Map should store abstractions, not observations

This distinction is fundamental.

Your evidence store might have:

Observation 001
Observation 002
Observation 003
...
Observation 937

The World Map might only have:

States:

Dashboard[admin]
Dashboard[user]

CreateProject[empty]
CreateProject[invalid]
CreateProject[valid]

ProjectDetail[owner]
ProjectDetail[non_owner]

Transitions:

Dashboard[admin]
   └─ create_project
       → CreateProject[empty]

CreateProject[valid]
   └─ submit
       → ProjectDetail[owner]

The 937 observations support those beliefs.

They aren't the model itself.

8. This gives you the append/update behavior you originally asked about

Suppose we already have:

Dashboard[user]

with:

actions:
  view_project
  edit_project

Explorer discovers another observation:

Dashboard[user]
projects = 42
actions:
  view_project
  edit_project

Updater:

MATCH
Dashboard[user]

NO NEW STATE

ADD EVIDENCE
obs_103

UPDATE:
projects = N

NO behavioral transition change

But later Explorer discovers:

Dashboard[user]
projects = 0

actions:
  create_first_project

Now the reconciler notices:

Same route
Same role
Different available actions

and creates:

Dashboard[user, empty]

while retaining:

Dashboard[user, populated]

That is exactly the state explosion problem you're trying to solve.

9. Then your "tests emerge from model" idea becomes very clean

Once you have:

State
Action
Transition
Constraint
Evidence
Uncertainty

you can derive behavioral hypotheses.

For example:

CreateProject[valid]
       │
     submit
       ↓
ProjectDetail

produces:

Hypothesis:
Valid project submission creates a project.

And:

CreateProject[invalid]
       │
     submit
       ↓
CreateProject[invalid]
       │
   validation error

produces:

Hypothesis:
Invalid project submission is rejected with validation feedback.

And:

CreateProject[valid]
       │
     submit
       ↓
ProjectDetail
       │
    refresh
       ↓
ProjectDetail

produces:

Hypothesis:
Created project persists across page reload.

So tests aren't generated from pages.

They're generated from edges and invariants in the behavioral graph.

That's a much better formulation.

10. I'd make one more conceptual distinction: state vs state variables

Your World Map could therefore look like:

StateTemplate
│
├── identity
│
├── behavioral_variables
│
├── available_actions
│
├── transitions
│
└── evidence

For example:

JSON
{
  "id": "state_create_project",

  "variables": {
    "form_status": {
      "domain": [
        "empty",
        "invalid",
        "valid"
      ],
      "behaviorally_relevant": true
    }
  },

  "actions": [
    "fill_name",
    "submit"
  ]
}

Then:

StateInstance
    ↓
CreateProject
    form_status = valid

The template defines what dimensions matter.

The instance gives their current values.

This is how you avoid:

S1
S2
S3
S4
...

while still representing meaningful differences.

11. The full AIVAR loop becomes
             ┌──────────────────────┐
             │    REAL APPLICATION  │
             └──────────┬───────────┘
                        │
                     observe
                        │
                        ▼
             ┌──────────────────────┐
             │      EXPLORER        │
             └──────────┬───────────┘
                        │
                 ObservationChunk
                        │
                        ▼
             ┌──────────────────────┐
             │ STATE ABSTRACTOR     │
             │                      │
             │ What changed?        │
             │ What matters?        │
             │ Same state or new?   │
             └──────────┬───────────┘
                        │
                  StateCandidate
                        │
                        ▼
             ┌──────────────────────┐
             │ WORLD MAP RECONCILER │
             │                      │
             │ append               │
             │ update               │
             │ merge                │
             │ split                │
             │ contradiction        │
             └──────────┬───────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │    BEHAVIOR MODEL    │
             │                      │
             │ states               │
             │ actions              │
             │ transitions          │
             │ constraints           │
             │ uncertainties        │
             └──────────┬───────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │ TEST HYPOTHESIS GEN. │
             └──────────┬───────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │      VERIFIER        │
             └──────────┬───────────┘
                        │
                   new experiment
                        │
                        └──────────────► EXPLORER

So, to answer your question directly:

The previous answer solved the observation → World Map data plumbing, but it did not fully solve the abstraction problem you highlighted.

The architecture needs one explicit concept between them:

Behavioral State Abstraction / State Reconciliation

And I think the most important design principle for it is:

A difference becomes part of state identity only when that difference changes observable actions, transitions, constraints, or outcomes.

That principle gives you a concrete way to attack state explosion rather than asking an LLM to subjectively decide which values matter.

The next thing I'd design is the State Abstraction algorithm itself: given ObservationChunk A, ObservationChunk B, and the existing World Map, exactly how does it decide same state / update state / split state / create state? That is probably the core intellectual piece of this architecture.
