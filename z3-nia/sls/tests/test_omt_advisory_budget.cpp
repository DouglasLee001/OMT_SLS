#include "../src/nia_ls.h"
#include "../src/nia_omt_optimality.h"
#include "../src/nia_omt_input.h"
#include <gmpxx.h>
#include <z3++.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>

// Observe real SDK check entry without replacing any result or query. This
// distinguishes an original default check from a worker paused in preprocessing.
extern "C" Z3_lbool __real_Z3_solver_check(Z3_context, Z3_solver);
extern "C" Z3_lbool __wrap_Z3_solver_check(Z3_context context, Z3_solver solver) {
    const std::string query = Z3_solver_to_string(context, solver);
    const bool dense_original = query.find("(< g 0)") != std::string::npos;
    std::cerr << "advisory-test check_begin kind="
              << (dense_original ? "dense_original" : "other") << std::endl;
    const auto answer = __real_Z3_solver_check(context, solver);
    std::cerr << "advisory-test check_return answer=" << int(answer) << std::endl;
    return answer;
}

namespace {
int failures = 0;
void check(bool okay, const std::string& message) {
    if (!okay) { ++failures; std::cerr << "FAIL: " << message << std::endl; }
}
std::string read(const std::filesystem::path& file) {
    std::ifstream input(file);
    return std::string(std::istreambuf_iterator<char>(input), {});
}
std::string dense_source() {
    std::mt19937_64 random(20261030);
    std::ostringstream out;
    out << "(declare-const g Int)";
    for (unsigned j = 0; j < 64; ++j) out << "(declare-const x" << j << " Int)";
    out << "(assert (= g (+";
    for (unsigned i = 0; i < 64; ++i) {
        std::ostringstream form; form << "(+";
        for (unsigned j = 0; j < 64; ++j) {
            mpz_class coefficient = 1;
            for (unsigned k = 0; k < 256; k += 64) {
                coefficient <<= 64;
                coefficient += mpz_class(std::to_string(random()));
            }
            form << " (* " << coefficient.get_str() << " x" << j << ")";
        }
        form << ')'; out << " (* " << form.str() << ' ' << form.str() << ")";
    }
    out << ")))(minimize g)";
    return out.str();
}
void verify_dense_source_witness(const std::string& source) {
    z3::context context; z3::optimize parser(context); parser.from_string(source.c_str());
    z3::model witness(context);
    auto zero = context.int_val(0);
    auto g = context.int_const("g").decl(); witness.add_const_interp(g, zero);
    for (unsigned i = 0; i < 64; ++i) {
        auto variable = context.int_const(("x" + std::to_string(i)).c_str()).decl();
        witness.add_const_interp(variable, zero);
    }
    for (const auto& hard : parser.assertions())
        check(witness.eval(hard, true).is_true(), "all-zero witness satisfies every original dense assertion");
    check(witness.eval(parser.objectives()[0], true).is_numeral() &&
          witness.eval(parser.objectives()[0], true).to_string() == "0",
          "the original dense objective is exactly zero at the real feasible witness");
    std::cout << "dense-source feasible_witness=all_zero original_objective=0 extra_hard_constraints=0 bytes="
              << source.size() << std::endl;
}
struct Fixture {
    nia::ls_solver native{19, 0, true};
    std::filesystem::path source;
    std::string original;
    Fixture(const std::filesystem::path& directory, const std::string& text,
            const std::string& objective = "g", const std::string& direction = "min")
        : source(directory / "original.smt2"), original(text) {
        std::filesystem::create_directories(directory);
        std::ofstream(source) << text;
        native.omt_loaded = true; native.omt_source = source.string();
        native.omt_original_objective = objective; native.omt_direction = direction;
        native._num_vars = native._num_lits = native._num_clauses = 0;
    }
    void unchanged() { check(read(source) == original, "source bytes remain unchanged"); }
    void load_native(const std::string& body) {
        auto hex = [](const std::string& text) {
            const char* digits = "0123456789abcdef"; std::string output;
            for (unsigned char ch : text) { output += digits[ch >> 4]; output += digits[ch & 15]; }
            return output;
        };
        std::istringstream input("NIA_LS_OMT 1\nSTATUS ready\nSOURCE " + hex(source.string())
            + "\nORIGINAL_OBJECTIVE " + hex(native.omt_original_objective)
            + "\nDIRECTION " + native.omt_direction + "\n" + body + "\nEND\n");
        std::string error; native.omt_loaded = false;
        if (!native.load_omt_instance(input, error)) throw std::runtime_error("native fixture: " + error);
    }
};
void dense(const std::filesystem::path& directory, bool supplemental, double share = .5) {
    const auto text = dense_source(); verify_dense_source_witness(text);
    Fixture fixture(directory, text);
    const auto rng = fixture.native.mt;
    const auto assignment = fixture.native._solution;
    const auto output = directory / "strict-query.smt2";
    nia::OmtOptimalityResult result;
    if (!supplemental) {
        result = nia::prove_omt_optimality(fixture.native, "0", 1, output.string());
    } else {
        nia::OmtOptimalitySession session(fixture.native, false, true, 0, true, true, false, false, 2.0
#ifndef NIA_ADVISORY_TEST_LEGACY
            , share
#endif
        );
        result = session.run("0", 1, output.string());
        std::cout << "dense-session starts=" << session.starts() << " resumes=" << session.resumes()
                  << " paused=" << session.paused() << std::endl;
    }
    fixture.unchanged();
    check(fixture.native.mt == rng && fixture.native._solution == assignment,
          "proof does not mutate the native RNG or assignment");
    const bool expect_proof = !supplemental || share > 0;
    check(expect_proof ? result.proved && result.status == "proved" : !result.proved && result.status == "budget",
          std::string(expect_proof ? "public proof must finish" : "default-OFF session retains legacy pause")
          + " inside the same one-second parent window: " + result.status + " / " + result.reason);
    check(!result.proved || result.seconds < 1, "proof is accepted within its actual parent window");
    check(read(output).find("(< g 0)") != std::string::npos, "query retains the original strict objective bound");
    std::cout << "dense-result mode=" << (!supplemental ? "default" : share > 0 ? "prefix" : "off")
              << " status=" << result.status << " proved=" << result.proved
              << " queries=" << result.queries << " seconds=" << result.seconds
              << " reason=" << result.reason << std::endl;
}
void dense_pause_resume(const std::filesystem::path& directory) {
    const auto source = dense_source(); verify_dense_source_witness(source);
    Fixture fixture(directory, source);
    const auto output = directory / "strict-query.smt2";
    nia::OmtOptimalitySession session(fixture.native, false, true, 0, true, true, false, false, 2.0
#ifndef NIA_ADVISORY_TEST_LEGACY
        , .5
#endif
    );
    const auto first = session.run("0", .04, output.string());
    check(first.status == "budget" && !first.proved && session.paused() && session.starts() == 1,
          "a real short dense-source session pauses its original worker");
    const auto resumed = session.run("0", 1, output.string());
    check(resumed.proved && resumed.status == "proved" && resumed.seconds < 1,
          "resumed worker reaches default after the original small prefix expires: " + resumed.reason);
    check(session.starts() == 1 && session.resumes() == 1 && !session.paused(),
          "same-bound resume keeps one worker and one prefix epoch");
    fixture.unchanged();
    std::cout << "pause-result first=" << first.status << " resumed=" << resumed.status
              << " starts=" << session.starts() << " resumes=" << session.resumes() << std::endl;
}
void wide_source_models(const std::filesystem::path& directory) {
    const std::string lower = "1267650600228229401496703205376";
    const std::string upper = "1267650600228229401496703205377";
    for (bool minimize : {true, false}) for (bool persistent : {false, true}) {
        const auto tag = std::string(minimize ? "min" : "max") + (persistent ? "-session" : "-finite");
        std::ostringstream text;
        text << "(declare-const x Int)(declare-const b Bool)(assert b)"
             << "(assert (>= x " << lower << "))(assert (<= x " << upper << "))";
        for (unsigned i = 0; i < 24; ++i)
            text << "(declare-const unused" << i << " Int)(assert (>= unused" << i << " 0))";
        text << '(' << (minimize ? "minimize" : "maximize") << " x)(check-sat)";
        Fixture fixture(directory / tag, text.str(), "x", minimize ? "min" : "max");
        fixture.load_native("VARIABLES 2\n0 I 78\n1 B 62\nLITERALS 3\n1 B 1\n2 L " + lower
            + " 1\n-1 1 0 1\n3 L -" + upper
            + " 1\n1 1 0 1\nCLAUSES 3\n1 1\n1 2\n1 3\nOBJECTIVE 0 1\n1 1 0 1");
        const auto rng = fixture.native.mt; const auto assignment = fixture.native._solution;
        __int128_t incumbent = 0;
        check(nia::parse_omt_integer(minimize ? upper : lower, incumbent), "wide feasible incumbent parses exactly");
        const std::vector<__int128_t> hint{incumbent, 1};
        const auto query = directory / tag / "strict-query.smt2";
        const auto incumbent_value = minimize ? upper : lower;
        const auto optimum_value = minimize ? lower : upper;
        nia::OmtOptimalityResult candidate, optimum;
        if (persistent) {
            nia::OmtOptimalitySession session(fixture.native, true, true, 2, true, true, false, true, 8.0
#ifndef NIA_ADVISORY_TEST_LEGACY
                , .5
#endif
            );
            candidate = session.run(incumbent_value, 2, query.string(), &hint);
            optimum = session.run(optimum_value, 2, query.string(), &hint);
            check(session.starts() == 2, "a genuinely improved feasible incumbent creates a new prefix epoch");
        } else {
            candidate = nia::prove_omt_optimality(fixture.native, incumbent_value, 2, query.string(),
                true, true, &hint, 2, true, true, false, true, 8.0
#ifndef NIA_ADVISORY_TEST_LEGACY
                , .5
#endif
            );
            optimum = nia::prove_omt_optimality(fixture.native, optimum_value, 2, query.string(),
                true, true, &hint, 2, true, true, false, true, 8.0
#ifndef NIA_ADVISORY_TEST_LEGACY
                , .5
#endif
            );
        }
        check(candidate.status == "sat" && !candidate.proved && candidate.candidate_available,
              "finite/session prefix preserves a complete strict source/native SAT model: " + candidate.reason);
        if (candidate.candidate_available)
            check(candidate.candidate.size() == 2 && candidate.candidate_value == optimum_value &&
                  nia::format_omt_integer(candidate.candidate[0]) == optimum_value && candidate.candidate[1] == 1,
                  "returned large integer and Boolean values satisfy the complete min/max native formula");
        check(optimum.proved && optimum.status == "proved", "the same unchanged source has no improvement at its feasible endpoint");
        check(fixture.native.mt == rng && fixture.native._solution == assignment,
              "all auxiliary channels preserve native RNG and assignment");
        fixture.unchanged();
        check(read(query).find(minimize ? "(< x " : "(> x ") != std::string::npos,
              "saved query retains the exact original objective direction");
        std::cout << "wide-result tag=" << tag << " candidate=" << candidate.status
                  << " value=" << candidate.candidate_value << " optimum=" << optimum.status << std::endl;
    }
}
void shared_auxiliary_pipeline(const std::filesystem::path& directory) {
    // x=0,b=true is a real original-source incumbent. No hard hint is added.
    Fixture fixture(directory, "(declare-const x Int)(declare-const b Bool)"
        "(assert (= x (ite b 0 1)))(minimize x)(check-sat)", "x");
    const auto result = nia::prove_omt_optimality(fixture.native, "0", 2,
        (directory / "strict-query.smt2").string(), true, true, nullptr, 2, true, true, false, true, 8.0
#ifndef NIA_ADVISORY_TEST_LEGACY
        , .5
#endif
    );
    check(result.proved && result.status == "proved", "shared finite auxiliary pipeline proves the complete original query");
    fixture.unchanged();
    std::cout << "pipeline-result status=" << result.status << " reason=" << result.reason << std::endl;
}
void invalid_api_and_source_protection(const std::filesystem::path& directory) {
#ifndef NIA_ADVISORY_TEST_LEGACY
    Fixture fixture(directory, "(declare-const x Int)(assert (>= x 0))(minimize x)", "x");
    for (double share : {-.1, 1., 1.1, std::numeric_limits<double>::quiet_NaN(),
                         std::numeric_limits<double>::infinity()}) {
        const auto invalid = nia::prove_omt_optimality(fixture.native, "0", 1,
            (directory / "invalid.smt2").string(), false, false, nullptr, 0, false, false, false, false, 2., share);
        check(invalid.status == "error" && !invalid.proved && !invalid.candidate_available && invalid.queries == 0,
              "invalid finite/share domain is rejected before a query starts");
        bool rejected = false;
        try { nia::OmtOptimalitySession session(fixture.native, false, false, 0, false, false, false, false, 2., share); }
        catch (const std::invalid_argument&) { rejected = true; }
        check(rejected, "persistent API rejects the same invalid share domain");
    }
    const auto protected_result = nia::prove_omt_optimality(fixture.native, "0", 1,
        fixture.source.string(), false, false, nullptr, 0, false, false, false, false, 2., .5);
    check(protected_result.status == "error" && !protected_result.proved,
          "advisory prefix cannot overwrite original source with its retained query");
    nia::OmtOptimalitySession session(fixture.native, false, false, 0, false, false, false, false, 2., .5);
    const auto persistent = session.run("0", 1, fixture.source.string());
    check(persistent.status == "error" && !persistent.proved,
          "persistent advisory prefix preserves original-source output protection");
    fixture.unchanged();
    std::cout << "invalid-api invalid_shares=5 original_source_protected=1" << std::endl;
#endif
}
}
int main(int argc, char** argv) {
    if (argc != 3) throw std::runtime_error("usage: advisory-budget MODE DIRECTORY");
    const std::string mode = argv[1];
    if (mode == "dense-default" || mode == "dense-prefix")
        dense(argv[2], mode == "dense-prefix");
    else if (mode == "dense-off") dense(argv[2], true, 0);
    else if (mode == "dense-pause") dense_pause_resume(argv[2]);
    else if (mode == "wide-models") wide_source_models(argv[2]);
    else if (mode == "pipeline") shared_auxiliary_pipeline(argv[2]);
    else if (mode == "invalid-api") invalid_api_and_source_protection(argv[2]);
    else throw std::runtime_error("unknown test mode");
    return failures ? 1 : 0;
}
