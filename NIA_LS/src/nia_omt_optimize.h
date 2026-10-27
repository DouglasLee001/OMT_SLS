#pragma once
#include <string>
namespace nia {
class ls_solver;
struct OmtOptimalityOptions {
    bool enabled = false;
    double stall_seconds = 50.0;
    double timeout_seconds = 30.0;
    double search_window_seconds = 0.0;
    std::string query_output;
    double initial_z3_after_seconds = 0.0;
    double initial_z3_timeout_seconds = 120.0;
    bool resume = false;
};
int run_omt_optimization(ls_solver& solver, const std::string& input,
    bool initialize_enabled, double timeout, double import_seconds,
    const std::string& model_output, const std::string& target,
    const std::string& large_target = "",
    const std::string& unbounded_threshold = "",
    const OmtOptimalityOptions& optimality = OmtOptimalityOptions());
}
