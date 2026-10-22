#pragma once
#include <cstdint>
#include <memory>
#include <random>
#include <string>
#include <vector>

namespace nia {
class ls_solver;
// A retained native search for a strict bound derived only from its own
// incumbent. Recreate this object when that incumbent changes. The original
// solver must remain alive; its topology is only read for exact validation.
class OmtBoundRepair {
public:
    enum class Status { unsupported,paused,sat,exhausted };
    OmtBoundRepair(const std::string& original_native_path,const ls_solver& original,
                   const std::vector<__int128_t>& own_incumbent,
                   const std::string& own_incumbent_exact,unsigned random_seed);
    ~OmtBoundRepair();
    OmtBoundRepair(const OmtBoundRepair&)=delete;
    OmtBoundRepair& operator=(const OmtBoundRepair&)=delete;
    OmtBoundRepair(OmtBoundRepair&&) noexcept;
    OmtBoundRepair& operator=(OmtBoundRepair&&) noexcept;

    bool resume_for(double slice_seconds,double global_remaining_seconds,
                    std::vector<__int128_t>& result);
    bool resume_steps(uint64_t max_steps,double slice_seconds,
                      double global_remaining_seconds,std::vector<__int128_t>& result);
    bool supported() const;
    Status status() const;
    bool initialized() const;
    uint64_t steps_completed() const;
    // Actual initialize() calls, excluding the setup marker for restart_seed.
    uint64_t initialization_round() const;
    std::mt19937 random_state() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
// Optional native local-search slice with a strict bound derived only from this
// solver's own incumbent. Unsupported legacy arithmetic returns false.
bool repair_omt_bound(const std::string& original_native_path,
                      const ls_solver& original,
                      const std::vector<__int128_t>& seed,
                      const std::string& incumbent_exact,
                      double budget, unsigned random_seed,
                      std::vector<__int128_t>& result);
}
