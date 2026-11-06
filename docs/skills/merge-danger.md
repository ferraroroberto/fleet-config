# Merge danger (how reversible a merge is)

Every `/issue-finish` PR body carries one line: `**Merge danger:** two-way door · blast radius: <word>`, repeated in the step-7 summary.

- **Two-way door**: a revert undoes the merge.
- **One-way door**: destructive, a data migration, an external publish, anything hard to walk back. The line appends ` · <reason>`.
- **Blast radius**: one word for what the merge touches, such as `local`, `fleet-wide` or `hooks-live`. A merge into `hooks/` is fleet-wide the moment it lands, because the folder is junctioned live.

The [PR-body checker](pr-body-check.md) refuses a body without a well-formed line (`merge-danger` rule). The line holds no paths, names or personal data.
