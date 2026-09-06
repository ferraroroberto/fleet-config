"""Opt-in provider smoke or model-free native ownership conformance."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
from git_run import run_git
from runner_adapters import ClaudeAdapter, CodexAdapter
from scheduled_runner import main as run_scheduled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ownership-only", action="store_true", help="run harmless native process controls without a provider")
    parser.add_argument("--harness", choices=("claude", "codex"))
    parser.add_argument("--model")
    parser.add_argument("--effort", default="low")
    args = parser.parse_args()
    if args.ownership_only:
        if args.harness or args.model:
            parser.error("--ownership-only cannot select a provider/model")
        import unittest
        from test_scheduled_runner import PosixScopeTests, ScheduledRunnerTests, WindowsScopeTests
        if sys.platform != "win32":
            parser.error("the native ownership probe requires Windows and its real venv")
        loader = unittest.TestLoader()
        suite = unittest.TestSuite([
            loader.loadTestsFromTestCase(WindowsScopeTests),
            loader.loadTestsFromTestCase(PosixScopeTests),
            ScheduledRunnerTests("test_cancellation_after_parent_exit_does_not_wait_for_descendant_eof"),
            ScheduledRunnerTests("test_orphan_pipe_matrix_and_unconfirmed_cleanup_are_bounded"),
        ])
        print("Native ownership conformance: real venv; no provider/model calls", flush=True)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if not args.harness or not args.model:
        parser.error("provider smoke requires --harness and --model")
    root = Path(tempfile.mkdtemp(prefix=f"scheduled_smoke_{args.harness}_"))
    run_git(["init", "-q", str(root)], check=True)
    skill = root / ".agents" / "skills" / "scheduled-smoke" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: scheduled-smoke\ndescription: Execute a bounded disposable scheduled smoke.\n---\n"
        "Read input.txt with a native tool. Write output.txt containing exactly the same text. "
        "Verify output.txt. Do not access files outside this repository, network, agents, "
        "other skills, or services. Then report SCHEDULED_SMOKE_COMPLETE.\n", encoding="utf-8",
    )
    (root / "input.txt").write_bytes("scheduled smoke café 750\n".encode("utf-8"))
    (root / "check.py").write_text(
        "from pathlib import Path\nimport sys\np=Path('output.txt')\n"
        "sys.exit(0 if p.exists() and p.read_bytes()==Path('input.txt').read_bytes() else 1)\n",
        encoding="utf-8",
    )
    if args.harness == "claude":
        prompt = (
            "Run the scheduled-smoke skill by reading .agents/skills/scheduled-smoke/SKILL.md "
            "and performing its steps completely. This is a fully authorized harmless unattended "
            "smoke in a disposable repository: perform the file copy and verify it. "
            "Do not ask questions. Do not invoke other skills or inspect anything outside this repository."
        )
        flags = ["--safe-mode", "--tools", "Read,Write", "--permission-mode", "bypassPermissions"]
        adapter = ClaudeAdapter()
    else:
        prompt = "/scheduled-smoke"
        flags = ["--ignore-user-config", "--ephemeral", "--approve-for-me", "--disable", "hooks",
                 "-c", "project_doc_max_bytes=0", "-c", "check_for_update_on_startup=false"]
        adapter = CodexAdapter()
    arguments = [prompt, "--model", args.model, "--effort", args.effort, *flags]
    # Validate before starting a native process. Authentication remains in its
    # normal home; omit metered environment credentials only for this probe.
    adapter.build_command(arguments)
    keys = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY")
    saved = {key: os.environ.pop(key) for key in keys if key in os.environ}
    previous = Path.cwd()
    try:
        os.chdir(root)
        print(f"Synthetic repository: {root}", flush=True)
        return run_scheduled(["--harness", args.harness, *arguments, "--stall-timeout", "120",
                              "--delivery-check", str(root / "check.py")])
    finally:
        os.chdir(previous)
        os.environ.update(saved)


if __name__ == "__main__":
    raise SystemExit(main())
