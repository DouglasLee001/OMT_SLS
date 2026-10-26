#pragma once
#include <string>
#include <vector>

namespace nia {
class ls_solver;
struct OmtOptimalityResult {
    bool proved = false;
    std::string status = "unknown";
    std::string reason;
    unsigned queries = 0;
    double seconds = 0;
    // SAT is only a counterexample to optimality. The caller must validate and
    // accept this complete native assignment before replacing its incumbent.
    bool candidate_available = false;
    std::vector<__int128_t> candidate;
    // Exact original objective; when available, also GMP-validated on candidate.
    std::string candidate_value;
};
// Caller supplies an incumbent already validated for the unchanged original
// problem. This proves the absence of a strictly better original assignment;
// it does not independently replay preprocessing equivalence or feasibility.
// The complete probe, including parsing/model recovery, runs in an isolated
// worker which is killed and reaped when this wallclock allowance expires.
OmtOptimalityResult prove_omt_optimality(const ls_solver&, const std::string& best,
                                       double seconds, const std::string& query_output);
}
