#pragma once
#include <string>

namespace nia {
class ls_solver;

// Search for one feasible assignment; never tighten an optimization bound.
// Every reported model is checked using GMP independently of search caches.
int run_omt_first_solution(ls_solver& solver, const std::string& input_path,
                           bool initialize_enabled, double timeout_seconds,
                           double import_seconds, const std::string& model_output);
}
