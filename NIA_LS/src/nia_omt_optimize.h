#pragma once
#include <string>
namespace nia {
class ls_solver;
int run_omt_optimization(ls_solver& solver, const std::string& input,
    bool initialize_enabled, double timeout, double import_seconds,
    const std::string& model_output, const std::string& target);
}
