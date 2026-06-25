"""Unit tests for the pure logic in skills/_lib/cert_drift.py (fleet-config#210).

No live gh — these exercise the classify truth table (the no-false-positive
guarantees), the signal predicates, and gather_signals over synthetic temp
trees (the walk fallback, so no git init needed). The drift/clean trees here ARE
the acceptance cases: a tailnet self-signed-only app trips it; a LAN-only app and
an already-migrated app come back clean.

Run: `py tests/test_cert_drift.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import cert_drift as cd  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


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


# ---- content signal predicates ----

check(cd.has_tailnet_signal("Reach it at https://myapp.tail1234.ts.net/"), "ts.net URL -> tailnet")
check(cd.has_tailnet_signal("Served over Tailscale on the home tailnet"), "Tailscale word -> tailnet")
check(not cd.has_tailnet_signal("Runs on http://127.0.0.1:8000 on the LAN"), "LAN-only text -> no tailnet")

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

# a tailnet app whose Tailscale exposure is documented under docs/ (not the
# README) — the voice-transcriber shape. Must still register the tailnet signal.
docs_tree = make_tree({
    "README.md": "# Voice\n\nLocal transcription web app.\n",
    "docs/webapp-architecture.md": "Reach it at home over Tailscale on the tailnet.\n",
    "scripts/gen_ssl_cert.py": "# self-signed CA generator\n",
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
finally:
    for t in (drift_tree, migrated_tree, lan_tree, docs_tree):
        shutil.rmtree(t, ignore_errors=True)


if _fails:
    print(f"FAILED {len(_fails)} check(s):")
    for f in _fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("cert_drift: all pure-logic checks passed")
raise SystemExit(0)
