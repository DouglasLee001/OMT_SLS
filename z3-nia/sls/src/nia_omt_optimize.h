#pragma once
#include <string>
namespace nia {
class ls_solver;
struct OmtOptimalityOptions {
    bool enabled = false;
    // Direct full-source default Z3 check, with no auxiliary proof stages.
    bool plain_z3_proof = false;
    double stall_seconds = 50.0;
    double timeout_seconds = 30.0;
    double search_window_seconds = 0.0;
    std::string query_output;
    double initial_z3_after_seconds = 0.0;
    double initial_z3_timeout_seconds = 120.0;
    bool resume = false;
    bool burst = false;
    bool cutset = false;
    bool balanced = false;
    bool tail = false;
    double advisory_share = 0.0;
    // Zero keeps the original proof solver. Positive values bound the native
    // BV tactic in wall time before the original default strategy resumes.
    double bv_seconds = 0.0;
    // Optional source-polynomial cubic consequences; relaxation SAT is ignored.
    bool guarded_product = false;
    bool objective_slice = false;
    double objective_slice_budget_seconds = 2.0;
    bool isolate_exchange_context = false;
    bool slack_repair = false;
    unsigned smt_exchange = 0; // 0: omit; 1: prepend the bounded arith2 tactic branch.
};
int run_omt_optimization(ls_solver& solver, const std::string& input,
    bool initialize_enabled, double timeout, double import_seconds,
    const std::string& model_output, const std::string& target,
    const std::string& large_target = "",
    const std::string& unbounded_threshold = "",
    const OmtOptimalityOptions& optimality = OmtOptimalityOptions());
}
