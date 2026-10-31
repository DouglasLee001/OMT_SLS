#pragma once
#include <z3++.h>
#include <cstdint>
#include <map>
#include <memory>
#include <string>

namespace nia {
struct OmtConvexExchangeResult {
    bool applicable = false;
    z3::check_result answer = z3::unknown;
    std::unique_ptr<z3::model> model;
    uint64_t queries = 0, cuts = 0;
    double seconds = 0;
    std::string reason, certificate_json;
};
// Operates exclusively on unchanged original source ASTs in this context.
// Caller supplies an already valid incumbent; hints only choose tangent points.
// Finite positive seconds or +infinity are accepted. No signed128 domain bounds
// are introduced. The optional integer mode subtracts only an exactly verified
// positive integer diagonal and otherwise retains aggregate tangent exchange.
// The optional affine mode retains that diagonal and represents the entire
// exact PSD residual by primitive integer affine squares. A 512-bit common
// denominator guard falls back to the existing integer/aggregate mode.
// UNSAT carries an exact independently replayable certificate;
// a requested certificate file must be written successfully before publication.
OmtConvexExchangeResult solve_omt_convex_exchange(
    z3::context&, const z3::expr_vector& original_hard,
    const z3::expr& original_objective, bool minimize,
    const std::string& exact_best, const std::map<std::string,std::string>& incumbent_hints,
    double seconds, const std::string& certificate_output = {}, bool integer_diagonal = false,
    bool integer_affine = false);
}
