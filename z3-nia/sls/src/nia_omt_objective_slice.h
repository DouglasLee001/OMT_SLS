#pragma once
#include <z3++.h>
#include <cstdint>
#include <memory>
#include <string>

namespace nia {
struct OmtObjectiveSliceResult {
    bool applicable=false;
    std::string answer="unknown";
    std::unique_ptr<z3::model> source_model;
    uint64_t queries=0,refinements=0;
    uint64_t initial_assertions=0,total_assertions=0,selected_assertions=0;
    uint64_t objective_ast_nodes=0,total_ast_nodes=0,initial_ast_nodes=0,selected_ast_nodes=0,traversal_nodes=0;
    double seconds=0;
    std::string reason,certificate_json;
};
// Uses only unchanged source conjuncts and the exact original strict bound.
// SAT carries a model validated against every original hard assertion. UNSAT
// establishes no better bound; it does not establish incumbent feasibility.
// The caller must provide a finite positive allowance (normally two seconds).
// Optional certificate publication is atomic and refuses existing destinations.
// Unsupported no-clobber rename uses same-directory POSIX hard-link creation;
// every other rename error remains a conservative failure without replacement.
// AST work is the sum of per-component unique DAG sizes; the objective is
// counted separately. Components have stable source DFS indices, splitting
// only top-level conjunctions. Guards cap 65536 components, 262144 hard AST
// work, 1048576 traversal visits, 8192 seed AST work and 256 refinement rounds.
OmtObjectiveSliceResult solve_omt_objective_slice(
    z3::context& caller_context,const z3::expr_vector& original_hard,
    const z3::expr& original_objective,bool minimize,
    const std::string& original_best,double seconds,
    const std::string& certificate_output={});
}
