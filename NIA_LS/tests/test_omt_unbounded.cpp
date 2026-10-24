#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_unbounded.h"
#include <gmpxx.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <set>
#include <sstream>

namespace {
using Values = std::vector<__int128_t>;
int failures = 0;
void check(bool condition, const std::string& message) {
    if (!condition) { ++failures; std::cerr << "FAIL: " << message << '\n'; }
}
mpz_class exact(__int128_t x) { return mpz_class(nia::format_omt_integer(x)); }
std::vector<std::string> array(const std::string& json, const std::string& key) {
    std::vector<std::string> values;
    auto at = json.find("\"" + key + "\":[");
    if (at == std::string::npos) return values;
    at = json.find('[', at) + 1;
    auto end = json.find(']', at);
    std::string body = json.substr(at, end - at);
    for (char& ch : body) if (ch == '"' || ch == ',') ch = ' ';
    std::istringstream input(body); std::string value;
    while (input >> value) values.push_back(value);
    return values;
}
std::string field(const std::string& json, const std::string& key) {
    const std::string marker = "\"" + key + "\":\"";
    auto at = json.find(marker);
    if (at == std::string::npos) return "";
    at += marker.size();
    return json.substr(at, json.find('"', at) - at);
}
mpz_class polynomial(const nia::ls_solver& s, __int128_t key,
                     const std::vector<nia::coff_term>& terms, const std::vector<mpz_class>& x) {
    mpz_class value = exact(key);
    for (auto ct : terms) {
        mpz_class product = exact(ct.coff);
        bool zero = false;
        for (auto f : s._terms[ct.term_idx].var_epxs) zero |= x[f.var_index] == 0;
        if (zero) continue;
        for (auto f : s._terms[ct.term_idx].var_epxs) {
            mpz_class power; mpz_pow_ui(power.get_mpz_t(), x[f.var_index].get_mpz_t(), f.exponent);
            product *= power;
        }
        value += product;
    }
    return value;
}
bool truth(const nia::ls_solver& s, int id, const std::vector<mpz_class>& x) {
    const auto& p = s._lits[std::abs(id)]; bool positive;
    if (!p.is_nia_lit) positive = x[size_t(p.delta)] == 1;
    else { auto q = polynomial(s, p.key, p.coff_terms, x); positive = p.is_equal ? q == 0 : q <= 0; }
    return positive == (id > 0);
}
void validate_certificate(const nia::ls_solver& s, const Values& base, const nia::OmtUnboundedResult& r) {
    check(r.status == "proved" && r.queries > 0, "proof reports its successful direction query");
    check(r.certificate_json.find("\"kind\":\"lia_ray\"") != std::string::npos &&
          r.certificate_json.find("\"version\":1") != std::string::npos, "versioned native LIA ray certificate");
    auto b = array(r.certificate_json, "base"), d = array(r.certificate_json, "direction");
    check(b.size() == base.size() && d.size() == base.size(), "certificate uses all native variable indices");
    if (b.size() != base.size() || d.size() != base.size()) return;
    std::vector<mpz_class> origin, direction;
    for (size_t v = 0; v < base.size(); ++v) {
        origin.emplace_back(b[v]); direction.emplace_back(d[v]);
        check(origin.back() == exact(base[v]), "certificate retains exact base values");
        if (!s._vars[v].is_nia) check(direction.back() == 0 && (origin.back() == 1 || origin.back() == -1), "Boolean branch stays fixed");
    }
    for (auto v : array(r.certificate_json, "fixed")) {
        size_t id = std::stoull(v);
        check(id < base.size() && s._vars[id].is_nia && direction[id] == 0, "fixed integer has zero direction");
    }
    std::set<int> selected;
    for (auto id : array(r.certificate_json, "literals")) selected.insert(std::stoi(id));
    for (const auto& c : s._clauses) {
        bool covered = false;
        for (int id : c.literals) covered |= selected.count(id) && truth(s, id, origin);
        check(covered, "every complete native hard clause has a selected true signed literal");
    }
    const auto initial = polynomial(s, s.omt_objective_constant, s.omt_objective_terms, origin);
    const auto slope_text = field(r.certificate_json, "objective_slope");
    check(!slope_text.empty(), "certificate declares its exact objective slope");
    mpz_class declared_slope(slope_text.empty() ? "0" : slope_text);
    check(s.omt_direction == "max" ? declared_slope >= 1 : declared_slope <= -1, "declared slope has the improving sign");
    for (const std::string& time : {std::string("0"), std::string("1"), std::string("103"), std::string("1000000000000000000000000000000")}) {
        mpz_class t(time); std::vector<mpz_class> point;
        for (size_t v = 0; v < base.size(); ++v) point.emplace_back(origin[v] + t * direction[v]);
        for (const auto& c : s._clauses) {
            bool sat = false; for (int id : c.literals) sat |= truth(s, id, point);
            check(sat, "independently evaluated full hard CNF holds far along ray");
        }
        for (int id : selected) check(truth(s, id, point), "all selected conjuncts survive along ray");
        auto obj = polynomial(s, s.omt_objective_constant, s.omt_objective_terms, point);
        check(obj == initial + t * declared_slope, "declared slope equals the original objective along the ray");
        check(t == 0 ? obj == initial : (s.omt_direction == "max" ? obj > initial : obj < initial), "exact original objective improves along ray");
    }
}
struct Fixture {
    nia::ls_solver s{9, 0, true}; Values base;
    Fixture(const std::string& sorts, const std::vector<std::string>& literals,
            const std::vector<std::vector<int>>& clauses, const std::string& objective,
            Values values, const std::string& direction = "max") : base(std::move(values)) {
        std::ostringstream text;
        text << "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION " << direction << "\nVARIABLES " << sorts.size() << '\n';
        for (size_t i = 0; i < sorts.size(); ++i) {
            std::string name = "v" + std::to_string(i); text << i << ' ' << sorts[i] << ' ';
            const char* hex = "0123456789abcdef";
            for (unsigned char ch : name) text << hex[ch >> 4] << hex[ch & 15];
            text << '\n';
        }
        text << "LITERALS " << literals.size() << '\n';
        for (size_t i = 0; i < literals.size(); ++i) text << i + 1 << ' ' << literals[i] << '\n';
        text << "CLAUSES " << clauses.size() << '\n';
        for (auto c : clauses) { text << c.size(); for (int id : c) text << ' ' << id; text << '\n'; }
        text << "OBJECTIVE " << objective << "\nEND\n";
        std::istringstream input(text.str()); std::string error;
        check(s.load_omt_instance(input, error), "real native fixture imports: " + error);
    }
    nia::OmtUnboundedResult run(bool expected, const std::string& name) {
        auto rng = s.mt; auto solution = s._solution; auto previous = base;
        auto r = nia::prove_omt_unbounded(s, base, 1.0);
        check(r.proved == expected, name + ": expected proof=" + std::to_string(expected) + ", got " + r.status);
        check(s.mt == rng && s._solution == solution && base == previous, "proof leaves source solver, seed and caller RNG untouched");
        check(r.status == "proved" || r.status == "not_found" || r.status == "budget" || r.status == "unknown", "status uses the public contract");
        if (r.proved) validate_certificate(s, base, r);
        else check(r.certificate_json == "null", "unsuccessful probes never emit a certificate");
        return r;
    }
};
void linear_and_signed() {
    Fixture upper("I", {"L 0 1 1 1 0 1"}, {{-1}}, "0 1 1 1 0 1", {1});
    auto positive = upper.run(true, "negated inequality admits increasing ray");
    check(array(positive.certificate_json, "literals") == std::vector<std::string>{"-1"}, "certificate preserves inequality polarity");
    Fixture lower("I", {"L 0 1 1 1 0 1"}, {{1}}, "0 1 1 1 0 1", {-1}, "min");
    lower.run(true, "minimization ray decreases objective");
    Fixture equal("II", {"E 0 2 1 1 0 1 -1 1 1 1"}, {{1}}, "0 1 1 1 0 1", {3,3});
    equal.run(true, "linear equality couples direction components");
    for (bool positive_base : {false,true}) for (bool maximize : {false,true}) {
        Fixture unequal("I", {"E 0 1 1 1 0 1"}, {{-1}}, "0 1 1 1 0 1", {positive_base ? 1 : -1}, maximize ? "max" : "min");
        unequal.run(positive_base == maximize, "negated equality keeps the incumbent sign branch");
    }
}
void nonlinear_and_exact() {
    Fixture bilinear("III", {"E 0 2 1 2 0 1 1 1 -1 1 2 1"}, {{1}}, "0 1 1 1 2 1", {2,3,6});
    bilinear.run(true, "xy=z equality linearizes by fixing a nonobjective factor");
    Fixture objective("II", {}, {}, "0 1 1 2 0 1 1 1", {2,3});
    objective.run(true, "bilinear objective is linearized together with selected literals");
    Fixture zero("II", {"E 0 1 1 2 0 1 1 1000"}, {{1}}, "0 1 1 1 1 1", {0,7});
    zero.run(true, "fixed zero kills a high degree monomial");
    Fixture cancel("II", {"E 0 2 1 1 0 2 -1 1 0 2"}, {{1}}, "0 3 1 2 0 1 1 1 -1 2 0 1 1 1 1 1 0 1", {4,5});
    auto cancelled = cancel.run(true, "canonical cancellation precedes linearity checks");
    check(array(cancelled.certificate_json, "fixed").empty(), "cancelled nonlinear terms do not freeze useful variables");
    Fixture wide("III", {"E 0 2 1 1 0 1 -1 2 1 100 2 1"}, {{1}}, "0 1 1 1 0 1", {0,10,0});
    auto big = wide.run(true, "fixed high power produces arbitrary precision direction coefficients");
    auto dirs = array(big.certificate_json, "direction");
    check(!dirs.empty() && mpz_class(dirs[0]) > mpz_class("170141183460469231731687303715884105727"), "certificate direction exceeds signed 128 bits without truncation");
    Fixture alternate("II", {"E -2 1 1 1 1 1"}, {{1}}, "0 1 1 2 0 1 1 1", {3,2});
    auto retry = alternate.run(true, "alternate fixed set avoids anchoring only improving variable");
    check(retry.queries > 1, "failed greedy fixed set is followed by a different exact query");
}
void exact_substitution_and_selection() {
    Fixture substituted("II", {"E 0 2 1 2 0 1 1 2 -2 1 1 2"}, {{1}}, "0 1 1 1 1 1", {2,3});
    substituted.run(true, "substitution cancels identical residual nonlinear monomials");
    Fixture zero_objective("II", {}, {}, "0 1 1 2 0 1 1 1", {0,1});
    zero_objective.run(true, "current zero does not erase an unfixed objective factor");
    Fixture choose_linear("I", {"L 1 1 -1 1 0 2", "L 0 1 -1 1 0 1"}, {{1,2}}, "0 1 1 1 0 1", {2});
    auto choice = choose_linear.run(true, "true linear disjunct is preferred over a nonlinear anchor");
    check(array(choice.certificate_json, "literals") == std::vector<std::string>{"2"}, "simpler chosen disjunct is explicitly certified");
    for (bool positive : {false,true}) {
        Fixture negative_equal("II", {"E -3 1 1 2 0 1 1 1"}, {{-1}}, "0 1 1 1 1 1", {1, positive ? 4 : 2}, positive ? "max" : "min");
        negative_equal.run(true, "negative bilinear equality preserves the exact nonzero sign branch");
    }
    Fixture coefficient("I", {}, {}, "0 1 -170141183460469231731687303715884105728 1 0 1", {0});
    auto boundary = coefficient.run(true, "minimum int128 objective coefficient is converted without signed overflow");
    check(boundary.certificate_json.find("\"objective_slope\":\"170141183460469231731687303715884105728\"") != std::string::npos,
          "objective slope stays arbitrary precision at the signed coefficient boundary");
    Fixture square_objective("I", {}, {}, "0 1 1 1 0 2", {1});
    square_objective.run(false, "unfixed objective square cannot be mislabeled a linear ray proof");
}
void booleans_and_declines() {
    Fixture branch("IB", {"B 1", "L 0 1 1 1 0 1"}, {{1,2}}, "0 1 1 1 0 1", {0,1});
    branch.run(true, "true Boolean branch admits an objective ray");
    Fixture blocked("IB", {"B 1", "L 0 1 1 1 0 1"}, {{1,2}}, "0 1 1 1 0 1", {0,-1});
    blocked.run(false, "fixed false Boolean retains the bounded arithmetic branch");
    __int128_t huge; check(nia::parse_omt_integer("1298074214633706907132624082305024", huge), "large bounded base parses");
    Fixture finite("I", {"L -1298074214633706907132624082305024 1 1 1 0 1"}, {{1}}, "0 1 1 1 0 1", {huge});
    finite.run(false, "large feasible objective does not prove actual unboundedness");
    Fixture curve("II", {"E 0 2 1 1 0 2 -1 1 1 1"}, {{1}}, "0 1 1 1 1 1", {1,1});
    curve.run(false, "unbounded curved feasible set has no supported affine ray");
    Fixture omitted("II", {"E 0 2 1 2 0 1 1 1 -1 1 0 1", "L -3 1 1 1 1 1", "L 3 1 -1 1 1 1"}, {{1},{2},{3}}, "0 1 1 1 0 1", {0,3});
    omitted.run(false, "all selected conjuncts remain enforced after fixing nonlinear factors");
    Fixture infeasible("I", {"L 0 1 1 1 0 1"}, {{1}}, "0 1 1 1 0 1", {1});
    infeasible.run(false, "infeasible supplied base cannot yield a proof");
    auto empty = nia::prove_omt_unbounded(branch.s, branch.base, 0);
    check(!empty.proved && empty.queries == 0 && empty.status == "budget", "zero proof budget makes no query");
    auto nan = nia::prove_omt_unbounded(branch.s, branch.base, std::numeric_limits<double>::quiet_NaN());
    check(!nan.proved && nan.queries == 0, "NaN proof budget cannot trigger work");
    auto tiny = nia::prove_omt_unbounded(branch.s, branch.base, 1e-300);
    check(!tiny.proved && tiny.queries == 0 && tiny.status == "budget", "tiny positive budget expires before launching Z3");
    auto bad_boolean = nia::prove_omt_unbounded(branch.s, Values{0,0}, 1);
    check(!bad_boolean.proved && bad_boolean.queries == 0, "Boolean base must be encoded as plus or minus one");
    auto padded = nia::prove_omt_unbounded(branch.s, Values{0,1,99,99}, 1);
    check(padded.proved && array(padded.certificate_json, "base").size() == 2, "native assignment padding never becomes phantom certificate variables");
    Fixture empty_clause("I", {}, {{}}, "0 1 1 1 0 1", {0});
    empty_clause.run(false, "empty native hard clause prevents every proof");
    auto missing = nia::prove_omt_unbounded(branch.s, Values{}, 1);
    check(!missing.proved && missing.queries == 0, "incomplete base is rejected before Z3");
}
}
int main() {
    linear_and_signed(); nonlinear_and_exact(); exact_substitution_and_selection(); booleans_and_declines();
    if (failures) { std::cerr << failures << " unbounded ray checks failed\n"; return 1; }
    std::cout << "native unbounded ray semantic checks passed\n";
}
