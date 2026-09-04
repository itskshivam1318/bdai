# Research framing, and the level ladder

> **External input, not a decision.** This is a captured ChatGPT conversation,
> kept verbatim for provenance. Nothing here is settled until it appears in
> `../product/decisions.md`. Treat the reasoning as an argument to weigh, and
> the citations as leads to check rather than established facts.

| | |
|---|---|
| Source | [Hackathon Operating System](https://chatgpt.com/share/6a9a91d2-4320-83ee-be03-abf8b0793459) |
| Held | 2026-09-04, ~11:28 AM - 12:46 PM IST |
| Captured | 2026-09-04, rendered with Playwright (the page is JS-only; a plain fetch returns an empty shell) |

## What it covers

Reframes the brief as *black-box behavioral system identification* rather
than AI test generation, and surveys the fields that already attack pieces of
it: active automata learning, GUI ripping (UMD, 2003), SLAM / cognitive maps,
Bayesian experimental design, stateful fuzzing, metamorphic testing, runtime
verification, agentic scientific discovery.

Then two concrete things:

- **A ladder, Level 0 to Level 8** — autonomous browser loop, behavioral model,
  state abstraction, intelligent exploration, model-to-tests, tests-as-experiments,
  failure diagnosis, semantic healing, full orchestration. Each level carries an
  estimated share of the challenge and an explicit no-regression rule.
- **Twelve blocks, B1 to B12**, with TypeScript contracts, per-block acceptance
  tests, four ground-truth fixture apps, and a phase order for handing work to
  Claude one block at a time.

---

Uploaded a file

the problem statement for the hackathon just released and here is the 0:03
I've been trying to write a test case for 3 days for a feature that I built in
0:08
one day. I wish there was an AI that could do this for me. Hey, have you
0:13
tried about play? No, you should definitely try that man because it has
0:17
inbuilt hidden like tanner generator and he so it should reduce your work.
0:22
>> Hey, I've been using the playright agents but still I am the one giving
0:26
them context again and again. It is a lot of manual work. I wish there is an
0:30
AI that can do this for us.
0:32
>> Why don't we just hire someone for that?
0:34
>> And how do we do that?
0:36
>> Bessimmer tech catalyst.
0:38
>> So we are I innovations
0:41
>> and we are native services company
0:43
>> and an AWS preferred partner. So here is your problem statement. So we will be
0:49
giving you an app URL, username and password and your agent should come up
0:53
with an working end to end test suit and it must be able to explore your app,
0:59
write your test cases, run your test cases and heal your test cases.
1:03
>> Show us that and we will hire you.

and also attaching the pdf

Uploaded a file
i want to talk about just the problem now... nothing else... how i will build what i will build are later discussion i urge you to not jump to think half ass
 giving playwright context again and again, a lot of manual work,

will be given one app url, username, password and the agent should be able to come up with working end to end test suite. And it must be able to explore the app…

write test cases… run test cases and heal ur test cases…

if the agent can explore a web site create a sitemap and also what each button or function does based on the input… have context of each and every possible action on the website… then save the state of the application and test the state on values changes

this can be about building a coordination layer that uses playwright or browser use…

or since agent coordination should be dynamic we build a tool like playwright which helps the agent… it doesn’t have to make sense for the human… playwright was developed for user use and then adopted to the ai… can we think in the direction where instead of focusing on agent we focus on the tools used by agent a smart tool….

the challenge says this

Build an autonomous test orchestration agent that takes a web application URL as input and drives the full testing lifecycle — planning, test generation, execution, and repair — without human intervention between stages. The agent should coordinate a pipeline of three specialised sub-agents: a Planner that explores the application and produces a structured test plan, a Generator that converts the plan into executable test code with live selector validation, and a Healer that replays failing tests and repairs broken locators or flows. The meta-agent must coordinate this pipeline intelligently — evaluating coverage quality between stages, deciding when to re-plan or escalate, and synthesising all outputs into a final test quality report. Success is a system where a developer provides a URL and receives a working, meaningful test suite with no manual scripting in between

Yes. I did a deeper pass across current web-agent research, black-box testing, active learning, robotics, fuzzing, runtime verification, and autonomous scientific discovery.

The strongest conclusion is that I would not frame your problem primarily as “AI test generation.”

I would frame it as:

An agent is trying to understand and interrogate an unknown dynamical system when its only access to that system is through observations and actions.

That framing opens up much better research.

The challenge itself almost says this indirectly: it says the hard part is deciding what to test, whether the right things were tested, and whether a failure is a real defect or a broken script. problem_explanation_9dm9yp4f98s

1. The closest research area is actually active model learning

There is a surprisingly old field called Active Automata Learning.

The setup is almost eerily similar to your problem:

You have a black-box system. You don't know its internal state machine. You interact with it by sending inputs and observing outputs. Your objective is to infer a useful model of its behavior.

The system progressively constructs a model and uses counterexamples to discover that its current model is wrong.

Recent work such as State Matching and Multiple References in Adaptive Active Automata Learning explicitly describes active automata learning as inferring state machines by interacting with black-box systems.
arXiv

And this is particularly interesting:

You don't need to discover every possible state. You need to discover enough of the system's structure to construct a useful model.

That is exactly the problem you're describing.

For a web application:

                UNKNOWN APP

                    ↓
              observe state

        ┌────────────┴────────────┐
        │                         │
     possible                 possible
     actions                  states
        │                         │
        └────────────┬────────────┘
                     ↓
                  act
                     ↓
              observe result
                     ↓
             update model
                     ↓
          choose next experiment

This is much more interesting than:

LLM → click buttons → generate tests
The catch: state explosion

There is old research specifically on this problem for web applications.

Web applications can be represented as finite-state machines, but input fields, values, ordering of interactions, and combinations of states cause enormous state-space explosion. Researchers therefore explored hierarchical and compressed state models.
ScienceDirect

This maps directly to your thought:

“context of each and every possible action on the website”

The research says: you cannot literally model everything.

You need abstraction.

That is a fundamental research question for your problem:

What constitutes the same application state for testing purposes?

For example:

Dashboard
  user_count = 10
  user_count = 11

Are these different states?

Probably not for navigation.

But:

Dashboard
  logged_in = admin

Dashboard
  logged_in = normal_user

probably are.

And:

Create form
  empty

Create form
  invalid email

Create form
  valid email

may represent meaningfully different behavioral states.

That is state abstraction.

And I think this is one of the most important concepts for your problem.

2. GUI Ripping is almost exactly your “explore the application” idea

This was one of the most interesting things I found.

In 2003, researchers at the University of Maryland developed GUI Ripping.

The system dynamically traversed an executable GUI, opened windows, interacted with widgets, extracted their properties and values, and reconstructed a model of the GUI and its execution behavior. The model contained GUI structure, event-flow graphs and an integration tree.
UMD Computer Science

GUI Ripping: Reverse Engineering of Graphical User Interfaces for Testing

The important distinction is:

DOM scraper

vs.

behavioral reverse engineering

The latter is much closer to what you're imagining.

The researchers represented:

GUI structure
      +
available events
      +
event relationships
      +
component relationships
      ↓
behavioral model

An event-flow graph represented which events could follow which other events.
UMD Computer Science

That gives you a very interesting interpretation of your proposed sitemap.

Instead of:

/login
/dashboard
/projects
/settings

you could conceptually have:

                 LOGIN
                   │
                   ▼
               DASHBOARD
              /         \
        Projects        Settings
           │
      Create Project
        /       \
    valid       invalid
      │            │
      ▼            ▼
  Project       validation
  Created         error
      │
   ┌──┴───────┐
   │          │
  edit      delete

Now you have something from which test cases can emerge naturally.

The really important historical caveat is that GUI Ripping still required humans to correct incomplete/incorrect extracted models.
UMD Computer Science

That is exactly where your problem becomes harder than traditional GUI ripping:

Can an LLM-driven system maintain and correct its own model rather than handing the model to a human?

3. Robotics gives you another very strong analogy: SLAM

Think about a robot entering an unknown building.

It doesn't know:

where rooms are
where doors are
what is behind them

It gets:

observations → actions → new observations

and progressively builds a representation of the environment.

This is the intuition behind mapping and exploration.

The interesting concept isn't necessarily the particular SLAM algorithm. It's the separation between:

WORLD
  ↓
OBSERVATION
  ↓
INTERNAL MODEL
  ↓
ACTION
  ↓
NEW OBSERVATION
  ↓
MODEL UPDATE

Your web application is a strange digital version of this.

APPLICATION
     ↓
browser observation
     ↓
APPLICATION MODEL
     ↓
choose action
     ↓
browser interaction
     ↓
new state
     ↓
update model

There is also newer research explicitly connecting navigation, memory and dynamically expanding cognitive maps. One 2024 paper describes an agent dynamically expanding its map when it encounters novel/unvisited regions and updating the map when new evidence contradicts previous beliefs.
arXiv

Learning Dynamic Cognitive Map with Autonomous Navigation

That phrase is particularly relevant:

update the map when new evidence contradicts previous beliefs

Imagine your testing agent believes:

Create User → User Detail

but after exploration discovers:

Create User
   ↓
Email verification required
   ↓
Pending Users
   ↓
User Detail

The model should not merely append another observation.

It should revise its understanding of the application.

That is much closer to a cognitive map / world model than a test-plan document.

4. Active learning gives you the missing concept: “What should I explore next?”

This may be the most important inspiration outside software engineering.

Suppose your agent has discovered:

80% understood
20% uncertain

There are potentially 50 things it could click next.

Why choose one?

A naïve crawler says:

click something not visited.

An intelligent explorer asks:

Which action will give me the most useful information?

That is the territory of active learning / experimental design.

In Bayesian experimental design, one objective is to select the next experiment based on expected information gain. The goal is to reduce uncertainty efficiently rather than blindly collect observations.
arXiv

And autonomous scientific systems are now actually implementing this basic loop.

SAMPLE, for example, starts with exploration to understand a broad landscape and then continually chooses experiments to acquire information and refine its understanding. It reports reaching good solutions while searching less than 2% of the full protein landscape.
Nature

This is a very strong analogy:

Scientific discovery:

observe
   ↓
hypothesis
   ↓
choose experiment
   ↓
observe result
   ↓
update hypothesis
   ↓
choose next experiment

Your testing problem:

explore app
   ↓
behavioral hypothesis
   ↓
choose interaction
   ↓
observe result
   ↓
update app model
   ↓
choose next interaction/test

The difference is that the scientist wants knowledge about nature.

Your agent wants knowledge about the application.

The underlying control problem is remarkably similar.

5. Scientific discovery may be the best inspiration for the meta-agent

There is now serious work on agentic science where systems continuously perform:

observation
→ hypothesis
→ experiment
→ analysis
→ revised hypothesis
→ next experiment

A 2026 Nature paper introduced Robin, a multi-agent scientific-discovery system that generates hypotheses, proposes experiments, analyzes results and generates updated hypotheses in a continuous loop.
Nature

A multi-agent system for automating scientific discovery

The interesting part isn't the biology.

It's the architecture of knowledge revision.

A fixed pipeline looks like:

Planner → Generator → Executor → Healer

An experimental scientist looks more like:

Current belief
      ↓
What don't I know?
      ↓
What experiment resolves it?
      ↓
Experiment
      ↓
Result
      ↓
Did my belief survive?
   ↙       ↘
 yes        no
 ↓           ↓
continue    revise model

That gives you a much more rigorous way to think about dynamic orchestration.

The agent isn't deciding:

“Should I call the Planner again?”

It's deciding:

“What do I currently believe about this application, how uncertain am I, and what observation/action would reduce the uncertainty most?”

That is a substantially more interesting problem.

6. Fuzzing gives you another missing piece: exploration should be feedback-driven

Modern fuzzers don't simply generate random inputs.

They use feedback from previous executions to decide what inputs deserve more exploration.

The canonical intuition is:

input
 ↓
execute
 ↓
observe coverage / behavior
 ↓
keep interesting input
 ↓
mutate it
 ↓
execute again

For stateful systems this becomes considerably harder because the effect of an action depends on the state accumulated from previous actions. There is an entire research literature on stateful fuzzing and state-selection strategies.
arXiv

Fuzzers for Stateful Systems: Survey and Research Directions

One particularly relevant idea is that not all states are equally important. Stateful fuzzers therefore try to select states that are more promising for discovering new behavior.
IEEE Xplore

Now translate that:

Instead of:

test every button

you could think:

Current application model

       ↓

Which unexplored state is
most likely to reveal
new behavior?

       ↓

Navigate there

       ↓

Observe

       ↓

Update model

This gives you a possible definition of behavioral coverage.

Not:

How many URLs did I visit?

But:

How much of the application's behavioral space have I exercised?

7. Metamorphic testing attacks your hardest problem: “How do I know it's correct?”

This is the deepest problem in your entire idea.

Suppose the agent discovers:

Create account

It performs:

name = Shivam
email = x@example.com
→ Account created

How does it know that's correct?

There may be no specification.

This is called the test oracle problem.

The classic survey defines it as the problem of determining whether observed behavior is the desired/correct behavior.
IEEE Xplore

The Oracle Problem in Software Testing: A Survey

LLMs don't magically eliminate this problem.

A 2024 paper specifically discusses test-oracle automation with LLMs and points out that generated oracles themselves introduce risks of oracle deficiencies.
arXiv

Test Oracle Automation in the era of LLMs

And this is where metamorphic testing becomes very interesting.

Instead of knowing:

What is the exact correct output?

you know a relationship that should hold.

For example:

Create user
→ user exists

Create user
→ refresh
→ user should still exist

or:

Sort ascending
→ A B C

Sort descending
→ C B A

or:

Add item
→ quantity = 1

Add same item again
→ quantity should increase

You don't necessarily know every correct output.

You know relationships between executions.

Metamorphic testing was specifically developed to address situations where expected outputs are difficult to determine.
IEEE Xplore

This could be extremely powerful for your problem.

Because your agent has only a black box, it may be able to construct stronger tests by asking:

“What should remain invariant when I change this input?”

rather than:

“What exact output should I expect?”

8. Runtime verification gives you another way to think about correctness

Another useful field is runtime verification.

Instead of trying to prove an entire system correct, you monitor executions and check whether observed behavior satisfies specified properties.

Recent work explicitly discusses runtime verification for partially observable and black-box systems, where uncertainty has to be resolved through observation.
arXiv

For your application:

Observed trace:

login
→ dashboard
→ create project
→ save
→ project appears
→ refresh
→ project still appears

You can derive properties such as:

P1:
If create succeeds,
the created entity should subsequently be observable.

P2:
If an action claims persistence,
refresh should not erase the result.

P3:
If validation rejects input,
the invalid entity should not appear as created.

P4:
Delete success
→ entity should no longer be accessible.

These are not individual test cases.

They are behavioral properties.

That distinction matters.

9. Current web-agent systems are converging on pieces of this, but not the whole thing

I looked at current systems rather than only papers.

Playwright

Current Playwright already has exactly:

Planner
Generator
Healer

The Planner explores the application and creates a plan; Generator validates selectors/assertions while generating tests; Healer replays failures and repairs tests.
Playwright

Playwright Test Agents

But notice an extremely important detail.

Its Planner currently expects:

a clear request;

a seed test;

optionally a PRD.

Playwright

That is materially different from your problem:

URL
username
password

GO.

The challenge explicitly requires URL as the sole required input and autonomous exploration. problem_explanation_9dm9yp4f98s

So your black-box formulation is stricter.

Browser agents

Current web-agent research largely focuses on task completion.

WebArena gives agents realistic websites and long-horizon tasks.
arXiv

WebVoyager focuses on end-to-end interaction with real websites using multimodal models.
arXiv

BrowserGym standardizes observation/action spaces and provides infrastructure for evaluating web agents.
arXiv

BrowserGym Ecosystem

But their fundamental question is:

Can I accomplish the requested task?

Your question is:

What is this system, what can it do, what should I test, and how do I know my understanding is correct?

That is a different objective.

Commercial testing systems

The market is already moving toward autonomous testing.

Momentic
 describes “Mo” as an AI QA agent that can be pointed at an application and return bugs, recordings and reproduction steps, with agents that author, repair and maintain tests.

Applitools Autonomous
 describes automatically scanning sites, building suites and executing tests without code.

BrowserStack AI Agents
 currently includes self-healing, failure analysis and smart test selection.

So the general direction you're thinking about is absolutely real.

But that also means:

“URL → AI generates tests → self-heals” is not enough of a conceptual distinction.

10. The most interesting synthesis

If I combine these fields, I get a very different mental model of your problem.

Not:

             TEST GENERATOR

URL → Planner → Generator → Healer

But:

                    UNKNOWN APPLICATION
                           │
                           ▼
                  ┌─────────────────┐
                  │ Current Model   │
                  │ of Application  │
                  └────────┬────────┘
                           │
                    What do I know?
                    What don't I know?
                    What might matter?
                           │
                           ▼
                  ┌─────────────────┐
                  │ Next Experiment │
                  └────────┬────────┘
                           │
                           ▼
                       ACTION
                           │
                           ▼
                     OBSERVATION
                           │
                           ▼
                  ┌─────────────────┐
                  │ Update Model    │
                  │ + Evidence      │
                  └────────┬────────┘
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
          New state     New behavior   Contradiction
             │             │              │
             └─────────────┴──────────────┘
                           │
                           ▼
                    Generate tests
                           │
                           ▼
                      Execute
                           │
                           ▼
                    Unexpected result
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
          Test bug      App defect    Model wrong
             │             │              │
             ▼             ▼              ▼
           repair        report        re-explore

That combines:

Active automata learning → learn the black-box state model

GUI ripping → reverse-engineer executable UI behavior

Robotics/SLAM → maintain a world/cognitive map

Active learning → choose the next information-producing action

Fuzzing → use runtime feedback to guide exploration

Metamorphic testing → test relationships when exact expected outputs are unavailable

Runtime verification → continuously evaluate behavioral properties

Scientific discovery → hypothesis → experiment → evidence → revised hypothesis

This is, in my view, a much stronger research foundation than “multi-agent testing.”

11. The six questions become much sharper

Earlier I asked:

What exactly do exploration, understanding, coverage, correctness and healing mean when the only ground truth is a running application?

After the research, I'd refine them to these:

A. Exploration

Not:

Have I visited every page?

But:

What action should I take next to maximally increase my knowledge of the application's behavior?

This connects to active learning, information gain and autonomous exploration.

B. Understanding

Not:

Did the LLM summarize the website?

But:

Can I maintain a predictive behavioral model of the application?

If I perform action X in state S, can my model predict the relevant consequences?

This connects to active automata learning, cognitive maps and world models.

C. Coverage

Not:

How many tests did I generate?

But:

What dimensions of behavioral space have I actually explored?

Potentially:

states
transitions
user intents
data classes
negative paths
permissions
error states
state transitions
invariants

This connects to stateful fuzzing and model-based testing.

D. Correctness

Not:

Did the test pass?

But:

What evidence allows me to believe the observed behavior is correct?

This connects directly to the test-oracle problem, metamorphic testing and runtime verification.

E. Healing

Not:

Can I make this test green?

But:

Can I preserve the semantic claim of the test despite changes in its execution path?

This is where the distinction between:

execution equivalence

and

semantic equivalence

becomes critical.

F. Termination

This one is especially important.

Not:

Have I run enough tests?

But:

When is the marginal value of another exploration/test lower than its cost and remaining risk?

That connects directly to active learning and autonomous scientific experimentation.

12. The research direction I would investigate hardest

If we temporarily forget the hackathon implementation entirely, I think the deepest formulation is:

Black-box behavioral system identification for web applications.

Then testing becomes an application of the learned model.

Something like:

                 BLACK BOX WEB APP
                         │
              observations + actions
                         │
                         ▼
              BEHAVIORAL MODEL
                         │
          ┌──────────────┼──────────────┐
          │              │              │
       states        transitions      properties
          │              │              │
          └──────────────┼──────────────┘
                         │
                         ▼
                 TEST HYPOTHESES
                         │
                         ▼
                    EXPERIMENTS
                         │
                         ▼
                      RESULTS
                         │
                  ┌──────┴──────┐
                  │             │
               confirms     contradicts
                  │             │
                  ▼             ▼
               increase       revise
               confidence     model

And then test generation is no longer the central intellectual problem.

It's a consequence of having a sufficiently good behavioral model.

That also gives your “smart tool for agents” idea much more substance. The tool would not merely be a better Playwright. Conceptually, it would provide an agent with primitives for:

observe application
discover actions
represent state
remember transitions
compare states
measure novelty
run experiment
capture evidence
detect contradiction
restore state

But I would deliberately stop there for now. That's already drifting into solution architecture.

The research question itself is the interesting part:

Can an autonomous agent efficiently construct, maintain, and test a useful behavioral model of an unfamiliar web application using only black-box interaction?

That question has legitimate foundations across active automata learning + cognitive mapping + active learning + fuzzing + metamorphic testing + runtime verification + agentic scientific discovery.

And importantly, there is a real research gap between those areas: each solves one slice, but I did not find a mature system that cleanly unifies black-box application exploration → behavioral model construction → coverage-directed experiment selection → oracle construction → semantic failure diagnosis → model revision for arbitrary web applications.

That is where I think your problem becomes genuinely interesting rather than just “another AI testing tool.”

Today 11:28 AM

so for exploring i need to know the app.
so for every action i have taken i need to find and intent and the intent comes from who has taken the action...

agent doesnt need to discover every possible state... they need to discover enough of the system structure to construct a useful model... observe state -> possible actions and possible states. -> act -> observe result -> update model -> chose next experiment. we have to make sure this doesnt cause state explosion... need to meaning full way to represent different behavioral states... state abstraction
GUI Rippling: reverse engineering of graphical user interface for testing... gui state + available events + event relationships + component relationship -> behavioral model... the event-flow graph representes which events could follow which other events so you have conceptually something like login -> dashboard -> project and setting-> in project create project -> valid input -> project created, invalid input -> validation error etc... creating this map... will help with which test cases can emerge naturally. but we need to look into the GUI rippling problem...
once we are able to explore the app.. observe, give inputs and take actions.. we can have an agent which dynamically expands its map when it encounters novel.unvisted refions and updating the map when new evidence contradicts the previous beliefs... update the map when new evidence contradicts previous beliefs.. imagine you testing agent believes create user->user details... but after exploration it discovers... create user -> email verification -> pending users -> user detail... it shouldnt be merely another observation it should revise its understanding of the application... congnitive map/ world model than a test-plan document.
which action gives me the most useful information.... explore app -> behaviour hypothesis -> choose interaction -> observe result -> update app model -> chose next interaction/test
deciding what do i currently believe about this application, how uncertain am i, and what obervation/action would reduce the uncertainty most.. have a current belief-> what dont i know -> what experiment resolves it -> experiment -> result -> did my belief survive -> yes -> continue, no-> revise model
now the relationships between executions...
                UNKNOWN APPLICATION
                       │
                       ▼
              ┌─────────────────┐
              │ Current Model   │
              │ of Application  │
              └────────┬────────┘
                       │
                What do I know?
                What don't I know?
                What might matter?
                       │
                       ▼
              ┌─────────────────┐
              │ Next Experiment │
              └────────┬────────┘
                       │
                       ▼
                   ACTION
                       │
                       ▼
                 OBSERVATION
                       │
                       ▼
              ┌─────────────────┐
              │ Update Model    │
              │ + Evidence      │
              └────────┬────────┘
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
      New state     New behavior   Contradiction
         │             │              │
         └─────────────┴──────────────┘
                       │
                       ▼
                Generate tests
                       │
                       ▼
                  Execute
                       │
                       ▼
                Unexpected result
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
      Test bug      App defect    Model wrong
         │             │              │
         ▼             ▼              ▼
       repair        report        re-explore

That combines:

Active automata learning → learn the black-box state model
GUI ripping → reverse-engineer executable UI behavior
Robotics/SLAM → maintain a world/cognitive map
Active learning → choose the next information-producing action
Fuzzing → use runtime feedback to guide exploration
Metamorphic testing → test relationships when exact expected outputs are unavailable
Runtime verification → continuously evaluate behavioral properties
Scientific discovery → hypothesis → experiment → evidence → revised hypothesis

something recently what chatgpt did... they had different agent try to jailbreak a system... where agents shared information among them selves and coordinated attack... we need a system
something like that which does the explortion

now i need help with minimizing the scope and incrementaly adding new steps making sure... that we are making progress and not regressing as all... so instead of planing 100 features and complex flow... whats the first thing i need to get this working... how closer is to the problem statement and the criteria from the pdf above... then describe different levels... once we know what to build... we will then next discuss about how to build and the tech to use...

Yes. I think the right move now is to stop designing the final system and define a sequence of increasingly capable problems.

The key is: each level must produce a real artifact that remains useful at the next level. We should never throw away the previous level and build a new system.

One correction first: “intent comes from who took the action” is not quite right. We need to distinguish four things:

Actor       → who is acting / role
Action      → what was done
Context     → state in which it was done
Intent      → what behavior the action appears to be trying to achieve
Outcome     → what actually happened

For example:

Actor: admin
State: project list

Action: click "Create Project"

Candidate intent:
  create a new project

Outcome:
  creation form appeared

The intent is inferred from the action + context + subsequent behavior. We should not assume the agent can directly observe intent.

Now, I would reduce the entire challenge to the following progression.

Level 0 — Prove the black-box interaction loop

Forget testing.

Input:

URL
username
password

The system should autonomously:

open app
→ login
→ observe
→ choose an action
→ perform action
→ observe
→ choose another action
→ ...

The only thing we care about:

Can the system reliably interact with an unfamiliar application without a human telling it what to click?

Success looks like:

URL
 ↓
login
 ↓
dashboard
 ↓
projects
 ↓
create project
 ↓
fill form
 ↓
submit
 ↓
project created

Not:

“It explored the whole application.”

Just prove that the basic autonomous loop works.

This is the smallest possible experiment.

Level 1 — Exploration becomes a model

Now we stop thinking of the agent's exploration as a conversation/log.

Every observation becomes part of an evolving application model.

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

For example:

S1: Login
 └─ login(valid)
       ↓
S2: Dashboard
 ├─ open_projects()
 └─ open_settings()

S3: Projects
 └─ create_project()
       ↓
S4: Create Project
 ├─ submit(valid)
 │     ↓
 │   S5: Project Created
 │
 └─ submit(invalid)
       ↓
     S6: Validation Error

Now you have your first genuinely important artifact:

Behavioral model of an unknown application.

This is where the GUI-ripping / active automata learning research becomes directly relevant.

The regression criterion

Every time you add exploration capability, the existing model must still be reproducible.

For example:

Given the same app:

login → dashboard → projects → create project

must continue to resolve to the same behavioral structure.

You don't yet care whether the model is perfect.

You care that it is stable, inspectable and incrementally extensible.

Level 2 — State abstraction

This is where I would spend serious thinking before adding more functionality.

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

This is not an implementation question yet.

It is one of the central research/product questions.

And we don't move on until we have a reasonable answer for a small application.

Level 3 — Exploration becomes intelligent

At Level 1 the explorer might say:

“I haven't clicked this button.”

Level 3 asks:

What should I do next?

Now introduce:

Current model
     ↓
Known behavior
Unknown behavior
Uncertain assumptions
     ↓
candidate experiments
     ↓
choose most valuable experiment
     ↓
observe
     ↓
update model

Example:

Known:

Create User
→ User Created

Unknown:

Does duplicate email work?
Does invalid email reject?
Does refresh preserve user?
Can normal users create users?
What happens after deleting the user?

Instead of randomly clicking things, the explorer deliberately chooses an experiment.

This is the point where your active-learning/scientific-discovery analogy becomes useful.

The first real definition of “smart exploration” could simply be:

Prefer actions that reveal previously unknown behavior while avoiding redundant exploration.

Do not initially try to implement sophisticated information theory.

You first need to demonstrate that model-guided exploration beats naïve crawling.

That is an experiment worth running.

Level 4 — Behavioral model → test hypotheses

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

That is a much better problem.

Level 5 — Tests become experiments

Now the relationship between executions becomes important.

You mentioned:

“now the relationships between executions…”

Exactly.

A single execution might tell you:

Create user
→ user created

But multiple executions allow much stronger reasoning.

For example:

Experiment A:
create user with email A
→ success

Experiment B:
refresh
→ user still exists

Therefore:

create → persistence

Or:

Experiment A:
valid email → success

Experiment B:
invalid email → rejection

Or:

Experiment A:
admin → can delete

Experiment B:
normal user → cannot delete

Now testing starts looking like experimental science over the application.

You are building evidence about behavioral properties.

This is where metamorphic testing and runtime verification become particularly relevant.

Level 6 — Failures challenge the model

Now deliberately introduce failure.

You have:

Expected model:

Create User
→ User Created

Actual execution:

Create User
→ error

The system now has competing hypotheses:

H1: locator/test is broken
H2: application is broken
H3: our model of the application is wrong
H4: environment/data caused the failure

This is much more interesting than simply calling a healer.

The failure is evidence that something in our current understanding may be wrong.

So:

unexpected result
       ↓
classify
       ↓
does model explain it?
       │
   ┌───┴────┐
  yes       no
   │         │
continue   investigate
             ↓
       revise model

This is where your cognitive-map idea becomes important.

The model isn't static documentation.

It is a hypothesis about the application that can be falsified and revised.

Level 7 — Healing becomes semantic

Only now should “healing” become sophisticated.

A weak healer does:

test failed
 ↓
find another selector
 ↓
test passes

Your model should eventually ask:

Original behavioral claim:
"Creating a project creates a project."

Old execution:
click Create Project
fill name
submit
verify project exists

After UI change:
click Add Workspace
fill name
submit
verify workspace exists

Does this preserve the original intent?

Maybe not.

Therefore:

GREEN
≠
CORRECT

The healer has to preserve the behavioral claim, not merely the execution.

That gives you a meaningful definition of healing:

Repair the test while preserving the behavior it was intended to verify, and do not repair away evidence of an application defect.

That is much stronger than “self-healing selectors.”

Level 8 — Autonomous orchestration

Only at this point does the full challenge emerge naturally.

The meta-agent can now decide:

Explore
 ↓
model sufficient?
 ├─ no → explore
 └─ yes
      ↓
generate tests
      ↓
execute
      ↓
results
      ↓
coverage sufficient?
 ├─ no → explore/re-plan
 └─ yes
      ↓
failures?
 ├─ test failure → investigate/heal
 ├─ app failure → report defect
 └─ model failure → revise/re-explore
      ↓
quality sufficient?
 ├─ no → continue
 └─ yes → final report

That is the actual orchestration layer the problem statement asks for: autonomous planning, generation, execution and repair, with coverage evaluation and decisions about re-planning/escalation. problem_explanation_9dm9yp4f98s

How far is each level from the actual hackathon?

I would think about it this way:

Level	Capability	Challenge coverage
0	Autonomous browser interaction	~5%
1	Application behavioral model	~15%
2	Useful state abstraction	~20%
3	Intelligent exploration	~30%
4	Model → meaningful test cases	~45%
5	Execute + reason across executions	~60%
6	Failure diagnosis + model revision	~70%
7	Semantic healing	~85%
8	Full autonomous orchestration + quality report	~100%

These percentages are planning estimates, not scores from the organizers.

The important thing is that Level 3 already solves a meaningful portion of the hardest intellectual problem.

And the challenge specifically rewards intelligent handling of coverage gaps, ambiguity and failure classification, rather than merely having a pipeline that executes. problem_explanation_9dm9yp4f98s

But I would make the first milestone even smaller

Do not start with Level 0 as:

“Explore arbitrary websites.”

Choose a deliberately small application.

Something like:

Login
Dashboard
CRUD entity
Search/filter
Validation
One permission distinction

Then the first target is:

Given URL + credentials, autonomously explore this unfamiliar application and produce a behavioral map containing meaningful states, actions and transitions.

Nothing about:

generating test code

Playwright

healing

multi-agent orchestration

coverage scoring

fancy UI

production architecture

Yet.

The output should simply be something humans can inspect:

Application
│
├── Authentication
│   └── Login
│
├── Dashboard
│
├── Projects
│   ├── Create
│   │   ├── valid → Created
│   │   └── invalid → Validation Error
│   ├── Edit
│   └── Delete
│
└── Settings

And alongside it:

Known:
  create project succeeds with valid data

Known:
  invalid project name produces validation error

Unknown:
  duplicate project behavior

Unknown:
  normal-user permissions

Unexplored:
  delete flow

That is already a meaningful result.

Then you can ask one extremely concrete question:

Does this behavioral map allow us to produce better test cases than an agent that simply explores the site and asks an LLM to generate tests from its transcript?

If yes, you have evidence for the core thesis.

If no, you don't need Levels 4–8 yet.

The progress/regression rule

I would make this almost an iron rule for the project:

Every level has a fixed evaluation target. New capability cannot invalidate old capability.

For example:

LEVEL 1 TEST

Given App A:

Expected:
Login
→ Dashboard
→ Projects
→ Create Project

Model contains those transitions.

                 ↓

LEVEL 2

Same transitions still exist,
but irrelevant data differences
have been abstracted.

                 ↓

LEVEL 3

Same model +
discovers an intentionally hidden/less-obvious flow.

                 ↓

LEVEL 4

Tests generated from the model
cover those meaningful transitions.

                 ↓

LEVEL 5

Tests execute successfully.

                 ↓

LEVEL 6

Inject a failure.
System correctly identifies why.

                 ↓

LEVEL 7

Change UI implementation.
System repairs test without
changing its semantic claim.

So at every step you have:

OLD CAPABILITY
     +
NEW CAPABILITY
     +
REGRESSION CHECK

rather than:

build huge thing
       ↓
hope it works
And this changes how I would interpret the “multi-agent jailbreak” idea

I would not copy the idea because it uses multiple agents.

The useful concept is multiple explorers with shared evolving knowledge.

Imagine:

                 SHARED MODEL
                     │
        ┌────────────┼────────────┐
        ↓            ↓            ↓
    Explorer A   Explorer B   Explorer C
        │            │            │
    auth flows    CRUD flows   edge cases
        │            │            │
        └────────────┼────────────┘
                     ↓
               evidence merge
                     ↓
              model revision

Each explorer could discover something the others don't know.

But the really interesting question is:

How do independent explorers coordinate around an evolving model without repeatedly exploring the same space or corrupting each other's assumptions?

That connects very naturally to the research directions you found: active exploration, state abstraction, shared maps, hypothesis testing, and information-seeking experiments.

I would not build this yet.

First prove:

ONE explorer
     ↓
ONE useful behavioral model
     ↓
ONE meaningful new discovery

Then:

multiple explorers
     ↓
shared model
     ↓
faster / better discovery

That gives you an extremely clean experimental progression.

The first thing I would work on now is therefore not “the autonomous testing agent.”

It is:

Can we make an unfamiliar web application become a progressively learned behavioral model through autonomous interaction, while keeping the model compact enough that state explosion doesn't destroy it?

If that works, almost everything else in your proposed system has a foundation to build on.

Today 12:46 PM
now that we have what to build lets discuss how to build... need precises answer and also how to test the component that was built... need to divide the thing into blocks which we can connect later.
so we need exact specs that i can give to claude and ask it to generate the required code.

Now we can switch from “what is the problem?” to “what are the engineering boundaries?”

I would not build this as one agent. I would build a set of deterministic components with very explicit contracts, and put LLM decision-making only where judgment is actually required.

The important architectural principle is:

The browser produces evidence. The model stores evidence. The agent makes decisions from the model. Nothing important exists only inside an agent's context.

Playwright is a reasonable browser substrate because it already provides structured accessibility snapshots, interaction primitives, storage state, network/devtools capabilities, etc.
Playwright
 But we should not inherit its Planner → Generator → Healer architecture as our architecture; Playwright already has that.
Playwright

1. The system we should actually build

At the highest level:

                    ┌─────────────────────┐
URL + credentials → │ Exploration Runtime │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Application Model   │
                    │                     │
                    │ states              │
                    │ actions             │
                    │ transitions         │
                    │ intents             │
                    │ evidence            │
                    │ unknowns            │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Exploration Policy  │
                    │                     │
                    │ what do I know?     │
                    │ what don't I know?  │
                    │ next experiment?    │
                    └──────────┬──────────┘
                               │
                         more exploration
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Test Synthesizer    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Test Executor       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Failure Analyzer    │
                    └──────┬─────┬────────┘
                           │     │
                         heal  defect
                           │     │
                           ▼     ▼
                       execute  report

But this is the final shape, not what we build on day one.

2. Divide it into blocks

I would use these blocks.

B1  Browser Runtime
B2  Observation / Action Model
B3  State Abstraction
B4  Behavioral Graph
B5  Explorer
B6  Coverage / Experiment Selector
B7  Test Synthesizer
B8  Test Executor
B9  Failure Analyzer
B10 Healer
B11 Orchestrator
B12 Quality Report

The dependency graph is:

B1
 ↓
B2
 ↓
B3
 ↓
B4
 ↓
B5
 ↓
B6
 ↓
B7
 ↓
B8
 ↓
B9
 ↓
B10
 ↓
B11
 ↓
B12

But there are useful parallel boundaries:

                  B4 Behavioral Graph
                  /        |        \
                 /         |         \
               B5         B7         B9
            Explorer     Tests     Diagnosis
               |           |          |
               └─────── B11 ─────────┘

This is important for your three-person team later.

3. B1 — Browser Runtime

This is the first thing to build.

It should know absolutely nothing about “testing.”

Its job is:

Give another component a reliable way to observe and manipulate a browser.

Use Playwright directly initially. Don't build your own browser automation framework yet.

Contract
TypeScript
interface BrowserRuntime {
  start(config: BrowserConfig): Promise<void>;

  navigate(url: string): Promise<Observation>;

  observe(): Promise<Observation>;

  execute(action: BrowserAction): Promise<ActionResult>;

  screenshot(): Promise<string>;

  saveState(path: string): Promise<void>;

  restoreState(path: string): Promise<void>;

  close(): Promise<void>;
}

The key object is Observation.

TypeScript
interface Observation {
  timestamp: string;

  url: string;
  title: string;

  page: PageObservation;

  interactiveElements: InteractiveElement[];

  forms: FormObservation[];

  dialogs: DialogObservation[];

  visibleText: string;

  screenshot?: string;

  consoleErrors?: ConsoleError[];
}

And:

TypeScript
interface InteractiveElement {
  id: string;

  role: string;

  name: string;

  type?: string;

  value?: string;

  checked?: boolean;

  disabled?: boolean;

  visible: boolean;

  locatorCandidates: LocatorCandidate[];
}

Do NOT make the LLM consume the raw DOM.

Playwright's accessibility snapshot is already designed to give agents a compact structured representation of the page.
Playwright

Actions
TypeScript
type BrowserAction =
  | {
      type: "click";
      elementId: string;
    }
  | {
      type: "fill";
      elementId: string;
      value: string;
    }
  | {
      type: "select";
      elementId: string;
      value: string;
    }
  | {
      type: "check";
      elementId: string;
    }
  | {
      type: "uncheck";
      elementId: string;
    }
  | {
      type: "press";
      key: string;
    }
  | {
      type: "navigate";
      url: string;
    }
  | {
      type: "back";
    }
  | {
      type: "reload";
    };
B1 test

Build a tiny local application:

/login
/dashboard
/projects
/create-project

Test:

start
→ navigate
→ observe
→ click
→ observe
→ fill
→ observe
→ submit
→ observe

Acceptance:

1. Browser starts.
2. URL navigation works.
3. Observation contains interactive elements.
4. Every returned elementId can be acted upon.
5. Action returns before/after observations.
6. Browser state can be saved/restored.
7. No LLM is involved.

This is your first regression suite.

4. B2 — Observation → Action semantics

Now introduce the first intelligence.

The browser tells us:

button:
  name = "Create Project"

We need to turn raw observations into semantic candidates.

TypeScript
interface ActionCandidate {
  id: string;

  action: BrowserAction;

  semanticDescription: string;

  likelyIntent: IntentHypothesis[];

  risk: "low" | "medium" | "high";

  novelty: "unknown" | "known" | "likely-new";
}

Intent:

TypeScript
interface IntentHypothesis {
  description: string;

  confidence: number;

  evidence: EvidenceRef[];
}

Example:

JSON
{
  "action": "click Create Project",
  "likelyIntent": [
    {
      "description": "begin creation of a project",
      "confidence": 0.96
    }
  ]
}

But don't pretend that intent is ground truth.

It is a hypothesis.

That distinction will become very important later.

B2 test

Given a fixed observation:

button "Create Project"
button "Settings"
button "Delete"

the semantic analyzer should produce:

Create Project → likely create project
Settings       → likely navigate to settings
Delete         → likely destructive deletion

Test the structured output, not whether an LLM used particular wording.

5. B3 — State Abstraction

This is probably the most important foundational component.

Do NOT initially ask an LLM:

“Are these two pages the same state?”

Build a deterministic state representation first.

TypeScript
interface ApplicationState {
  id: string;

  url: string;

  route: string;

  role?: string;

  pageType?: string;

  visibleComponents: ComponentState[];

  forms: FormState[];

  overlays: OverlayState[];

  availableActions: ActionSignature[];

  fingerprint: string;
}

Then define:

TypeScript
StateIdentity.compare(
  observationA,
  observationB
): StateComparison;

where:

TypeScript
interface StateComparison {
  same: boolean;

  similarity: number;

  differences: StateDifference[];
}

Initially, fingerprint using stable behavioral information:

route
+
page structure
+
interactive controls
+
form structure
+
selected/checked states
+
modal/dialog presence
+
role

Do not include:

timestamps
random IDs
volatile counters
analytics
irrelevant dynamic text

unless they affect behavior.

This is exactly where you control state explosion.

6. B4 — Behavioral Graph

Now connect states and actions.

This becomes the application's learned model.

TypeScript
interface BehavioralModel {
  application: ApplicationMetadata;

  states: Map<StateId, ApplicationState>;

  transitions: Transition[];

  unknowns: Unknown[];

  evidence: Evidence[];
}

Transition:

TypeScript
interface Transition {
  id: string;

  fromState: StateId;

  action: ActionSignature;

  toState: StateId;

  intent: IntentHypothesis[];

  outcome: Outcome;

  evidence: EvidenceRef[];

  confidence: number;
}

Example:

S1 Login
  |
  | submit valid credentials
  ↓
S2 Dashboard
  |
  | click Projects
  ↓
S3 Projects
  |
  | click Create Project
  ↓
S4 Create Project
  |
  | submit valid form
  ↓
S5 Project Created

But now you also store:

S4
 |
 | submit invalid form
 ↓
S6 Validation Error

This is your first real “application understanding.”

B4 test

Give it a predetermined action sequence:

login
open projects
create project
submit valid

Assert that the resulting graph contains exactly:

Login → Dashboard
Dashboard → Projects
Projects → Create Project
Create Project → Project Created

Then run:

create project
submit invalid

and assert that:

Create Project → Validation Error

is added rather than creating a duplicate Create Project state.

That last assertion tests your state abstraction.

7. B5 — Explorer

Only now build an autonomous explorer.

Its input:

TypeScript
interface ExplorationContext {
  model: BehavioralModel;

  currentState: ApplicationState;

  observation: Observation;

  budget: ExplorationBudget;
}

Its output:

TypeScript
interface ExplorationDecision {
  action: ActionCandidate;

  rationale: string;

  expectedDiscovery: string;

  confidence: number;
}

The loop is:

observe
 ↓
update model
 ↓
generate candidate actions
 ↓
choose action
 ↓
execute
 ↓
observe
 ↓
update model
 ↓
repeat

Initially, don't implement sophisticated information theory.

Use a simple policy:

1. Prefer unvisited actions.
2. Prefer actions likely to produce a new state.
3. Prefer actions with high semantic significance.
4. Avoid destructive actions initially.
5. Avoid repeating identical transitions.
6. Stop when exploration budget is exhausted or no useful unknowns remain.

The important thing is that the policy is replaceable.

Later:

RandomExplorer
CoverageExplorer
InformationGainExplorer
LLMExplorer
MultiAgentExplorer

can all implement:

TypeScript
interface ExplorationPolicy {
  chooseNextAction(
    context: ExplorationContext
  ): Promise<ExplorationDecision>;
}
8. B6 — Coverage / Experiment Selector

This is where your “what don't I know?” idea enters.

Given:

known states
known transitions
known intents
unknown actions
uncertain transitions

produce experiments.

TypeScript
interface Experiment {
  id: string;

  hypothesis: string;

  preconditions: Preconditions;

  actions: BrowserAction[];

  expectedObservations: ExpectedObservation[];

  informationGain?: number;

  priority: number;
}

Example:

Hypothesis:
Submitting duplicate project names is rejected.

Experiment:
1. create project "Alpha"
2. return to create form
3. create project "Alpha"

Expected:
validation error OR explicit duplicate handling

The first version can score experiments using simple heuristics:

+ unvisited transition
+ high-risk action
+ unresolved uncertainty
+ edge case
+ new state potential
- duplicate action
- destructive action
- already verified behavior

Later you can replace that scoring with an LLM or information-gain strategy.

This gives you an extremely useful experimental boundary:

Explorer discovers. Experiment selector decides what knowledge is worth acquiring.

9. B7 — Test Synthesizer

Only after the behavioral model exists.

Input:

TypeScript
BehavioralModel
+
Experiment[]

Output:

TypeScript
interface TestSpecification {
  id: string;

  name: string;

  intent: string;

  preconditions: string[];

  steps: TestStep[];

  assertions: Assertion[];

  sourceTransitions: string[];

  confidence: number;
}

Example:

TEST: Create project with valid data

Intent:
Verify that a user can create a project.

Preconditions:
Authenticated user.

Steps:
1. Navigate to Projects.
2. Open Create Project.
3. Enter valid project name.
4. Submit.

Assertions:
1. Project creation succeeds.
2. Created project is visible.
3. Reload preserves project.

Notice that this is not Playwright code yet.

It's a semantic test specification.

Then:

TestSpecification
        ↓
Playwright generator
        ↓
.spec.ts

This separation will help enormously with healing later.

10. B8 — Test Executor

This should be almost completely deterministic.

TypeScript
interface TestExecutor {
  run(
    test: GeneratedTest
  ): Promise<TestExecution>;
}
TypeScript
interface TestExecution {
  testId: string;

  status: "passed" | "failed" | "blocked";

  steps: StepExecution[];

  finalObservation: Observation;

  errors: ExecutionError[];

  trace?: string;

  screenshots?: string[];
}

The executor doesn't decide whether a failure is a bug.

It reports evidence.

11. B9 — Failure Analyzer

Now we finally introduce diagnosis.

Input:

Test specification
+
generated test
+
execution trace
+
current application observation
+
behavioral model

Output:

TypeScript
type FailureClassification =
  | "test_locator_failure"
  | "test_timing_failure"
  | "test_data_failure"
  | "application_defect"
  | "model_mismatch"
  | "environment_failure"
  | "unknown";

And:

TypeScript
interface FailureAnalysis {
  classification: FailureClassification;

  confidence: number;

  evidence: EvidenceRef[];

  explanation: string;

  recommendedAction:
    | "heal"
    | "retest"
    | "reexplore"
    | "report"
    | "escalate";
}

This component should initially be tested against synthetic failures.

Create known scenarios:

1. Rename button
   → locator failure

2. Add artificial delay
   → timing failure

3. Break application validation
   → application defect

4. Change flow:
   Create User → Email Verification → User
   → model mismatch

5. Give invalid credentials
   → data/auth failure

The classifier should identify them.

Do not judge it based on whether its explanation sounds intelligent.

Judge the classification.

12. B10 — Healer

Now healing has a clean constraint:

Original Test Intent
        ↓
     MUST STAY
        ↓
repair execution

Input:

TypeScript
interface HealingRequest {
  test: TestSpecification;

  failure: FailureAnalysis;

  execution: TestExecution;

  model: BehavioralModel;
}

Output:

TypeScript
interface HealingResult {
  status: "healed" | "rejected" | "needs_replanning";

  patch: TestPatch | null;

  semanticValidation: SemanticValidation;

  attempts: HealingAttempt[];
}

And:

TypeScript
interface SemanticValidation {
  preservesIntent: boolean;

  preservedAssertions: string[];

  changedSemantics: string[];

  confidence: number;
}

This is where you explicitly prevent:

test failed
→ find anything that works
→ green

The healer must prove:

old intent
==
new test intent

to an acceptable confidence.

13. B11 — Orchestrator

Only after all those pieces work independently.

The orchestrator should not contain the actual browser logic, model logic, test generation logic, etc.

It decides:

TypeScript
type PipelineDecision =
  | "explore"
  | "generate_tests"
  | "execute_tests"
  | "heal"
  | "reexplore"
  | "report"
  | "escalate";

Based on:

TypeScript
interface PipelineState {
  model: BehavioralModel;

  coverage: CoverageAssessment;

  tests: TestSpecification[];

  executions: TestExecution[];

  failures: FailureAnalysis[];

  healing: HealingResult[];

  budget: Budget;
}

Then:

                     ┌──────────────┐
                     │ Orchestrator │
                     └──────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              ↓             ↓             ↓
           explore       generate      execute
              ↑             │             │
              │             │             ↓
              │             │          analyze
              │             │          /     \
              │             │       heal     defect
              │             │        │          │
              └─────────────┴────────┴──────────┘
                            │
                         report
14. B12 — Quality Report

This is the final challenge deliverable.

It should not simply say:

17 tests
14 passed
3 failed

It should say:

Application understanding

Discovered:
  12 behavioral states
  31 meaningful transitions
  8 user journeys

Verified:
  7 high-confidence journeys
  4 edge cases
  3 validation paths
  2 persistence properties

Failures:
  2 test implementation failures → healed
  1 genuine application defect

Unknown:
  3 potentially important behaviors
  1 permission boundary not verified

Remaining risk:
  Medium

That directly maps to the challenge's requirement for scenarios covered, outcomes, healer actions, coverage gaps and untested risk. problem_explanation_9dm9yp4f98s

The critical thing: every block gets its own test target

I would create this repository structure:

src/
  browser/
  observation/
  state/
  model/
  exploration/
  coverage/
  testing/
  execution/
  diagnosis/
  healing/
  orchestration/
  reporting/

contracts/
  observation.ts
  action.ts
  state.ts
  transition.ts
  experiment.ts
  test.ts
  execution.ts
  failure.ts
  healing.ts

tests/
  browser/
  observation/
  state/
  model/
  exploration/
  coverage/
  testing/
  execution/
  diagnosis/
  healing/
  orchestration/

fixtures/
  apps/
    simple-crud/
    auth-app/
    stateful-app/
    broken-app/

The fixtures/apps directory is extremely important.

You need known ground-truth applications to test the system.

Otherwise you have the terrible situation:

“The agent says it discovered the application correctly.”

How do you know?

Build a tiny application where you know the true state graph.

The first four fixture applications

Don't build them all immediately.

Eventually:

Fixture A — Basic CRUD
Login
 ↓
Dashboard
 ↓
Projects
 ├── Create
 ├── Edit
 └── Delete
Fixture B — Edge states
Create
 ├── valid → success
 ├── empty → validation
 ├── invalid → validation
 └── duplicate → error
Fixture C — Hidden transition
Create User
 ↓
Email verification
 ↓
Pending
 ↓
User

The explorer should initially form an incorrect hypothesis and then revise it.

This tests your “new evidence contradicts previous belief” idea.

Fixture D — Changed application

Version 1:

Create User → User

Version 2:

Create User → Verification → User

Run the same generated test.

This becomes your test for:

model mismatch

and eventually semantic healing.

The development order I would actually use

Do not give Claude the entire architecture and ask it to build everything.

Do this:

PHASE 1

B1 Browser Runtime
B2 Observation
B3 State abstraction
B4 Behavioral graph

Then stop.

You should be able to run:

URL
 ↓
browser
 ↓
observe
 ↓
actions
 ↓
states
 ↓
transitions
 ↓
behavioral graph

against a known fixture.

That is the first milestone.

Then:

PHASE 2

B5 Explorer

Now:

URL
 ↓
Explorer
 ↓
Browser
 ↓
Observation
 ↓
Model
 ↓
Explorer
 ↓
...

The explorer should autonomously discover the fixture's known behavior.

Then:

PHASE 3

B6 Experiment / Coverage

Now deliberately put something the explorer hasn't discovered into the fixture.

See whether it identifies the uncertainty and chooses an action that discovers it.

Only then:

PHASE 4

B7 Test Synthesizer
B8 Executor

Then:

PHASE 5

B9 Failure Analysis
B10 Healing

Then:

PHASE 6

B11 Orchestrator
B12 Report

This gives you six independently testable milestones instead of one enormous “AI QA agent.”

The first Claude specification

I would literally give Claude something like this for the first implementation:

You are implementing Phase 1 of an autonomous black-box web application exploration system.

Do NOT implement test generation, healing, orchestration, coverage optimization, multi-agent behavior, or an LLM explorer.

The only goal of this phase is:

Given a URL and optional credentials, interact with a web application through Playwright and construct a deterministic behavioral model consisting of:

1. observations
2. available actions
3. abstract application states
4. transitions between states
5. evidence for each transition

Architecture:

src/
  browser/
  observation/
  state/
  model/

contracts/
  observation.ts
  action.ts
  state.ts
  transition.ts

tests/
  browser/
  observation/
  state/
  model/

Requirements:

1. Implement BrowserRuntime using Playwright.

2. Implement Observation extraction from the browser.

3. Each interactive element must receive a stable observation-local ID.

4. Implement BrowserAction for:
   - click
   - fill
   - select
   - check
   - uncheck
   - press
   - navigate
   - back
   - reload

5. Every action execution must produce:
   - previous observation
   - action
   - resulting observation
   - execution status
   - error if any

6. Implement StateAbstraction.

A state fingerprint must NOT include volatile values such as:
   - timestamps
   - random DOM IDs
   - analytics data
   - arbitrary dynamic text

It SHOULD consider:
   - normalized route
   - page structure
   - visible interactive elements
   - element roles
   - accessible names
   - form structure
   - selected/checked state
   - visible dialogs
   - authentication role if known

7. Implement BehavioralModel with:
   - states
   - transitions
   - evidence
   - confidence

8. If an observation maps to an existing abstract state, reuse that state rather than creating a new state.

9. If two observations differ only in volatile data, they must resolve to the same state.

10. Every transition must preserve:
    from state
    action
    to state
    evidence

11. The implementation must be deterministic except for browser behavior.

12. Do not put application understanding inside global variables or LLM conversation history.

13. All important information must exist in typed data structures.

14. Write unit tests for state fingerprinting.

15. Write integration tests against a local fixture application.

Create a fixture application containing:

Login
→ Dashboard
→ Projects
→ Create Project

Create Project has:
- valid submission → Project Created
- empty submission → Validation Error

Tests must verify that the resulting behavioral graph contains these states and transitions.

The implementation is complete only when:

npm test

passes and an integration test can start the fixture application, run the browser runtime, perform the defined actions, and produce the expected behavioral graph.

Do not add functionality outside this specification.

That is a good first Claude task because it is small enough to verify mechanically.

And the acceptance test is not:

“Claude says exploration works.”

It is:

known application
      ↓
known interaction sequence
      ↓
known expected graph
      ↓
actual graph
      ↓
structural comparison

That gives us an objective regression test.

Then the second Claude task

Once Phase 1 passes:

Implement Phase 2: Autonomous Explorer.

Use the existing BrowserRuntime and BehavioralModel APIs.
Do not modify their public contracts unless absolutely necessary.

Implement:

interface ExplorationPolicy {
  chooseNextAction(
    context: ExplorationContext
  ): Promise<ExplorationDecision>;
}

Implement a baseline policy with these priorities:

1. unexplored actions
2. actions likely to produce an unknown state
3. meaningful semantic actions
4. unresolved transitions
5. avoid duplicate transitions
6. avoid destructive actions unless explicitly enabled

The explorer must have a hard action budget.

For every action it takes, persist:

- current state
- action
- intent hypothesis
- expected discovery
- resulting state
- transition
- evidence

Tests:

1. Explorer discovers all reachable states in fixture A within N actions.
2. Explorer does not repeatedly execute an already-known transition indefinitely.
3. Explorer records unknown/unexplored actions.
4. Explorer terminates when no useful actions remain.
5. Explorer respects its action budget.
6. Existing Phase 1 tests must continue passing.

Do not implement test generation, healing, coverage optimization, or orchestration.

That is how I would continue.

The important pattern is:

SPEC
 ↓
IMPLEMENT ONE BLOCK
 ↓
TEST BLOCK
 ↓
TEST AGAINST KNOWN ENVIRONMENT
 ↓
INTEGRATION TEST
 ↓
FREEZE CONTRACT
 ↓
NEXT BLOCK

Not:

SPEC
 ↓
Claude builds 10 agents
 ↓
three hours later
 ↓
"it mostly works"

The first major technical milestone I'd aim for is therefore very concrete:

Given a small known web application, can our system autonomously construct a compact behavioral graph that matches the application's actual state/transition structure?

Once that passes, we have something real to build on. Everything after that—information-seeking exploration, behavioral coverage, test synthesis, diagnosis, semantic healing and orchestration—can be attached to a stable foundation rather than being developed simultaneously.
