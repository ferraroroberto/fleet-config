"""Section functions for the context-filter acceptance check (`acceptance.checks_context_filter`).

Each section is one `def name(check: _Checker) -> None` that runs its cases against a shared
`_Checker`; the runner calls them in a fixed order, so the run prints the same cases in the same
order as the single long function they were cut from. Case tables live in `tables.py`.
"""
