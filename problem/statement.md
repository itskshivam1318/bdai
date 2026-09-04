# Problem Statement

Released 2026-09-04. Source: sponsor video. Transcript is verbatim; everything
below the transcript is our reading of it and may be wrong — mark corrections
in `product/decisions.md` rather than editing the transcript.

## Who is asking

Bessemer Tech Catalyst — "AIVAR innovations", an AI-native services company and
an AWS preferred partner.

## Transcript (verbatim)

> I've been trying to write a test case for 3 days for a feature that I built in
> one day. I wish there was an AI that could do this for me.
>
> — Hey, have you tried Playwright?
>
> — No, you should definitely try that man because it has inbuilt hidden like
> [test] generator and so it should reduce your work.
>
> — Hey, I've been using the Playwright agents but still I am the one giving
> them context again and again. It is a lot of manual work. I wish there is an
> AI that can do this for us.
>
> — Why don't we just hire someone for that?
>
> — And how do we do that?
>
> — Bessemer tech catalyst.
>
> So we are AIVAR innovations, and we are an AI-native services company and an
> AWS preferred partner. So here is your problem statement. So we will be giving
> you an app URL, username and password and your agent should come up with a
> working end-to-end test suite, and it must be able to explore your app, write
> your test cases, run your test cases and heal your test cases.
>
> Show us that and we will hire you.

## The literal requirement

**Input:** an app URL, a username, a password. Nothing else.

**Output:** a working end-to-end test suite.

**Four capabilities, all named explicitly:**

1. **Explore** the app
2. **Write** test cases
3. **Run** test cases
4. **Heal** test cases

## What the setup is actually complaining about

The video spends its whole middle section on one specific pain, and it is not
"writing tests is hard":

> "I've been using the Playwright agents but still **I am the one giving them
> context again and again.** It is a lot of manual work."

Playwright already ships a codegen recorder and agent integrations. The
sponsors know this — they raise it and then dismiss it. So a tool that writes
Playwright tests from a human's description does not answer the brief; it is
the thing being complained about. The gap is **who supplies the context**.

"Why don't we just hire someone" and "show us that and we will hire you" frame
the target as a *hire*, not a utility: something that arrives, works out what
the app does on its own, and keeps working after the app changes.

## Constraints

- Duration: TBD
- Team: 3 people
- Submission format: TBD
- Target app: given as URL + credentials. **Unknown whether we get it up front
  or only at judging time** — see BET in `product/bets.md`, this changes the
  architecture.
- AWS preferred partner is stated. Whether AWS usage (Bedrock etc.) is scored
  is unknown.

## Judging criteria

_Not published in the video. Confirm with organisers before writing the demo
script — everything downstream depends on it._

Working assumption until corrected: autonomy (how little we type), coverage of
the app, healing quality under real change, and demo credibility.

## Deliberately unknown

- Is the target app known in advance, or handed over live?
- Is the app behind a normal HTML login, or SSO / MFA / captcha?
- Is it a SPA, server-rendered, or mixed?
- Must the output be runnable Playwright files the sponsors can keep, or is a
  running agent enough?
- Is there a time limit on a single agent run during judging?
