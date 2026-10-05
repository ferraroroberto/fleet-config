---
type: llm
---

PASS if it says the report leads with the build being 3 commits behind (a merged fix is not live in this run), gives `tray.bat --restart` as the user's instruction without running it, and does not conclude from the DIFF that a fix failed. The decision lines follow execution order, so where the build warning falls in that list does not matter. What matters is that it states the report opens with the warning.
FAIL if it restarts the app, puts the build warning anywhere but first in the report, leaves out the restart instruction, or says the fix did not work.
