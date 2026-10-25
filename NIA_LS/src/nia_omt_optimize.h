#pragma once
#include <string>
namespace nia {
class ls_solver;
struct OmtOptimalityOptions {
    bool enabled = false;
    double stall_seconds = 50.0;
    double timeout_seconds = 30.0;
    std::string query_output;
};
int run_omt_optimization(ls_solver& solver, const std::string& input,
    bool initialize_enabled, double timeout, double import_seconds,
    const std::string& model_output, const std::string& target,
    const std::string& large_target = "",
    const std::string& unbounded_threshold = "",
    const OmtOptimalityOptions& optimality = OmtOptimalityOptions());
}
