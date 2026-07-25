"""Unit tests for the pure logic in skills/_lib/cert_drift.py (fleet-config#210).

No live gh — these exercise the classify truth table (the no-false-positive
guarantees), the signal predicates, and gather_signals over synthetic temp
trees (the walk fallback, so no git init needed). The drift/clean trees here ARE
the acceptance cases: a tailnet self-signed-only app trips it; a LAN-only app and
an already-migrated app come back clean.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_cert_drift.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import cert_drift as cd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- classify truth table ----

check(cd.classify(True, True, False)[0] == "drift",
      "tailnet + self-signed + no ts-cert -> drift")
check(cd.classify(False, True, False)[0] == "clean",
      "LAN-only (no tailnet) self-signed -> clean (acceptance: no false positive)")
check(cd.classify(True, True, True)[0] == "clean",
      "migrated (ts-cert present) -> clean even with lingering self-signed (acceptance: grocery)")
check(cd.classify(True, False, False)[0] == "clean",
      "no self-signed provisioning at all -> clean (nothing to migrate)")
check(cd.classify(False, False, False)[0] == "clean",
      "a bare non-web repo -> clean")
# the reason strings are distinct per branch (distinct messages for distinct causes)
reasons = {
    cd.classify(True, True, False)[1],
    cd.classify(False, True, False)[1],
    cd.classify(True, True, True)[1],
    cd.classify(True, False, False)[1],
}
check(len(reasons) == 4, "each verdict branch has a distinct reason")

# ---- opt-out precedence (fleet-config#418) ----

check(cd.classify(True, True, False, opted_out=True)[0] == "clean",
      "opt-out -> clean even though the signals alone would be drift")
check("triaged non-adopter opt-out" in cd.classify(True, True, False, opted_out=True)[1],
      "opt-out reason names itself as a triaged non-adopter opt-out")
check("loopback SANs" in cd.classify(True, True, False, opted_out=True, opt_out_reason="loopback SANs")[1],
      "opt-out reason string is folded into the verdict reason")
check(cd.classify(False, False, False, opted_out=True)[0] == "clean",
      "opt-out is clean regardless of any underlying signal combination")


# ---- content signal predicates ----

check(cd.has_tailnet_signal("Reach it at https://myapp.tail1234.ts.net/"), "ts.net URL -> tailnet")
check(cd.has_tailnet_signal("tailscale serve https / 8443 / http://127.0.0.1:8000"), "tailscale serve -> tailnet")
check(cd.has_tailnet_signal("tailscale funnel 443 on"), "tailscale funnel -> tailnet")
check(cd.has_tailnet_signal("Bind to --host 100.101.102.103 on the tailnet"), "tailnet CGNAT IP -> tailnet")
check(not cd.has_tailnet_signal("Served over Tailscale on the home tailnet"),
      "bare Tailscale word, no host/IP/command -> NOT tailnet (fleet-config#418, was a false positive)")
check(not cd.has_tailnet_signal(
    "a dropped Wi-Fi/Tailscale handoff or a browser tab closed mid-handshake surfaces as WinError 64"),
    "Tailscale named as an incidental troubleshooting-table cause -> NOT tailnet (the voice-transcriber#151 false positive)")
check(not cd.has_tailnet_signal("Runs on http://127.0.0.1:8000 on the LAN"), "LAN-only text -> no tailnet")
check(not cd.has_tailnet_signal("Uses 192.168.1.50 on the home LAN"), "non-tailnet private IP -> no tailnet")

check(cd.has_install_ca('@app.get("/install-ca")'), "install-ca route -> self-signed")
check(cd.has_install_ca("return FileResponse('trust.mobileconfig')"), "mobileconfig -> self-signed")
check(not cd.has_install_ca("def install_dependencies(): pass"), "unrelated install_ -> no match")

check(cd.has_tailscale_cert_cmd("subprocess.run(['tailscale', 'cert', host])  # noqa"), "tailscale cert in argv -> cmd")
check(cd.has_tailscale_cert_cmd("tailscale cert myapp.tail1234.ts.net"), "tailscale cert CLI -> cmd")
check(not cd.has_tailscale_cert_cmd("# uses tailscale for access"), "tailscale w/o cert -> no cmd")


# ---- filename predicates ----

check(cd.is_self_signed_cert_script("gen_ssl_cert.py"), "gen_ssl_cert.py -> self-signed script")
check(cd.is_self_signed_cert_script("make_ssl_cert.py"), "make_ssl_cert.py -> self-signed script")
check(not cd.is_self_signed_cert_script("gen_tailscale_cert.py"), "tailscale cert script is NOT self-signed")
check(not cd.is_self_signed_cert_script("server.py"), "ordinary file -> not self-signed script")

check(cd.is_tailscale_cert_script("gen_tailscale_cert.py"), "gen_tailscale_cert.py -> ts-cert script")
check(not cd.is_tailscale_cert_script("gen_ssl_cert.py"), "ssl cert script is NOT a ts-cert script")


# ---- gather_signals over synthetic trees (walk fallback, no git) ----

def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="cert_drift_"))
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# a tailnet-reachable app still on the self-signed dance, no tailscale-cert -> DRIFT
drift_tree = make_tree({
    "README.md": "# MyApp\n\nReach it at https://myapp.tail1234.ts.net/ from your phone.\n",
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\nprint('cert')\n",
    "app/webapp/server.py": '@app.get("/install-ca")\ndef install_ca(): ...\n',
})
# an already-migrated app: serves over tailscale AND has the ts-cert provisioner -> CLEAN
migrated_tree = make_tree({
    "README.md": "# Grocery\n\nServed over Tailscale at https://grocery.tail1234.ts.net/.\n",
    "scripts/gen_tailscale_cert.py": "# tailscale cert (--check auto-renew)\nimport subprocess\n",
    "webapp.bat": "tailscale cert grocery.tail1234.ts.net\n",
    "scripts/gen_ssl_cert.py": "# legacy self-signed, kept around\n",
})
# a genuinely LAN-only app: self-signed, NO tailnet signal -> CLEAN (no false positive)
lan_tree = make_tree({
    "README.md": "# LanApp\n\nRuns on http://127.0.0.1:8443 on the home LAN only.\n",
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\n",
    "app/server.py": '@app.get("/install-ca")\ndef install_ca(): ...\n',
})

# a tailnet app whose *real* exposure evidence is documented under docs/ (not
# the README) — must still register the tailnet signal from that location.
docs_tree = make_tree({
    "README.md": "# Voice\n\nLocal transcription web app.\n",
    "docs/webapp-architecture.md": "Reachable at https://voice.tail1234.ts.net/ from your phone.\n",
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\n",
})

# the actual fleet-config#418 false positive: Tailscale named only as an
# incidental cause in a troubleshooting-table row, no real exposure evidence
# anywhere -> must report CLEAN, not drift.
false_positive_tree = make_tree({
    "README.md": (
        "# Voice\n\n"
        "| Symptom | Cause |\n"
        "|---|---|\n"
        "| Webapp goes completely unresponsive | a dropped Wi-Fi/Tailscale handoff "
        "or a browser tab closed mid-handshake surfaces as WinError 64 |\n"
    ),
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\n",
})

# a repo that would otherwise be drift, but has triaged and declared the
# non-adopter opt-out in its own .fleet.toml -> must report CLEAN.
optout_tree = make_tree({
    "README.md": "# Voice\n\nReachable at https://voice.tail1234.ts.net/ from your phone.\n",
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\n",
    ".fleet.toml": (
        "layer = \"enabling\"\n"
        "icon = \"\U0001F399\"\n"
        "description = \"Voice.\"\n\n"
        "[cert]\n"
        "not_applicable = true\n"
        "reason = \"tailscale cert cannot serve this app's loopback SANs\"\n"
        "disproof = \"https://github.com/ferraroroberto/voice-transcriber/issues/151\"\n"
    ),
})

try:
    d = cd.gather_signals(drift_tree)
    check(d["tailnet"]["present"] and d["self_signed"]["present"] and not d["ts_cert"]["present"],
          "drift tree: tailnet + self-signed signals, no ts-cert")
    check(cd.classify(True, True, False)[0] == "drift" and
          str(d["self_signed"]["evidence"]).startswith("scripts/gen_ssl_cert.py"),
          "drift tree: self-signed evidence points at the provisioner")
    check(str(d["tailnet"]["evidence"]).startswith("README.md:"),
          "drift tree: tailnet evidence is a README line ref")

    m = cd.gather_signals(migrated_tree)
    check(m["ts_cert"]["present"], "migrated tree: ts-cert provisioner detected")
    check(cd.classify(bool(m["tailnet"]["present"]), bool(m["self_signed"]["present"]),
                      bool(m["ts_cert"]["present"]))[0] == "clean",
          "migrated tree -> clean (acceptance: grocery-shopping-automation)")

    l = cd.gather_signals(lan_tree)
    check(not l["tailnet"]["present"] and l["self_signed"]["present"],
          "lan tree: self-signed present, NO tailnet signal")
    check(cd.classify(bool(l["tailnet"]["present"]), bool(l["self_signed"]["present"]),
                      bool(l["ts_cert"]["present"]))[0] == "clean",
          "lan-only tree -> clean (acceptance: no false positive)")

    dv = cd.gather_signals(docs_tree)
    check(dv["tailnet"]["present"] and str(dv["tailnet"]["evidence"]).startswith("docs/"),
          "docs tree: tailnet signal found under docs/ (voice-transcriber shape)")
    check(cd.classify(bool(dv["tailnet"]["present"]), bool(dv["self_signed"]["present"]),
                      bool(dv["ts_cert"]["present"]))[0] == "drift",
          "docs-documented tailnet app -> drift (would be missed by README-only scan)")

    fp = cd.gather_signals(false_positive_tree)
    check(not fp["tailnet"]["present"],
          "false-positive tree: incidental troubleshooting-table mention does NOT register as tailnet")
    check(cd.classify(bool(fp["tailnet"]["present"]), bool(fp["self_signed"]["present"]),
                      bool(fp["ts_cert"]["present"]))[0] == "clean",
          "the actual fleet-config#418 shape -> clean, no longer refiled (voice-transcriber#151)")

    optout = cd.read_cert_optout(optout_tree)
    check(optout is not None and "loopback SANs" in optout["reason"],
          "optout tree: .fleet.toml [cert] not_applicable table is read back")
    check(optout is not None and "voice-transcriber/issues/151" in optout["disproof"],
          "optout tree: disproof URL is read back")
    ov = cd.gather_signals(optout_tree)
    check(bool(ov["tailnet"]["present"]) and bool(ov["self_signed"]["present"]),
          "optout tree: signals alone would classify as drift (ts.net URL + self-signed, no ts-cert)")
    check(cd.classify(bool(ov["tailnet"]["present"]), bool(ov["self_signed"]["present"]),
                      bool(ov["ts_cert"]["present"]),
                      opted_out=optout is not None,
                      opt_out_reason=cd._format_optout_reason(optout) if optout else "")[0] == "clean",
          "optout tree: the .fleet.toml opt-out overrides otherwise-drift signals -> clean")

    check(cd.read_cert_optout(drift_tree) is None,
          "a repo with no .fleet.toml [cert] table -> no opt-out, falls back to signal classification")
finally:
    for t in (drift_tree, migrated_tree, lan_tree, docs_tree, false_positive_tree, optout_tree):
        shutil.rmtree(t, ignore_errors=True)


_h.report_and_exit("cert_drift")
