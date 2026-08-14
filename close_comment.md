Superseded and landed: this PR's real, already-AUDIT:PASS'd commit(s) were rebased onto current `main` (conflict confined to the shared PROGRESS.md header stamp, resolved mechanically -- no behavior change) and merged via #387 (merge commit 3ec13e838b0a23f59c18bb4285ec1cd5ee473156).

This PR itself is being closed unmerged only because this repo's worker-branch-enforcement hook does not allow pushing the rebase resolution directly onto this PR's own head branch (see #387's description for the established precedent this follows). The real content landed intact and unsquashed in #387.
