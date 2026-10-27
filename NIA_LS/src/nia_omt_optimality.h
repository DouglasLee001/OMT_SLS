#pragma once
#include <memory>
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
// Search the unchanged original hard formula without an incumbent or an
// objective bound. A SAT model goes through the same complete source/native
// validation bridge. Even an original UNSAT result (status "unsat") never sets
// proved: feasibility without an incumbent cannot establish an OMT optimum.
// Parsing, solving and model restoration share one hard wallclock allowance.
OmtOptimalityResult find_omt_feasible(const ls_solver&, double seconds,
                                     const std::string& query_output);
// Reuse one strict-bound solver across finite wallclock slices. An unfinished
// worker is SIGSTOP'ed before run returns; the same incumbent resumes that
// worker, while a changed incumbent/output path kills and reaps it first.
// No solver slice timeout discards the worker's learned state. Results that
// miss a slice stay pending and can only be accepted within a later allowance.
// The referenced native formula must remain alive and unchanged throughout.
class OmtOptimalitySession {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    explicit OmtOptimalitySession(const ls_solver&);
    ~OmtOptimalitySession();
    OmtOptimalitySession(const OmtOptimalitySession&) = delete;
    OmtOptimalitySession& operator=(const OmtOptimalitySession&) = delete;
    OmtOptimalityResult run(const std::string& best, double seconds,
                            const std::string& query_output);
    void reset();
    unsigned starts() const;
    unsigned resumes() const;
    bool paused() const;
};
}
