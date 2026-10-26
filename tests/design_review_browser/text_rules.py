import json
import subprocess

from _suite import REPO, STATE, _walk_failure, _walk_run, check, interp, scaffold
import walk
from design_review import evaluate as ev
from design_review_support import FIX, L10_MISSING as _L10_MISSING, c18_of as _c18_of, doc as _doc, rubric, specs as _specs


# label fit and container overflow at the 390px phone leg: the camera dialog that scored clean while broken (#1325)
lf = {}
for _kind in ("broken", "fixed"):
    lf_dir = STATE / f"fixture-label-fit-{_kind}"
    proc_lf = subprocess.run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / f"label_fit_{_kind}.html").as_uri(),
         "--out", str(lf_dir), "--devices", "iphone", "--scaffold", str(scaffold),
         "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    lf_screens = json.loads((lf_dir / "screens.json").read_text(encoding="utf-8")) if proc_lf.returncode == 0 else []
    lf[_kind] = next((s for s in lf_screens if s["id"] == "iphone-light-root"), {})
    lf[_kind + "_err"] = proc_lf.stderr[-300:]
_lf_b, _lf_f = (lf[k].get("metrics") or {} for k in ("broken", "fixed"))
check(_lf_b.get("layout", {}).get("inner_w") == 390 and _lf_f.get("layout", {}).get("inner_w") == 390,
      f"the iphone leg is 390px wide (#1325) -- {_lf_b.get('layout', {}).get('inner_w')} ({lf['broken_err']})")
_lf_bad = _lf_b.get("controls", {}).get("label_fit_bad", [])
check([(b["label"], b["lines"] > 1, b["spill_px"] > 1.5) for b in _lf_bad] == [("Zoom in", True, False), ("Screenshot", False, True)],
      f"COMP-07 before: the wrapped Zoom in and the overrun Screenshot are found -- {_lf_bad}")
_lf_over = _lf_b.get("layout", {}).get("control_overflow", [])
check([(o["label"], o["container"], o["side"]) for o in _lf_over] == [("Record clip", "div.card", "right")]
      and _lf_b["layout"]["overflow_x"] is False,
      f"LAYOUT-08 before: Record clip leaves the card on the right while the page does not scroll sideways (LAYOUT-01 green) -- {_lf_over}")
check(_lf_f.get("controls", {}).get("label_fit_bad_count") == 0 and _lf_f.get("layout", {}).get("control_overflow_count") == 0,
      f"COMP-07 + LAYOUT-08 after: the fixed dialog passes, and a prose button, an ellipsis label and a self-scrolling chip strip are not findings "
      f"-- {_lf_f.get('controls', {}).get('label_fit_bad')} {_lf_f.get('layout', {}).get('control_overflow')}")

# LAYOUT-09: a step line's ellipsis must not cut its counts at the 390px leg (#1343)
def _walk_clipped(case: str) -> dict:
    out_dir = STATE / f"fixture-clipped-trailing-{case}"
    proc = _walk_run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "clipped_trailing.html").as_uri() + f"?case={case}",
         "--out", str(out_dir), "--devices", "iphone", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-300:]}
    return next((s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s["id"] == "iphone-light-root"), {})

def _l09_status(screen: dict) -> str:
    d = _doc("compliant")
    d["screens"] = [dict(d["screens"][1], id=screen["id"], view=screen.get("view"), metrics=screen["metrics"])]
    return next(r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] == "LAYOUT-09")["status"]

ct = {case: _walk_clipped(case) for case in ("fail", "pass", "edge", "prose", "status")}
_ct_bad = {case: _walk_failure(s) for case, s in ct.items() if _walk_failure(s)}
check(not _ct_bad, f"clipped_trailing.html: every case walks the 390px leg with metrics -- {_ct_bad}")
if not _ct_bad:
    _ct_hits = lambda s: [(h["text"][:12], h["why"]) for h in (s["metrics"]["layout"].get("clipped_trailing") or [])]
    check(_ct_hits(ct["fail"]) == [("Ran 2 comman", "cuts failed 1m30s"), ("Ran 2 comman", "cuts 1m30s")] and _l09_status(ct["fail"]) == "fail",
          f"LAYOUT-09: 'Ran 2 commands, read 3 files +128 -46 2 failed 1m30s' in one ellipsis element, and a label ending in a duration, fail -- {_ct_hits(ct['fail'])}")
    check(_ct_hits(ct["pass"]) == [] and _l09_status(ct["pass"]) == "pass",
          f"LAYOUT-09: the same counts as non-shrinking trailing items pass, a label clipped on words only passes, a fitting label passes -- {_ct_hits(ct['pass'])}")
    check(_ct_hits(ct["edge"]) == [] and _l09_status(ct["edge"]) == "pass",
          f"LAYOUT-09: a cut file name and date, a cut 'failed' with no number before it, a cut tail of words, a wrapping line, a line clamp and a hidden row are not cut figures -- {_ct_hits(ct['edge'])}")
    # narrowed to status and meta text (#1438): a figure inside a run of words a person typed is not a finding, and what is let
    # pass is kept as a capped sample so the exclusion can be reviewed
    _ct_lay = lambda s: (s.get("metrics") or {}).get("layout") or {}
    _ct_excl = lambda s: _ct_lay(s).get("clipped_trailing_excluded") or []
    check(_ct_hits(ct["prose"]) == [] and _l09_status(ct["prose"]) == "pass",
          f"LAYOUT-09: a house number cut from an address line, a version string and a clock time cut from a free-text title pass -- {_ct_hits(ct['prose'])}")
    check(_ct_lay(ct["prose"]).get("clipped_trailing_excluded_count") == 3 and len(_ct_excl(ct["prose"])) == 3
          and [fig in e["why"] for fig, e in zip(("14", "1.38.0", "14:30"), _ct_excl(ct["prose"]))] == [True] * 3
          and all(e["why"].endswith("in prose") and e["sel"] and e["text"] for e in _ct_excl(ct["prose"])),
          f"LAYOUT-09: the three prose rows are kept in the excluded sample with the figure each cut -- {_ct_excl(ct['prose'])}")
    check(len(_ct_hits(ct["status"])) == 3 and _l09_status(ct["status"]) == "fail"
          and "16:00" in _ct_hits(ct["status"])[0][1] and all("2m10s" in h[1] for h in _ct_hits(ct["status"])[1:]),
          f"LAYOUT-09: a .meta slot, a status-row dd and an unmarked status line still fail on the figure they cut -- {_ct_hits(ct['status'])}")
    check(all(_ct_excl(ct[c]) == [] and _ct_lay(ct[c]).get("clipped_trailing_excluded_count") == 0 for c in ("fail", "status")),
          "LAYOUT-09: a row that counts is never also listed as excluded")

# COMP-18: a turn is one block under one header (#1344)
def _walk_turn(case: str) -> dict:
    out_dir = STATE / f"fixture-repeated-header-{case}"
    proc = _walk_run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "repeated_header.html").as_uri() + f"?case={case}",
         "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-300:]}
    return next((s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s["id"].endswith("-light-root")), {})

def _c18_status(screen: dict) -> str:
    d = _doc("compliant")
    d["screens"] = [dict(d["screens"][0], id=screen["id"], view=screen.get("view"), metrics=screen["metrics"])]
    return _c18_of(d)["status"]

tn = {case: _walk_turn(case) for case in ("fail", "pass", "list", "edge", "none")}
_tn_bad = {case: _walk_failure(s) for case, s in tn.items() if _walk_failure(s)}
check(not _tn_bad, f"repeated_header.html: every case walks the desktop leg with metrics -- {_tn_bad}")
if not _tn_bad:
    _tn_runs = lambda s: [(h["who"], h["blocks"], h["text"]) for h in (s["metrics"]["layout"].get("repeated_headers") or [])]
    _tn_whos = lambda s: sorted((h["who"], h["count"]) for h in (s["metrics"]["layout"].get("turn_headers") or []))
    check(_tn_runs(tn["fail"]) == [("Agent", 3, "Agent · <time>"), ("Claude", 2, "Claude · <time>")] and _c18_status(tn["fail"]) == "fail",
          f"COMP-18: three consecutive 'Agent 10:42/10:42/10:43' disclosures and two plain 'Claude 9:05 AM' cards each fail as one run -- {_tn_runs(tn['fail'])}")
    check(_tn_runs(tn["pass"]) == [] and _tn_whos(tn["pass"]) == [("Agent", 1)] and _c18_status(tn["pass"]) == "pass",
          f"COMP-18: one 'Agent 10:42' header over three parts passes and counts as a transcript reached -- {_tn_runs(tn['pass'])} {_tn_whos(tn['pass'])}")
    check(_tn_runs(tn["list"]) == [] and _tn_whos(tn["list"]) == [],
          f"COMP-18: a list of rows that each start 'Today' and event cards that each read 'Standup 09:00' are not turns -- {_tn_runs(tn['list'])} {_tn_whos(tn['list'])}")
    check(_tn_runs(tn["edge"]) == [] and _c18_status(tn["edge"]) == "pass" and ("You", 2) in _tn_whos(tn["edge"]),
          f"COMP-18: alternating speakers, a differing hint, a header with no control, a header-only card, a step line between two cards and a hidden twin are not runs -- {_tn_runs(tn['edge'])} {_tn_whos(tn['edge'])}")
    check(_tn_runs(tn["none"]) == [] and _tn_whos(tn["none"]) == [] and _c18_status(tn["none"]) == "unmeasured",
          f"COMP-18: a page with no transcript is unmeasured, not a pass -- {_tn_whos(tn['none'])}")

# LAYOUT-10: a glance card that grows as its late reads land (#1354). Served over loopback: `/late?ms=N` answers after N ms
# (capped at 120 s, past every bound the walk has), so the page's reads are real requests the walk's idle wait can see.
import functools as _gl_functools, http.server as _gl_http, threading as _gl_threading, time as _gl_time, urllib.parse as _gl_parse  # noqa: E402

class _LateReads(_gl_http.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401 -- keep the test output clean
        pass

    def do_GET(self):  # noqa: N802 -- http.server's name
        url = _gl_parse.urlparse(self.path)
        if url.path != "/late":
            return super().do_GET()
        _gl_time.sleep(min(int((_gl_parse.parse_qs(url.query).get("ms") or ["0"])[0]), 120000) / 1000)
        try:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:  # the walk closed the page mid-read (the hang case)
            pass

_gl_srv = _gl_http.ThreadingHTTPServer(("127.0.0.1", 0), _gl_functools.partial(_LateReads, directory=str(FIX)))
_gl_threading.Thread(target=_gl_srv.serve_forever, daemon=True).start()

def _walk_glance(case: str, device: str = "desktop") -> dict:
    out_dir = STATE / f"fixture-glance-{case}-{device}"
    t0 = _gl_time.time()
    proc = _walk_run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"),
         "--url", f"http://127.0.0.1:{_gl_srv.server_address[1]}/glance_shift.html?case={case}",
         "--out", str(out_dir), "--devices", device, "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-300:]}
    tabs = [s for s in json.loads((out_dir / "screens.json").read_text(encoding="utf-8")) if s.get("kind") == "tab"]
    return {"tabs": tabs, "seconds": round(_gl_time.time() - t0, 1)}

def _l10_on(walked: dict) -> dict:
    return next((r for r in ev.evaluate({"screens": walked["tabs"], "wide_views": []}, rubric, _specs("compliant"))["rules"]
                 if r["id"] == "LAYOUT-10"), _L10_MISSING)

gl = {case: _walk_glance(case) for case in ("fail", "pass", "shrink", "animate", "cols", "last", "none", "hang")}
gl["fail-iphone"] = _walk_glance("fail", "iphone")
_gl_srv.shutdown()
_gl_bad = {case: (w["error"] if "error" in w else _walk_failure((w["tabs"] or [None])[0]) if len(w["tabs"]) != 4
                  else next((f for f in map(_walk_failure, w["tabs"]) if f), None))
           for case, w in gl.items()}
_gl_bad = {case: why for case, why in _gl_bad.items() if why}
check(not _gl_bad, f"glance_shift.html: every case walks both tabs in light and dark with metrics -- {_gl_bad}")
if not _gl_bad:
    _gl = lambda case: {s["id"]: s["metrics"].get("glance") or {} for s in gl[case]["tabs"]}
    _gl_grew = lambda case: {sid: g.get("grew_px") for sid, g in _gl(case).items()}
    _gl_fail = _gl("fail")
    check(all(g.get("state") == "measured" and g.get("below") is True and g.get("grew_px", 0) >= 132 for g in _gl_fail.values())
          and _l10_on(gl["fail"])["status"] == "fail" and len(_l10_on(gl["fail"])["measured"]) == 4,
          f"LAYOUT-10: three rows appended after the read fail on both tabs, light and dark -- {_gl_grew('fail')}")
    _gl_today = _gl_fail.get("desktop-light-today") or {}
    check(_gl_today.get("first_px", 999) < 100 and _gl_today.get("change_count", 0) >= 1
          and all(c.get("ms", 0) >= 4500 for c in _gl_today.get("changes") or [{}]),
          f"LAYOUT-10: Today's 5 s read lands after the walk's fixed settle; only the idle wait sees the card grow at ~5 s -- {_gl_today}")
    check(all(g.get("grew_px", 0) >= 132 for g in _gl("fail-iphone").values()) and _l10_on(gl["fail-iphone"])["status"] == "fail",
          f"LAYOUT-10: the same growth is read in WebKit at the phone width -- {_gl_grew('fail-iphone')}")
    check(all(g.get("state") == "measured" and g.get("grew_px") == 0 for g in _gl("pass").values()) and _l10_on(gl["pass"])["status"] == "pass",
          f"LAYOUT-10: every row drawn at first paint with a dash and a spinning glyph, filled in place, passes -- {_gl_grew('pass')}")
    check(all(g.get("grew_px", 0) < 0 for g in _gl("shrink").values()) and _l10_on(gl["shrink"])["status"] == "pass",
          f"LAYOUT-10: a 'Reading...' block replaced by one shorter row only gets shorter and passes -- {_gl_grew('shrink')}")
    check(all(g.get("state") == "measured" and g.get("grew_px") == 0 and g.get("first_px", 0) > 100 for g in _gl("animate").values())
          and _l10_on(gl["animate"])["status"] == "pass",
          f"LAYOUT-10: a max-height entrance and a scale-in entrance are not growth: the baseline waits for them -- {_gl('animate')}")
    check(all(g.get("state") == "measured" and g.get("grew_px", 0) >= 132 for g in _gl("cols").values()) and _l10_on(gl["cols"])["status"] == "fail",
          f"LAYOUT-10: a card in the left column of a two-column pane is still the first card, and its growth fails -- {_gl('cols')}")
    _gl_last = _l10_on(gl["last"])
    check(all(g.get("below") is False and g.get("grew_px", 0) >= 132 for g in _gl("last").values())
          and _gl_last["status"] == "pass" and "nothing under the card" in _gl_last["reason"],
          f"LAYOUT-10: the same growth with nothing under the card moves nothing: n/a -- {_gl_last['reason']}")
    _gl_none = _l10_on(gl["none"])
    check(all(g.get("state") == "no_card" for g in _gl("none").values()) and _gl_none["status"] == "pass"
          and "no card under the page header" in _gl_none["reason"],
          f"LAYOUT-10: a tab with only a paragraph under its header is n/a -- {_gl_none['reason']}")
    _gl_hang = _l10_on(gl["hang"])
    check(all(g.get("state") == "not_idle" and g.get("idle") is False and g.get("pending") == ["/late"] for g in _gl("hang").values())
          and _gl_hang["status"] == "unmeasured" and "never went quiet" in _gl_hang["reason"],
          f"LAYOUT-10: a read that never lands is unmeasured, naming the request, never a pass -- {_gl_hang['reason']}")
    check(gl["hang"]["seconds"] < gl["pass"]["seconds"] + 4 * getattr(walk, "IDLE_WAIT_MS", 10000) / 1000 + 30,
          f"LAYOUT-10: the idle wait is bounded, so a page that never goes quiet does not hang the walk -- "
          f"{gl['hang']['seconds']}s against {gl['pass']['seconds']}s")

# stretched graphic: a chart SVG with preserveAspectRatio="none" is not an icon; a real off-step icon still is (#1211)
sg_dir = STATE / "fixture-stretched-graphic"
proc_sg = subprocess.run(
    [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "stretched_graphic.html").as_uri(),
     "--out", str(sg_dir), "--devices", "desktop", "--scaffold", str(scaffold),
     "--params", str(STATE / "steps-params.json")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
sgs = json.loads((sg_dir / "screens.json").read_text(encoding="utf-8")) if proc_sg.returncode == 0 else []
_sg_icons = ((sgs[0].get("metrics") or {}).get("icons") or {}) if sgs else {}
check(_sg_icons.get("boxes") == {"22x22": 1, "20x20": 1},
      f"COMP-02: the two stretched 162x64 chart SVGs are not icons; the 22px and 20px icons still are (#1211) -- {_sg_icons} ({proc_sg.stderr[-300:]})")

# pane_split: two tall regions side by side inside one pane count; a lone column and a row of short cards do not (#1361)
ps_dir = STATE / "fixture-pane-split"
proc_ps = subprocess.run(
    [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "pane_split.html").as_uri(),
     "--out", str(ps_dir), "--devices", "desktop", "--scaffold", str(scaffold),
     "--params", str(STATE / "steps-params.json")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
pss = {s["id"]: s for s in json.loads((ps_dir / "screens.json").read_text(encoding="utf-8"))} if proc_ps.returncode == 0 else {}
_ps = {k: ((pss.get(f"desktop-light-{k}") or {}).get("metrics") or {}).get("layout", {}).get("pane_split") for k in ("split", "column", "cards")}
check(_ps == {"split": True, "column": False, "cards": False},
      f"LAYOUT-06: pane_split is true for a month + day pane inside one pane, false for a lone column and for a row of short cards (#1361) "
      f"-- {_ps} ({proc_ps.stderr[-300:]})")

# COMP-08: a destructive control in a dialog or menu is unfilled and not the largest labelled control there (#1364)
do_dir = STATE / "fixture-destructive-overlay"
proc_do = subprocess.run(
    [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "destructive_overlay.html").as_uri(),
     "--out", str(do_dir), "--devices", "desktop", "--scaffold", str(scaffold),
     "--params", str(STATE / "steps-params.json")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
dos = {s["id"]: s for s in json.loads((do_dir / "screens.json").read_text(encoding="utf-8"))} if proc_do.returncode == 0 else {}

def _overlay(view: str) -> dict:
    return ((dos.get(f"desktop-light-{view}") or {}).get("metrics") or {}).get("overlay") or {}

_do_bad = {v: (_overlay(v).get("destructive_bad_count"), [d.get("why") for d in _overlay(v).get("destructive_bad", [])])
           for v in ("menu", "menubad", "dialog-okdialog", "dialog-filldialog", "dialog-tintdialog", "dialog-bigdialog", "dialog-confirmdialog")}
check(_do_bad == {"menu": (0, []), "menubad": (1, ["filled"]), "dialog-okdialog": (0, []),
                  "dialog-filldialog": (1, ["filled"]), "dialog-tintdialog": (1, ["filled"]), "dialog-bigdialog": (1, ["largest"]),
                  "dialog-confirmdialog": (1, ["filled"])},
      f"COMP-08: a filled Delete (solid or tinted) or one larger than Redo fails; an unfilled last menu Delete and an outlined one "
      f"as wide as Copy pass, and the tinted Delete of a confirm dialog that holds only Cancel beside it fails like any other (#1403) -- {_do_bad} ({proc_do.stderr[-300:]})")
check(all(_overlay(v).get("destructive") for v in ("menu", "dialog-okdialog")) and _overlay("menu").get("surfaces", 0) >= 1,
      f"COMP-08: the passing menu and dialog still report the destructive control they measured -- {_overlay('menu')}")

# COMP-16: a dialog's commit (its submit or primary button) is not quieter than another control on its row or below (#1339)
qc_dir = STATE / "fixture-quiet-commit"
proc_qc = subprocess.run(
    [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "quiet_commit.html").as_uri(),
     "--out", str(qc_dir), "--devices", "desktop", "--scaffold", str(scaffold),
     "--params", str(STATE / "steps-params.json")],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
qcs = {s["id"]: s for s in json.loads((qc_dir / "screens.json").read_text(encoding="utf-8"))} if proc_qc.returncode == 0 else {}

def _dialogs(view: str) -> dict:
    return ((qcs.get(f"desktop-light-{view}") or {}).get("metrics") or {}).get("dialogs") or {}

_qc_views = ("okdialog", "ghostdialog", "filledpair", "equaldialog", "smalldialog", "bodydialog", "nocommitdialog")
_qc = {v: (_dialogs(f"dialog-{v}").get("quiet_commit_count"), [q.get("why") for q in _dialogs(f"dialog-{v}").get("quiet_commit", [])])
       for v in _qc_views}
check(_qc == {"okdialog": (0, []), "ghostdialog": (1, ["filled"]), "filledpair": (0, []), "equaldialog": (0, []),
              "smalldialog": (1, ["larger"]), "bodydialog": (0, []), "nocommitdialog": (0, [])},
      f"COMP-16: a ghost Save beside a filled Delete, and a narrow ghost Save beside a wider Cancel, fail; a filled full-width Save alone, "
      f"a filled Save beside an outlined Delete, equal ghosts, a Delete in the body above Save and a dialog with no commit pass -- {_qc} ({proc_qc.stderr[-300:]})")
check(_dialogs("dialog-okdialog").get("with_commit") == 1 and _dialogs("dialog-nocommitdialog").get("with_commit") == 0
      and _dialogs("dialog-ghostdialog").get("count") == 1,
      f"COMP-16: a dialog with a commit is counted as measured, one without is not -- {_dialogs('dialog-okdialog')} {_dialogs('dialog-nocommitdialog')}")

# TYPE-05 / TYPE-06: capitals typed in the markup, and one class of label cased two ways across the tabs (#1365)
def _walk_casing(case: str) -> list:
    out_dir = STATE / f"fixture-casing-{case}"
    proc = _walk_run(
        [str(interp), str(REPO / "skills" / "_lib" / "design_review" / "walk.py"), "--url", (FIX / "casing.html").as_uri() + f"?case={case}",
         "--out", str(out_dir), "--devices", "desktop", "--scaffold", str(scaffold), "--params", str(STATE / "steps-params.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if proc.returncode != 0:
        return [{"error": proc.stderr[-300:]}]
    walked = json.loads((out_dir / "screens.json").read_text(encoding="utf-8"))
    return [s for s in walked if s["id"].startswith("desktop-light-") and s.get("status") == "ok"] or [{"error": f"no screen walked ok -- {[(s.get('id'), s.get('status'), s.get('error')) for s in walked]}"}]

def _casing_rules(screens: list) -> dict:
    d = _doc("compliant")
    d["screens"] = [dict(base, id=s["id"], view=s.get("view"), metrics=s["metrics"]) for base, s in zip(d["screens"], screens)]
    return {r["id"]: r for r in ev.evaluate(d, rubric, _specs("compliant"))["rules"] if r["id"] in ("TYPE-05", "TYPE-06")}

cs_ok, cs_caps, cs_mixed = _walk_casing("ok"), _walk_casing("caps"), _walk_casing("mixed")
_cs_all = cs_ok + cs_caps + cs_mixed
_cs_good = all(_walk_failure(s) is None for s in _cs_all) and len(cs_ok) == len(cs_caps) == len(cs_mixed) == 2
check(_cs_good, f"casing: each case walks its tabs with metrics -- {[_walk_failure(s) or s['id'] for s in _cs_all]}")
if _cs_good:
    _lit = [lab["text"] for s in cs_caps for lab in s["metrics"]["casing"]["labels"] if lab["literal"]]
    check(_lit == ["RECORD", "PDF", "ES", "EXPORT PDF", "SETTINGS"],
          f"casing: every label typed in capitals is recorded as literal, the identifier-shaped ACER-PC1 heading is not -- {_lit}")
    _r_caps, _r_ok, _r_mixed = _casing_rules(cs_caps), _casing_rules(cs_ok), _casing_rules(cs_mixed)
    _ok_texts = {lab["text"] for s in cs_ok for lab in s["metrics"]["casing"]["labels"]}
    check("Translate to English" in _ok_texts and not _ok_texts & {"Instructions View", "(no transcript) 0 words"},
          f"casing: a proper noun is recorded, a name-and-action row and a reading with a digit are not -- {sorted(_ok_texts)}")
    check(_r_caps["TYPE-05"]["status"] == "fail"
          and [i["text"] for e in _r_caps["TYPE-05"]["evidence"] for i in e["items"]] == ["RECORD", "EXPORT PDF", "SETTINGS"]
          and _r_caps["TYPE-06"]["status"] == "pass",
          f"TYPE-05: RECORD, a SETTINGS heading and EXPORT PDF fail; PDF and the ES chip pass -- {_r_caps['TYPE-05']['evidence']}")
    check(_r_ok["TYPE-05"]["status"] == "pass" and _r_ok["TYPE-06"]["status"] == "pass",
          f"TYPE-05 / TYPE-06: sentence case, an acronym button, an ES chip and list-row data cased either way pass -- "
          f"{ [(r['id'], r['status'], r['measured']) for r in _r_ok.values()] }")
    _clash = sorted(i["text"] for e in _r_mixed["TYPE-06"]["evidence"] for i in e["items"])
    check(_r_mixed["TYPE-06"]["status"] == "fail" and _clash == ["Open Settings", "Running"] and _r_mixed["TYPE-05"]["status"] == "pass",
          f"TYPE-06: the capitalised stat label and the Title Case button are the minority of their classes across the two tabs -- {_clash}")
