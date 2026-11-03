#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace nia {
class ls_solver;
// Advisory search statistics only. They never become hard constraints or
// proof premises; missing entries mean no information about that coordinate.
struct OmtUnboundedSearchHints {
    std::vector<uint64_t> improvement_activity;
    std::vector<uint64_t> move_activity;
    std::vector<uint64_t> clause_pressure;
};
struct OmtUnboundedResult {
    bool proved = false;
    unsigned queries = 0;
    std::string status = "not_found";
    std::string certificate_json = "null";
    std::string direction_backend = "sls";
    uint64_t direction_steps = 0;
    unsigned guided_attempts = 0;
    unsigned fixed_set_adjustments = 0;
};
OmtUnboundedResult prove_omt_unbounded(const ls_solver&,
                                    const std::vector<__int128_t>& base,
                                    double seconds);
OmtUnboundedResult prove_omt_unbounded(const ls_solver&,
                                    const std::vector<__int128_t>& base,
                                    double seconds,
                                    const OmtUnboundedSearchHints* hints);
}
