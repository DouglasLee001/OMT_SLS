#pragma once
#include <string>
#include <vector>
#include <z3++.h>

namespace nia {
class ls_solver;
struct OmtModelBridgeResult {
    bool imported = false;
    std::string status = "unknown", reason;
    std::vector<__int128_t> assignment;
    std::string objective;
    double seconds = 0;
};

// Restore a source SAT model into the complete native variable space. Failure
// rejects only this candidate and must never be used as an UNSAT proof.
OmtModelBridgeResult restore_omt_native_model(
    const ls_solver&, const z3::model& original_model,
    const z3::expr_vector& original_hard, const z3::expr& original_objective,
    double seconds);
}
