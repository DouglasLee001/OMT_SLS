# Cooperative Z3 scheduling implementation and experiment plan

Goal: improve independent A1+A2 and objective-value losses over the frozen
6310-case NIA_20261027_182302_810605 baseline (5798 solved, 47 losses to the
per-instance best competitor). Aim for at least 30 more solved and at least 10
fewer losses, verified by a complete new full run; do not infer this from a
selected subset. The baseline has three interrupted B rows and remains labelled
incomplete. Keep all raw historical runs and competitor results unchanged.

Design: an opt-in bounded SMT improvement burst. Once the existing stall
criterion gives Z3 a slice, a validated SAT counterexample immediately tightens
the incumbent and continues the same burst with its remaining wall-clock budget.
UNSAT independently certifies the current incumbent; timeout/unknown/import
failure returns control to SLS. Each burst has one fixed deadline, including
parsing, validation, output and all repeated queries. Preserve the default
scheduler, proof checks, Z3 5.1 runtime and SLS search. Retain current resumable
same-bound workers and reset them after a genuine improvement. Do not use
competitor answers to schedule or terminate the solver.

The evidence motivating this change is that SAT acceptance currently imposes
another 50-second stall before any next strict query. Conversely, many of the
60 equal-value B cases already spend about 930 seconds in same-bound proofs;
those require diagnostics rather than a claim that more time solves them.
Compare generic API, explicit QF_NIA API and CLI on the identical retained strict
query before changing proof tactics. Consider additional changes only from this
evidence. Do not claim research novelty without a literature comparison.

- [x] Root: add failing native burst tests; implement bounded burst and CLI flag
  in call.cpp, nia_omt_optimize.h/.cpp, preserving old mode; compile and check.
- [x] Runner worker: add failing option/metadata tests, pass --optimality-burst
  through Config, Slurm snapshot/state/manifest, results and strict verifiers.
- [x] Diagnostics worker: submit 60-case proof-engine diagnostic after checking
  server,node1-5 idle; retain queries, stdout, times and explicit run kind.
- [x] Evidence worker: retain focus293 list, classifications and baseline counts.
- [x] Run focused experiments, including historical regressions and solved
  controls, with fixed 1200-second case budgets, <=180 cases per node. Check
  sinfo/squeue immediately before every submission and use only idle nodes.
  Prefix launches with TMPDIR=/tmp to avoid the previous NFS temp cleanup race.
- [x] Check models, finite proof metadata, ray certificates, complete flags and
  compare focus results. Revise only after inspecting per-case evidence.
- [x] Run complete 6310-case evaluation of the selected policy, generate the
  existing three-table summary and a baseline comparison in its NIA_timestamp
  result directory. Audit target gains, limitations and regressions; document
  the implemented algorithm and commands in NIA_LS docs. Do not commit.

All experiment outputs go under /data/shared/lbh/OMT/result/NIA_<timestamp>.
Analysis-only files may live in /home/lbh/.tmp/omt_schedule_evidence_20261027.

## First diagnostic and controlled experiment evidence

Proof diagnostic NIA_20261027_223617_126494, Slurm87141 on idle node2,
60 retained strict queries x 3 variants x 120 seconds: generic API proved3,
logic-specific API proved4, CLI proved4; remaining tasks timed out. No SAT or
invocation error. Explicit QF_NIA construction is not a general fix.

The three complete-budget focus293 ablations finished from immutable source
snapshots (each pre-submit check and C++ source archive is retained in its run):

| Variant | Slurm job | Result directory | Nodes |
| --- | --- | --- | --- |
| original50 | 87142 | NIA_20261027_224259_815206 | node1,node2 |
| burst50 | 87143 | NIA_20261027_224404_993995 | node3,node4 |
| burst10 | 87144 | NIA_20261027_224510_324532 | server,node5 |

All use1200 seconds/case,180 workers/node, persistent strict-bound sessions,
initial feasibility probe at300 seconds for120 seconds, existing unbounded
proof and magnitude1000000, and node-local TMPDIR=/tmp. Current source changes
do not alter these snapshots. Compare only after complete=true and artifact
validation, using the evidence worker's compare_runs.py; it rejects final goal
claims from partial selections, changed budgets or historical-target stops.

First results (all293 complete): control50 A1/A2/B/C=27/143/104/19, solved170,
losses38; burst50=28/145/102/18, solved173, losses37; burst10=28/156/90/19,
solved184, losses31. Same-binary net for burst10 versus control is+14 solved,
-7 losses. Historical same293 solved160/lost47; its gains cannot all be
attributed to the new policy. Root full-run gate is still unproven.

Finite-cover helper diagnostic NIA_20261027_230233_585355/job87145 completed
on idle node1:60/60,33 UNSAT,1 SAT,26 not applicable,0 timeout/error. The SAT
query retained an old cost<273 bound while the original last incumbent was272;
it is not an optimality counterexample at272. Source-model validation passed.

The repaired native implementation also restores the previous published model
if acceptance crosses its deadline. This closes a real regression check in
test_unbounded_optimization.py (model/objective differed by8192 near2^127).
The8 tests pass after repair. Burst deadlines include parent validation and
acceptance, and defer the separate unbounded check until returning to the loop.

Second same-binary focus293 arms (SHA256
94fb31671412e302d960199fda9c851ebec1b490d1b9eec9aca49c017dd53964):

| Variant | Job | Result directory | Nodes |
| --- | --- | --- | --- |
| burst50+cutset | 87146 | NIA_20261027_230643_424713 | node2,node3 |
| repaired burst10 control | 87148 | NIA_20261027_231007_413053 | node1,node4 |
| burst10+cutset | 87149 | NIA_20261027_231226_950887 | server,node5 |

Job87147/NIA_20261027_230851_865642 was explicitly cancelled and marked
superseded: an accidental temporary refactor during setup changed its binary.
Do not count it in any comparison; retain raw records and the reason. Its
replacement87149 is checked against the exact binary of87146 and87148.
Never edit native sources while preparing a multi-arm binary-frozen batch.

All three second-round arms are complete and strictly artifact-verified with
zero errors. Same293 results: refined burst10=28/155/91/19, solved183/lost34;
burst50+cutset=30/188/56/19, solved218/lost27; burst10+cutset=29/193/52/19,
solved222/lost25. Choose burst10+cutset for full evaluation. It gains42 solved
and loses3 versus refined burst10 (net+39);38 gains have explicit cutset UNSAT
provenance. Historical same293 gains+62/-22 are not a full-run success claim.

## First full6310 evaluation (completed intermediate comparison)

| Arm | Job | Result directory | Nodes |
| --- | --- | --- | --- |
| control50, both new features off | 87150 | NIA_20261027_233354_518309 | node1,node2,node4 |
| burst10+cutset candidate | 87152 | NIA_20261027_234324_686912 | server,node5 |

Both immutable binaries are94fb31671412e302...,180 cases/node and1200 seconds
per case, independent optimality and unbounded proofs, no competitor-target
stops. Candidate was submitted only after293 model/ray/proof metadata audit
passed. Pre-submit idle checks are retained. The originally planned third
candidate node3 was taken by another user's job87151 immediately before launch;
the first attempt failed its idle assertion before any submission. Respect that
job; candidate uses only idle server/node5. Different total node counts affect
batch makespan, so do not compare aggregate wall-clock throughput as solver
speed. Per-instance budgets/concurrency remain the same.

At this stage the goal was still unproven until both full runs and artifact
validation finished. The first full candidate missed the loss target, as
recorded below, and prompted the balanced scheduling refinement.

## Finite nonlinear cutset proof module

Scope: the diagnostics worker owns only new `NIA_LS/src/nia_omt_cutset.h`,
`NIA_LS/src/nia_omt_cutset.cpp`, and `NIA_LS/tests/test_omt_cutset.cpp`.
The root worker integrates build/proof calls separately. Do not submit another
experiment or change production scheduling in this task.

API: `OmtCutsetResult solve_omt_finite_cutset(z3::context&,
const z3::expr_vector& hard_with_strict_bound, double seconds)`. Return
`applicable`, `answer`, optional complete source `model`, `branches`, and
`reason`; incomplete traversal/unknown/timeouts must never return UNSAT.

- [x] Write focused tests and an initially unknown implementation; run the
  standalone C++ test binary and observe the missing finite-cover proof fail.
  Cover finite bilinear UNSAT, SAT model restoration including fixed variables,
  bounded multi-affine objectives with a sum equality, exact strict/negative
  integer bounds, disjunctions that cannot provide unconditional bounds,
  unsupported unbounded nonlinear terms, and exhausted allowance.
- [x] Parse supported integer polynomial atoms exactly with GMP. Only top-level
  conjunction rows provide unconditional linear inequalities; strict integer
  comparisons add one before rounding. Propagate interval bounds using minimum
  row contributions, retaining all Boolean/disjunctive hard assertions.
- [x] Greedily choose explicitly finite variables covering nonlinear monomials
  until every residual monomial has total degree at most one. Enumerate the
  complete finite cover with linear-row pruning after every assignment; do not
  reject a design instance solely because its Cartesian product is large.
- [x] Substitute fixed values into the full original strict formula and check
  the resulting goal with `is-qflia` before using a QF_LIA solver. Retain fixed
  symbol equalities in SAT leaves so the existing bridge receives a full source
  model. All leaves UNSAT certify the original strict formula; SAT returns a
  counterexample; any unvisited or unknown leaf returns unknown.
- [x] Run the standalone regression binary against the exact local Z3 5.1
  library, review bounds/model/completeness invariants, and report the fresh
  red/green evidence and conservative applicability limits. Do not commit.

Correctness invariant: for every partial assignment the propagated domains
still cover every model of the original strict formula extending that prefix.
Each cutset split covers every integer in a logically entailed finite domain.
Thus UNSAT requires exhaustion of the cover, independently of incumbent quality
or any competitor answer. This path intentionally starts from the original
strict formula; no local-search heuristic bounds enter the proof.

Verification: the initial unknown stub failed `finite bilinear cover must prove
UNSAT`. The expanded independent-enumeration test then caught a reused temporary
Z3 AST id being mistaken for a cached prior-leaf polynomial; clearing the cache
before each leaf fixed the demonstrated SAT-to-UNSAT regression. Fresh
`make test-omt-cutset` passes 16 test groups, including 128 independently
enumerated polynomial/linear-row cases, full SAT-model checks, persistent
infinite allowance, and strict negative/arbitrary-precision endpoints.

Cost guard: raw covers up to 50,000 leaves are accepted directly. Larger covers
receive a linear-propagation-only counting preflight, limited to 50,000 leaves,
200,000 nodes, and min(2 seconds, 10% of the remaining allowance); exceeding any
limit returns not-applicable for QF_NIA fallback. The 10-variable [0,5] sum=5
regression retains its 2,002 feasible prefixes despite a raw 6^9 product. A large
80-variable binary cover is rejected. Interval propagation stops conservatively
after 64 rounds, avoiding an endless one-sided negative cycle; stopping does not
invalidate any bound or authorize UNSAT. Construction additionally declines
degree above 32, expansion above 8,192 monomials, or nonlinear cover variables
with interval width above 65,535. These are applicability limits, not proof rules.

## Continue after full loss target was missed

Candidate87152 completed6310 with step0:2347 A1,3515 A2,421 B,27 C;
solved5862, losses44. Historical5798/47 gives+64 solved but only3 fewer losses,
so the original goal remains active. Fresh full control87150 is2350/3439/495/26,
solved5789, losses50; its complete artifact audit passed with zero failures.
Candidate artifact audit runs independently and its frozen files stay unchanged.

There are15 recovered old losses and12 newly introduced losses (original7,
packing5). Original new-loss queries are cover-ineligible, often spending930
seconds without SAT. Five packing workers remain inside finite-cover checks for
630–975 seconds. Backend eligibility alone cannot detect both problems.

Next policy: opt-in `--optimality-balanced`, defaultfalse. After budget/unknown,
retain the doubled next proof slice but give SLS a window equal to
max(configured stall,next slice) before resuming. SAT bursts, incumbent-triggered
halving and proof acceptance stay unchanged. Positive search-window conflicts
with this option. It uses outcome feedback, not instance-family special cases.

- [x] Architecture worker: RED/GREEN schedule tests and one final constructor
  bool in nia_omt_optimality_schedule.h, preserving existing defaults.
- [x] Root: native options/CLI/JSON/constructor wiring and failing integration
  tests; compile after the scheduling implementation is present.
- [x] Runner worker: balanced bool across Config/Slurm/CSV/manifest/summary and
  strict verifiers with historicalfalse compatibility and regression tests.
- [x] Evidence worker: extend fixed focus for all recent/full losses, observed
  successful regressions and60/128 controls. Compare balanced10 and cutset50
  against completed full94fb baselines before another full run.
- [x] Test the remaining net loss deficit of7 while preserving at least30 extra
  solved; recheck chosen nodes idle before each submission and verify full6310
  again. Do not claim success from the focus collection.

Balanced validation: native first-proof/flag regression was RED on the missing
CLI and GREEN after wiring; complete native22 tests pass. One initial30ms-scaled
unbounded probe test transiently failed after compilation; isolated old94fb/new
binary comparisons3 each all proved in about11ms, isolated tests and the full22
rerun passed. No unbounded implementation was changed. Schedule RED/GREEN and
259 script regressions pass; independent increment review found no issues.

The refinement uses369 source files:363 covering all current/historical/full
losses and observed successful regressions, plus6 previously uncovered long
proofs. It includes all21 current A2s requiring>=300 seconds of proof work,
including all11 requiring>450 seconds. Frozen full-candidate369 baseline is
37/219/87/26, solved256, losses44; historical224/47, fresh full-control214/50.

| Refinement | Job | Result directory | Nodes |
| --- | --- | --- | --- |
| balanced10+burst+cutset,369 | 87153 | NIA_20261028_005022_271239 | server,node5 |
| cutset50+burst,369 | 87154 | NIA_20261028_005127_742490 | node1,node2 |
| finite-proof-off new-loss12 | 87155 | NIA_20261028_005239_533990 | node4 |

All three have the same new binary,180 workers/node and1200 seconds/case.
Every launch rechecked all6 nodes and selected only idle ones. The12-case arm
retains original initialization, unbounded proof and large-target search, but
disables finite proof, scalar target and known-optimum stops. It is only a
primal-capacity diagnostic, never final goal evidence. Full94fb artifacts and
the successful full audits remain unchanged.

Matched diagnostic correction: prove-optimal=false also disables the300-second
initial-Z3 fallback in current code. Retain87155 as pure original/progressive
search capacity, with that limitation stated. Additional job87156,
NIA_20261028_010214_245966 on prechecked idle node3, keeps prove-optimal and all
initial300/120 parameters but sets stall=1200, so no finite query can start in
its1200-second case. Both are12-case diagnostics, never goal evidence. Legacy
known-match A2 export in the proof-disabled diagnostic is not an own finite
proof; use objective values, native is_optimal=false and own A1 only.

Balanced369 is complete and strictly verified:49/234/66/20, solved283, losses23;
cutset50 same-binary369:43/232/75/19, solved275, losses32. Balanced vs previous
full's same369 gains31 solved/loses4 (net+27), with21 fewer losses;12 newly
introduced losses recover9. Long-proof21 retain18/lose3 (cyclic1112,
packing1091,portfolio1032); report this tradeoff. A frozen-subset projection
would be5889/23, but this is not full acceptance.

After both369 strict audits passed and idle checks repeated, launched:

| Arm | Job | Directory | Nodes |
| --- | --- | --- | --- |
| full6310 balanced10+burst+cutset | 87157 | NIA_20261028_011635_736862 | server,node1,node2,node5 |
| isolated unbalanced10,369 | 87158 | NIA_20261028_011740_306073 | node4 |

Both use1646d182ff25298f712dc9681f0b05794531b3f82f946473178a20a48707f694,
180 cases/node,1200/case. The369 attribution arm keeps stall10 and toggles only
balanced against87153; their node counts/pools differ, so disclose batching
conditions. Matched diagnostic87156 continues on node3; do not interfere.
This full6310 submission was accepted only after completion, artifact audit and
the original5798/47 threshold comparison, recorded below. Exact commands and
C++ archives are in each run.

## Final full6310 acceptance

Final job87157 completed all6310 cases with complete=true, step return code0 and
no worker errors. Its immutable binary SHA256 is
1646d182ff25298f712dc9681f0b05794531b3f82f946473178a20a48707f694,
matching the current native binary. Strict verification finished normally with
ok=true, case_failures=0 and errors=[]: all6310 rows,6286 retained models,
2352 unbounded certificates and3522 finite-optimality reports checked. The24
remaining cases have no initial solution. All accepted events and proofs meet
the1200-second per-case budget; maximum process cleanup overhead is0.046106s.
The audit checks native constraints, events, retained ray certificates,
finite UNSAT metadata and provenance; it does not replay
every SMT proof or establish an independent preprocessing-equivalence proof.

| Full run | A1 | A2 | B | C | A1+A2 | vsbest W/T/L |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| historical182302 (3 interrupted B retained) | 2355 | 3443 | 487 | 25 | 5798 | 2131/4132/47 |
| fresh control233354, complete | 2350 | 3439 | 495 | 26 | 5789 | 2127/4133/50 |
| intermediate234324, complete | 2347 | 3515 | 421 | 27 | 5862 | 2124/4142/44 |
| final011635, complete | 2352 | 3522 | 412 | 24 | **5874** | **2134/4145/31** |

Against the fixed historical reference this is+76 solved and16 fewer losses,
exceeding both original targets. Against the fresh complete control it is+85
solved and19 fewer losses. Historical baseline interruptions are not silently
filled or relabelled. The final result is one full run, not a per-case merge of
favourable experiments. All runs retain the same frozen competitor result,
1200-second case budgets and180 workers/node. Different node pools/batching
and a single observation limit causal and statistical generalization.

Per-case historical changes include96 newly solved and20 solved regressions;
22 old losses recover and6 new losses appear. Thus the gain is not monotone.
The60 same-value B cases yield36 A2 and24 B, with one new objective loss.
The128 historical solved controls retain125 successes. Of21 protected long
proofs,17 remain solved and4 regress: cyclic1105/1112/1115 andportfolio1032.
The12 intermediate new losses recover8 in the final full run, rather than9
as seen in the earlier369-case batch; report actual full results, not subset
projections. The final packing1081 incumbent is663, distinct from660 in the
369-case run, and its actual trace is retained in the final study.

The additional same-binary369 unbalanced10 control87158 also completed and
passed strict verification:38/223/82/26,261 solved,37 losses. Balanced87153 is
283/23 on the same selection (+22 solved/-14 losses), but uses two nodes rather
than one, so batching is disclosed rather than presenting an isolated causal
estimate. The no-finite-proof12-case diagnostics remain diagnostics only.

Final artifacts: `/data/shared/lbh/OMT/result/NIA_20261028_011635_736862/`.
`summary.md` keeps exactly the requested three statistics sections;
`scheduling_comparison.md/.json/_cases.csv` preserve the historical reference,
and the fresh/prior-full comparison subdirectories preserve both complete
controls. `study.md`, `study.json` and detailed case tables document the
algorithm, focused ablations, family effects, real search/proof traces and
regressions. `verification.json`, source snapshots and pre-submit idle-node
records provide the audit trail. README and implementation details document
the opt-in flags, exact reproduction command and proof limits. No commit.

Root completion audit: independently reloaded historical, fresh-control and
final canonical rows with the official loader and exact objective scoring;
all three6310-case totals and final gate assertions passed. Checked study's
6310 unique case rows,347 change events, three comparison deltas,21 long-proof
rows, verification SHA and unchanged canonical-summary SHA. All17 submitted
experiment preparations retain sinfo/squeue evidence covering server,node1-5;
every selected node was idle in its recorded pre-submit check. Final docs are
written, generated test executable removed, and git diff --check passes.
No converter, competitor, benchmark or tracked historical-result change was
introduced by this scheduling task. No further required work remains.
