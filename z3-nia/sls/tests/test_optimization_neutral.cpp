// Focused State checks: no native CLI, initializer, repair solver, or benchmark run.
#include "../src/nia_omt_optimize.cpp"

#include <cstdlib>
#include <iostream>
#include <sstream>

namespace {
void check(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

struct Snapshot {
    nia::Assignment x;
    std::vector<nia::Int> terms, delta;
    std::vector<bool> truth;
    std::vector<int> sat;
    std::set<size_t> unsat;
    nia::Int objective;
    uint64_t cost, iterations;
    double distance;
    explicit Snapshot(const nia::State& state)
        : x(state.x), terms(state.terms), delta(state.delta), truth(state.truth),
          sat(state.sat), unsat(state.unsat), objective(state.objective),
          cost(state.total_cost), iterations(state.iterations), distance(state.total_distance) {}
    void unchanged(const nia::State& state, const std::string& operation) const {
        check(x == state.x && terms == state.terms && delta == state.delta
              && truth == state.truth && sat == state.sat && unsat == state.unsat
              && objective == state.objective && cost == state.total_cost
              && iterations == state.iterations && distance == state.total_distance,
              operation + " must preserve assignment and exact evaluation caches");
    }
};

std::string fixture(bool negative_literals, bool maximize, bool constant_objective = false) {
    // r in [1,3], p,q in [0,2], p+1<=r, q+1<=r. With negative literals,
    // use NOT(r-p<=0) and NOT(r-q<=0), equivalent over integer assignments.
    std::string result =
        "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 72\nDIRECTION ";
    result += maximize ? "max\n" : "min\n";
    result +=
        "VARIABLES 3\n0 I 72\n1 I 70\n2 I 71\nLITERALS 8\n"
        "1 L 1 1\n-1 1 0 1\n2 L -3 1\n1 1 0 1\n"
        "3 L 0 1\n-1 1 1 1\n4 L -2 1\n1 1 1 1\n"
        "5 L 0 1\n-1 1 2 1\n6 L -2 1\n1 1 2 1\n";
    if (negative_literals) {
        result += "7 L 0 2\n1 1 0 1\n-1 1 1 1\n"
                  "8 L 0 2\n1 1 0 1\n-1 1 2 1\n";
    } else {
        result += "7 L 1 2\n-1 1 0 1\n1 1 1 1\n"
                  "8 L 1 2\n-1 1 0 1\n1 1 2 1\n";
    }
    result += "CLAUSES 8\n1 1\n1 2\n1 3\n1 4\n1 5\n1 6\n";
    result += negative_literals ? "1 -7\n1 -8\n" : "1 7\n1 8\n";
    if (constant_objective) result += "OBJECTIVE 17 0\nEND\n";
    else result += maximize ? "OBJECTIVE 0 1\n-1 1 0 1\nEND\n"
                            : "OBJECTIVE 0 1\n1 1 0 1\nEND\n";
    return result;
}

void load(nia::ls_solver& solver, const std::string& text) {
    std::istringstream input(text);
    std::string error;
    check(solver.load_omt_instance(input, error), "neutral fixture imports: " + error);
}

void plateau_case(bool negative_literals, bool maximize) {
    nia::ls_solver solver(1, 0, true);
    load(solver, fixture(negative_literals, maximize));
    nia::State state(solver);
    state.reset({3, 2, 2});
    check(state.unsat.empty(), "initial plateau assignment is feasible");
    const nia::Int initial = maximize ? -3 : 3;
    check(nia::validate(solver, state.x) == initial, "independent initial objective evaluation");

    // Enumerate the entire small coordinate domains, rather than assuming
    // candidate generation omits a useful strict feasible move.
    for (size_t variable = 0; variable < 3; ++variable) {
        const int lower = variable == 0 ? 1 : 0;
        const int upper = variable == 0 ? 3 : 2;
        for (int value = lower; value <= upper; ++value) {
            auto evaluated = state.evaluate({{variable, value}});
            check(evaluated.violations || !state.better(evaluated.objective, initial),
                  "initial plateau has no strict feasible coordinate improvement");
        }
    }
    Snapshot before_field(state);
    auto field = state.plateau_field();
    before_field.unchanged(state, "plateau field construction");
    check(!field.empty(), "objective-improving probes must produce a neutral pressure field");
    check(state.pressure(field) > 0, "simultaneous tight blockers have positive pressure");

    Snapshot before_preview(state);
    auto preview = state.evaluate({{1, 1}});
    before_preview.unchanged(state, "candidate evaluation preview");
    check(state.pressure(field, &preview) < state.pressure(field),
          "preview recognizes pressure relief from a feasible neutral move");
    before_preview.unchanged(state, "candidate pressure preview");

    nia::State::Evaluation selected;
    const std::function<bool()> exhausted = [] { return false; };
    check(!state.neutral_step(field, selected, exhausted), "neutral search respects an exhausted budget");
    before_preview.unchanged(state, "budget-exhausted neutral preview");

    // Exhaust the budget immediately after the first candidate is found.
    // Returning a previously selected move would let the caller apply it
    // after cancellation, even though an entry-only budget check passes.
    nia::State::Evaluation interrupted;
    const std::function<bool()> interrupted_budget = [&] { return interrupted.move.empty(); };
    check(!state.neutral_step(field, interrupted, interrupted_budget),
          "neutral search must reject a selected move when its budget expires mid-scan");
    before_preview.unchanged(state, "mid-scan budget cancellation");

    const std::function<bool()> available = [] { return true; };
    bool strict_available = false;
    unsigned neutral_steps = 0;
    for (; neutral_steps < 4; ++neutral_steps) {
        const nia::Int old_pressure = state.pressure(field);
        Snapshot before_step(state);
        check(state.neutral_step(field, selected, available), "neutral step finds a compaction move");
        before_step.unchanged(state, "neutral candidate selection");
        check(selected.cost != UINT64_MAX && selected.violations == 0,
              "selected neutral move preserves hard feasibility");
        check(selected.objective == initial, "neutral move preserves the exact primary objective");
        const nia::Int preview_pressure = state.pressure(field, &selected);
        check(preview_pressure < old_pressure,
              "selected neutral move strictly lowers the fixed pressure");
        state.apply(selected);
        check(state.unsat.empty() && nia::validate(solver, state.x) == initial,
              "applied neutral move passes independent full validation");
        check(state.pressure(field) == preview_pressure, "applied pressure matches candidate preview exactly");
        auto improvement = state.evaluate({{0, 2}});
        if (improvement.cost != UINT64_MAX && improvement.violations == 0
            && state.better(improvement.objective, initial)) {
            state.apply(improvement);
            strict_available = true;
            break;
        }
    }
    check(strict_available && neutral_steps < 4, "neutral compaction exposes a strict objective improvement");
    check(nia::validate(solver, state.x) == (maximize ? -2 : 2),
          "strict improvement after neutral moves remains exactly feasible");

    state.reset({1, 0, 0});
    Snapshot at_bound(state);
    auto no_field = state.plateau_field();
    check(no_field.empty(), "no field when no bound-valid objective-improving probe exists");
    at_bound.unchanged(state, "field construction at the objective bound");
}
}

int main() {
    plateau_case(false, false);
    plateau_case(true, false);
    plateau_case(false, true);
    plateau_case(true, true);
    nia::ls_solver solver(1, 0, true);
    load(solver, fixture(false, false, true));
    nia::State state(solver);
    state.reset({3, 2, 2});
    auto field = state.plateau_field();
    check(field.empty(), "constant objective has no neutral pressure field");
    std::cout << "neutral plateau checks passed\n";
}
