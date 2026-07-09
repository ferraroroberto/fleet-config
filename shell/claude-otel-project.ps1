# Wraps the `claude` CLI so every invocation auto-tags OTEL_RESOURCE_ATTRIBUTES
# with the current git repo's name, feeding local-llm-hub's OTel metrics
# receiver (project.name column in its "Claude Code (host CLI)" panel).
# See docs/otel-project-attribution.md for the full why/how/troubleshoot —
# this is fleet-config-hosted infrastructure whose only current consumer is
# that one repo, not a generic requirement.
#
# Dot-sourced from $PROFILE by install.ps1 (marked block — see install.ps1's
# Install-OtelProjectProfileHook). Requires a fresh shell (a new PowerShell
# session that reloads $PROFILE) to take effect after install/edit.
function claude {
    $repoRoot = git rev-parse --show-toplevel 2>$null
    if ($repoRoot) {
        $projectName = Split-Path -Leaf $repoRoot
        $env:OTEL_RESOURCE_ATTRIBUTES = "project.name=$projectName"
    } else {
        $env:OTEL_RESOURCE_ATTRIBUTES = $null
    }
    # Calling claude.exe (not claude) resolves the real binary directly and
    # cannot recurse back into this function.
    & claude.exe @args
}
