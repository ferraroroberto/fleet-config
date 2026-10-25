"""worktree_claim's runtime twin: config, .env and port copying, naming and primary-checkout refusals, junction targets.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/worktree_claim/test_runtime_copy.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from _suite import _h, check, wc, wgit  # noqa: E402


# ---- copy_runtime_config: the worktree gets its OWN port, not the primary's (#537) ----
#
# Carrying the primary's port across is what made every worktree lane's e2e
# suite report a collision with the live tray and refuse to run. Secrets and
# every other field must still copy verbatim -- the worktree is a faithful
# runtime twin, differing only where sharing is the bug.

import json as _json

port_base = Path(tempfile.mkdtemp(prefix="wc-port-"))
try:
    primary = port_base / "myrepo"
    (primary / "config").mkdir(parents=True)
    (primary / "config" / "webapp_config.json").write_text(
        _json.dumps({"host": "0.0.0.0", "port": 8447, "auth_token": "s3cret"}), encoding="utf-8")
    # A second ported config: app-launcher's webapp + session-host shape.
    (primary / "config" / "hosts.json").write_text(
        _json.dumps({"port": 8447, "name": "session-host"}), encoding="utf-8")
    # No top-level port -- must stay byte-identical.
    plain = {"names": {"a": "b"}, "nested": {"port": 8447}}
    (primary / "config" / "display_names.json").write_text(_json.dumps(plain), encoding="utf-8")
    # Not an object, and not parseable -- neither may break setup.
    (primary / "config" / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (primary / "config" / "broken.json").write_text("{not json", encoding="utf-8")
    (primary / "config" / "webapp_config.sample.json").write_text("{}", encoding="utf-8")

    wt = port_base / ("myrepo" + wc.WT_SEP + "579")
    wt.mkdir()
    copied = wc.copy_runtime_config(primary, wt)

    names = sorted(q.name for q in copied)
    check("webapp_config.sample.json" not in names,
          "copy_runtime_config: *.sample.json still excluded")

    got = _json.loads((wt / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(got["port"] != 8447, "copy_runtime_config: worktree port differs from the primary's (#537)")
    check(wc.WT_PORT_BASE <= got["port"] < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
          "copy_runtime_config: repointed port lands in the 8500-8999 band (#537)")
    check(got["auth_token"] == "s3cret" and got["host"] == "0.0.0.0",
          "copy_runtime_config: every other field copies verbatim, secrets included (#537)")

    got2 = _json.loads((wt / "config" / "hosts.json").read_text(encoding="utf-8"))
    check(got2["port"] != got["port"],
          "copy_runtime_config: two ported configs get two DISTINCT ports (#537)")
    check(got2["name"] == "session-host", "copy_runtime_config: sibling config keeps its other fields")

    check(_json.loads((wt / "config" / "display_names.json").read_text(encoding="utf-8")) == plain,
          "copy_runtime_config: no top-level port -> untouched, nested 'port' not walked (#537)")
    check((wt / "config" / "list.json").read_text(encoding="utf-8") == "[1, 2, 3]",
          "copy_runtime_config: a non-object JSON copies verbatim (#537)")
    check((wt / "config" / "broken.json").read_text(encoding="utf-8") == "{not json",
          "copy_runtime_config: an unparseable config copies verbatim, never breaks setup (#537)")

    # The primary must come out of this untouched.
    src_now = _json.loads((primary / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(src_now["port"] == 8447, "copy_runtime_config: the PRIMARY's own port is never rewritten (#537)")

    # Deterministic: same repo + same issue -> same port when it is still free.
    shutil.rmtree(wt / "config")
    wc.copy_runtime_config(primary, wt)
    again = _json.loads((wt / "config" / "webapp_config.json").read_text(encoding="utf-8"))
    check(again["port"] == got["port"],
          "copy_runtime_config: same lane re-setup reproduces the same port (#537)")

    # An already-present destination is still left alone.
    wt2 = port_base / ("myrepo" + wc.WT_SEP + "580")
    (wt2 / "config").mkdir(parents=True)
    (wt2 / "config" / "webapp_config.json").write_text('{"port": 1}', encoding="utf-8")
    wc.copy_runtime_config(primary, wt2)
    check(_json.loads((wt2 / "config" / "webapp_config.json").read_text(encoding="utf-8"))["port"] == 1,
          "copy_runtime_config: an existing destination file is not overwritten or repointed")
finally:
    shutil.rmtree(port_base, ignore_errors=True)


# ---- copy_runtime_config blanks machine-bound paths (#713) --------------------
#
# A copied config can point at a real, machine-bound sink outside the
# worktree -- a synced mirror/backup folder, another repo's database -- and a
# worktree instance that boots to hand the owner a "ready to validate" app
# then reads and writes the owner's REAL data (task-os#80). The default
# heuristic (no .fleet.toml declaration) must catch the exact task-os shape;
# an explicit `blank_config_keys` declaration must be used verbatim instead,
# including an empty list as an opt-out.

blank_base = Path(tempfile.mkdtemp(prefix="wc-blank-"))
try:
    task_os_shape = {
        "port": 8448,
        "placeholders": {"onedrive": "E:/onedrive", "user": "rober"},
        "mirror": {"dir": "{onedrive}/task-os/mirror", "backup_dir": "{onedrive}/task-os/backup"},
        "search": {"folder_roots": ["{onedrive}/Documentos"],
                   "email_db": "E:/automation/email-archiver/data/emails.db"},
        "team": {"enabled": False, "people": ["Roberto Ferraro"]},
        "auth": {"token": "s3cret"},
    }

    # -- default heuristic: no .fleet.toml at all --
    no_toml = blank_base / "no-toml"
    (no_toml / "config").mkdir(parents=True)
    (no_toml / "config" / "config.json").write_text(_json.dumps(task_os_shape), encoding="utf-8")
    wt = blank_base / ("no-toml" + wc.WT_SEP + "713")
    wt.mkdir()
    wc.copy_runtime_config(no_toml, wt)
    got = _json.loads((wt / "config" / "config.json").read_text(encoding="utf-8"))
    check(got["mirror"] == {"dir": "", "backup_dir": ""},
          "copy_runtime_config: default heuristic blanks {onedrive}-templated mirror paths (#713)")
    check(got["search"] == {"folder_roots": [], "email_db": ""},
          "copy_runtime_config: default heuristic blanks a templated list AND a plain absolute path (#713)")
    check(got["placeholders"]["onedrive"] == "",
          "copy_runtime_config: default heuristic also blanks the raw absolute-path root it templates from (#713)")
    check(got["team"] == {"enabled": False, "people": ["Roberto Ferraro"]} and got["auth"] == {"token": "s3cret"},
          "copy_runtime_config: default heuristic leaves non-path fields (incl. secrets) untouched (#713)")

    # -- declared blank_config_keys: exactly those dotted keys, nothing else --
    declared = blank_base / "declared"
    (declared / "config").mkdir(parents=True)
    (declared / "config" / "config.json").write_text(_json.dumps(task_os_shape), encoding="utf-8")
    (declared / ".fleet.toml").write_text(
        '[worktree]\nblank_config_keys = ["mirror.dir", "mirror.backup_dir"]\n', encoding="utf-8")
    wt_d = blank_base / ("declared" + wc.WT_SEP + "713")
    wt_d.mkdir()
    wc.copy_runtime_config(declared, wt_d)
    got_d = _json.loads((wt_d / "config" / "config.json").read_text(encoding="utf-8"))
    check(got_d["mirror"] == {"dir": "", "backup_dir": ""},
          "copy_runtime_config: declared blank_config_keys blanks exactly what's declared (#713)")
    check(got_d["search"]["email_db"] == "E:/automation/email-archiver/data/emails.db",
          "copy_runtime_config: declared blank_config_keys leaves UNDECLARED machine-bound keys "
          "untouched -- declaration takes over from the default heuristic entirely (#713)")

    # -- empty list is a deliberate opt-out of the default heuristic --
    opt_out = blank_base / "opt-out"
    (opt_out / "config").mkdir(parents=True)
    (opt_out / "config" / "config.json").write_text(_json.dumps(task_os_shape), encoding="utf-8")
    (opt_out / ".fleet.toml").write_text('[worktree]\nblank_config_keys = []\n', encoding="utf-8")
    wt_o = blank_base / ("opt-out" + wc.WT_SEP + "713")
    wt_o.mkdir()
    wc.copy_runtime_config(opt_out, wt_o)
    got_o = _json.loads((wt_o / "config" / "config.json").read_text(encoding="utf-8"))
    check(got_o["mirror"]["dir"] == "{onedrive}/task-os/mirror",
          "copy_runtime_config: an explicit empty blank_config_keys opts out of the default heuristic (#713)")

    # -- a declared-but-absent key must not break setup --
    absent = blank_base / "absent-key"
    (absent / "config").mkdir(parents=True)
    (absent / "config" / "config.json").write_text('{"port": 1, "name": "x"}', encoding="utf-8")
    (absent / ".fleet.toml").write_text(
        '[worktree]\nblank_config_keys = ["mirror.dir", "no.such.path"]\n', encoding="utf-8")
    wt_a = blank_base / ("absent-key" + wc.WT_SEP + "713")
    wt_a.mkdir()
    wc.copy_runtime_config(absent, wt_a)
    got_a = _json.loads((wt_a / "config" / "config.json").read_text(encoding="utf-8"))
    check(got_a["name"] == "x" and set(got_a.keys()) == {"port", "name"},
          "copy_runtime_config: declared-but-absent blank_config_keys is a no-op, never a setup failure (#713)")

    # -- worktree_blank_config_keys: same degrade-on-any-error contract as
    #    worktree_junction_targets (no .fleet.toml / no [worktree] table / a
    #    non-list value all fall back to None -> the default heuristic).
    check(wc.worktree_blank_config_keys(no_toml) is None,
          "worktree_blank_config_keys: no .fleet.toml -> None (#713)")

    no_table = blank_base / "no-table"
    no_table.mkdir()
    (no_table / ".fleet.toml").write_text('layer = "enabling"\n', encoding="utf-8")
    check(wc.worktree_blank_config_keys(no_table) is None,
          "worktree_blank_config_keys: .fleet.toml without [worktree] -> None (#713)")

    no_key = blank_base / "no-key"
    no_key.mkdir()
    (no_key / ".fleet.toml").write_text('[worktree]\nextra_junctions = ["vendor/x"]\n', encoding="utf-8")
    check(wc.worktree_blank_config_keys(no_key) is None,
          "worktree_blank_config_keys: [worktree] present without blank_config_keys -> None (#713)")

    wrong_type = blank_base / "wrong-type"
    wrong_type.mkdir()
    (wrong_type / ".fleet.toml").write_text(
        '[worktree]\nblank_config_keys = "mirror.dir"\n', encoding="utf-8")
    check(wc.worktree_blank_config_keys(wrong_type) is None,
          "worktree_blank_config_keys: blank_config_keys not a list -> None (#713)")

    invalid_toml = blank_base / "invalid-toml"
    invalid_toml.mkdir()
    (invalid_toml / ".fleet.toml").write_text("not [ valid toml", encoding="utf-8")
    check(wc.worktree_blank_config_keys(invalid_toml) is None,
          "worktree_blank_config_keys: unparseable .fleet.toml -> None, no crash (#713)")

    check(wc.worktree_blank_config_keys(declared) == ["mirror.dir", "mirror.backup_dir"],
          "worktree_blank_config_keys: declared list is returned verbatim (#713)")
    check(wc.worktree_blank_config_keys(opt_out) == [],
          "worktree_blank_config_keys: an explicit empty list is returned as [], not None (#713)")

    # -- a file that isn't a JSON object, or doesn't parse, must not break setup --
    non_object = blank_base / "non-object"
    (non_object / "config").mkdir(parents=True)
    (non_object / "config" / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (non_object / "config" / "broken.json").write_text("{not json", encoding="utf-8")
    wt_no = blank_base / ("non-object" + wc.WT_SEP + "713")
    wt_no.mkdir()
    wc.copy_runtime_config(non_object, wt_no)
    check((wt_no / "config" / "list.json").read_text(encoding="utf-8") == "[1, 2, 3]",
          "copy_runtime_config: a non-object JSON is never touched by the blanking pass (#713)")
    check((wt_no / "config" / "broken.json").read_text(encoding="utf-8") == "{not json",
          "copy_runtime_config: an unparseable config is never touched by the blanking pass (#713)")
finally:
    shutil.rmtree(blank_base, ignore_errors=True)


# ---- copy_*_config removes repo-declared secret keys (#839) ------------------
#
# `[worktree] secret_config_keys` is additive and opt-in: declared keys are
# removed outright (any value type) on top of whichever blanking mode is in
# force -- it must never switch off the default heuristic the way declaring
# `blank_config_keys` does -- and a repo declaring neither copies exactly as
# before. Synthetic, obviously-fake values only.

secret_base = Path(tempfile.mkdtemp(prefix="wc-secret-"))
try:
    fake_shape = {
        "port": 8448,
        "mirror": {"dir": "{onedrive}/fake/mirror"},
        "search": {"email_db": "E:/fake/emails.db"},
        "auth": {"token": "FAKE-TOKEN-0000", "user": "fake-user"},
        "credentials": {"api_key": "FAKE-KEY-0000", "scopes": ["read"]},
        "name": "fake",
    }

    def _secret_lane(name: str, toml: "str | None", root: bool = False) -> dict:
        """Copy `fake_shape` from a fresh temp primary into its worktree and
        return the worktree's parsed copy (config/ by default, or a
        gitignored root-level config.json when `root`)."""
        primary = secret_base / name
        primary.mkdir()
        if toml is not None:
            (primary / ".fleet.toml").write_text(toml, encoding="utf-8")
        wt = secret_base / (name + wc.WT_SEP + "839")
        wt.mkdir()
        if root:
            # `check-ignore` needs a real repo but no commit, so no hook-bypass
            # fixture (`_git_t`, further down) is required.
            subprocess.run(["git", "init", "-q", str(primary)], check=True, capture_output=True)
            (primary / ".gitignore").write_text("config.json\n", encoding="utf-8")
            (primary / "config.json").write_text(_json.dumps(fake_shape), encoding="utf-8")
            wc.copy_root_config(primary, wt)
            return _json.loads((wt / "config.json").read_text(encoding="utf-8"))
        (primary / "config").mkdir()
        (primary / "config" / "config.json").write_text(_json.dumps(fake_shape), encoding="utf-8")
        wc.copy_runtime_config(primary, wt)
        return _json.loads((wt / "config" / "config.json").read_text(encoding="utf-8"))

    def _without_port(d: dict) -> dict:
        return {k: v for k, v in d.items() if k != "port"}

    # -- neither key declared: byte-for-byte today's behaviour (heuristic only) --
    today = {
        "mirror": {"dir": ""},
        "search": {"email_db": ""},
        "auth": {"token": "FAKE-TOKEN-0000", "user": "fake-user"},
        "credentials": {"api_key": "FAKE-KEY-0000", "scopes": ["read"]},
        "name": "fake",
    }
    check(_without_port(_secret_lane("neither-no-toml", None)) == today,
          "secret_config_keys: no .fleet.toml -> exactly today's copy, nothing removed (#839)")
    check(_without_port(_secret_lane("neither-other-keys", '[worktree]\nextra_junctions = ["x"]\n')) == today,
          "secret_config_keys: [worktree] without either key -> exactly today's copy (#839)")

    # -- secret keys alone: removed, AND the default heuristic still runs --
    got = _secret_lane("secret-only", '[worktree]\nsecret_config_keys = ["auth.token", "credentials"]\n')
    check("token" not in got["auth"] and got["auth"] == {"user": "fake-user"},
          "secret_config_keys: a declared scalar leaf is removed, its siblings kept (#839)")
    check("credentials" not in got,
          "secret_config_keys: a declared whole sub-table is removed outright (#839)")
    check(got["mirror"] == {"dir": ""} and got["search"] == {"email_db": ""},
          "secret_config_keys: declaring it does NOT switch off the default machine-bound heuristic (#839)")
    check(isinstance(got["port"], int) and wc.WT_PORT_BASE <= got["port"] < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
          "secret_config_keys: the port rewrite still applies alongside removal (#839)")

    # -- composes with an explicit blank_config_keys --
    got = _secret_lane("secret-and-blank",
                       '[worktree]\nblank_config_keys = ["mirror.dir"]\nsecret_config_keys = ["auth"]\n')
    check("auth" not in got and got["mirror"] == {"dir": ""},
          "secret_config_keys: composes with blank_config_keys -- both applied (#839)")
    check(got["search"] == {"email_db": "E:/fake/emails.db"},
          "secret_config_keys: with blank_config_keys declared, blanking stays declared-only (#839)")

    # -- root-level configs get the same removal --
    got = _secret_lane("secret-root", '[worktree]\nsecret_config_keys = ["auth.token"]\n', root=True)
    check("token" not in got["auth"] and got["mirror"] == {"dir": ""},
          "copy_root_config: declared secret keys are removed from a root-level config too (#839)")

    # -- declared-but-absent / non-object / unparseable never break setup --
    absent = secret_base / "absent.json"
    absent.write_text('{"name": "x"}', encoding="utf-8")
    check(wc.remove_secret_config(absent, ["auth.token", "no.such"]) == []
          and absent.read_text(encoding="utf-8") == '{"name": "x"}',
          "remove_secret_config: declared-but-absent keys are a no-op, file untouched (#839)")
    not_obj = secret_base / "list.json"
    not_obj.write_text("[1, 2]", encoding="utf-8")
    broken = secret_base / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    check(wc.remove_secret_config(not_obj, ["auth"]) == [] and wc.remove_secret_config(broken, ["auth"]) == []
          and not_obj.read_text(encoding="utf-8") == "[1, 2]"
          and broken.read_text(encoding="utf-8") == "{not json",
          "remove_secret_config: a non-object or unparseable config is left untouched (#839)")

    # -- worktree_secret_config_keys: always a list, degrade-to-[] on anything odd --
    def _keys_for(name: str, toml: "str | None") -> list:
        d = secret_base / ("keys-" + name)
        d.mkdir()
        if toml is not None:
            (d / ".fleet.toml").write_text(toml, encoding="utf-8")
        return wc.worktree_secret_config_keys(d)

    check(_keys_for("none", None) == [], "worktree_secret_config_keys: no .fleet.toml -> [] (#839)")
    check(_keys_for("no-key", '[worktree]\nblank_config_keys = ["a"]\n') == [],
          "worktree_secret_config_keys: [worktree] without the key -> [] (#839)")
    check(_keys_for("wrong-type", '[worktree]\nsecret_config_keys = "auth"\n') == [],
          "worktree_secret_config_keys: non-list value -> [] (#839)")
    check(_keys_for("invalid", "not [ valid toml") == [],
          "worktree_secret_config_keys: unparseable .fleet.toml -> [], no crash (#839)")
    check(_keys_for("declared", '[worktree]\nsecret_config_keys = [" auth.token ", "", 3, "credentials"]\n')
          == ["auth.token", "credentials"],
          "worktree_secret_config_keys: declared list returned stripped, non-strings and blanks dropped (#839)")
finally:
    shutil.rmtree(secret_base, ignore_errors=True)


# ---- copy_env_file: the primary's .env mirrors into a fresh worktree (#698) ----
#
# A repo whose startup path reads a secret/setting from .env (e.g.
# local-llm-hub's LOCAL_LLM_HUB_SSH_KEY) never gets it in a worktree, since
# `git worktree add` populates tracked files only and .env is gitignored --
# the same shape as copy_runtime_config, one level up from config/.

env_base = Path(tempfile.mkdtemp(prefix="wc-env-"))
try:
    primary = env_base / "myrepo"
    primary.mkdir(parents=True)
    (primary / ".env").write_text("LOCAL_LLM_HUB_SSH_KEY=s3cret\n", encoding="utf-8")

    wt = env_base / ("myrepo" + wc.WT_SEP + "698")
    wt.mkdir()
    dst = wc.copy_env_file(primary, wt)

    check(dst == wt / ".env", "copy_env_file: returns the copied destination path")
    check((wt / ".env").read_text(encoding="utf-8") == "LOCAL_LLM_HUB_SSH_KEY=s3cret\n",
          "copy_env_file: .env content copies verbatim (#698)")

    # A primary with no .env must not fail setup.
    no_env_primary = env_base / "noenvrepo"
    no_env_primary.mkdir()
    wt_noenv = env_base / ("noenvrepo" + wc.WT_SEP + "1")
    wt_noenv.mkdir()
    check(wc.copy_env_file(no_env_primary, wt_noenv) is None,
          "copy_env_file: no primary .env -> no-op, never a setup failure (#698)")
    check(not (wt_noenv / ".env").exists(), "copy_env_file: no .env created when primary has none")

    # An already-present destination is left alone, same as copy_runtime_config.
    wt2 = env_base / ("myrepo" + wc.WT_SEP + "699")
    wt2.mkdir()
    (wt2 / ".env").write_text("PRESERVE_ME=1\n", encoding="utf-8")
    check(wc.copy_env_file(primary, wt2) is None,
          "copy_env_file: existing destination -> no-op")
    check((wt2 / ".env").read_text(encoding="utf-8") == "PRESERVE_ME=1\n",
          "copy_env_file: an existing destination file is not overwritten")
finally:
    shutil.rmtree(env_base, ignore_errors=True)


# ---- worktree_port: band, determinism, avoids what is taken ----

check(wc.worktree_port("579") == wc.worktree_port("579"),
      "worktree_port: deterministic for the same issue (#537)")
_p = wc.worktree_port("579")
check(wc.worktree_port("579", {_p}) != _p,
      "worktree_port: skips a port already handed out in this worktree (#537)")
check(wc.WT_PORT_BASE <= wc.worktree_port("fix-no-digits") < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
      "worktree_port: a non-numeric issue still lands in band (#537)")


# ---- _looks_like_worktree_name: the <repo>-wt-<N> naming convention (#589) ----

check(wc._looks_like_worktree_name("fleet-config" + wc.WT_SEP + "143") is True,
      "_looks_like_worktree_name: a real <repo>-wt-<N> name matches (#589)")
check(wc._looks_like_worktree_name("fleet-config") is False,
      "_looks_like_worktree_name: a bare repo name (no separator) does not match (#589)")
check(wc._looks_like_worktree_name("fleet-config" + wc.WT_SEP) is False,
      "_looks_like_worktree_name: an empty issue suffix does not match (#589)")
check(wc._looks_like_worktree_name(wc.WT_SEP + "143") is False,
      "_looks_like_worktree_name: an empty stem does not match (#589)")


# ---- _is_primary_checkout_safe: tolerates a non-git target (#589) ----

def _raise_not_a_repo(_p):
    raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

_real_is_primary = wc.is_primary_checkout
try:
    wgit.is_primary_checkout = _raise_not_a_repo
    check(wc._is_primary_checkout_safe(Path("not-a-git-repo")) is False,
          "_is_primary_checkout_safe: swallows CalledProcessError -> False, never raises (#589)")
    wgit.is_primary_checkout = lambda p: True
    check(wc._is_primary_checkout_safe(Path("whatever")) is True,
          "_is_primary_checkout_safe: passes through a real True (#589)")
finally:
    wgit.is_primary_checkout = _real_is_primary


# ---- remove_worktree: refuses a primary checkout, never deletes it (#589) -----
#
# The exact confusion that destroyed life-os's gitignored personal data: an
# agent passed remove-worktree the PRIMARY repo root (the shape every sibling
# subcommand accepts as repo_root) instead of a worktree path. This must now
# refuse loudly and touch nothing, both for a detected primary checkout and
# for a path whose basename doesn't even look like a worktree.

guard_base = Path(tempfile.mkdtemp(prefix="wc-guard-"))
try:
    # Detected as a primary checkout -> hard refusal, exit 2, nothing touched.
    primary_like = guard_base / "myrepo"
    primary_like.mkdir()
    (primary_like / "sentinel.txt").write_text("still here", encoding="utf-8")

    real_is_primary = wc.is_primary_checkout
    wgit.is_primary_checkout = lambda p: True
    try:
        rc = wc.remove_worktree(primary_like)
        check(rc == 2, "remove_worktree: refuses a primary checkout, exit 2 (#589)")
        check(primary_like.exists() and (primary_like / "sentinel.txt").exists(),
              "remove_worktree: a primary checkout is left completely untouched (#589)")
    finally:
        wgit.is_primary_checkout = real_is_primary

    # Not a primary checkout, but the basename doesn't match <repo>-wt-<N> ->
    # the naming-convention guard refuses too.
    wgit.is_primary_checkout = lambda p: False
    try:
        weird_name = guard_base / "not-a-worktree-name"
        weird_name.mkdir()
        rc = wc.remove_worktree(weird_name)
        check(rc == 2, "remove_worktree: refuses a non-<repo>-wt-<N> basename (#589)")
        check(weird_name.exists(),
              "remove_worktree: the mismatched-name path is left untouched (#589)")

        # --force-nonstandard-name is the deliberate override.
        weird_name2 = guard_base / "another-nonstandard-dir"
        weird_name2.mkdir()
        rc = wc.remove_worktree(weird_name2, force_nonstandard_name=True)
        check(rc == 0,
              "remove_worktree: --force-nonstandard-name overrides the naming guard (#589)")
        check(not weird_name2.exists(),
              "remove_worktree: with the override, a nonstandard-named path is removed (#589)")

        # A real <repo>-wt-<N> worktree (correctly not flagged as primary)
        # still tears down normally -- the guard must not break the happy path.
        ok_wt = guard_base / ("myrepo" + wc.WT_SEP + "589")
        ok_wt.mkdir()
        rc = wc.remove_worktree(ok_wt)
        check(rc == 0, "remove_worktree: a real <repo>-wt-<N> worktree still tears down (#589)")
        check(not ok_wt.exists(), "remove_worktree: the real worktree is actually gone (#589)")
    finally:
        wgit.is_primary_checkout = real_is_primary
finally:
    shutil.rmtree(guard_base, ignore_errors=True)


# ---- worktree_junction_targets: declarative extra paths from .fleet.toml (#620) ----

wjt_base = Path(tempfile.mkdtemp(prefix="wc-wjt-"))
try:
    bare = wjt_base / "no-toml"
    bare.mkdir()
    check(wc.worktree_junction_targets(bare) == [".venv"],
          "worktree_junction_targets: no .fleet.toml -> .venv-only default (#620)")

    no_table = wjt_base / "no-table"
    no_table.mkdir()
    (no_table / ".fleet.toml").write_text(
        'layer = "enabling"\nicon = "x"\ndescription = "d"\n', encoding="utf-8")
    check(wc.worktree_junction_targets(no_table) == [".venv"],
          "worktree_junction_targets: .fleet.toml without [worktree] -> .venv-only default (#620)")

    invalid = wjt_base / "invalid-toml"
    invalid.mkdir()
    (invalid / ".fleet.toml").write_text("not [ valid toml", encoding="utf-8")
    check(wc.worktree_junction_targets(invalid) == [".venv"],
          "worktree_junction_targets: unparseable .fleet.toml -> .venv-only default, no crash (#620)")

    wrong_type = wjt_base / "wrong-type"
    wrong_type.mkdir()
    (wrong_type / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = "vendor/comfyui"\n', encoding="utf-8")
    check(wc.worktree_junction_targets(wrong_type) == [".venv"],
          "worktree_junction_targets: extra_junctions not a list -> .venv-only default (#620)")

    declared = wjt_base / "declared"
    declared.mkdir()
    (declared / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ["vendor/comfyui", "  /models/cache/  ", 42, "", "../escape"]\n',
        encoding="utf-8")
    check(wc.worktree_junction_targets(declared) == [".venv", "vendor/comfyui", "models/cache"],
          "worktree_junction_targets: declared extras appended after .venv; "
          "non-strings, blanks, and '..'-escaping entries dropped, slashes trimmed (#620)")

    # A declared target is used to build an OS-level command, so an entry whose
    # components are not ordinary directory names is dropped here, at the one
    # place `.fleet.toml` data enters -- and ordinary names must still pass.
    unsafe = wjt_base / "unsafe"
    unsafe.mkdir()
    (unsafe / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ['
        '"vendor/ok", "a&whoami", "a|whoami", "a>out", "a<in", "a^b", '
        r'"x\" & whoami & \"y", "C:/windows/temp", "pct%VAR%"'
        ']\n',
        encoding="utf-8")
    check(wc.worktree_junction_targets(unsafe) == [".venv", "vendor/ok"],
          "worktree_junction_targets: entries whose components are not ordinary "
          "directory names are dropped before use; ordinary names still pass")

    ordinary = wjt_base / "ordinary"
    ordinary.mkdir()
    (ordinary / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ['
        '"vendor/comfyui", "models/cache", "my models", "node_modules", "a.b-c"'
        ']\n',
        encoding="utf-8")
    check(wc.worktree_junction_targets(ordinary) == [
              ".venv", "vendor/comfyui", "models/cache", "my models",
              "node_modules", "a.b-c"],
          "worktree_junction_targets: ordinary directory names (dots, dashes, "
          "underscores, spaces, nesting) are not turned away by the guard")

    # An unreadable-but-present .fleet.toml (permission error, vanished mid-read,
    # a leftover worktree with a half-deleted checkout) must degrade the same
    # way a MISSING file does -- never raise. If this raised, remove_worktree's
    # `for rel in worktree_junction_targets(wt): _strip_junction(...)` loop would
    # never even start, and the exception would propagate uncaught out of
    # remove_worktree, skipping the .venv strip entirely (fleet-config#620).
    unreadable = wjt_base / "unreadable"
    unreadable.mkdir()
    (unreadable / ".fleet.toml").write_text('[worktree]\nextra_junctions = ["x"]\n', encoding="utf-8")
    real_read_text = Path.read_text

    def _boom(self, *a, **k):
        if self.name == ".fleet.toml":
            raise OSError("simulated: permission denied / vanished mid-read")
        return real_read_text(self, *a, **k)

    Path.read_text = _boom
    try:
        check(wc.worktree_junction_targets(unreadable) == [".venv"],
              "worktree_junction_targets: OSError reading .fleet.toml -> .venv-only "
              "default, never raises (#620)")
    finally:
        Path.read_text = real_read_text
finally:
    shutil.rmtree(wjt_base, ignore_errors=True)

_h.report_and_exit("test_runtime_copy")
