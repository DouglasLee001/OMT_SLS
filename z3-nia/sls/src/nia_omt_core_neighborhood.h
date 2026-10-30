#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace nia {
class ls_solver;
// A resumable exact native neighborhood. No result is an original-problem proof.
// Native data must remain alive and unchanged for this object's lifetime. The
// seed/best/free lists are copied, without constructing Z3 objects in the ctor.
// Native variable names and the original SMT2 source are never consulted.
class OmtCoreNeighborhood {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    struct Result {
        std::string status = "paused", reason;
        bool candidate_available = false;
        std::vector<__int128_t> candidate;
        std::string candidate_value;
        // Cumulative native indices, including the initial objective coordinate
        // and every initially_free coordinate.
        std::vector<size_t> released_variables, core_variables;
        // Counts and elapsed time for this resume only.
        uint64_t queries = 0, cores = 0;
        size_t anchors = 0;
        double seconds = 0;
    };
    // Initially releases all initially_free coordinates and one remaining
    // objective-support coordinate chosen reproducibly by the local RNG.
    OmtCoreNeighborhood(const ls_solver&, const std::vector<__int128_t>& seed,
                        const std::string& best, unsigned random_seed,
                        const std::vector<size_t>& initially_free = {},
                        double max_slice_seconds = 2.0);
    ~OmtCoreNeighborhood();
    OmtCoreNeighborhood(const OmtCoreNeighborhood&) = delete;
    OmtCoreNeighborhood& operator=(const OmtCoreNeighborhood&) = delete;
    // Each call performs at most one ordinary Z3 check. Construction and exact
    // validation share min(seconds, max_slice_seconds, 2s); invalid/tiny input
    // budgets perform no work. State and late solver answers persist for resume.
    // Checks receive a remaining-time timeout. Z3/GMP primitives and context
    // allocation are not preemptible: wall time can exceed a slice by one such
    // operation, and late candidates are never published. No work runs between
    // calls. SAT is delivered once, then finished() is true. A real unknown
    // finishes this incumbent's search without releasing anchors. Construction
    // slice expiration stays resumable. Empty-core UNSAT finishes only this search.
    Result resume_for(double seconds);
    bool finished() const;
};
}
