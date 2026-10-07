# Doubting your own filings: the #633 incident and checklist

Loaded on demand from the `/chief` skill (`SKILL.md`, "Verify before you trust a worker's report"), which keeps the rule itself.

**Doubt your own filings hardest — re-test the premise, not the conclusion
(fleet-config#633).** A table-heavy defect you filed against
`/cleanup-fleet-all`'s step-5 state gate — named root cause, derived hours
wasted — rested entirely on one unchecked unit conversion: GitHub's UTC
`closedAt` read as local time (the clock rule lives in `global-CLAUDE.md`'s
recurring gotchas; elapsed-vs-wall-clock job logs are item 1 of
[lane-silence.md](lane-silence.md)). That run's *own* lanes had closed both
issues, hours **after** the gate ran. Every later check re-confirmed the
**conclusion** and never the **premise**: re-running `issue_state_gate.py
check` by hand returned `closed`, true *by then* and silent about what the
gate could see *back then*. Before filing any defect against fleet tooling:

- **Write the premise as one sentence and test that sentence alone.** Here it
  was "these two issues were already closed at `11:53Z`" — one `gh` query
  from disproof, and never asked, because it looked too obvious to check.
- **Reconstruct what the tool could observe at time T**, not what it returns
  now. A tool re-run today is not a witness to yesterday.
- **Treat a confident, table-heavy draft as a warning sign, not a finish
  line.** Presentation quality is not evidence quality, least of all in your
  own filings — #633 read as rigorous *precisely* while being wrong, and that
  rigour carried it into the handover log and onward to Roberto as a real
  defect.

A claim that survives all three is a defect worth filing. One that cannot say
what it re-tested is a hypothesis — file it as a question, or don't file it.
