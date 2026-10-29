#include "nia_omt_feasibility.h"
#include "nia_ls.h"
#include "nia_omt_legacy_arithmetic.h"

#include <algorithm>
#include <chrono>
#include <cmath>

namespace nia {
class OmtFeasibilityContinuation::RunScope {
public:
    RunScope(OmtFeasibilityContinuation& continuation, double global_remaining_seconds)
        : continuation_(continuation), caller_mt_(continuation.solver_.mt),
          caller_start_(continuation.solver_.start), caller_cutoff_(continuation.solver_._cutoff),
          began_(std::chrono::steady_clock::now()), exact_arithmetic_(continuation.exact_arithmetic_) {
        auto& solver = continuation_.solver_;
        solver.mt = continuation_.legacy_mt_;
        solver.start = began_ - std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(continuation_.active_seconds_));
        solver._cutoff = continuation_.active_seconds_ + global_remaining_seconds;
    }

    ~RunScope() {
        auto& solver = continuation_.solver_;
        continuation_.legacy_mt_ = solver.mt;
        continuation_.active_seconds_ = solver.TimeElapsed();
        solver.mt = caller_mt_;
        solver.start = caller_start_;
        solver._cutoff = caller_cutoff_;
    }

    bool within_slice(double seconds) const {
        // Infinity is useful for deterministic step-budget tests. Never cast
        // an infinite duration into the steady clock's integer representation.
        return !std::isfinite(seconds) ||
            std::chrono::duration<double>(std::chrono::steady_clock::now() - began_).count() < seconds;
    }

private:
    OmtFeasibilityContinuation& continuation_;
    std::mt19937 caller_mt_;
    std::chrono::steady_clock::time_point caller_start_;
    double caller_cutoff_;
    std::chrono::steady_clock::time_point began_;
    omt_legacy_arithmetic::Scope exact_arithmetic_;
};

OmtFeasibilityContinuation::OmtFeasibilityContinuation(ls_solver& solver, bool exact_arithmetic)
    : solver_(solver), exact_arithmetic_(exact_arithmetic), legacy_mt_(solver.mt) {}

OmtFeasibilityContinuation::Status OmtFeasibilityContinuation::resume_steps(
        uint64_t max_steps, double slice_seconds, double global_remaining_seconds) {
    if (status_ != Status::paused) return status_;
    // The comparison also rejects NaNs. In particular, a zero-step probe does
    // not reset the step number, initialize, or consume the legacy RNG.
    if (max_steps == 0 || !(slice_seconds > 0) || !(global_remaining_seconds > 0)) return status_;

    RunScope scope(*this, global_remaining_seconds);
    auto& solver = solver_;
    try {
        if (!initialized_) {
            solver._step = 0;
            if (solver.build_unsat) {
                status_ = Status::exhausted;
                return status_;
            }
            // The legacy constructors do not initialize the previous-operation
            // fields, and clear_prev_data() resets them only on later restarts.
            // Establish a defined fresh-search state in optimization mode.
            solver.last_op_var = UINT64_MAX;
            solver.last_op_value = 0;
            solver.initialize();
            solver.random_walk_stall_steps = 0;
            solver.random_walk_window_keeps = 0;
            solver._outer_layer_step = 1;
            solver._step = 1;
            initialized_ = true;
        }

        uint64_t completed = 0;
        const double allotted_seconds = std::min(slice_seconds, global_remaining_seconds);
        for (;;) {
            // This order matches the original for-loop: reaching _max_step exits
            // before another top-of-loop SAT check.
            if (solver._step >= solver._max_step) {
                status_ = Status::exhausted;
                return status_;
            }
            if (solver.unsat_clauses->empty()) {
                solver.up_bool_vars();
                status_ = Status::sat;
                return status_;
            }
            if (completed >= max_steps || !scope.within_slice(allotted_seconds)) return status_;

            if (uint64_t(no_improve_count_) > solver.restart_stall_limit) {
                if (solver.restart_repair_enabled && !restart_prepared_) {
                    solver.prepare_restart_repair();
                    restart_prepared_ = true;
                    if (solver.TimeElapsed() >= solver._cutoff) return status_;
                }
                // Repair and initialization are one logical transition. A slice
                // cannot consume the repair result and then rerun repair on resume.
                solver.initialize();
                restart_prepared_ = false;
                no_improve_count_ = 0;
                if (solver.unsat_clauses->empty()) {
                    ++solver._step;
                    ++steps_completed_;
                    ++completed;
                    continue;
                }
            }
            solver.random_walk_stall_steps = no_improve_count_;
            if (no_improve_count_ == 0) solver.random_walk_window_keeps = 0;
            const bool time_up_bool =
                solver.no_improve_cnt_bool * solver._lit_in_unsat_clause_num >
                5 * solver._bool_lit_in_unsat_clause_num;
            const bool time_up_nia =
                solver.no_improve_cnt_nia * solver._lit_in_unsat_clause_num >
                20 * (solver._lit_in_unsat_clause_num - solver._bool_lit_in_unsat_clause_num);
            if ((solver.is_in_bool_search &&
                 solver._bool_lit_in_unsat_clause_num < solver._lit_in_unsat_clause_num && time_up_bool) ||
                solver._bool_lit_in_unsat_clause_num == 0) {
                solver.enter_nia_mode();
            } else if ((!solver.is_in_bool_search && solver._bool_lit_in_unsat_clause_num > 0 && time_up_nia) ||
                       solver._lit_in_unsat_clause_num == solver._bool_lit_in_unsat_clause_num) {
                solver.enter_bool_mode();
            }

            if (solver.is_in_bool_search) {
                const int variable = solver.pick_critical_move_bool();
                if (variable != -1) solver.critical_move(variable, change_value_);
                if (solver.update_outer_best_solution()) solver.no_improve_cnt_bool = 0;
                else ++solver.no_improve_cnt_bool;
            } else {
                const int variable = solver.pick_critical_move(change_value_);
                if (variable != -1) solver.critical_move(variable, change_value_);
                if (solver.update_inner_best_solution()) solver.no_improve_cnt_nia = 0;
                else ++solver.no_improve_cnt_nia;
            }
            no_improve_count_ = solver.update_best_solution() ? 0 : no_improve_count_ + 1;
            ++solver._step;
            ++steps_completed_;
            ++completed;
        }
    } catch(const omt_legacy_arithmetic::Overflow&) {
        // Only this optional legacy trajectory stops. The caller's GMP engine
        // retains the original problem and any independently accepted model.
        status_=Status::exhausted;
        std::cerr<<"omt_legacy_arithmetic status=exhausted reason=overflow\n";
        return status_;
    }
}

OmtFeasibilityContinuation::Status OmtFeasibilityContinuation::resume_for(
        double slice_seconds, double global_remaining_seconds) {
    return resume_steps(UINT64_MAX, slice_seconds, global_remaining_seconds);
}
}
