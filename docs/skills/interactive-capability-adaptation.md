# Interactive capability adaptation

[workflow-capabilities.md](../workflow-capabilities.md) owns dispatch, collection, wait/cancel, model/effort and user-input adaptation for shared/fleet workflows. `skills/_lib/cleanup_workflow.cjs` provides the optional interactive request/result path for the existing cleanup decision script when native `Workflow` is absent; it never spawns workers or alters scheduled launching.
