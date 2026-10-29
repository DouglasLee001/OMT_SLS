#include "nia_omt_unbounded.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <gmpxx.h>
#include <z3++.h>
#include <chrono>
#include <cmath>
#include <climits>
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>
#include <utility>

namespace nia {
namespace {
using Integer = mpz_class;
using Factors = std::vector<std::pair<size_t, unsigned long>>;
using Polynomial = std::map<Factors, Integer>;
using Clock = std::chrono::steady_clock;
struct Expired {};
struct Unsupported {};
struct Budget {
    Clock::time_point start = Clock::now();
    double seconds;
    explicit Budget(double value) : seconds(value) {}
    double remaining() const {
        return seconds - std::chrono::duration<double>(Clock::now() - start).count();
    }
    void check() const { if (!(remaining() > 0)) throw Expired{}; }
};
Integer integer(__int128_t value) { return Integer(format_omt_integer(value)); }
void accumulate(Polynomial& p, const Factors& factors, const Integer& value) {
    if (value == 0) return;
    auto inserted = p.emplace(factors, value);
    if (!inserted.second) {
        inserted.first->second += value;
        if (inserted.first->second == 0) p.erase(inserted.first);
    }
}
Polynomial polynomial(const ls_solver& s, __int128_t key,
                      const std::vector<coff_term>& terms, const Budget& budget) {
    Polynomial p;
    accumulate(p, {}, integer(key));
    for (const auto& ct : terms) {
        budget.check();
        if (ct.coff == 0) continue;
        if (ct.term_idx < 0 || size_t(ct.term_idx) >= s._terms.size()) throw Unsupported{};
        std::map<size_t, unsigned long> powers;
        for (auto f : s._terms[ct.term_idx].var_epxs) {
            if (f.var_index < 0 || size_t(f.var_index) >= s._num_vars ||
                !s._vars[f.var_index].is_nia || f.exponent <= 0) throw Unsupported{};
            auto& exponent = powers[size_t(f.var_index)];
            if (exponent > ULONG_MAX - unsigned(f.exponent)) throw Unsupported{};
            exponent += unsigned(f.exponent);
        }
        Factors factors(powers.begin(), powers.end());
        accumulate(p, factors, integer(ct.coff));
    }
    return p;
}
Integer power(const Integer& base, unsigned long exponent, const Budget& budget) {
    budget.check();
    if (base == 0) return exponent ? Integer(0) : Integer(1);
    if (base == 1) return Integer(1);
    if (base == -1) return Integer(exponent % 2 ? -1 : 1);
    // Decline pathological input powers before asking GMP for an enormous
    // allocation. This is only a conservative proof resource limit.
    const size_t bits = mpz_sizeinbase(base.get_mpz_t(), 2);
    if (exponent > 8UL * 1024 * 1024 / bits) throw Unsupported{};
    Integer result;
    mpz_pow_ui(result.get_mpz_t(), base.get_mpz_t(), exponent);
    budget.check();
    return result;
}
Polynomial substitute(const Polynomial& p, const std::vector<bool>& fixed,
                      const std::vector<Integer>& base, const Budget& budget) {
    Polynomial result;
    for (const auto& item : p) {
        budget.check();
        bool zero = false;
        for (auto f : item.first) if (fixed[f.first] && base[f.first] == 0) zero = true;
        if (zero) continue; // A zero factor eliminates even very high powers.
        Integer coefficient = item.second;
        Factors free;
        for (auto f : item.first) {
            if (fixed[f.first]) {
                auto factor = power(base[f.first], f.second, budget);
                const size_t bits = mpz_sizeinbase(coefficient.get_mpz_t(), 2);
                const size_t factor_bits = mpz_sizeinbase(factor.get_mpz_t(), 2);
                if (bits > 8UL * 1024 * 1024 || factor_bits > 8UL * 1024 * 1024 - bits)
                    throw Unsupported{};
                coefficient *= factor;
                budget.check();
            } else free.push_back(f);
        }
        accumulate(result, free, coefficient);
    }
    return result;
}
bool nonlinear(const Factors& factors) {
    return factors.size() > 1 || (!factors.empty() && factors.front().second > 1);
}
bool linear(const Polynomial& p) {
    for (const auto& item : p) if (nonlinear(item.first)) return false;
    return true;
}
Integer evaluate(const Polynomial& p, const std::vector<Integer>& base, const Budget& budget) {
    std::vector<bool> fixed(base.size(), true);
    auto value = substitute(p, fixed, base, budget);
    if (value.empty()) return Integer(0);
    if (value.size() != 1 || !value.begin()->first.empty()) throw Unsupported{};
    return value.begin()->second;
}
size_t literal_index(const ls_solver& s, int signed_id) {
    if (signed_id == 0 || signed_id == INT_MIN) throw Unsupported{};
    size_t id = size_t(signed_id < 0 ? -signed_id : signed_id);
    if (id >= s._lits.size() || id > s._num_lits) throw Unsupported{};
    return id;
}
size_t boolean_index(const ls_solver& s, const lit& p) {
    if (p.delta < 0 || __uint128_t(p.delta) >= s._num_vars) throw Unsupported{};
    size_t v = size_t(p.delta);
    if (s._vars[v].is_nia) throw Unsupported{};
    return v;
}
bool truth(const ls_solver& s, int signed_id, const std::vector<Integer>& base,
           const Budget& budget, Integer* value = nullptr) {
    const auto& p = s._lits[literal_index(s, signed_id)];
    bool positive;
    if (!p.is_nia_lit) positive = base[boolean_index(s, p)] == 1;
    else {
        Integer q = evaluate(polynomial(s, p.key, p.coff_terms, budget), base, budget);
        if (value) *value = q;
        positive = p.is_equal ? q == 0 : q <= 0;
    }
    return positive == (signed_id > 0);
}
unsigned long long complexity(const ls_solver& s, int signed_id, const Budget& budget) {
    const auto& p = s._lits[literal_index(s, signed_id)];
    if (!p.is_nia_lit) return 0;
    auto poly = polynomial(s, p.key, p.coff_terms, budget);
    unsigned long long cost = 1;
    for (auto& item : poly) {
        if (nonlinear(item.first)) cost += 1000000;
        cost += item.first.size();
    }
    return cost;
}
std::vector<int> select_literals(const ls_solver& s, const std::vector<Integer>& base,
                                 const Budget& budget) {
    std::set<int> selected;
    for (const auto& clause : s._clauses) {
        budget.check();
        int best = 0;
        unsigned long long cost = ULLONG_MAX;
        for (int id : clause.literals) {
            if (!truth(s, id, base, budget)) continue;
            auto score = complexity(s, id, budget);
            if (!best || score < cost) { best = id; cost = score; }
        }
        if (!best) throw Unsupported{};
        selected.insert(best);
    }
    return {selected.begin(), selected.end()};
}
std::vector<bool> choose_fixed(const std::vector<Polynomial>& polynomials,
                              const std::vector<Integer>& base,
                              const std::set<size_t>& objective_variables,
                              unsigned variant, const Budget& budget) {
    std::vector<bool> fixed(base.size(), false);
    for (;;) {
        budget.check();
        Factors conflict;
        for (const auto& p : polynomials) {
            auto residual = substitute(p, fixed, base, budget);
            for (const auto& item : residual) if (nonlinear(item.first)) { conflict = item.first; break; }
            if (!conflict.empty()) break;
        }
        if (conflict.empty()) return fixed;
        auto rank = [&](size_t v) {
            const bool objective = objective_variables.count(v) != 0;
            // Alternate anchors without consuming the caller's RNG. Early
            // variants protect objective variables; later ones reverse it.
            unsigned priority = variant < 4 ? unsigned(objective) : unsigned(!objective);
            unsigned zero = (variant % 2 == 0) ? unsigned(base[v] != 0) : unsigned(base[v] == 0);
            size_t order = (variant % 4 < 2) ? v : base.size() - 1 - v;
            if (variant % 2) order = base.size() - 1 - order;
            return std::make_tuple(priority, zero, order);
        };
        size_t pick = conflict.front().first;
        for (auto f : conflict) if (rank(f.first) < rank(pick)) pick = f.first;
        fixed[pick] = true;
    }
}
Integer slope(const Polynomial& p, const std::vector<Integer>& direction) {
    Integer result = 0;
    for (const auto& item : p) {
        if (nonlinear(item.first)) throw Unsupported{};
        if (!item.first.empty()) result += item.second * direction[item.first.front().first];
    }
    return result;
}
bool invariant(const lit& p, int signed_id, const Integer& value, const Integer& derivative) {
    if (p.is_equal && signed_id > 0) return value == 0 && derivative == 0;
    if (!p.is_equal && signed_id > 0) return value <= 0 && derivative <= 0;
    if (!p.is_equal) return value >= 1 && derivative >= 0;
    // Negated equality retains the sign branch true at this base.
    return value >= 1 ? derivative >= 0 : value <= -1 && derivative <= 0;
}
// This verifier rereads the original CNF and polynomials. It never consumes a
// Z3 expression, a model assertion, or the rows used to construct the query.
bool verify(const ls_solver& s, const std::vector<Integer>& base,
            const std::vector<Integer>& direction, const std::vector<bool>& fixed,
            const std::vector<int>& selected, Integer& objective_slope,
            const Budget& budget) {
    if (direction.size() != s._num_vars || base.size() != s._num_vars) return false;
    for (size_t v = 0; v < base.size(); ++v) {
        budget.check();
        if (fixed[v] && direction[v] != 0) return false;
        if (!s._vars[v].is_nia && ((base[v] != 1 && base[v] != -1) || direction[v] != 0)) return false;
    }
    std::set<int> chosen(selected.begin(), selected.end()), occurs;
    for (const auto& clause : s._clauses) {
        bool satisfied = false, covered = false;
        for (int id : clause.literals) {
            bool holds = truth(s, id, base, budget);
            satisfied |= holds;
            covered |= holds && chosen.count(id);
            occurs.insert(id);
        }
        if (!satisfied || !covered) return false;
    }
    for (int id : selected) {
        budget.check();
        if (!occurs.count(id) || !truth(s, id, base, budget)) return false;
        const auto& p = s._lits[literal_index(s, id)];
        if (!p.is_nia_lit) {
            if (direction[boolean_index(s, p)] != 0) return false;
            continue;
        }
        auto original = polynomial(s, p.key, p.coff_terms, budget);
        auto residual = substitute(original, fixed, base, budget);
        if (!linear(residual) || !invariant(p, id, evaluate(original, base, budget), slope(residual, direction))) return false;
    }
    auto objective = polynomial(s, s.omt_objective_constant, s.omt_objective_terms, budget);
    auto residual = substitute(objective, fixed, base, budget);
    if (!linear(residual)) return false;
    objective_slope = slope(residual, direction);
    return s.omt_direction == "max" ? objective_slope >= 1 : objective_slope <= -1;
}
z3::expr z3_slope(z3::context& context, const Polynomial& row, const std::vector<z3::expr>& d, const Budget& budget) {
    z3::expr sum = context.int_val(0);
    for (const auto& item : row) {
        budget.check();
        if (nonlinear(item.first)) throw Unsupported{};
        if (!item.first.empty()) sum = sum + context.int_val(item.second.get_str().c_str()) * d[item.first.front().first];
    }
    return sum;
}
std::string certificate(const ls_solver& s, const std::vector<Integer>& base,
                        const std::vector<Integer>& direction, const std::vector<bool>& fixed,
                        const std::vector<int>& selected, const Integer& objective_slope, const Budget& budget) {
    std::ostringstream out;
    out << "{\"kind\":\"lia_ray\",\"version\":1,\"base\":[";
    for (size_t v = 0; v < base.size(); ++v) { budget.check(); if (v) out << ','; out << '"' << base[v] << '"'; }
    out << "],\"direction\":[";
    for (size_t v = 0; v < direction.size(); ++v) { budget.check(); if (v) out << ','; out << '"' << direction[v] << '"'; }
    out << "],\"fixed\":[";
    bool comma = false;
    for (size_t v = 0; v < fixed.size(); ++v) if (fixed[v] && s._vars[v].is_nia) {
        budget.check();
        if (comma) out << ',';
        comma = true;
        out << v;
    }
    out << "],\"literals\":[";
    for (size_t i = 0; i < selected.size(); ++i) { budget.check(); if (i) out << ','; out << selected[i]; }
    out << "],\"objective_slope\":\"" << objective_slope << "\"}";
    return out.str();
}
}

OmtUnboundedResult prove_omt_unbounded(const ls_solver& s,
                                    const std::vector<__int128_t>& supplied_base,
                                    double seconds) {
    OmtUnboundedResult result;
    if (!std::isfinite(seconds) || !(seconds > 0)) { result.status = "budget"; return result; }
    Budget budget(seconds);
    try {
        if (!s.omt_loaded || s._vars.size() < s._num_vars || supplied_base.size() < s._num_vars ||
            (s.omt_direction != "min" && s.omt_direction != "max")) return result;
        std::vector<Integer> base;
        base.reserve(s._num_vars);
        for (size_t v = 0; v < s._num_vars; ++v) {
            budget.check();
            if (!s._vars[v].is_nia && supplied_base[v] != 1 && supplied_base[v] != -1) return result;
            base.push_back(integer(supplied_base[v]));
        }
        auto selected = select_literals(s, base, budget);
        std::vector<Polynomial> polynomials;
        for (int id : selected) {
            const auto& p = s._lits[literal_index(s, id)];
            if (p.is_nia_lit) polynomials.push_back(polynomial(s, p.key, p.coff_terms, budget));
        }
        auto objective = polynomial(s, s.omt_objective_constant, s.omt_objective_terms, budget);
        polynomials.push_back(objective);
        std::set<size_t> objective_variables;
        for (const auto& item : objective) for (auto f : item.first) objective_variables.insert(f.first);
        std::set<std::vector<bool>> tried;
        bool saw_unknown = false;
        for (unsigned attempt = 0; attempt < 8; ++attempt) {
            budget.check();
            auto fixed = choose_fixed(polynomials, base, objective_variables, attempt, budget);
            if (!tried.insert(fixed).second) continue;
            z3::context context;
            z3::solver solver(context, "QF_LIA");
            std::vector<z3::expr> d;
            d.reserve(base.size());
            for (size_t v = 0; v < base.size(); ++v) {
                budget.check();
                d.push_back(context.int_const(("ray_d_" + std::to_string(v)).c_str()));
                if (fixed[v] || !s._vars[v].is_nia) solver.add(d.back() == 0);
            }
            for (int id : selected) {
                budget.check();
                const auto& p = s._lits[literal_index(s, id)];
                if (!p.is_nia_lit) continue;
                auto original = polynomial(s, p.key, p.coff_terms, budget);
                auto residual = substitute(original, fixed, base, budget);
                if (!linear(residual)) throw Unsupported{};
                auto derivative = z3_slope(context, residual, d, budget);
                if (p.is_equal && id > 0) solver.add(derivative == 0);
                else if (id > 0) solver.add(derivative <= 0);
                else if (!p.is_equal || evaluate(original, base, budget) > 0) solver.add(derivative >= 0);
                else solver.add(derivative <= 0);
            }
            auto residual_objective = substitute(objective, fixed, base, budget);
            auto derivative = z3_slope(context, residual_objective, d, budget);
            solver.add(s.omt_direction == "max" ? derivative >= 1 : derivative <= -1);
            budget.check();
            const double remaining = budget.remaining();
            if (!(remaining > 0)) throw Expired{};
            double milliseconds = remaining * 1000.0 / (8 - attempt);
            unsigned timeout = milliseconds >= double(UINT_MAX) ? UINT_MAX :
                               std::max(1U, unsigned(milliseconds));
            solver.set("timeout", timeout);
            ++result.queries;
            auto status = solver.check();
            budget.check();
            if (status == z3::unknown) { saw_unknown = true; continue; }
            if (status != z3::sat) continue;
            auto model = solver.get_model();
            std::vector<Integer> direction;
            for (auto value : d) {
                budget.check();
                std::string numeral;
                if (!model.eval(value, true).is_numeral(numeral)) throw Unsupported{};
                direction.emplace_back(numeral);
            }
            Integer objective_slope;
            if (!verify(s, base, direction, fixed, selected, objective_slope, budget)) continue;
            auto json = certificate(s, base, direction, fixed, selected, objective_slope, budget);
            budget.check();
            result.proved = true;
            result.status = "proved";
            result.certificate_json = std::move(json);
            return result;
        }
        if (saw_unknown) result.status = "unknown";
    } catch (const Expired&) {
        result.status = "budget";
    } catch (const Unsupported&) {
        // Unsupported linearizations and invalid inputs are conservative misses.
    } catch (const z3::exception&) {
        result.status = "unknown";
    } catch (const std::exception&) {
        result.status = "unknown";
    } catch (...) {
        result.status = "unknown";
    }
    return result;
}
}
