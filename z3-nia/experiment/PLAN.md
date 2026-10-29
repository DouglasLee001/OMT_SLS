# Embedded NIA experiment harness

The approved scope is a separate experiment launcher, with no solver changes,
builds, or submissions during implementation. Reuse the committed reference
runner's total case deadline, validation, retained artifacts and result schema.
The embedded adapter only translates converter and executable commands.

1. Write policy, command translation, freeze, paired reporting and submission
   recovery tests; verify RED without invoking a scheduler or solver.
2. Implement `prepare`, `watch`, `status`, and `report`. Freeze both executable
   runtimes, converter SDK, sources, reference tables, configuration and all
   benchmark hashes. Require a pinned HEAD baseline build receipt.
3. Request 80 CPUs per node job and a proportional explicit memory allocation.
   Admit healthy IDLE/MIXED nodes only when effective free CPUs and memory
   suffice. Keep at most one outstanding harness task per node. Use a locked
   watcher and durable submission intents; never retry an uncertain submission.
4. Run a deterministic stratified paired regression first. Record exact paired
   state/objective comparisons; continue automatically to full only after the
   regression gate passes. Both variants retain baseline default seeds and the
   full historical proof configuration, with a 1200-second total case budget.
5. Verify mock tests and obtain an independent read-only review. The parent
   performs builds, preparation and launch after reviewing this implementation.

## Full-evidence collection extension

The default strict regression stop remains unchanged. An explicit
`watch --collect-full-evidence` may collect the full experiment after a failed
gate, keeping that failure visible. Completion is `evaluation_complete`, not a
noninferiority claim. Before each newly frozen experiment, record the aggregate
policy: no new error/interrupted instances, no lower feasible-model count, no
lower A1+A2, objective wins >= losses. `full_evaluation.json` is incomplete
until exactly 6,310 paired cases finish; zero individual objective losses is a
separate stronger diagnostic. This change does not modify earlier frozen runs.
