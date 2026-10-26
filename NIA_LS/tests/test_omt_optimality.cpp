#include "../src/nia_ls.h"
#include "../src/nia_omt_optimality.h"
#include "../src/nia_omt_input.h"
#include <z3++.h>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <cerrno>
#include <chrono>

namespace {
int failures = 0;
void check(bool condition, const std::string& message) {
    if (!condition) { ++failures; std::cerr << "FAIL: " << message << '\n'; }
}
std::string read(const std::string& path) {
    std::ifstream input(path);
    return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}
struct Fixture {
    nia::ls_solver s{3, 0, true};
    std::string directory, source, query;
    Fixture(const std::string& text, const std::string& objective = "x", const std::string& direction = "min") {
        char path[] = "/tmp/omt-optimality-test-XXXXXX";
        char* created = mkdtemp(path);
        if (!created) throw std::runtime_error("mkdtemp failed");
        directory = created; source = directory + "/original.smt2"; query = directory + "/proof.smt2";
        std::ofstream(source) << text;
        s.omt_loaded = true; s.omt_source = source;
        s.omt_original_objective = objective; s.omt_direction = direction;
        // Intentionally no usable preprocessed formula/model. A proof must
        // obtain the complete hard problem and objective from the source.
        s._num_vars = 0; s.omt_objective_constant = 777;
    }
    void native(const std::string& body) {
        auto hex = [](const std::string& value) {
            const char* digits = "0123456789abcdef";
            std::string out;
            for (const unsigned char ch : value) { out += digits[ch >> 4]; out += digits[ch & 15]; }
            return out;
        };
        std::istringstream stream("NIA_LS_OMT 1\nSTATUS ready\nSOURCE " + hex(source)
            + "\nORIGINAL_OBJECTIVE " + hex(s.omt_original_objective)
            + "\nDIRECTION " + s.omt_direction + "\n" + body + "\nEND\n");
        std::string error;
        s.omt_loaded = false;
        if (!s.load_omt_instance(stream, error)) throw std::runtime_error("native fixture: " + error);
    }
    ~Fixture() { std::filesystem::remove_all(directory); }
    nia::OmtOptimalityResult run(const std::string& best, const std::string& expected) {
        const auto before = read(source);
        auto result = nia::prove_omt_optimality(s, best, 2, query);
        check(result.status == expected, "expected " + expected + ", got " + result.status + ": " + result.reason);
        check(result.proved == (expected == "proved"), "UNSAT alone sets proved");
        check(std::isfinite(result.seconds) && result.seconds >= 0, "probe reports finite elapsed time");
        check(!result.proved || result.seconds < 2, "proof finishes within the complete probe allowance");
        check(read(source) == before, "original source is immutable");
        if (expected == "proved" || expected == "sat") {
            check(result.queries == 1, "exactly one ordinary SMT query is counted");
            check(std::filesystem::is_regular_file(query), "actual query retained for independent audit");
            if (std::filesystem::is_regular_file(query)) {
                z3::context context; z3::solver audit(context);
                audit.from_file(query.c_str());
                const auto answer = audit.check();
                check(answer == (expected == "proved" ? z3::unsat : z3::sat), "retained complete query reproduces the result");
                const auto text = read(query);
                check(text.find("minimize") == std::string::npos && text.find("maximize") == std::string::npos,
                      "saved query is plain SMT, never an optimization check");
            }
        }
        return result;
    }
};
void finite_minimum_and_maximum() {
    Fixture minimum("(declare-const x Int) (assert (>= x 0)) (minimize x) (check-sat) (get-model) (get-objectives)");
    minimum.run("0", "proved");
    minimum.run("1", "sat");
    Fixture maximum("(declare-const x Int) (assert (<= x 10)) (maximize x) (check-sat) (get-objectives)", "x", "max");
    maximum.run("10", "proved");
    maximum.run("9", "sat");
    Fixture unbounded("(declare-const x Int) (minimize x) (check-sat)");
    unbounded.run("0", "sat");
    Fixture negative("(declare-const x Int) (assert (<= x (- 10))) (maximize x) (check-sat)", "x", "max");
    negative.run("-10", "proved");
    negative.run("-11", "sat");
}
void complete_formula_and_eliminated_objectives() {
    Fixture disjunction("(declare-const x Int) (declare-const b Bool) "
        "(assert (or (and b (= x 0)) (and (not b) (= x (- 1))))) (minimize x) (check-sat)");
    disjunction.s._solution = {0, 1};
    disjunction.run("0", "sat");
    disjunction.run("-1", "proved");
    Fixture eliminated("(declare-const x Int) (assert (= x 7)) (minimize x) (check-sat)");
    eliminated.run("7", "proved");
    Fixture constant("(assert true) (minimize 7) (check-sat)", "7");
    constant.run("7", "proved");
    Fixture max_constant("(assert true) (maximize (- 7)) (check-sat)", "(- 7)", "max");
    max_constant.run("-7", "proved");
    Fixture defined("(define-fun cost () Int 12) (assert true) (minimize cost) (check-sat)", "cost");
    defined.run("12", "proved");
    Fixture nonlinear("(declare-const x Int) (assert (>= (* x x) 4)) (minimize (* x x)) (check-sat)", "(* x x)");
    nonlinear.run("4", "proved");
}
void safe_parser_and_exact_incumbents() {
    Fixture quoted("; (maximize bogus)\n(set-option :timeout 0) (set-option :opt.priority pareto) "
        "(set-info :notes \"a ; (quoted \"\"string\"\")\") "
        "(declare-const |x ; ()| Int) (assert (>= |x ; ()| 2)) "
        "(minimize ; comment\n |x ; ()|) (check-sat) (get-objectives) (echo \"do not execute\")",
        "|x ; ()|");
    quoted.run("2", "proved");
    if (std::filesystem::exists(quoted.query)) {
        const auto text = read(quoted.query);
        check(text.find("pareto") == std::string::npos && text.find("do not execute") == std::string::npos,
              "source solver options/output commands cannot alter or execute proof queries");
    }
    const std::string huge = "10000000000000000000000000000000000000000000000000000000000000000000000000000000000";
    Fixture exact("(declare-const x Int) (assert (>= x " + huge + ")) (minimize x) (check-sat)");
    exact.run(huge, "proved");
    exact.run(huge + "0", "sat");
    for (const auto& malformed : {"", "1.5", "1e3", "(- 1)", "--2", "nan", "0) (assert false)"}) {
        auto result = nia::prove_omt_optimality(exact.s, malformed, 1, exact.query);
        check(!result.proved && result.status == "error" && result.queries == 0,
              "incumbent must be a safe exact decimal integer");
    }
}
void reject_unsafe_or_changed_sources() {
    const std::string prefix = "(declare-const x Int) (assert (>= x 0)) ";
    for (const char* tail : {"(minimize x) (maximize x) (check-sat)",
                                  "(minimize x) (check-sat) (check-sat)",
                                  "(push 1) (minimize x) (check-sat)",
                                  "(minimize x) (check-sat) (assert false)",
                                  "(assert-soft (> x 0)) (minimize x) (check-sat)",
                                  "(minimize x) (reset) (check-sat)",
                                  "(minimize x) (check-sat-assuming ())"}) {
        Fixture bad(prefix + tail);
        auto result = bad.run("0", "error");
        check(result.queries == 0, "unsupported source never starts a proof query");
    }
    Fixture direction(prefix + "(maximize x) (check-sat)");
    direction.run("0", "error");
    Fixture objective(prefix + "(minimize (+ x 1)) (check-sat)");
    objective.run("0", "error");
    Fixture malformed("(declare-const x Int) (assert (>= x 0) (minimize x)");
    malformed.run("0", "error");
    Fixture semantic("(declare-const x Int) (assert (= x missing)) (minimize x) (check-sat)");
    semantic.run("0", "error");
    Fixture absent(prefix + "(minimize x) (check-sat)");
    absent.s.omt_source += ".missing";
    absent.run("0", "error");
}
void sat_native_candidates() {
    Fixture minimum("(declare-const x Int) (assert (>= x 0)) (minimize x) (check-sat)");
    minimum.native("VARIABLES 1\n0 I 78\nLITERALS 1\n1 L 0 1\n-1 1 0 1\n"
                   "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1");
    const auto lower = minimum.run("10", "sat");
    check(lower.candidate_available && lower.candidate.size() == 1 && lower.candidate[0] >= 0
          && lower.candidate[0] < 10, "SAT min probe recovers a complete strictly better native assignment");
    check(lower.candidate_available && lower.candidate_value == nia::format_omt_integer(lower.candidate[0]),
          "SAT min probe returns the exact original/native objective");
    Fixture maximum("(declare-const x Int) (assert (<= x 10)) (maximize x) (check-sat)", "x", "max");
    maximum.native("VARIABLES 1\n0 I 78\nLITERALS 1\n1 L -10 1\n1 1 0 1\n"
                   "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1");
    const auto upper = maximum.run("9", "sat");
    check(upper.candidate_available && upper.candidate.size() == 1 && upper.candidate[0] == 10
          && upper.candidate_value == "10", "SAT max probe keeps the original objective sign");

    Fixture auxiliary("(declare-const x Int) (assert (>= x 0)) (minimize x) (check-sat)");
    auxiliary.native("VARIABLES 3\n0 I 78\n1 I 6b2130\n2 B 6175782d626f6f6c\n"
        "LITERALS 3\n1 L 0 1\n-1 1 0 1\n2 E -1 2\n1 1 1 1\n-1 1 0 1\n3 B 2\n"
        "CLAUSES 3\n1 1\n1 2\n1 3\nOBJECTIVE 0 1\n1 1 0 1");
    const auto completed = auxiliary.run("10", "sat");
    check(completed.candidate_available && completed.candidate.size() == 3
          && completed.candidate[1] == completed.candidate[0] + 1 && completed.candidate[2] == 1,
          "SAT original model completes arithmetic and Boolean native auxiliaries");

    Fixture eliminated("(declare-const x Int) (declare-const cost Int) (assert (= cost (* x x))) "
        "(minimize cost) (check-sat)", "cost");
    eliminated.native("VARIABLES 1\n0 I 78\nLITERALS 0\nCLAUSES 0\nOBJECTIVE 0 1\n1 1 0 2");
    const auto rewritten = eliminated.run("10", "sat");
    check(rewritten.candidate_available && rewritten.candidate.size() == 1
          && rewritten.candidate_value == nia::format_omt_integer(rewritten.candidate[0] * rewritten.candidate[0]),
          "SAT original cost recovers a native objective whose cost symbol was eliminated");

    Fixture negative("(declare-const x Int) (assert (<= x (- 10))) (maximize x) (check-sat)", "x", "max");
    negative.native("VARIABLES 1\n0 I 78\nLITERALS 1\n1 L 10 1\n1 1 0 1\n"
                    "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1");
    const auto signed_maximum = negative.run("-11", "sat");
    check(signed_maximum.candidate_available && signed_maximum.candidate_value == "-10",
          "SAT maximize negative objectives preserve their original sign");

    const std::string value = "1000000000000000000000000000000"; // within signed 128
    const std::string square = "1" + std::string(60, '0'); // exact objective exceeds signed 128
    Fixture wide("(declare-const x Int) (assert (= x " + value + ")) (minimize (* x x)) (check-sat)", "(* x x)");
    wide.native("VARIABLES 1\n0 I 78\nLITERALS 1\n1 E -" + value + " 1\n1 1 0 1\n"
                "CLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 2");
    const auto large = wide.run("1" + std::string(59, '0') + "1", "sat");
    check(large.candidate_available && large.candidate_value == square,
          "SAT bridge supports arbitrary precision objective values with signed128 variables");

    const std::string outside = "170141183460469231731687303715884105728";
    Fixture overflow("(declare-const x Int) (assert (>= x " + outside + ")) (minimize x) (check-sat)");
    overflow.native("VARIABLES 1\n0 I 78\nLITERALS 0\nCLAUSES 0\nOBJECTIVE 0 1\n1 1 0 1");
    const auto rejected = overflow.run("170141183460469231731687303715884105729", "sat");
    check(!rejected.candidate_available && rejected.candidate.empty() && !rejected.reason.empty(),
          "SAT outside native signed128 range remains unproved SAT with an explicit rejected candidate");
}
void hard_wallclock_and_worker_reaping() {
    Fixture fixture("(declare-const x Int) (assert (>= x 0)) (minimize x) (check-sat)");
    const std::string input = read(fixture.source);
    std::filesystem::remove(fixture.source);
    if (mkfifo(fixture.source.c_str(), 0600) != 0) throw std::runtime_error("mkfifo failed");
    const pid_t writer = fork();
    if (writer < 0) throw std::runtime_error("fixture fork failed");
    if (writer == 0) {
        const int fd = open(fixture.source.c_str(), O_RDWR);
        usleep(800000); // A source read ignores Z3's advisory solver timeout.
        if (fd >= 0) { (void)write(fd, input.data(), input.size()); close(fd); }
        _exit(0);
    }
    const auto began = std::chrono::steady_clock::now();
    auto result = nia::prove_omt_optimality(fixture.s, "0", 0.05, fixture.query);
    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
    check(result.status == "budget" && !result.proved, "blocked parsing exhausts finite proof allowance");
    check(elapsed < 0.30, "wallclock deadline interrupts a blocked worker rather than waiting for source/Z3");
    int status = 0;
    while (waitpid(writer, &status, 0) < 0 && errno == EINTR) {}
    errno = 0;
    check(waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD,
          "timed out proof worker is reaped before returning");
}
void budget_and_output_failures() {
    Fixture fixture("(declare-const x Int) (assert (>= x 0)) (minimize x) (check-sat)");
    for (double budget : {0., -1., 1e-300, std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::infinity()}) {
        auto result = nia::prove_omt_optimality(fixture.s, "0", budget, fixture.query);
        check(!result.proved && result.queries == 0 && result.status == "budget", "invalid or expired budget performs no query");
    }
    auto missing = nia::prove_omt_optimality(fixture.s, "0", 1, fixture.directory + "/absent/query.smt2");
    check(!missing.proved && missing.status == "error" && missing.queries == 0, "unwritable audit query cannot claim a proof");
    const auto original = read(fixture.source);
    auto overwrite = nia::prove_omt_optimality(fixture.s, "0", 1, fixture.source);
    check(!overwrite.proved && overwrite.status == "error" && read(fixture.source) == original,
          "query export cannot overwrite original input");
}
}
int main() {
    finite_minimum_and_maximum(); complete_formula_and_eliminated_objectives();
    safe_parser_and_exact_incumbents(); reject_unsafe_or_changed_sources(); budget_and_output_failures();
    hard_wallclock_and_worker_reaping(); sat_native_candidates();
    if (failures) { std::cerr << failures << " optimality checks failed\n"; return 1; }
    std::cout << "native finite optimality checks passed\n";
}
