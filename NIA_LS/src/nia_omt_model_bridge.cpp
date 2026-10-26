#include "nia_omt_model_bridge.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <gmpxx.h>
#include <algorithm>
#include <chrono>
#include <climits>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <utility>

namespace nia {
namespace {
using Clock = std::chrono::steady_clock;
struct Expired {};
struct Rejected : std::runtime_error {
    std::string status;
    Rejected(std::string state, const std::string& reason)
        : std::runtime_error(reason), status(std::move(state)) {}
};
struct Budget {
    Clock::time_point began = Clock::now();
    double allowance;
    explicit Budget(double value) : allowance(value) {}
    double elapsed() const { return std::chrono::duration<double>(Clock::now() - began).count(); }
    double remaining() const { return allowance - elapsed(); }
    void check() const { if (!(remaining() > 0)) throw Expired{}; }
};
void require(bool condition, const std::string& reason) {
    if (!condition) throw Rejected("unsupported", reason);
}
std::string integer_text(const z3::expr& value) {
    require(value.is_int() && value.is_numeral(), "model value is not an exact integer numeral");
    return Z3_get_numeral_string(value.ctx(), value);
}
using Name = std::pair<std::string, bool>; // Raw symbol name and integer-vs-Boolean sort.
void collect(const z3::expr& root, std::map<Name, z3::expr>& declarations,
             std::set<unsigned>& visited, const Budget& budget) {
    std::vector<z3::expr> pending{root};
    while (!pending.empty()) {
        budget.check();
        const z3::expr current = pending.back();
        pending.pop_back();
        if (!visited.insert(current.id()).second) continue;
        require(!current.is_quantifier(), "quantified source expressions cannot be restored");
        if (!current.is_app()) continue;
        if (current.num_args() == 0 && current.decl().decl_kind() == Z3_OP_UNINTERPRETED &&
            (current.is_int() || current.is_bool())) {
            declarations.emplace(Name(current.decl().name().str(), current.is_int()), current);
        }
        for (unsigned i = 0; i < current.num_args(); ++i) pending.push_back(current.arg(i));
    }
}
z3::expr power(z3::expr base, unsigned exponent, const Budget& budget) {
    z3::expr result = base.ctx().int_val(1);
    while (exponent) {
        budget.check();
        if (exponent & 1) result = result * base;
        exponent >>= 1;
        if (exponent) base = base * base;
    }
    return result;
}
void check_factor(const ls_solver& native, const var_exp& factor) {
    require(factor.var_index >= 0 && size_t(factor.var_index) < native._num_vars &&
            native._vars[factor.var_index].is_nia && factor.exponent > 0,
            "invalid native integer polynomial factor");
}
const term& get_term(const ls_solver& native, const coff_term& coefficient) {
    require(coefficient.term_idx >= 0 && size_t(coefficient.term_idx) < native._terms.size(),
            "invalid native polynomial term index");
    return native._terms[coefficient.term_idx];
}
z3::expr polynomial(const ls_solver& native, __int128_t constant,
                    const std::vector<coff_term>& coefficients,
                    const z3::expr_vector& variables, const Budget& budget) {
    z3::context& context = variables.ctx();
    z3::expr value = context.int_val(format_omt_integer(constant).c_str());
    for (const auto& coefficient : coefficients) {
        budget.check();
        if (coefficient.coff == 0) continue;
        z3::expr product = context.int_val(format_omt_integer(coefficient.coff).c_str());
        for (const auto& factor : get_term(native, coefficient).var_epxs) {
            check_factor(native, factor);
            product = product * power(variables[factor.var_index], unsigned(factor.exponent), budget);
        }
        value = value + product;
    }
    return value;
}
size_t literal_index(const ls_solver& native, int signed_id) {
    require(signed_id != 0 && signed_id != INT_MIN, "invalid native signed literal");
    const size_t index = size_t(signed_id < 0 ? -signed_id : signed_id);
    require(index <= native._num_lits && index < native._lits.size(), "invalid native literal index");
    return index;
}
size_t boolean_index(const ls_solver& native, const lit& literal) {
    require(literal.delta >= 0 && __uint128_t(literal.delta) < native._num_vars,
            "invalid native Boolean variable index");
    const size_t index = size_t(literal.delta);
    require(!native._vars[index].is_nia, "native Boolean literal refers to integer variable");
    return index;
}
z3::expr literal_expression(const ls_solver& native, int signed_id,
                            const z3::expr_vector& variables, const Budget& budget) {
    const auto& literal = native._lits[literal_index(native, signed_id)];
    z3::expr positive = variables.ctx().bool_val(false);
    if (literal.is_nia_lit) {
        const auto value = polynomial(native, literal.key, literal.coff_terms, variables, budget);
        positive = literal.is_equal ? value == 0 : value <= 0;
    } else positive = variables[unsigned(boolean_index(native, literal))];
    return signed_id > 0 ? positive : !positive;
}
mpz_class exact_power(const mpz_class& base, unsigned exponent, const Budget& budget) {
    budget.check();
    if (base == 0) return exponent ? mpz_class(0) : mpz_class(1);
    if (base == 1) return mpz_class(1);
    if (base == -1) return mpz_class(exponent % 2 ? -1 : 1);
    // This conservative arithmetic resource guard is not a numeric range
    // restriction on objective values. Large values within the budget use GMP.
    const size_t bits = mpz_sizeinbase(base.get_mpz_t(), 2);
    require(exponent <= 8UL * 1024 * 1024 / bits,
            "exact polynomial validation exceeds its arithmetic resource budget");
    mpz_class result;
    mpz_pow_ui(result.get_mpz_t(), base.get_mpz_t(), exponent);
    budget.check();
    return result;
}
mpz_class evaluate(const ls_solver& native, __int128_t constant,
                   const std::vector<coff_term>& coefficients,
                   const std::vector<mpz_class>& assignment, const Budget& budget) {
    mpz_class result(format_omt_integer(constant));
    for (const auto& coefficient : coefficients) {
        budget.check();
        if (coefficient.coff == 0) continue;
        const auto& monomial = get_term(native, coefficient);
        bool zero = false;
        for (const auto& factor : monomial.var_epxs) {
            check_factor(native, factor);
            zero |= assignment[factor.var_index] == 0;
        }
        if (zero) continue;
        mpz_class product(format_omt_integer(coefficient.coff));
        for (const auto& factor : monomial.var_epxs)
            product *= exact_power(assignment[factor.var_index], unsigned(factor.exponent), budget);
        result += product;
    }
    return result;
}
bool truth(const ls_solver& native, int signed_id, const std::vector<mpz_class>& assignment,
           const Budget& budget) {
    const auto& literal = native._lits[literal_index(native, signed_id)];
    bool positive;
    if (!literal.is_nia_lit) positive = assignment[boolean_index(native, literal)] == 1;
    else {
        const auto value = evaluate(native, literal.key, literal.coff_terms, assignment, budget);
        positive = literal.is_equal ? value == 0 : value <= 0;
    }
    return positive == (signed_id > 0);
}
}

OmtModelBridgeResult restore_omt_native_model(
    const ls_solver& native, const z3::model& original_model,
    const z3::expr_vector& original_hard, const z3::expr& original_objective,
    double seconds) {
    OmtModelBridgeResult result;
    Budget budget(seconds);
    if (!std::isfinite(seconds) || seconds <= 0) {
        result.status = "budget";
        result.reason = "model restoration requires a finite positive time allowance";
        result.seconds = budget.elapsed();
        return result;
    }
    try {
        budget.check();
        require(native.omt_loaded && native._num_vars <= native._vars.size() &&
                native._num_clauses == native._clauses.size(), "missing or invalid native optimization model");
        z3::context& context = original_model.ctx();
        require(&original_hard.ctx() == &context && &original_objective.ctx() == &context,
                "source model, constraints and objective must share one Z3 context");
        require(original_objective.is_int(), "source objective must have integer sort");
        std::map<Name, z3::expr> declarations;
        std::set<unsigned> visited;
        for (const auto& constraint : original_hard) {
            budget.check();
            require(constraint.is_bool(), "source hard constraint must have Boolean sort");
            if (!original_model.eval(constraint, true).is_true())
                throw Rejected("rejected", "source model does not satisfy the supplied original hard formula");
            collect(constraint, declarations, visited, budget);
        }
        collect(original_objective, declarations, visited, budget);
        const auto source_value = original_model.eval(original_objective, true);
        const mpz_class expected(integer_text(source_value));
        z3::solver completion(context);
        z3::expr_vector variables(context);
        for (size_t i = 0; i < native._num_vars; ++i) {
            budget.check();
            const auto& variable = native._vars[i];
            const auto sort = variable.is_nia ? context.int_sort() : context.bool_sort();
            variables.push_back(z3::expr(context, Z3_mk_fresh_const(context, "omt-native-bridge", sort)));
            const auto found = declarations.find(Name(variable.var_name, variable.is_nia));
            if (found == declarations.end()) continue;
            const auto source = original_model.eval(found->second, true);
            if (variable.is_nia) {
                __int128_t value;
                require(parse_omt_integer(integer_text(source), value),
                        "source integer variable is outside the signed 128-bit assignment range");
            } else require(source.is_true() || source.is_false(), "source Boolean variable has no concrete truth value");
            completion.add(variables[unsigned(i)] == source);
        }
        for (const auto& clause : native._clauses) {
            budget.check();
            z3::expr disjunction = context.bool_val(false);
            for (int id : clause.literals)
                disjunction = disjunction || literal_expression(native, id, variables, budget);
            completion.add(disjunction);
        }
        completion.add(polynomial(native, native.omt_objective_constant,
                                  native.omt_objective_terms, variables, budget) == source_value);
        budget.check();
        const double milliseconds = std::floor(budget.remaining() * 1000);
        if (milliseconds < 1) throw Expired{};
        completion.set("timeout", unsigned(std::min(milliseconds, double(std::numeric_limits<unsigned>::max()))));
        const auto answer = completion.check();
        budget.check();
        if (answer == z3::unsat)
            throw Rejected("rejected", "source SAT model cannot be completed in the native model; this is not an original UNSAT proof");
        if (answer != z3::sat) throw Rejected("unknown", completion.reason_unknown());
        const auto model = completion.get_model();
        std::vector<__int128_t> restored;
        std::vector<mpz_class> exact;
        for (size_t i = 0; i < native._num_vars; ++i) {
            budget.check();
            const auto value = model.eval(variables[unsigned(i)], true);
            __int128_t native_value;
            if (native._vars[i].is_nia) {
                const std::string text = integer_text(value);
                require(parse_omt_integer(text, native_value),
                        "completed integer variable is outside the signed 128-bit assignment range");
            } else {
                require(value.is_true() || value.is_false(), "completed Boolean variable has no concrete truth value");
                native_value = value.is_true() ? 1 : -1;
            }
            restored.push_back(native_value);
            exact.emplace_back(format_omt_integer(native_value));
        }
        // Re-evaluate the unchanged complete native CNF with GMP. No Z3
        // expression, solver cache, or objective equality is trusted here.
        for (const auto& clause : native._clauses) {
            budget.check();
            bool satisfied = false;
            for (int id : clause.literals) satisfied |= truth(native, id, exact, budget);
            if (!satisfied) throw Rejected("rejected", "restored assignment failed exact native hard-clause validation");
        }
        const auto objective = evaluate(native, native.omt_objective_constant,
                                        native.omt_objective_terms, exact, budget);
        if (objective != expected)
            throw Rejected("rejected", "restored native objective differs from the exact original model objective");
        budget.check();
        result.assignment = std::move(restored);
        result.objective = objective.get_str();
        result.imported = true;
        result.status = "imported";
    } catch (const Expired&) {
        result.status = "budget";
        result.reason = "model restoration exhausted its time allowance";
    } catch (const Rejected& error) {
        result.status = error.status;
        result.reason = error.what();
    } catch (const z3::exception& error) {
        result.status = "error";
        result.reason = error.msg();
    } catch (const std::exception& error) {
        result.status = "error";
        result.reason = error.what();
    }
    result.seconds = budget.elapsed();
    if (result.imported && result.seconds >= seconds) {
        result.imported = false;
        result.status = "budget";
        result.reason = "model restoration completed after its time allowance";
    }
    if (!result.imported) {
        result.assignment.clear();
        result.objective.clear();
    }
    return result;
}
}
