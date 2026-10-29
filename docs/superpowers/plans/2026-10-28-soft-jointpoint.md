# Soft Jointpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a bounded set of actual, jointly observed Z3 integer predicate assignments into one soft native multivariable kick, while retaining the existing exact incumbent checks.

**Architecture:** Decode supported actual fixed Boolean predicates into exact unary integer intervals, trail their intersection across the same Z3 branch, and clamp the current native snapshot into that partial box. A generation- and revision-scoped shared mailbox transports a complete point only after atomic commitment. Native search consumes it once at the existing kick location; it never changes the hard formula, native hard bounds, or the proof conditions.

**Tech Stack:** C++17, pinned Z3 5.1.0 AST API, GMP, existing MAP_SHARED exchange protocol, native State evaluation and full incumbent validation.

---

## Ownership and scope

The helper task owns only these three new files:

- `NIA_LS/src/nia_omt_soft_jointpoint.h`
- `NIA_LS/tests/test_omt_soft_jointpoint.cpp`
- `docs/superpowers/plans/2026-10-28-soft-jointpoint.md`

It does not change the Makefile, V3 source, existing plans, adapter, protocol, optimizer, or benchmark jobs. The adapter owner owns `nia_omt_exchange_z3.h/.cpp` and its existing tests. The protocol owner owns `nia_omt_search_exchange.h/.cpp` and protocol tests. Root owns production lifetime declaration order, State integration, and the final integration checks. Finish and freeze the independent V3 budget repair before measuring this increment against that matched control.

This document records the approved overall design. Its completed implementation checklist below covers the helper only; the transport, callback anchor, and State integration have separate owners.

## Evidence and expected benefit

The inspected design examples have 11 native integer coordinates, no Boolean variables or equality definitions, and dense cubic or quartic objectives. The portfolio examples have 12 or 16 native integer coordinates, no auxiliary definitions, linear hard constraints, and quadratic objectives. Root dependency reduction is therefore an identity operation for these examples. Variable frequency sorting normally revisits the same candidate set, while the existing feasible-coordinate path rejects every non-strict objective improvement.

The new information is a jointly observed branch assignment, not another structural block-move pattern. For `0 <= x,y,z <= 1`, minimizing `x+y+z-4*x*y*z`, the point `000` has value zero; every proper nonempty subset of coordinates moved to one is worse, but the jointly observed point `111` has value minus one. Unary endpoints are already present in the native domains; their joint assignment is the increment.

A point describes one coherent observed partial branch, not a full tableau or a guaranteed model at the exact SIGSTOP instant. Actual useful bound coverage and benchmark gains remain empirical questions. No helper test invokes `solver.check()`.

## Helper API and exact semantics

The header provides these types in namespace `nia`:

```cpp
struct SoftJointInterval {
    size_t slot = SIZE_MAX;
    __int128_t lower = soft_joint_minimum();
    __int128_t upper = soft_joint_maximum();
};
using SoftJointValue = std::pair<size_t, __int128_t>;
using SoftJointSlotLookup = std::function<bool(const z3::expr&, size_t&)>;
enum class SoftJointDecode { ignored, interval, invalid };

SoftJointDecode decode_soft_joint_fixed(const z3::expr& term, bool truth,
    const SoftJointSlotLookup&, SoftJointInterval& output) noexcept;

class SoftJointBox {
public:
    static constexpr size_t max_slots = 16, max_trail = 256, max_depth = 1024;
    SoftJointDecode fixed(const z3::expr&, bool truth, const SoftJointSlotLookup&) noexcept;
    bool intersect(const SoftJointInterval&) noexcept;
    void push() noexcept;
    void pop(unsigned scopes) noexcept;
    void invalidate() noexcept;
    void reset() noexcept;
    bool valid() const noexcept;
    size_t slots() const noexcept;
    size_t trail_size() const noexcept;
    size_t depth() const noexcept;
    std::vector<SoftJointValue> build(const SearchExchangeSnapshot&) const noexcept;
};
```

The lookup must map an exact native source leaf of integer sort to its native slot. Arithmetic symbols unknown to that map reject the entire predicate; they are never partially stripped out. The helper does not retain AST identifiers, expressions, solver references, callback anchors, or channel state.

Supported Boolean syntax is negation plus integer `<=`, `<`, `>=`, `>`, and a true equality. Arithmetic syntax is integer numerals, mapped integer leaves, addition, subtraction, unary minus, and multiplication by numeric affine constants. After exact cancellation there must be one nonzero source coefficient. Unsupported syntax, multiple remaining source variables, unknown auxiliaries, nonlinear multiplication, division/modulo, and a false equality return `ignored`. A false equality denotes a hole, not a convex interval.

All affine arithmetic uses GMP. Normalize an inequality to `a*x+b <= 0`; its false phase is `1-(a*x+b) <= 0`. Strict integer comparisons add one before rounding. Positive `a` gives `x <= floor(-b/a)`; negative `a` gives `x >= ceil(-b/a)`. A true equality requires exact divisibility. Final endpoints must fit the signed 128-bit range. Impossible constants, indivisible true equalities, out-of-range endpoints, parser limits, or exceptions return `invalid`; the output argument remains unchanged on every non-success result.

The parser bounds one decode to 64 AST visits, 128 characters per numeral, and 1024 bits per arithmetic coefficient or constant. Repeated DAG edges count as visits so recursion and work remain bounded. GMP intermediate values may exceed 128 bits within that budget. Narrowing handles `INT128_MIN` without negating it in the signed native type.

The box stores at most 16 source slots and 256 tightening undo entries. Root-level facts need no undo entry. Repeating a bound that does not tighten the interval does not grow the trail. Each push saves the undo offset and prior invalid flag. Pop restores all requested scope frames, including the validity flag after empty intervals, arithmetic failures, and slot/trail limits. Up to 1024 frames are stored; deeper scopes suppress output and their pops restore the pre-overflow validity without growing frame storage. An unbalanced pop clears the data and remains invalid until explicit reset.

`build(snapshot)` requires nonzero formula token, bound epoch and sequence, valid integer native slots, a nonempty valid box, and at least two snapshot values changed by clamping. Its sorted vector includes **every restricted slot**, even a slot unchanged from the original snapshot: the parent State can drift before the eventual kick. Allocation failure or a malformed snapshot returns an empty vector without mutating branch state. Source formula/epoch matching and primary-root membership remain bridge/parent responsibilities.

## Callback and lifetime integration contract

Only actual fixed notifications update the box. A decision callback's requested phase is not an actual assignment. Reuse predicates already watched under the V3 registration policy; this feature introduces no registrations and does not alter its registration budget.

The adapter connects `push()` and `pop(n)` to real user-propagator scope callbacks. Scope tracking must remain balanced even during the adapter's registration guard. Arithmetic fixed notifications skipped by that guard merely leave a conservative observed subset. Decode via the existing exact source AST map; do not duplicate the parser in the adapter.

Use a caller-owned, non-copyable, non-movable `OmtZ3ExchangeScopeAnchor`, containing an owner pointer and an attach-once flag. Declare it before the `z3::context`, then declare the parser/solver, and finally the adapter. It contains no Z3 objects. The adapter sets its owner before installation and clears it first on constructor failure and destruction. Every callback checks the anchor and owner; null user contexts from fresh clones are no-ops. The same anchor cannot attach to another adapter later.

Do not detach scope callbacks by calling `Z3_solver_propagate_init()` again. In the pinned implementation `smt_context.cpp::user_propagate_init` allocates a new user theory, while `register_plugin` deletes a duplicate family plugin instead of replacing the old one. Reinitialization does not safely remove the original callbacks. Existing clause/fixed/decide replacement remains a secondary cleanup; a live anchor with null owner is the lifetime guarantee.

Incoming-only operation rebuilds points from `SearchExchangeSnapshot::variables`, not `cached_values`, which is populated only when sending SLS hints to Z3. A new source snapshot within the same strict-query epoch leaves the branch trail intact and rebuilds numerical point values from the new source assignment. A different query scope cannot reuse the old box.

## Atomic transport contract

The protocol owner supplies a bounded `SearchJointPoint` containing validity, formula token, bound epoch, sequence, branch revision, depth, and at most 16 `(native slot, signed int128 value)` entries. Shared memory contains only fixed-capacity 64-bit atomic fields; int128 values use two words. No shared vector, GMP object, pointer, or process-shared mutex is introduced.

A 64-bit control word encodes the existing 32-bit generation and a 30-bit mailbox revision plus two-bit `EMPTY/WRITING/READY` status. Generation and revision must not wrap.

1. Before changing the tracked box, the child validates scope and CAS-claims a new exact `WRITING` ticket, invalidating any prior box.
2. It updates branch state, builds the whole point, writes the complete bounded atomic payload, revalidates formula/epoch/sequence, and release-CASes that exact ticket to `READY`. A missing point or exception cancels the ticket.
3. The parent drains only after `waitpid` confirms STOPPED or termination/reap. It never waits for a stopped writer. A `WRITING` or invalid packet is cleared with a newer revision and returns no point.
4. A `READY` packet is acquire-read in full, followed by an acquire fence and control/header revalidation, then consumed exactly once by CAS to a newer `EMPTY` revision.
5. Parent snapshot publication and epoch reset install a new-generation empty control word. A child stopped halfway through writing can continue payload stores on resume, but its exact old ticket can never commit into the new generation.
6. Parent rejection or clearing in the same generation also advances revision. Consequently a paused old writer cannot resurrect the point after the parent rejected it.

Payload emission must not call Z3 operations that can reenter callbacks. This preserves the existing single-child-writer model. Parent and child execution remain sequential under the existing stop/continue lifecycle.

## State integration contract

The State bridge keeps one pending point. Each proof-return frame replaces or clears it, including a frame with no valid box. Query reset, accepted incumbent, channel failure, and malformed scope clear it. `clear_search_feedback()` clears it alongside the existing scores. Observe and SLS-to-Z3 modes may record telemetry but cannot consume native points; Z3-to-SLS and both can consume them. Off creates no exchange data.

At the existing kick location, after resetting the exploratory State to its retained representative, take the pending point once and form the whole move. Require distinct, in-range, integer primary native slots and at least two values changed relative to the actual current State. Reject the whole point if any entry fails; do not invent an inverse mapping from a derived auxiliary to roots.

Call the existing `State::evaluate(move, true, true)`. A hard-feasible strict improvement may use the existing `apply()` and `accept()` flow, which independently recomputes every hard clause and objective in GMP before publication. A representable non-improving or hard-infeasible point may replace one existing random diversification kick only when there is no better established improving kick, then use the existing native repair loop. It is never an incumbent by itself and is not supplied as the valid incumbent required by `OmtBoundRepair`. `UINT64_MAX` cost, empty moves, invalid boxes and out-of-range values fall back to the original kick.

Limit this to one point attempt per returned proof slice. Add no solver, extra repair allowance, hard box constraint, proof shortcut, or change to an already proved model.

## Helper task checklist and validation

- [x] Add standalone test before the helper. Its header-absence compatibility path compiles and exits one with `FAIL: soft jointpoint helper is not implemented`.
- [x] Confirm that RED failure before writing production helper code.
- [x] Implement exact AST decoding, interval intersection, bounded trail and joint construction in the assigned new header.
- [x] Compile and run real Z3 AST/GMP tests without any solver search or existing-object rebuild.
- [x] Test the cubic `000 -> 111` example and every proper nonempty subset.
- [x] Check supported affine intervals against independent small integer arithmetic across positive/negative coefficients, four comparisons, and both phases.
- [x] Check nested and multi-scope pop, duplicate fixed notifications, malformed snapshots, unknown symbols, large GMP intermediates, 128-bit endpoints, parser limits, slot/trail/depth limits, and restoration after invalidation.
- [x] Complete ASan/UBSan verification with leak checking and independent read-only review before the helper handoff.

Standalone optimized build, from `/home/lbh/OMT`:

```bash
g++ -std=c++17 -Wall -Wextra -Werror -O2 \
  -isystem competitor/z3-5.1.0/source/src/api/c++ \
  -isystem competitor/z3-5.1.0/source/src/api \
  NIA_LS/tests/test_omt_soft_jointpoint.cpp \
  -Lcompetitor/z3-5.1.0/build \
  -Wl,-rpath,/home/lbh/OMT/competitor/z3-5.1.0/build \
  -l:libz3.so.5.1.0.0 -lgmpxx -lgmp \
  -o /tmp/nia-soft-jointpoint-tests
/tmp/nia-soft-jointpoint-tests
```

Expected: exit zero and `soft jointpoint helper checks passed`, with no warnings.

Sanitizer build uses the same command with `-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer` instead of `-O2`, and output `/tmp/nia-soft-jointpoint-tests-sanitized`:

```bash
ASAN_OPTIONS=detect_leaks=1 /tmp/nia-soft-jointpoint-tests-sanitized
```

Expected: the same success message, exit zero, and no sanitizer diagnostics. These commands write only temporary test binaries and do not touch V3 objects or invoke solver search.

## Remaining owner integration checks

- [ ] Protocol: deterministic real-fork SIGSTOP checkpoints after WRITING, after the first payload entry and before commit; parent clear, new sequence and new epoch each prevent stale commit or replay. Check full int128 transport, one-shot drain, and rejection without confirmed child stop.
- [ ] Adapter: real fixed notifications are distinct from requested phases; push/pop keeps one coherent partial branch; source snapshot replacement rebuilds values; incoming-only mode works without outgoing cached values.
- [ ] Lifetime: after adapter destruction, solver check/pop and context destruction remain safe under ASan; constructor failure after hook installation clears owner; fresh-null clones do nothing; a second attach is refused.
- [ ] State: evaluate/preview remains transactional, `111` passes the unrestricted cubic formula and improves the independently validated objective, and adding hard `x+y+z <= 2` rejects its publication. Popped phases and cleared/new-scope points cannot be reused.
- [ ] Modes: observe and SLS-to-Z3 never alter State through this point; incoming-only and both can use one point; no extra RNG draw occurs for a rejected or absent point.
- [ ] Compare the completed increment against frozen V3, then apply the existing full-corpus success/loss gate before making any performance claim.

## Helper handoff evidence

On 2026-10-28 the optimized `-Wall -Wextra -Werror` build and the ASan/UBSan build with `detect_leaks=1` both exited zero with `soft jointpoint helper checks passed`. The initial standalone RED binary exited one with `FAIL: soft jointpoint helper is not implemented` before the header was written.

The protocol owner independently read the helper, tests, and this design and rebuilt and ran the optimized real AST/GMP tests. The review found no important issue and explicitly checked negative-coefficient rounding, integer complements, exact equality division, `INT128_MIN`, rejection of unknown auxiliaries despite algebraic cancellation, resource-limit rollback, and inclusion of unchanged constrained slots. No solver search, existing-object rebuild, benchmark job, or existing-source edit was part of this helper task.
