# PROGRESS -- task-20260806-165912-re-run-real-existing-unregistered-mentio

## Completed
- [x] Verified SPEC claims independently against live state before any write (per
      `[[veridian-task-prompt-false-premise-pattern]]`): unregistered_mentions 8-rows/all-
      resolved/all-dated-2026-07-29 claim = TRUE. wiring_registry total-8447/20-engines claim
      = 20 engines TRUE, total was 8562 (grown since SPEC minted, not a contradiction).
      "24 more real engines than the 20 registered" claim = FALSE PREMISE (category error --
      wiring_registry's `engine` entity_type is a closed, hand-curated 20-item AI-OS
      architectural taxonomy, confirmed via `generate_wiring_registry.py --report-only`
      showing 20/20 already in sync with its own source; the 22+5 VCEL business-computation
      `*-engine.ts` files in compliance-tracker/src/lib/engines are a separate, already
      code-documented category explicitly excluded from that registry
      (`compliance-engine-registry.ts`'s own comment: "NOT part of this registry")).
- [x] Cross-checked compliance-tracker/src/lib/engines (27 real `*-engine.ts` files) against
      wiring_registry (all entity_types, not just `engine`): 22/24 (minus the 3 already
      wired as engine exists_as evidence) were already fully tracked as `file`+`function`
      entities from FUNCTION_CATALOG.json. Only 2 files were genuinely unregistered under
      any entity_type: `src/lib/engines/ae/corporate-tax-engine.ts`,
      `src/lib/engines/ae/vat-engine.ts` (UAE country pack, modified after
      FUNCTION_CATALOG.json's last generation).
- [x] Registered both real gaps through the canonical script only (no raw SQL, no new
      registry, no hand-edit to the DO-NOT-HAND-EDIT engine_inventory block): `superboss-
      register.py register-knowledge` (KE-20260806-170554-1960, KE-20260806-170556-130c),
      then a real (non-`--report-only`) run of `generate_wiring_registry.py`, which picked
      both up as new `wiring_registry` `file` entities (VERIFIED_MATCH).
- [x] Re-ran the real unregistered_mentions detector fresh (`regenerate_master_index.py
      --apply`, unscoped) for the first time since 2026-07-29. Result: 0 new rows were
      pending (postflight_audit_gate.py, the flagger, wasn't invoked here and hadn't flagged
      anything new) -- a real, live-verified re-run, not a re-assertion.
- [x] Before/after row counts by entity_type recorded as proof: `file` 1978->1980,
      total wiring_registry 8562->8564; unregistered_mentions unchanged at 8 (all resolved).
- [x] Documented full findings in
      `UNREGISTERED_MENTIONS_ENGINE_CROSSCHECK_VERIFICATION_2026-08-06.md`.
- [x] Updated the false-premise memory file with this case.

## Remaining
- [ ] None -- SPEC fully actioned (detector re-run for real; genuine gap found and closed
      via canonical scripts; false "24-engine" premise corrected and documented instead of
      acted on literally). Commit + push this doc-only change.
