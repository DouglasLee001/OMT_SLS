#pragma once

#include <cstdint>
#include <limits>
#include <random>

namespace nia {
class ls_solver;

// Optimization-only continuation of the legacy feasibility search. The solver
// retains its search caches; this object retains locals from local_search().
class OmtFeasibilityContinuation {
public:
    enum class Status { paused, sat, exhausted };

    // Own-bound repair keeps the exact default. Initial feasibility may opt
    // into the unchanged legacy arithmetic heuristic with false.
    explicit OmtFeasibilityContinuation(ls_solver& solver, bool exact_arithmetic = true);

    OmtFeasibilityContinuation(const OmtFeasibilityContinuation&) = delete;
    OmtFeasibilityContinuation& operator=(const OmtFeasibilityContinuation&) = delete;

    // Budgets in seconds; nonpositive or NaN budgets do no work. The global
    // budget is deliberately independent of the slice: repair keeps its normal
    // allowance even when a slice boundary is close. Pauses occur between full
    // legacy iterations, so a single operation can finish after a slice ends.
    Status resume_steps(uint64_t max_steps, double slice_seconds,
                        double global_remaining_seconds = std::numeric_limits<double>::infinity());
    Status resume_for(double slice_seconds, double global_remaining_seconds);

    Status status() const { return status_; }
    bool initialized() const { return initialized_; }
    uint64_t steps_completed() const { return steps_completed_; }
    int no_improve_count() const { return no_improve_count_; }
    double active_seconds() const { return active_seconds_; }
    const std::mt19937& random_state() const { return legacy_mt_; }

private:
    class RunScope;
    ls_solver& solver_;
    bool exact_arithmetic_;
    std::mt19937 legacy_mt_;
    Status status_ = Status::paused;
    bool initialized_ = false;
    bool restart_prepared_ = false;
    uint64_t steps_completed_ = 0;
    int no_improve_count_ = 0;
    __int128_t change_value_ = 0;
    double active_seconds_ = 0;
};
}
