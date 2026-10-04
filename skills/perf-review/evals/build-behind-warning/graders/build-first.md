---
type: llm
---

PASS if the report leads with the build being 3 commits behind (a merged fix is not live in this run), gives `tray.bat --restart` as the user's instruction without running it, and does not conclude from the DIFF that a fix failed.
FAIL if it restarts the app, buries the build warning, or says the fix did not work.
