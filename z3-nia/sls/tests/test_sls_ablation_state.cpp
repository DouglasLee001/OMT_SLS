// Exercise the exact projection switch used by feasible objective candidates.
// The optimizer translation unit is included to keep State private to its code.
#include "../src/nia_omt_optimize.cpp"

#include <cstdlib>
#include <iostream>
#include <sstream>

namespace {
void check(bool condition, const char* reason) {
    if (!condition) {
        std::cerr << "FAIL: " << reason << '\n';
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
          cost(state.total_cost), iterations(state.iterations),
          distance(state.total_distance) {}
    void unchanged(const nia::State& state) const {
        check(x == state.x && terms == state.terms && delta == state.delta
              && truth == state.truth && sat == state.sat && unsat == state.unsat
              && objective == state.objective && cost == state.total_cost
              && iterations == state.iterations && distance == state.total_distance,
              "candidate previews preserve assignments and every evaluation cache");
    }
};

nia::Assignment apply(nia::Assignment values, const nia::Move& move) {
    for (const auto& entry : move) values[entry.first] = entry.second;
    return values;
}

void projected_objective_candidate() {
    // z=x*x, 0<=x<=3, 0<=z<=9, minimize z. Variable IDs are preserved by import.
    const std::string fixture =
        "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 7a\n"
        "DIRECTION min\nVARIABLES 2\n0 I 78\n1 I 7a\n"
        "LITERALS 5\n1 E 0 2\n1 1 1 1\n-1 1 0 2\n"
        "2 L 0 1\n-1 1 0 1\n3 L -3 1\n1 1 0 1\n"
        "4 L 0 1\n-1 1 1 1\n5 L -9 1\n1 1 1 1\n"
        "CLAUSES 5\n1 1\n1 2\n1 3\n1 4\n1 5\n"
        "OBJECTIVE 0 1\n1 1 1 1\nEND\n";
    nia::ls_solver solver(1, 0, true);
    std::istringstream input(fixture);
    std::string error;
    check(solver.load_omt_instance(input, error), "nonlinear objective-alias fixture imports");
    nia::State state(solver);
    state.reset({1, 1});
    check(state.unsat.empty() && nia::validate(solver, state.x) == 1,
          "own seed is independently feasible with objective one");
    Snapshot before(state);

    nia::OmtOptimalityOptions full;
    check(full.sls_objective_neighborhoods,
          "objective neighborhoods stay enabled in the default variant");
    const auto projected = state.evaluate({{0, 0}}, full.sls_objective_neighborhoods);
    before.unchanged(state);
    const auto projected_values = apply(state.x, projected.move);
    check(projected.cost != UINT64_MAX && projected.violations == 0
          && projected.objective == 0 && projected_values == nia::Assignment({0, 0}),
          "enabled objective projection jointly updates x and its nonlinear cost alias");
    check(nia::validate(solver, projected_values) == 0,
          "projected improving candidate passes independent full-formula validation");

    auto without = full;
    without.sls_objective_neighborhoods = false;
    const auto raw = state.evaluate({{0, 0}}, without.sls_objective_neighborhoods);
    before.unchanged(state);
    const auto raw_values = apply(state.x, raw.move);
    check(raw_values == nia::Assignment({0, 1}) && raw.move.size() == 1,
          "disabled objective projection leaves the unrequested alias unchanged");
    check(raw.violations > 0 && raw.objective == 1,
          "raw candidate is infeasible and cannot advertise a spurious objective improvement");
    bool rejected = false;
    try { (void)nia::validate(solver, raw_values); }
    catch (const std::runtime_error&) { rejected = true; }
    check(rejected, "full original formula rejects the raw inconsistent model");

    // Projection is a shared primitive. Infeasible repair may still
    // request it explicitly when feasible objective neighborhoods are disabled.
    const auto shared = state.evaluate({{0, 0}}, true);
    before.unchanged(state);
    check(shared.violations == 0 && shared.objective == 0,
          "ablation does not remove the shared exact projection capability");
    state.apply(projected);
    check(state.unsat.empty() && state.objective == 0
          && nia::validate(solver, state.x) == 0,
          "applying the projected candidate keeps caches and original formula consistent");
}
}

int main() {
    projected_objective_candidate();
    std::cout << "SLS ablation State checks passed\n";
}
