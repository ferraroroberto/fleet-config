"""Tests for skills/_lib/fleet_password against temp fixture apps only.

Each fixture app is a temp folder with a `.fleet.toml` declaring the vendored
`password` component and a copy of the scaffold's `src/password.py`
(`tests/fixtures/fleet_password/password.py`), driven through the real child
setter with this interpreter. No live config is read or written.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/fleet_password/test_fleet_password.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from fleet_password import cli, core  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
SETTER = REPO / "tests" / "fixtures" / "fleet_password" / "password.py"
NEW_PW = "correct horse 4417 battery"
OLD_PW = "12345678"
DECLARED = '[vendored]\npassword = { src = "src/password.py", sha = "x", dest = "src/password.py" }\n'


def _spec():
    spec = importlib.util.spec_from_file_location("fixture_password", SETTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PW = _spec()


def _python(_repo: Path) -> Path:
    return Path(sys.executable)


def _app(root: Path, name: str, toml: str = DECLARED, setter: str = "") -> Path:
    app = root / name
    (app / "src").mkdir(parents=True)
    (app / ".git").mkdir()
    (app / ".fleet.toml").write_text(toml, encoding="utf-8")
    (app / "src" / "__init__.py").write_text("", encoding="utf-8")
    if setter:
        (app / "src" / "password.py").write_text(setter, encoding="utf-8")
    else:
        shutil.copy2(SETTER, app / "src" / "password.py")
    return app


def _hash_file(app: Path) -> Path:
    return app / "config" / "app_password.json"


def _run(argv, repos, backups, entries=(NEW_PW, NEW_PW), python_for=_python):
    out, err = io.StringIO(), io.StringIO()
    answers = iter(entries)
    with mock.patch("getpass.getpass", side_effect=lambda _prompt="": next(answers)) as prompt, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, repos=repos, backup_root=backups, python_for=python_for)
    return code, out.getvalue(), err.getvalue(), prompt.call_count


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    a = _app(root, "alpha")
    b = _app(root, "bravo")
    PW.set_password(OLD_PW, path=_hash_file(b))
    old_b = _hash_file(b).read_text(encoding="utf-8")
    _app(root, "plain", toml='[vendored]\nnav = { src = "x", sha = "x", dest = "x" }\n')
    wt = _app(root, "alpha-wt-9")
    shutil.rmtree(wt / ".git")
    (wt / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    repos = {p.name: p for p in root.iterdir()}
    backups = root / "_backups"

    # ---- discovery ----
    check(list(core.find_targets(repos)) == ["alpha", "bravo"],
          f"only declaring real checkouts are targets: {list(core.find_targets(repos))}")

    # ---- dry run: no prompt, no write ----
    code, out, err, prompts = _run(["--dry-run"], repos, backups)
    check(code == 0 and prompts == 0, f"dry run exits 0 without prompting: {code} {prompts}")
    check("WOULD-SET alpha (first hash" in out and "WOULD-SET bravo (replaces the stored hash)" in out,
          f"dry run names each app's change: {out!r}")
    check(not _hash_file(a).exists() and _hash_file(b).read_text(encoding="utf-8") == old_b,
          "dry run writes nothing")

    # ---- refusals before any app is touched ----
    code, _, err, _ = _run([], repos, backups, entries=(NEW_PW, "different"))
    check(code == 2 and "differ" in err and not _hash_file(a).exists(), "mismatched entries refuse the run")
    code, _, err, _ = _run([], repos, backups, entries=("", ""))
    check(code == 2 and "empty" in err and not _hash_file(a).exists(), "an empty password is refused")
    code, _, err, _ = _run(["--only", "nosuch"], repos, backups)
    check(code == 2 and "nosuch" in err, "--only with an unknown repo refuses")

    # ---- the real pass ----
    stray = root / "stray.json"
    with mock.patch.dict(os.environ, {core.OVERRIDE_ENV: str(stray)}):
        code, out, err, prompts = _run([], repos, backups)
    check(code == 0 and prompts == 2, f"real pass exits 0 after two prompts: {code} {prompts} {err!r}")
    check("OK        alpha (no earlier hash)" in out and "OK        bravo (backed up)" in out,
          f"one OK line per app: {out!r}")
    check(NEW_PW not in out and NEW_PW not in err and OLD_PW not in out + err, "no password in any output")
    check(not stray.exists(), "an inherited APP_PASSWORD_FILE never redirects an app's write")
    for app in (a, b):
        check(PW.check_password(NEW_PW, path=_hash_file(app)), f"{app.name} accepts the new password")
        check(not PW.check_password(OLD_PW, path=_hash_file(app)), f"{app.name} refuses the old password")
        check(NEW_PW not in _hash_file(app).read_text(encoding="utf-8"), f"{app.name} stores only a hash")
        check(PW.is_configured(path=_hash_file(app)), f"{app.name} reads as configured")
    saved = list(backups.glob("*/bravo/app_password.json"))
    check(len(saved) == 1 and saved[0].read_text(encoding="utf-8") == old_b, "bravo's old hash file is backed up")
    check(not list(backups.glob("*/alpha")), "nothing to back up for an app with no earlier hash")

    # ---- --only limits the run ----
    before_a = _hash_file(a).read_text(encoding="utf-8")
    code, out, _, _ = _run(["--only", "bravo"], repos, backups, entries=(OLD_PW, OLD_PW))
    check(code == 0 and "alpha" not in out and _hash_file(a).read_text(encoding="utf-8") == before_a,
          "--only touches just the named app")
    check(PW.check_password(OLD_PW, path=_hash_file(b)), "bravo now holds the --only password")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    good = _app(root, "good")
    broken = _app(root, "broken", setter="import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    liar_src = SETTER.read_text(encoding="utf-8") + "\n\ndef check_password(*_a, **_k):\n    return False\n"
    liar = _app(root, "liar", setter=liar_src)
    PW.set_password(OLD_PW, path=_hash_file(liar))
    liar_before = _hash_file(liar).read_text(encoding="utf-8")
    novenv = _app(root, "novenv")
    repos = {p.name: p for p in (good, broken, liar, novenv)}

    def python_for(repo: Path):
        return None if repo.name == "novenv" else Path(sys.executable)

    code, out, err, _ = _run([], repos, root / "_backups", python_for=python_for)
    check(code == 1, f"any failure exits 1: {code}")
    check("OK        good" in out, f"one broken app does not stop the others: {out!r}")
    check("FAILED    broken (setter exit 3: boom)" in out, f"setter failure carries its reason: {out!r}")
    check("FAILED    liar (app did not accept it back" in out and "previous hash restored" in out,
          f"a failed check-back is reported: {out!r}")
    check(_hash_file(liar).read_text(encoding="utf-8") == liar_before, "a failed check-back restores the old hash")
    check("FAILED    novenv (no .venv interpreter)" in out and not _hash_file(novenv).exists(),
          "an app with no .venv is reported, not written")
    check(NEW_PW not in out + err, "no password in failure output")
    check("Done" not in out, "no success banner after a failure")

_h.report_and_exit("test_fleet_password")
