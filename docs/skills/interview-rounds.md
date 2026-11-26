# Interview rounds

The shared format for a skill that has to settle several owner decisions before it acts. `/issue-grill` is built on it; any skill that interviews the owner links here instead of restating it.

## Decisions, not facts

Ask the owner only for a **decision**: a choice between real options that the code, the docs and the issue history cannot settle. A **fact** (how the code behaves today, which file owns a thing, what a command prints, whether an issue already exists) is the agent's job: look it up, read-only, and never put it to the owner. When a lookup fails, say what is unknown and why; an unknown fact is not a decision to hand over.

## The decision tree

Before the first round, list every open decision and, for each one, the decisions it depends on. A decision is **ready** when everything it depends on is settled. A question whose answer would change after another answer lands waits for a later round.

## One round

Each round asks **every** ready decision at once, numbered from 1 within the round:

```
Round 2 — 3 decisions

1. <Decision, one line>. Recommended: <answer>. <one-line why>
   Alternatives: <b>, <c>.
2. ...

Reply "yes" to accept every recommendation, or answer by number ("2: c").
```

- **Recommended answer rule.** Every question carries a recommended answer, phrased so a plain "yes" accepts it. The recommendation is the agent's real pick, grounded in what research found, not a neutral placeholder. Alternatives appear only when they are real options.
- A bare "yes" accepts every recommendation in the round. Answers by number override only the numbers given; the rest take their recommendation.
- An answer that reopens a settled decision updates the tree: any decision that depended on it goes back to open and is asked again in the next round.
- After each round, print the decisions settled so far in one short list, so the owner sees the state without scrolling.

## Ending

The interview ends when no decision is open. Show the result (the draft, the plan) and ask for one final OK before acting on it. With no human present (an unattended or scheduled run, or a question channel that is not callable and no reply), stop at the current round: nothing is decided by silence, and nothing is filed or changed.

## Asking

Use the session's question tool when one is callable and allowed in the current mode; otherwise print the round as plain text and wait for the reply ([workflow capabilities](../workflow-capabilities.md), user input row). A question tool that caps the number of questions per call takes the round in consecutive calls; the round is still one round.
