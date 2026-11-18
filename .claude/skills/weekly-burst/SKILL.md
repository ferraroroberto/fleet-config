---
name: weekly-burst
description: Run the weekly burst — the burn, load and wall-clock acceptance units, a fresh-clone hermetic re-run of the whole gate, and per-unit runtime medians. E.g. "/weekly-burst". No model; runs weekly unattended.
---

# weekly-burst

Run `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/weekly-burst/burst.py` from the repo root, in the foreground, and report its last lines. It takes as long as a full acceptance run, so never start it while another lane is gating, and never in the background of a turn you then end.

Exit `0` green, `1` a red unit (named in the output), `3` a leg that could not be established. Results and per-leg logs: `<hooks state dir>/weekly-burst/`. Design and unit list: [docs/skills/weekly-burst.md](E:/automation/fleet-config/docs/skills/weekly-burst.md).
