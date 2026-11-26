"""The fleet's standard proposal report: one scored page plus one Board ask per suggestion.

`model` loads and checks the suggestion JSON and places each one in an
impact/effort quadrant, `page` renders the self-contained HTML page, `asks`
posts one Board question per suggestion through `chief_ops.py plan ask`, and
`cli` is the entry point skills call by path. Contract: `docs/proposal-reports.md`.

stdlib only.
"""
