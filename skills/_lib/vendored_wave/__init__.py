"""One propagation PR per repo per wave, for every component that repo is behind on.

`plan` turns a `vendored_drift` scan (any number of components) into one entry per
repo: the components it carries, the branch and commit subject its single PR uses, and
the PR body listing each component with its old and new sha. It reads and prints; it
clones, branches and opens nothing, so a dry run is the same call a real wave starts
from. How it fits into the skill: skills/propagate-vendored/SKILL.md. stdlib + the
sibling `vendored_drift` only.
"""
