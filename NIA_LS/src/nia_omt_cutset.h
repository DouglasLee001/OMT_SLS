#pragma once
#include <z3++.h>
#include <cstdint>
#include <memory>
#include <string>

namespace nia {
struct OmtCutsetResult {
    bool applicable = false;
    z3::check_result answer = z3::unknown;
    std::unique_ptr<z3::model> model;
    uint64_t branches = 0;
    std::string reason;
};

// The caller supplies the complete original hard formula and its strict bound.
// Only exhausting an entailed finite cover authorizes an UNSAT answer.
OmtCutsetResult solve_omt_finite_cutset(z3::context&,
    const z3::expr_vector& hard_with_strict_bound, double seconds);
}
