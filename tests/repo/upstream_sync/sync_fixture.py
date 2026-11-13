"""Throwaway-repo helpers shared by the /upstream-sync and /downstream-sync tests."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from upstream_sync import cli  # noqa: E402

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"


def git(repo: Path, *args: str) -> str:
    done = run_git(["-C", str(repo), *args])
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


def commit(repo: Path, name: str, body: str, subject: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def configure(repo: Path) -> None:
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    # `core.hooksPath` -> an empty dir: this machine carries a global commit hook
    # that refuses the fixture's author email.
    empty = repo.parent / "no-hooks"
    empty.mkdir(exist_ok=True)
    git(repo, "config", "core.hooksPath", str(empty))


def init(repo: Path) -> None:
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    configure(repo)


def run_cli(argv: list) -> tuple:
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()
