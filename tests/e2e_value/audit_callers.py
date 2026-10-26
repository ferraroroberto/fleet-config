from _suite import _norm, v
from parsing_and_measures import tmp
from parallel_and_budget import pt, tb
from slimming_lessons import sf, tt, wr


# ---- characterization pin: e2e_test_audit.py's caller paths into e2e_value (fleet-config#1247) ----------
# Every subcommand that reaches e2e_value without GitHub, on the fixture repos above; `routing` lists
# merged PRs through `gh`, so its report is pinned through `routing_report(pr_list=...)` above instead.
import contextlib  # noqa: E402
import io  # noqa: E402

import e2e_test_audit  # noqa: E402

v.parallel(pt, ["tests/e2e"])
for _argv in (["timing", str(tmp), "--suite-nodes", "3"], ["timing", str(tt), "--suite-nodes", "5"],
              ["failures", str(tmp), "--no-gh"], ["parallel", str(pt)], ["budget", str(tb)],
              ["scan", str(wr)], ["scan", str(sf)], ["record", str(tb)], ["budget", str(tb)]):
    _out = io.StringIO()
    with contextlib.redirect_stdout(_out):
        _code = e2e_test_audit.main(_argv)
    v.calls.append(["e2e_test_audit.main", _norm(_argv), _code, _norm(_out.getvalue())])
