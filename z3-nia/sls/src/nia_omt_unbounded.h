#pragma once
#include <string>
#include <vector>

namespace nia {
class ls_solver;
struct OmtUnboundedResult {
    bool proved = false;
    unsigned queries = 0;
    std::string status = "not_found";
    std::string certificate_json = "null";
};
OmtUnboundedResult prove_omt_unbounded(const ls_solver&,
                                    const std::vector<__int128_t>& base,
                                    double seconds);
}
