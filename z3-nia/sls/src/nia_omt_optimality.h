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
                                       double seconds, const std::string& query_output,
                                       bool finite_cutset = false, bool convex_exchange = false,
                                       const std::vector<__int128_t>* incumbent_hint = nullptr,
                                       unsigned smt_exchange = 0, bool integer_convex = false,
                                       bool isolate_context = false, bool integer_affine = false,
                                       bool objective_slice = false,
                                       double objective_slice_budget_seconds = 2.0,
                                       double advisory_share = 0,
                                       double bv_seconds = 0,
                                       bool guarded_product = false);
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
// Optional advisory_share is zero (OFF) or a finite fraction strictly between
// zero and one. It caps the common advisory prefix of each new bound's first
// actual window. This is soft steady-wall time including pauses and setup;
// it does not change the persistent default solver's infinite internal budget.
// Optional bv_seconds is zero (OFF), or finite in [0.001,1200]. It selects a
// bounded native extended-BV branch followed by the original default tactic
// for the complete strict query. Its soft wall timer includes SIGSTOP pauses;
// same-bound resumes do not replenish it. Parent deadlines remain authoritative.
// Optional guarded_product adds a separate source-bound Real relaxation with
// guarded cubic consequences, at most 3 wall seconds per new worker. Only
// UNSAT is used; other results retain the original source solver and SAT bridge.
class OmtOptimalitySession {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    explicit OmtOptimalitySession(const ls_solver&, bool finite_cutset = false,
                                 bool convex_exchange = false, unsigned smt_exchange = 0, bool integer_convex = false,
                                 bool isolate_context = false, bool integer_affine = false,
                                 bool objective_slice = false,
                                 double objective_slice_budget_seconds = 2.0,
                                 double advisory_share = 0,
                                 double bv_seconds = 0,
                                 bool guarded_product = false);
    ~OmtOptimalitySession();
    OmtOptimalitySession(const OmtOptimalitySession&) = delete;
    OmtOptimalitySession& operator=(const OmtOptimalitySession&) = delete;
    OmtOptimalityResult run(const std::string& best, double seconds,
                            const std::string& query_output,
                            const std::vector<__int128_t>* incumbent_hint = nullptr);
    void reset();
    unsigned starts() const;
    unsigned resumes() const;
    bool paused() const;
};
}
