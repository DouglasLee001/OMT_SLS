#pragma once
#include <string>

namespace nia {
class ls_solver;
struct OmtOptimalityResult {
    bool proved = false;
    std::string status = "unknown";
    std::string reason;
    unsigned queries = 0;
    double seconds = 0;
};
// Caller supplies an incumbent already validated for the unchanged original
// problem. This proves the absence of a strictly better original assignment;
// it does not independently replay preprocessing equivalence or feasibility.
OmtOptimalityResult prove_omt_optimality(const ls_solver&, const std::string& best,
                                       double seconds, const std::string& query_output);
}
