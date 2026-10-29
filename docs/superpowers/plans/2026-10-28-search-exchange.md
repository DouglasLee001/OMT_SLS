# OMT search-information exchange implementation and experiment plan

> For agentic workers: use subagent-driven-development for independent modules,
> test-driven-development for behavioral changes, and independent code review.

**Goal:** Retain the NIA_LS main process and isolated, pausable Z3 workers; exchange
actual search information in both directions and verify at least 50 additional
successful cases on the same complete 6310-case benchmark, relative to the latest
complete baseline, without a material increase in losses.

**Architecture:** A bounded, scoped shared-memory channel connects the existing
parent search state and child solver. The public Z3 clause and user-propagator
decision callbacks observe search and optionally request soft branch choices.
Callbacks add no constraints. SAT models and proofs retain all existing checks.

**Tech stack:** C++17/GMP, the pinned local Z3 5.1 SDK, Python experiment/report
tools, Slurm. No Z3 library/source changes are required for this first mechanism.

## Authorization and invariants

The user approved retaining the architecture, implementing information exchange,
and executing staged experiments. Run only on server/node1/node2 after checking
that each chosen node is IDLE with no overlapping job; use 160 workers per node
and the existing 1200-second total per-case deadline. Do not commit automatically.
Do not change frozen competitor binaries, old runs, or unrelated dirty files.

Baseline: `/data/shared/lbh/OMT/result/NIA_20261028_011635_736862`, 6310 cases,
A1/A2/B/C = 2352/3522/412/24, success=5874, vs-best win/tie/loss=2134/4145/31.
The full success target is at least 5924. Track loss changes, objective improvements,
success regressions, and initial-model regressions independently. A focus-set
projection never establishes the complete goal. The latest official loaders and
strict artifact verifier, not ad-hoc string counting, determine final outcomes.

The old +30/-10 study gates do not apply. The preferred loss gate is no increase
above 31; any increase must be reported explicitly, not relabeled as success.
Do not use opponent values, models, names, or case categories to guide execution.

## Design decisions

Modes: `off`, `observe`, `sls-to-z3`, `z3-to-sls`, `both`.

- `off` preserves the existing solver route and search behavior.
- All four enabled modes use the same callback-compatible SMT route. In the
  pinned Z3, on-clause registration changes the default strategic solver to its
  incremental SMT engine. Thus observe is an essential matched control.
- Export actual active optimizer State weights, assignments, and unsatisfied
  clause incidence, not the separate legacy feasibility caches.
- Variable scores distinguish weighted degree, unsatisfied-clause occurrence,
  and weighted unsatisfied pressure; repeated variables count once per clause.
- The child maps shared slots to original expressions by symbol name and sort.
  Skip unmatched preprocessed auxiliaries; record mapping coverage.
- Outgoing guidance: initial numerical hints only before check; live soft
  Boolean branch preferences/priorities through decision callbacks. Do not call
  set_initial_value during check and pretend it affects current search.
- Incoming guidance: actual clause-event occurrence pressure and observed
  Boolean phases. Do not label these exact internal CDCL activity or numerical
  tableau values. The public callback cannot expose arbitrary Int fixed values.
- Scope every channel by formula identity, bound epoch, sequence, and native
  variable count. Old-bound observations cannot silently modify a new search.
- Advice affects ordering/heuristic proposals only. A partial assignment never
  becomes an incumbent. No observed/heuristic clause is installed as a lemma.
- Preserve exact original/native SAT validation, whole-query UNSAT requirements,
  ray verification, total deadlines, worker cleanup, and SIGSTOP/SIGCONT semantics.

## Tasks

### 1. Shared protocol and pressure computation

Files: new `NIA_LS/src/nia_omt_search_exchange.{h,cpp}` and
`NIA_LS/tests/test_omt_search_exchange.cpp`.

- [ ] Test RED: malformed scopes/sizes, deduplication within a clause, stale
  epoch/sequence, exact signed128 values, saturation, no mutation of input state.
- [ ] Implement mode parsing, immutable scoped channel, bounded variable slots,
  parent snapshots and child observations, and pressure derivation.
- [ ] Test real fork publication, pause/resume visibility, drain behavior and
  destruction. Keep this module independent of Z3 and ls_solver.

### 2. Z3 observation and soft-guidance adapter

Files: new `NIA_LS/src/nia_omt_exchange_z3.{h,cpp}` and
`NIA_LS/tests/test_omt_exchange_z3.cpp`.

- [ ] Test RED against the actual pinned library: callback delivery, SAT and
  UNSAT preservation, input vs deletion/other clause events, source mapping,
  real decision requests, and unmapped/quoted/wrong-sort symbols.
- [ ] Implement no-throw C callback boundaries and bounded observation work.
- [ ] Apply startup numerical hints only in outgoing modes; poll new pressure
  at supported decision callbacks and use soft next_split suggestions.
- [ ] Export clause occurrence and Boolean observations only in incoming modes;
  observe still gathers diagnostic counters without using them in either search.
- [ ] Verify every enabled mode follows the same backend route and that no
  callback asserts a formula or accepts a candidate.

### 3. Native optimization and worker integration

Files: `nia_omt_optimality.{h,cpp}`, `nia_omt_optimize.{h,cpp}`, `call.cpp`,
`Makefile`, and focused native tests. Owned by the root agent.

- [ ] Extend options with search_exchange default off; validate requires proof
  mode and reject unknown values without creating result artifacts.
- [ ] Let session own a channel created before fork; publish actual State
  snapshots before starting/resuming its worker; clear on a changed incumbent.
- [ ] Observe/drain only after the worker has stopped or exited. Fold incoming
  scores into bounded scan ordering/soft candidate generation; preserve best.
- [ ] Preserve all exact validation and deadline conditions. Record messages,
  mapping coverage, decisions requested, and feedback actually used.
- [ ] Run existing optimality, schedule, model-bridge and optimization tests plus
  new mode-off and scope/expiry regressions. Resolve all failures before jobs.

### 4. Runner plumbing and reproducible study helper

Files: `script/run_optimize.py`, `script/run_optimize_slurm.py`, new
`script/search_exchange_study.py` and focused script tests.

- [ ] Test RED for valid modes, proof-mode requirement, default-off command
  compatibility, manifests/config round-trip and native option propagation.
- [ ] Add Config.search_exchange and both CLI entry points. Enabled runs append
  `--search-exchange=MODE`; old commands remain unchanged when off.
- [ ] Build a diagnostic cohort from latest unsolved436 plus independent solved
  controls. Suggested complete focus set: union with previous369 = 708 cases,
  including all current31 losses and all21 long-proof protection cases.
- [ ] Support frozen old-binary controls without mutating historical runtime;
  freeze candidate executable, library, scripts and rivals for every run.
- [ ] Check current Slurm state immediately before every submission; only idle
  authorized nodes, 160 workers/node, 1200 seconds total per instance.
- [ ] Report focus-set diagnostics without claiming full-goal completion; report
  exact identities and current baseline comparison for every full experiment.

### 5. Evidence-driven experiments

- [ ] Independent spec and code review of protocol, adapter, integration and
  runner changes; address critical/important issues before sustained execution.
- [ ] Trace smoke tests prove both directions fire under real solver work and
  native candidates are still independently validated. Test benign/no-hint cases.
- [ ] Freeze the source and compiler/runtime hashes for every candidate.
- [ ] Screen observe vs one-way vs both on representative unsolved/control cases
  if needed to eliminate nonfunctional/adverse mechanisms before a full focus run.
- [ ] Evaluate all436 unsolved and solved protections at the original1200 seconds;
  compare all one-way/both variants worth carrying forward. Preserve failures.
- [ ] Iterate from measured per-case information-use and regressions, without
  introducing benchmark-family or opponent-dependent rules.
- [ ] Once focus evidence is promising, run complete6310; strict verification,
  standard summary and direct baseline comparison. Require >=5924 success and
  explicitly audit losses and all old-success regressions.
- [ ] If the gate fails, keep the goal active and continue from diagnostic
  evidence; never complete the goal just because a feature or focus run passes.

## Verification and reporting

Each sustained run belongs under `/data/shared/lbh/OMT/result/NIA_<timestamp>`.
Study selections/comparisons belong in a separately named study directory under
the same result root. Preserve raw logs and statuses; OOM/exit/unknown are not
UNSAT, no-result cases are not inferred infeasible, and independent extra-time
proofs are not retroactively included in the original1200-second success count.
Final documentation must explain the exact exchanged information, enabled path,
fair matched controls, fullrun counts and material limitations.

## Candidate V2: proposed arithmetic decisions

V1 is frozen under the study candidate_v1 directory and running unchanged.
Live117 evidence confirms real two-way use on113cases and accepted requests
on102cases, but accepted requests are not proof of changed phases/branch order.

A bounded V2 adapter will inspect the actual proposed ground Boolean predicate
inside Z3's public decide callback. It registers the canonical predicate using
register_cb, evaluates a substituted copy under mapped NIA values, and may
request that same predicate with a preferred phase. All enabled modes apply the
same registration policy; only outgoing modes request guidance. Reject unknown,
non-ground, quantified or unsupported inputs. Registration can emit nested clause
notifications, so an adapter reentrancy guard and bounded caches are mandatory.
Integer-variable values must never be mislabeled Boolean phases. Numerical
initialization hints remain restricted to before_check().

Keep accepted-request telemetry unchanged and add distinct proposed-atom
registration, proposed-phase-change, and different-atom-request counters. These
are API-level observations, not private CDCL activity or proof of eventual branch
selection. Tests must preserve SAT/UNSAT and assertions, exercise integer-only
proposed arithmetic decisions, clone/lifetime cleanup, stale epochs, and fork
resume. Review independently before building/freeze; never replace the running
V1 runtime. Judge V2 against matched observe controls and V1 on completed runs.

## V2 finite-cover boundary (evidence-based refinement)

Completed V1 screen117: off solves35, observe27, both27. Both vs observe has
six solved gains and six regressions. Nine solved regressions are shared by the
two enabled modes relative to off; several use the same final bound/query and
450-second proof budget. Changing finite-cover leaves from QF_LIA to callback SMT
lost proof throughput. Therefore all V2 finite-cover leaves retain original
QF_LIA without callbacks. Only an unsupported/inconclusive complete-query
fallback installs the callback adapter. This rule is generic and independent of
benchmark identities. A feasible guarded pigeonhole fixture distinguishes these
paths: its optimum c=1 requires a real linear proof after c<1; all four enabled
modes must prove it with no callback work from finite-cover leaves. The test was
red on V1 and green after restoration.

The proposed-predicate adapter separately caps registration at512 API calls per
adapter in addition to128 cached predicates and128 AST nodes per predicate.
Telemetry distinguishes clause kinds, callback errors, actual decision-callback
counts, proposed registrations/requests, requested phase changes and budget
skips. Accepted requests never imply actual assignment or successful guidance.
All V1 source/runtime snapshots stay immutable; V2 gets a separate frozen binary,
source archive and matched observe/both/incoming-only screening runs when the
allowed nodes are confirmed idle. No full-goal claim from any partial selection.

## V3 continued use of admitted predicates

V2 real logs show the registration-call budget is exhausted during the first
slice on many cases. In one unchanged bound epoch, sequence2 has512 dynamic
requests while sequence3 has zero requests and1886646 skipped callbacks. V3
therefore limits distinct canonical predicates, not lifetime registration calls.
At most512 canonical terms are strongly held, in addition to128 static atoms;
the128-entry phase cache remains bounded. Existing terms can be registered again
after backtracking and consume fresh SLS snapshots. Source inspection and review
confirm owned attachments make repeated same-term calls idempotent and pop
removes previous scoped watchers before reattachment. Actual API-call count and
distinct-admission deltas have separate telemetry. Real callbacks test more than
600 decisions, a stopped/resumed same check with a new snapshot, and full
admission followed by pop/readding old terms. V2 snapshots remain immutable.
