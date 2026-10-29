// Deterministic legacy continuation tests. No CLI benchmark or repair solver.
#include "../src/nia_omt_feasibility.h"
#include "../src/nia_ls.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <new>
#include <sstream>
#include <string>
#include <vector>

namespace {
bool fail_next_allocation = false;
}

// Make initialize() throw at its first cache allocation, so exception safety is
// tested without adding a test hook to production code or relying on timeouts.
void* operator new(std::size_t bytes) {
    if (fail_next_allocation) {
        fail_next_allocation = false;
        throw std::bad_alloc();
    }
    if (void* memory = std::malloc(bytes ? bytes : 1)) return memory;
    throw std::bad_alloc();
}
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }

namespace {
using Continuation = nia::OmtFeasibilityContinuation;
using Status = Continuation::Status;

void check(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

std::string fixture(bool satisfiable = false) {
    // The quadratic integer equation is impossible. Four binary clauses also
    // conflict without unit clauses, exercising both search modes and RNG.
    if (!satisfiable) return
        "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
        "VARIABLES 3\n0 I 78\n1 B 62\n2 B 63\n"
        "LITERALS 3\n1 E -2 1\n1 1 0 2\n2 B 1\n3 B 2\n"
        "CLAUSES 5\n1 1\n2 2 3\n2 -2 3\n2 2 -3\n2 -2 -3\n"
        "OBJECTIVE 0 1\n1 1 0 1\nEND\n";
    return
        "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
        "VARIABLES 2\n0 I 78\n1 B 62\n"
        "LITERALS 2\n1 E -1 1\n1 1 0 1\n2 B 1\n"
        "CLAUSES 2\n1 1\n1 2\nOBJECTIVE 0 1\n1 1 0 1\nEND\n";
}

void load(nia::ls_solver& solver, bool satisfiable = false) {
    std::istringstream input(fixture(satisfiable));
    std::string error;
    check(solver.load_omt_instance(input, error), "fixture imports: " + error);
    check(!solver.build_unsat, "fixture reaches the real legacy loop");
    solver.restart_stall_limit = 3;
    solver.restart_repair_enabled = false;
    solver._cutoff = 60;
}

template<class T> void scalar(std::ostream& out, T value) { out << value << ','; }
void scalar(std::ostream& out, __int128_t value) {
    // All fixture values fit int64; encode without an overloaded << extension.
    out << static_cast<long long>(value) << ',';
}
template<class T> void sequence(std::ostream& out, const std::vector<T>& values) {
    out << '[';
    for (const auto& value : values) scalar(out, value);
    out << ']';
}
void array(std::ostream& out, const Array* values) {
    if (!values) { out << "null"; return; }
    out << '[';
    for (int i = 0; i < values->size(); ++i) scalar(out, values->element_at(i));
    out << ']';
    // Membership indices affect future insert/delete operations as well.
    for (int i = 0; i < values->capacity(); ++i) scalar(out, values->index_in_array[i]);
}

std::string topology(const nia::ls_solver& s) {
    std::ostringstream out;
    scalar(out, s._num_vars); scalar(out, s._num_lits); scalar(out, s._num_clauses);
    for (const auto& v : s._vars) {
        out << v.var_name;
        scalar(out, v.is_nia); scalar(out, v.low_bound); scalar(out, v.upper_bound);
        sequence(out, v.clause_idxs); sequence(out, v.literal_idxs);
        for (const auto& t : v.term_exps) { scalar(out, t.term_idx); scalar(out, t.exponent); }
    }
    for (const auto& t : s._terms)
        for (const auto& factor : t.var_epxs) { scalar(out, factor.var_index); scalar(out, factor.exponent); }
    for (const auto& l : s._lits) {
        scalar(out, l.lits_index); scalar(out, l.is_equal); scalar(out, l.is_nia_lit); scalar(out, l.key);
        for (const auto& c : l.coff_terms) { scalar(out, c.term_idx); scalar(out, c.coff); }
    }
    for (const auto& c : s._clauses) {
        sequence(out, c.literals); sequence(out, c.nia_literals); sequence(out, c.bool_literals);
    }
    return out.str();
}

std::string state(const nia::ls_solver& s) {
    std::ostringstream out;
    sequence(out, s._solution); sequence(out, s._best_solutin);
    sequence(out, s._best_literal_assignment);
    for (const auto& v : s._vars) {
        scalar(out, v.score); scalar(out, v.up_bool);
        scalar(out, v.scoring_coffs_valid); sequence(out, v.scoring_coffs);
    }
    for (const auto& t : s._terms) scalar(out, t.value);
    for (const auto& l : s._lits) { scalar(out, l.delta); scalar(out, l.is_true); }
    for (const auto& c : s._clauses) {
        scalar(out, c.weight); scalar(out, c.sat_count); scalar(out, c.watch_lit_idx);
    }
    array(out, s.unsat_clauses); array(out, s.sat_clause_with_false_literal);
    array(out, s.contain_bool_unsat_clauses); array(out, s.false_lit_occur);
    array(out, s.var_in_long_term);
    sequence(out, s.tabulist); sequence(out, s.last_move);
    sequence(out, s.is_chosen_bool_var); sequence(out, s._frequency_in_unsat_clauses);
    sequence(out, s.operation_var_idx_vec); sequence(out, s.operation_change_value_vec);
    sequence(out, s.operation_var_idx_bool_vec); sequence(out, s.term_coffs);
    sequence(out, s._score_clause_deltas); sequence(out, s._score_touched_clauses);
    scalar(out, s._step); scalar(out, s._outer_layer_step);
    scalar(out, s.no_improve_cnt_bool); scalar(out, s.no_improve_cnt_nia);
    scalar(out, s.random_walk_stall_steps); scalar(out, s.random_walk_window_keeps);
    scalar(out, s.is_in_bool_search); scalar(out, s._best_found_hard_cost_this_bool);
    scalar(out, s._best_found_hard_cost_this_nia); scalar(out, s.last_op_var); scalar(out, s.last_op_value);
    scalar(out, s.best_found_cost); scalar(out, s.best_found_this_restart);
    scalar(out, s.initialization_round); scalar(out, s.total_clause_weight);
    scalar(out, s._lit_in_unsat_clause_num); scalar(out, s._bool_lit_in_unsat_clause_num);
    scalar(out, s.candidate_cache_hits); scalar(out, s.candidate_cache_rebuilds);
    for (const auto& cache : s.critical_operation_caches) {
        scalar(out, cache.valid);
        for (const auto& op : cache.moves) { scalar(out, op.first); scalar(out, op.second); }
    }
    scalar(out, s.restart_repair_calls); scalar(out, s.restart_repair_installed);
    scalar(out, s.restart_repair_failures);
    sequence(out, s.restart_best_assignment); sequence(out, s.restart_seed);
    return out.str();
}

void trajectory(int seed, uint64_t restart_limit) {
    constexpr uint64_t total_steps = 257;
    nia::ls_solver uninterrupted(seed, total_steps + 1, true);
    nia::ls_solver sliced(seed, total_steps + 1, true);
    load(uninterrupted); load(sliced);
    // Define the unchanged legacy reference's otherwise uninitialized fields.
    // The continuation must establish its own sentinels from a stale state.
    uninterrupted.last_op_var = UINT64_MAX;
    uninterrupted.last_op_value = 0;
    sliced.last_op_var = 0;
    sliced.last_op_value = 7;
    uninterrupted.restart_stall_limit = sliced.restart_stall_limit = restart_limit;
    const std::string original_topology = topology(sliced);
    check(!uninterrupted.local_search(), "uninterrupted impossible fixture exhausts its step budget");
    check(uninterrupted._step == total_steps + 1, "reference executed the requested legacy steps");
    check(uninterrupted.initialization_round > 1, "reference exercises restart initialization");
    check(uninterrupted.candidate_cache_rebuilds > 0, "reference exercises real critical moves");
    if (restart_limit > 10)
        check(uninterrupted._outer_layer_step > 1, "reference exercises Boolean as well as arithmetic moves");

    Continuation continuation(sliced);
    const auto caller_start = std::chrono::steady_clock::time_point(std::chrono::seconds(123));
    sliced.start = caller_start;
    sliced._cutoff = 987;
    uint64_t done = 0;
    const uint64_t chunks[] = {1, 7, 31};
    unsigned chunk = 0;
    while (done < total_steps) {
        const uint64_t count = std::min(chunks[chunk++ % 3], total_steps - done);
        // Simulate exact optimization consuming its own RNG between slices.
        for (unsigned i = 0; i < chunk * 3; ++i) sliced.mt();
        const auto caller_rng = sliced.mt;
        const Status status = continuation.resume_steps(count, 60, 60);
        done += count;
        check(continuation.steps_completed() == done, "step-budget resume executes each legacy iteration exactly once");
        check(status == (done == total_steps ? Status::exhausted : Status::paused),
              "resume distinguishes a paused slice from the original max-step limit");
        check(sliced.mt == caller_rng, "legacy resume restores the caller RNG after every slice");
        check(sliced.start == caller_start && sliced._cutoff == 987,
              "legacy resume restores the caller clock and cutoff");
        check(topology(sliced) == original_topology, "initialization and restarts preserve the imported topology");
    }
    check(state(sliced) == state(uninterrupted), "sliced legacy search matches uninterrupted search caches and counters");
    check(continuation.random_state() == uninterrupted.mt, "sliced legacy RNG exactly matches uninterrupted search");
    check(continuation.active_seconds() > 0, "continuation accounts for active search time");
    check(sliced.restart_repair_calls == 0, "focused trajectory test never invokes a repair solver");
    std::cout << "PASS trajectory seed=" << seed << " steps=" << done
              << " restarts=" << sliced.initialization_round - 1
              << " bool_moves=" << sliced._outer_layer_step - 1 << '\n';
}

void exhausted_budgets() {
    nia::ls_solver solver(17, 100, true);
    load(solver);
    Continuation continuation(solver);
    const auto rng = solver.mt;
    check(continuation.resume_steps(0, 60, 60) == Status::paused, "zero steps pause");
    check(continuation.resume_steps(1, 0, 60) == Status::paused, "expired slice pauses");
    check(continuation.resume_steps(1, 60, 0) == Status::paused, "expired global budget pauses");
    check(continuation.resume_steps(1, -1, 60) == Status::paused, "negative slice pauses");
    check(continuation.resume_steps(1, std::numeric_limits<double>::quiet_NaN(), 60) == Status::paused,
          "NaN budget cannot start work");
    check(continuation.resume_steps(1, 60, std::numeric_limits<double>::quiet_NaN()) == Status::paused,
          "NaN global budget cannot start work");
    check(!continuation.initialized() && solver.initialization_round == 0
          && continuation.steps_completed() == 0 && solver.mt == rng,
          "zero or expired budgets never initialize or consume RNG");
}

void fresh_initialization_resets_last_operation() {
    nia::ls_solver solver(37, 100, true);
    std::istringstream input(
        "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
        "VARIABLES 1\n0 I 78\nLITERALS 1\n1 E 0 1\n1 1 0 1\n"
        "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n");
    std::string error;
    check(solver.load_omt_instance(input, error), "fresh initial-SAT fixture imports: " + error);
    // Legacy constructors leave these fields uninitialized. Supply a known
    // stale operation so the test detects the missing initialization without
    // reading indeterminate memory or relying on a particular stack layout.
    solver.last_op_var = 0;
    solver.last_op_value = 7;
    Continuation continuation(solver);
    check(continuation.resume_steps(0, 60, 60) == Status::paused
          && solver.last_op_var == 0 && solver.last_op_value == 7
          && solver.initialization_round == 0,
          "zero-step continuation preserves stale operation fields without initializing");
    check(continuation.resume_for(60, 60) == Status::sat && continuation.steps_completed() == 0,
          "fresh fixture is SAT before any critical move can hide stale operation fields");
    check(solver.last_op_var == UINT64_MAX && solver.last_op_value == 0,
          "fresh optimization feasibility initialization resets the last-operation sentinel");
}

void exception_restores_caller() {
    nia::ls_solver solver(29, 100, true);
    load(solver);
    Continuation continuation(solver);
    // Use a different caller stream from the saved legacy stream.
    solver.mt(); solver.mt();
    const auto caller_rng = solver.mt;
    const auto caller_start = std::chrono::steady_clock::time_point(std::chrono::seconds(456));
    solver.start = caller_start;
    solver._cutoff = 321;
    bool threw = false;
    fail_next_allocation = true;
    try { continuation.resume_steps(1, 60, 60); }
    catch (const std::bad_alloc&) { threw = true; }
    fail_next_allocation = false;
    check(threw, "allocation failure actually interrupts legacy initialization");
    check(solver.mt == caller_rng && solver.start == caller_start && solver._cutoff == 321,
          "exception restores caller RNG, clock and cutoff");
    check(!continuation.initialized() && continuation.steps_completed() == 0,
          "interrupted initialization does not count as a completed step");
    check(continuation.resume_steps(1, std::numeric_limits<double>::infinity()) == Status::paused
          && continuation.steps_completed() == 1,
          "continuation remains usable after an allocation exception and accepts an infinite step-only clock");
}

void restart_budget_gate() {
    constexpr uint64_t total_steps = 12;
    nia::ls_solver reference(31, total_steps + 1, true), sliced(31, total_steps + 1, true);
    load(reference); load(sliced);
    reference.last_op_var = UINT64_MAX;
    reference.last_op_value = 0;
    reference.restart_repair_enabled = sliced.restart_repair_enabled = true;
    // This invokes only the existing zero-budget gate, never a repair solver.
    reference.restart_repair_budget = sliced.restart_repair_budget = 0;
    const auto original_topology = topology(sliced);
    check(!reference.local_search(), "repair-gate reference exhausts its step budget");
    Continuation continuation(sliced);
    for (uint64_t i = 0; i < total_steps; ++i) continuation.resume_steps(1, 60, 60);
    check(sliced.restart_repair_calls > 0 && sliced.restart_repair_installed == 0,
          "test exercises restart repair's budget gate without a solver search");
    check(state(sliced) == state(reference) && continuation.random_state() == reference.mt,
          "restart repair counters, seeds, and enabled-mode reset match original loop");
    check(topology(sliced) == original_topology, "restart repair retains literal, term and clause topology");
}

void sat_is_terminal() {
    nia::ls_solver reference(23, 100, true), sliced(23, 100, true);
    load(reference, true); load(sliced, true);
    reference.last_op_var = UINT64_MAX;
    reference.last_op_value = 0;
    check(reference.local_search(), "reference fixture reaches SAT");
    Continuation continuation(sliced);
    Status status = Status::paused;
    for (unsigned i = 0; i < 100 && status == Status::paused; ++i)
        status = continuation.resume_steps(1, 60, 60);
    check(status == Status::sat, "step-sliced legacy search reaches SAT");
    check(state(sliced) == state(reference) && continuation.random_state() == reference.mt,
          "SAT top-of-loop check and bool reconstruction match original search");
    const auto snapshot = state(sliced);
    const auto rng = continuation.random_state();
    check(continuation.resume_for(60, 60) == Status::sat, "SAT continuation stays terminal");
    check(state(sliced) == snapshot && continuation.random_state() == rng,
          "SAT continuation cannot rerun initialization or bool reconstruction");
}
}

int main() {
    fresh_initialization_resets_last_operation();
    exhausted_budgets();
    trajectory(1, 3);
    trajectory(47, 17);
    exception_restores_caller();
    restart_budget_gate();
    sat_is_terminal();
    std::cout << "PASS optimization-only legacy continuation\n";
}
