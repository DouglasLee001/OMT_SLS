#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_model_bridge.h"
#include <gmpxx.h>
#include <cmath>
#include <iostream>
#include <limits>
#include <sstream>

namespace {
int failures = 0;
void check(bool condition, const std::string& message) {
    if (!condition) { ++failures; std::cerr << "FAIL: " << message << '\n'; }
}
std::string hex(const std::string& value) {
    static const char digits[] = "0123456789abcdef";
    std::string result;
    for (unsigned char ch : value) { result += digits[ch >> 4]; result += digits[ch & 15]; }
    return result;
}
struct Fixture {
    nia::ls_solver native{19, 0, true};
    Fixture(const std::vector<std::pair<char, std::string>>& variables,
            const std::vector<std::string>& literals,
            const std::vector<std::vector<int>>& clauses, const std::string& objective) {
        std::ostringstream text;
        text << "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES " << variables.size() << '\n';
        for (size_t i = 0; i < variables.size(); ++i)
            text << i << ' ' << variables[i].first << ' ' << hex(variables[i].second) << '\n';
        text << "LITERALS " << literals.size() << '\n';
        for (size_t i = 0; i < literals.size(); ++i) text << i + 1 << ' ' << literals[i] << '\n';
        text << "CLAUSES " << clauses.size() << '\n';
        for (const auto& clause : clauses) {
            text << clause.size();
            for (int id : clause) text << ' ' << id;
            text << '\n';
        }
        text << "OBJECTIVE " << objective << "\nEND\n";
        std::istringstream input(text.str()); std::string error;
        check(native.load_omt_instance(input, error), "native fixture imports: " + error);
    }
};
struct Source {
    z3::context context;
    z3::expr_vector hard{context};
    z3::expr objective{context.int_val(0)};
    z3::solver query{context};
    explicit Source(const std::string& source) {
        z3::optimize parser(context);
        parser.from_string(source.c_str());
        hard = parser.assertions();
        objective = parser.objectives()[0];
        query.add(hard);
        check(query.check() == z3::sat, "source fixture is SAT");
    }
    nia::OmtModelBridgeResult run(const nia::ls_solver& native, double seconds = 2.0) {
        return nia::restore_omt_native_model(native, query.get_model(), hard, objective, seconds);
    }
};
void auxiliary_completion_and_eliminated_objectives() {
    Fixture f({{'I',"x"}, {'I',"native-square"}, {'I',"native-target"}},
              {"E 0 2 1 1 0 2 -1 1 1 1", "E -1 2 1 1 2 1 -1 1 1 1"},
              {{1}, {2}}, "0 1 1 1 2 1");
    Source source("(declare-const x Int)(assert (= x 3))"
                  "(define-fun target () Int (+ (* x x) 1))(minimize target)");
    const auto old_solution = f.native._solution;
    const auto old_rng = f.native.mt;
    auto result = source.run(f.native);
    check(result.imported && result.status == "imported", "restore renamed square and target auxiliaries: " + result.reason);
    check(result.assignment == std::vector<__int128_t>({3,9,10}), "auxiliary values are solved rather than guessed");
    check(result.objective == "10", "expanded define-fun original objective is preserved");
    check(f.native._solution == old_solution && f.native.mt == old_rng, "bridge does not mutate native state or consume RNG");

    Fixture eliminated({{'I',"x"}}, {}, {}, "1 1 1 1 0 2");
    Source original_target("(declare-const x Int)(declare-const cost Int)"
                           "(assert (= x 3))(assert (= cost (+ (* x x) 1)))(minimize cost)");
    result = original_target.run(eliminated.native);
    check(result.imported && result.assignment == std::vector<__int128_t>({3}) && result.objective == "10",
          "native objective may eliminate the original target variable");
}
void quoted_names_booleans_and_fresh_symbols() {
    Fixture f({{'I',"x with space"}, {'B',"b flag"}, {'B',"bridge-variable!0"}, {'I',"square aux"}},
              {"B 1", "B 2", "E -9 1 1 1 3 1"}, {{-1}, {2}, {3}}, "0 1 1 1 0 2");
    Source source("(declare-const |x with space| Int)(declare-const |b flag| Bool)"
                  "(declare-const bridge-variable!0 Int)(assert (= bridge-variable!0 77))"
                  "(assert (= |x with space| 3))(assert (not |b flag|))"
                  "(minimize (* |x with space| |x with space|))");
    auto result = source.run(f.native);
    check(result.imported && result.assignment == std::vector<__int128_t>({3,-1,1,9}),
          "quoted names and Bool values map by exact name and sort; aux symbols are fresh: " + result.reason);
}
void arbitrary_precision_objective_and_variable_limits() {
    const std::string big = "1267650600228229401496703205376"; // 2^100
    Fixture square({{'I',"x"}}, {}, {}, "0 1 1 1 0 2");
    Source source("(declare-const x Int)(assert (= x " + big + "))(minimize (* x x))");
    auto result = source.run(square.native);
    const mpz_class expected = mpz_class(big) * mpz_class(big);
    __int128_t parsed = 0;
    check(result.imported && result.objective == expected.get_str() &&
          nia::parse_omt_integer(big, parsed) && result.assignment == std::vector<__int128_t>({parsed}),
          "128-bit variable supports an objective wider than 128 bits: " + result.reason);

    Fixture boundary({{'I',"x"}}, {}, {}, "0 1 1 1 0 1");
    const std::string minimum = "-170141183460469231731687303715884105728";
    Source low("(declare-const x Int)(assert (= x " + minimum + "))(minimize x)");
    result = low.run(boundary.native);
    check(result.imported && result.objective == minimum, "signed 128-bit minimum imports exactly");
    Source too_wide("(declare-const x Int)(assert (= x 1361129467683753853853498429727072845824))(minimize x)");
    result = too_wide.run(boundary.native);
    check(!result.imported && result.status == "unsupported" && result.assignment.empty(),
          "source variables wider than signed128 reject the candidate without truncation");
}
void rejection_never_proves_unsat() {
    Source source("(declare-const x Int)(assert (= x 3))(minimize x)");
    Fixture wrong_objective({{'I',"x"}}, {}, {}, "4 0");
    auto result = source.run(wrong_objective.native);
    check(!result.imported && result.status == "rejected" && result.assignment.empty(),
          "incompatible native objective rejects SAT model and never claims original UNSAT");
    Fixture wrong_clause({{'I',"x"}}, {"E -3 1 1 1 0 1"}, {{-1}}, "0 1 1 1 0 1");
    result = source.run(wrong_clause.native);
    check(!result.imported && result.status == "rejected", "negative native equality is included in full CNF");
    Fixture alternatives({{'I',"x"}}, {"E -3 1 1 1 0 1", "L -4 1 1 1 0 1"}, {{-1,2}}, "0 1 1 1 0 1");
    result = source.run(alternatives.native);
    check(result.imported && result.objective == "3", "native disjunction remains disjunctive");
    auto incompatible_hard = source.hard;
    incompatible_hard.push_back(source.context.int_const("x") == 4);
    result = nia::restore_omt_native_model(alternatives.native, source.query.get_model(),
                                          incompatible_hard, source.objective, 2);
    check(!result.imported && result.status == "rejected", "caller model must satisfy supplied original hard constraints");
    for (double allowance : {0.0, -1.0, std::numeric_limits<double>::quiet_NaN(),
                              std::numeric_limits<double>::infinity()}) {
        result = source.run(alternatives.native, allowance);
        check(!result.imported && result.status == "budget" && result.assignment.empty(), "invalid budget rejects without solver work");
    }
}
void malformed_native_and_context_mismatch_are_rejected() {
    Source source("(declare-const x Int)(assert (= x 3))(minimize x)");
    Fixture bad_term({{'I',"x"}}, {}, {}, "0 1 1 1 0 1");
    bad_term.native.omt_objective_terms[0].term_idx = std::numeric_limits<int>::max();
    auto result = source.run(bad_term.native);
    check(!result.imported && result.status == "unsupported", "invalid native term index never escapes into Z3");
    Fixture bad_literal({{'I',"x"}}, {"E -3 1 1 1 0 1"}, {{1}}, "0 1 1 1 0 1");
    bad_literal.native._clauses[0].literals[0] = std::numeric_limits<int>::min();
    result = source.run(bad_literal.native);
    check(!result.imported && result.status == "unsupported", "INT_MIN literal rejects without signed overflow");
    Fixture valid({{'I',"x"}}, {}, {}, "0 1 1 1 0 1");
    z3::context other;
    z3::expr_vector other_hard(other);
    result = nia::restore_omt_native_model(valid.native, source.query.get_model(),
                                          other_hard, source.objective, 2);
    check(!result.imported && result.status == "unsupported", "cross-context inputs reject before a Z3 API assertion");
    result = nia::restore_omt_native_model(valid.native, source.query.get_model(),
                                          source.hard, source.context.bool_val(true), 2);
    check(!result.imported && result.status == "unsupported", "noninteger original objectives cannot be restored");
}
}
int main() {
    auxiliary_completion_and_eliminated_objectives();
    quoted_names_booleans_and_fresh_symbols();
    arbitrary_precision_objective_and_variable_limits();
    rejection_never_proves_unsat();
    malformed_native_and_context_mismatch_are_rejected();
    if (!failures) std::cout << "PASS OMT SAT model bridge\n";
    return failures ? 1 : 0;
}
