#pragma once

#include <cstddef>
#include <cstdint>
#include <array>
#include <memory>
#include <random>
#include <string>
#include <utility>
#include <vector>

namespace nia {
class ls_solver;

namespace bilinear_detail {
struct BipartiteComponent {std::array<std::vector<size_t>,2> colors;};
std::vector<std::vector<size_t>> component_cover_portfolio(
    const std::vector<BipartiteComponent>& components,std::mt19937& rng,
    size_t maximum_covers=32);
using MonomialFactors=std::vector<std::pair<size_t,unsigned>>;
// At most 32 greedy independent-set orders. Higher powers are mandatory
// anchors; a clique for each monomial leaves at most one linear free factor.
// True fixed factors are substituted before the residual degree cap of 64.
std::vector<std::vector<size_t>> polynomial_cover_portfolio(
    const std::vector<MonomialFactors>& monomials,
    const std::vector<__int128_t>& lo,const std::vector<__int128_t>& hi,
    const std::vector<size_t>& objective_variables,std::mt19937& rng,
    size_t maximum_covers=32);
class PrivateNeutralHistory {
public:
    using Assignment=std::vector<__int128_t>;
    explicit PrivateNeutralHistory(size_t limit=64):limit_(limit){}
    void reset(const Assignment& current);
    bool retain(const Assignment& candidate,bool strict_improvement,bool same_level);
    uint64_t neutral_acceptances()const{return total_;}
private:
    size_t limit_,used_=0;
    uint64_t total_=0;
    std::vector<Assignment> seen_;
};
// Enumerate anchored blocks of at most three domains with at most nine values.
// Every block's full Cartesian product precedes rotation to the next block.
std::vector<__int128_t> finite_block_trial(
    const std::vector<__int128_t>& seed, const std::vector<__int128_t>& lo,
    const std::vector<__int128_t>& hi, const std::vector<size_t>& finite,
    uint64_t variant);
// Preserve each eight-step absolute-probe block, then add zero and both
// incumbent-centered directions for a wide anchor. All arithmetic is exact.
std::vector<__int128_t> anchored_trial(
    const std::vector<__int128_t>& seed, const std::vector<__int128_t>& incumbent,
    const std::vector<__int128_t>& lo,
    const std::vector<__int128_t>& hi, const std::vector<size_t>& finite,
    const std::vector<size_t>& wide,uint64_t variant);
}

// In-process repair. Bipartite bilinear graphs retain their color-class
// portfolio; other polynomial graphs use a bounded fixed-set fallback.
// Each subproblem keeps the complete original CNF and a strict bound derived
// only from the caller's own feasible incumbent. Residual degrees above 64,
// or fixed constant powers requiring over 65536 bits, conservatively decline.
class OmtBilinearRepair {
public:
    using Assignment = std::vector<__int128_t>;
    OmtBilinearRepair(const ls_solver& solver, const Assignment& own_seed,
                     const std::string& own_best_exact, const Assignment& lo,
                     const Assignment& hi, unsigned random_seed);
    ~OmtBilinearRepair();
    OmtBilinearRepair(const OmtBilinearRepair&) = delete;
    OmtBilinearRepair& operator=(const OmtBilinearRepair&) = delete;

    bool supported() const;
    // False includes unsupported input, timeout and unsuccessful local repair;
    // it never proves UNSAT and leaves result unchanged. Partial assignments
    // remain internal. A true result passed full GMP validation and improves
    // own_best_exact strictly within both supplied time budgets.
    bool resume_for(double slice_seconds, double global_remaining_seconds,
                    Assignment& result);
    bool resume_steps(uint64_t max_rounds, double slice_seconds,
                      double global_remaining_seconds, Assignment& result);
    uint64_t rounds_completed() const;
    uint64_t neutral_moves_retained() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
}
