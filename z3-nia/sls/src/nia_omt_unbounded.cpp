#include "nia_omt_unbounded.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <gmpxx.h>
#include "nia_omt_direction_sls.h"
#include <algorithm>
#include <deque>
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
                              unsigned variant, const Budget& budget,
                              const std::vector<double>* keep_free = nullptr) {
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
        for (auto f : conflict) {
            if (keep_free) {
                // Activity and previous failed direction rows are soft costs
                // of freezing a coordinate, never facts about feasibility.
                const double a = (*keep_free)[f.first], b = (*keep_free)[pick];
                if (a < b || (a == b && rank(f.first) < rank(pick))) pick = f.first;
            } else if (rank(f.first) < rank(pick)) pick = f.first;
        }
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
direction_sls::Row direction_row(const Polynomial& row, bool equality,
                                      bool negate, Integer rhs = 0) {
    direction_sls::Row result;
    result.equality = equality;
    result.rhs = std::move(rhs);
    for (const auto& item : row) {
        if (nonlinear(item.first)) throw Unsupported{};
        if (!item.first.empty()) {
            const Integer coefficient = negate ? Integer(-item.second) : item.second;
            result.terms.emplace_back(item.first.front().first, coefficient);
        }
    }
    return result;
}
std::vector<size_t> variables_in(const Polynomial& polynomial) {
    std::set<size_t> variables;
    for (const auto& item : polynomial)
        for (auto factor : item.first) variables.insert(factor.first);
    return {variables.begin(), variables.end()};
}
std::vector<double> search_preferences(const ls_solver& s,
                                       const OmtUnboundedSearchHints* hints,
                                       const Budget& budget) {
    std::vector<double> result(s._num_vars, 0.0);
    if (!hints) return result;
    auto activity = [](const std::vector<uint64_t>& counts, size_t v) {
        return v < counts.size() ? std::log1p(double(counts[v])) : 0.0;
    };
    for (size_t v = 0; v < result.size(); ++v) {
        budget.check();
        result[v] = 4 * activity(hints->improvement_activity, v)
                    + activity(hints->move_activity, v);
    }
    for (size_t c = 0; c < s._clauses.size() && c < hints->clause_pressure.size(); ++c) {
        budget.check();
        if (!hints->clause_pressure[c]) continue;
        std::set<size_t> variables;
        for (int id : s._clauses[c].literals) {
            const auto& literal = s._lits[literal_index(s, id)];
            if (!literal.is_nia_lit) continue;
            for (const auto& term : literal.coff_terms)
                for (auto factor : s._terms[term.term_idx].var_epxs)
                    variables.insert(size_t(factor.var_index));
        }
        const double pressure = std::log1p(double(hints->clause_pressure[c]));
        for (size_t v : variables) result[v] += pressure;
    }
    return result;
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
    return prove_omt_unbounded(s, supplied_base, seconds, nullptr);
}
OmtUnboundedResult prove_omt_unbounded(const ls_solver& s,
                                    const std::vector<__int128_t>& supplied_base,
                                    double seconds,
                                    const OmtUnboundedSearchHints* hints) {
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
        struct Attempt {
            unsigned variant;
            bool guided;
            std::vector<bool> fixed;
            std::vector<Integer> initial;
            bool resumed = false;
        };
        std::deque<Attempt> pending, retry;
        unsigned retries = 0;
        auto preferences = search_preferences(s, hints, budget);
        const bool has_search_information = std::any_of(preferences.begin(), preferences.end(),
                                                       [](double value) { return value > 0; });
        if (has_search_information) pending.push_back({0, true, {}});
        // Keep every old static strategy available. A guided miss cannot
        // permanently blacklist a coordinate or a legacy fixed set.
        for (unsigned variant = 0; variant < 8; ++variant) pending.push_back({variant, false, {}});
        unsigned adjustments = 0;
        bool saw_unknown = false;
        std::vector<Integer> previous_direction;
        while (!pending.empty() || !retry.empty()) {
            budget.check();
            if (pending.empty()) {
                // Duplicate static variants must not throw away the rest of
                // this probe's allowance. Continue missed local searches using
                // their own best point and a fresh private seed.
                Attempt resumed = std::move(retry.front());
                retry.pop_front();
                resumed.resumed = true;
                pending.push_back(std::move(resumed));
                ++retries;
            }
            Attempt attempt = std::move(pending.front());
            pending.pop_front();
            auto fixed = attempt.fixed.empty()
                ? choose_fixed(polynomials, base, objective_variables, attempt.variant, budget,
                               attempt.guided ? &preferences : nullptr)
                : std::move(attempt.fixed);
            if (!attempt.resumed && !tried.insert(fixed).second) continue;
            std::vector<direction_sls::Row> rows;
            std::vector<std::vector<size_t>> origins;
            for (size_t v = 0; v < base.size(); ++v) {
                budget.check();
                if (fixed[v] || !s._vars[v].is_nia) {
                    direction_sls::Row row;
                    row.equality = true;
                    row.terms.emplace_back(v, 1);
                    rows.push_back(std::move(row));
                    origins.push_back({v});
                }
            }
            for (int id : selected) {
                budget.check();
                const auto& p = s._lits[literal_index(s, id)];
                if (!p.is_nia_lit) continue;
                auto original = polynomial(s, p.key, p.coff_terms, budget);
                auto residual = substitute(original, fixed, base, budget);
                if (!linear(residual)) throw Unsupported{};
                const bool equality = p.is_equal && id > 0;
                const bool negate = id < 0 && (!p.is_equal || evaluate(original, base, budget) > 0);
                rows.push_back(direction_row(residual, equality, negate));
                // Keep pre-substitution factors: the frozen variables no
                // longer occur in the slope row but may cause its failure.
                origins.push_back(variables_in(original));
            }
            auto residual_objective = substitute(objective, fixed, base, budget);
            rows.push_back(direction_row(residual_objective, false, s.omt_direction == "max", -1));
            origins.push_back(variables_in(objective));
            budget.check();
            const double remaining = budget.remaining();
            if (!(remaining > 0)) throw Expired{};
            direction_sls::Options options;
            options.seconds = remaining / double(std::max<size_t>(1,
                attempt.resumed ? retry.size() + 1 : pending.size() + 1));
            options.checkpoint = [&budget]() { budget.check(); };
            options.seed = 1469598103934665603ULL;
            for (bool value : fixed) options.seed = (options.seed ^ unsigned(value)) * 1099511628211ULL;
            if (attempt.resumed) options.seed ^= 0x9e3779b97f4a7c15ULL * retries;
            if (!attempt.initial.empty()) previous_direction = attempt.initial;
            if (previous_direction.size() == base.size()) {
                options.initial = previous_direction;
                for (size_t v = 0; v < fixed.size(); ++v)
                    if (fixed[v] || !s._vars[v].is_nia) options.initial[v] = 0;
            }
            ++result.queries;
            if (attempt.guided) ++result.guided_attempts;
            const auto answer = direction_sls::solve(rows, base.size(), options);
            result.direction_steps += answer.steps;
            saw_unknown |= answer.expired;
            if (answer.found) {
                Integer objective_slope;
                if (verify(s, base, answer.direction, fixed, selected, objective_slope, budget)) {
                    auto json = certificate(s, base, answer.direction, fixed, selected, objective_slope, budget);
                    budget.check();
                    result.proved = true;
                    result.status = "proved";
                    result.certificate_json = std::move(json);
                    return result;
                }
            }
            previous_direction = answer.direction;
            if (answer.expired || answer.steps)
                retry.push_back({attempt.variant, attempt.guided, fixed, answer.direction, true});
            if (adjustments >= 4 || pending.empty()) continue;
            std::vector<double> pressure(base.size(), 0.0);
            for (size_t r = 0; r < answer.row_pressure.size() && r < origins.size(); ++r) {
                budget.check();
                if (!answer.row_pressure[r]) continue;
                const double amount = std::log1p(double(answer.row_pressure[r]));
                for (size_t v : origins[r]) pressure[v] += amount;
            }
            size_t release = base.size();
            for (size_t v = 0; v < fixed.size(); ++v) if (fixed[v] && pressure[v] > 0) {
                if (release == base.size() || pressure[v] + preferences[v] > pressure[release] + preferences[release])
                    release = v;
            }
            if (release == base.size()) continue;
            // Exchange an implicated anchor for other factors while retaining
            // exact affine residuals. This is advisory feedback, not UNSAT.
            const double largest = *std::max_element(preferences.begin(), preferences.end());
            preferences[release] = largest + 8 + pressure[release];
            auto revised = choose_fixed(polynomials, base, objective_variables,
                                        adjustments % 4, budget, &preferences);
            ++adjustments;
            if (tried.count(revised)) continue;
            pending.push_front({adjustments % 4, true, std::move(revised)});
            ++result.fixed_set_adjustments;
        }
        if (saw_unknown) result.status = "unknown";
    } catch (const Expired&) {
        result.status = "budget";
    } catch (const Unsupported&) {
        // Unsupported linearizations and invalid inputs are conservative misses.
    } catch (const std::exception&) {
        result.status = "unknown";
    } catch (...) {
        result.status = "unknown";
    }
    return result;
}
}
