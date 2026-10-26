"""Read-only code-structure census of every fleet repo, and a before-and-after comparison.

`measure` holds the per-file definitions (which files count, how lines, functions,
flat directories and `src`->`app` imports are measured), `repo_read` reads one repo
at a named ref through `git archive` and applies them, and `census` is the CLI that
runs the whole fleet and compares two runs. Nothing is fetched, built or written
outside the output file. How to run it and what each number means:
docs/structure-census.md. stdlib + the shared git wrapper only.
"""
