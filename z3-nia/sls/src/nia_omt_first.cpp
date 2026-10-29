#include "nia_omt_first.h"
#include "nia_omt_input.h"
#include "nia_ls.h"
#include "nia_linear_initialize.h"

#include <gmpxx.h>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace nia {
namespace {
std::string json_string(const std::string& value) {
    std::ostringstream out;
    out << '"';
    for (unsigned char ch : value) {
        if (ch == '"' || ch == '\\') out << '\\' << char(ch);
        else if (ch < 32) out << "\\u" << std::hex << std::setw(4)
                             << std::setfill('0') << unsigned(ch) << std::dec;
        else out << char(ch);
    }
    out << '"';
    return out.str();
}

// The heuristic search uses fixed-width arithmetic.  These values are
// recomputed from the assignment, without trusting literal/term caches.
class ExactAssignment {
    const ls_solver& solver;
    const std::vector<__int128_t>& assignment;
    std::vector<mpz_class> monomials;
    std::vector<bool> evaluated;

    mpz_class integer(__int128_t value) const {
        return mpz_class(format_omt_integer(value), 10);
    }

    const mpz_class& monomial(size_t id) {
        if (!evaluated.at(id)) {
            mpz_class result = 1;
            for (const auto& factor : solver._terms.at(id).var_epxs) {
                mpz_class base = integer(assignment.at(factor.var_index)), power;
                mpz_pow_ui(power.get_mpz_t(), base.get_mpz_t(), factor.exponent);
                result *= power;
            }
            monomials[id] = std::move(result);
            evaluated[id] = true;
        }
        return monomials[id];
    }

public:
    ExactAssignment(const ls_solver& problem, const std::vector<__int128_t>& values)
        : solver(problem), assignment(values), monomials(problem._terms.size()),
          evaluated(problem._terms.size(), false) {
        if (values.size() != problem._num_vars)
            throw std::runtime_error("candidate assignment has the wrong size");
        for (size_t id = 0; id < values.size(); ++id)
            if (!problem._vars[id].is_nia && values[id] != -1 && values[id] != 1)
                throw std::runtime_error("candidate has an invalid Boolean value");
    }

    void validate() {
        for (size_t id = 0; id < solver._clauses.size(); ++id) {
            bool satisfied = false;
            for (int signed_literal : solver._clauses[id].literals) {
                const auto& literal = solver._lits.at(std::abs(signed_literal));
                bool truth;
                if (literal.is_nia_lit) {
                    mpz_class delta = integer(literal.key);
                    for (const auto& term : literal.coff_terms)
                        delta += integer(term.coff) * monomial(term.term_idx);
                    truth = literal.is_equal ? delta == 0 : delta <= 0;
                } else {
                    truth = assignment.at(static_cast<size_t>(literal.delta)) > 0;
                }
                if (truth == (signed_literal > 0)) { satisfied = true; break; }
            }
            if (!satisfied)
                throw std::runtime_error("candidate failed exact validation at clause " + std::to_string(id));
        }
    }

    std::string objective() {
        mpz_class value = integer(solver.omt_objective_constant);
        for (const auto& term : solver.omt_objective_terms)
            value += integer(term.coff) * monomial(term.term_idx);
        return value.get_str();
    }
};

std::vector<__int128_t> initial_assignment(const ls_solver& solver,
                                         const initial_model& model) {
    std::vector<__int128_t> values(solver._num_vars, 0);
    for (size_t id = 0; id < solver._num_vars; ++id) {
        const auto& var = solver._vars[id];
        auto entry = model.values.find(var.var_name);
        if (entry == model.values.end()) {
            // Variables absent from constraints are free, including targets.
            values[id] = var.is_nia ? 0 : -1;
            continue;
        }
        std::string text = entry->second;
        if (!var.is_nia) {
            if (text != "true" && text != "false")
                throw std::runtime_error("initializer returned an invalid Boolean value");
            values[id] = text == "true" ? 1 : -1;
        } else {
            if (text.rfind("(- ", 0) == 0 && text.back() == ')')
                text = "-" + text.substr(3, text.size() - 4);
            if (!parse_omt_integer(text, values[id]))
                throw std::runtime_error("initializer value exceeds the search's signed 128-bit range");
        }
    }
    return values;
}
}

int run_omt_first_solution(ls_solver& solver, const std::string& input_path,
                           bool initialize_enabled, double timeout_seconds,
                           double import_seconds, const std::string& model_output) {
    const auto began = std::chrono::steady_clock::now();
    auto elapsed = [&]() {
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
    };
    std::string status = "unknown", objective, error, stage = "none";
    std::string termination = "timeout";
    uint64_t steps = 0;
    bool validated = false;
    int exit_code = 0;
    try {
        std::vector<__int128_t> assignment;
        bool found = false;
        if (solver.build_unsat) {
            status = "unsat";
            termination = "preprocessing_unsat";
        } else {
            if (initialize_enabled && import_seconds + elapsed() < timeout_seconds) {
                stage = "initializer";
                initial_model initial;
                const double budget = std::min(10.0, timeout_seconds - import_seconds - elapsed());
                if (make_progressive_initial_model(input_path, budget, initial)) {
                    assignment = initial_assignment(solver, initial);
                    found = true;
                } else {
                    solver.progressive_values = std::move(initial.values);
                }
            }
            const double remaining = timeout_seconds - import_seconds - elapsed();
            if (!found && remaining > 0) {
                stage = "local_search";
                solver._cutoff = remaining;
                found = solver.local_search();
                steps = solver._step;
                if (found) assignment.assign(solver._solution.begin(), solver._solution.begin() + solver._num_vars);
            }
            if (found) {
                ExactAssignment exact(solver, assignment);
                exact.validate();
                objective = exact.objective();
                validated = true;
                if (!model_output.empty()) {
                    std::ofstream out(model_output);
                    if (!out) throw std::runtime_error("cannot open model output");
                    for (size_t id = 0; id < solver._num_vars; ++id) {
                        out << solver._vars[id].var_name << '\t';
                        if (solver._vars[id].is_nia) out << format_omt_integer(assignment[id]);
                        else out << (assignment[id] > 0 ? "true" : "false");
                        out << '\n';
                    }
                    out.close();
                    if (!out) throw std::runtime_error("cannot write model output");
                }
                status = "sat";
                termination = "first_feasible_model";
            }
        }
    } catch (const std::exception& exception) {
        status = "error";
        objective.clear();
        validated = false;
        error = exception.what();
        termination = "error";
        exit_code = 2;
    }
    const double seconds = elapsed();
    std::cout << std::setprecision(10)
              << "{\"status\":" << json_string(status)
              << ",\"objective_value\":" << (status == "sat" ? json_string(objective) : "null")
              << ",\"search_seconds\":" << seconds
              << ",\"elapsed_seconds\":" << import_seconds + seconds
              << ",\"steps\":" << steps
              << ",\"stage\":" << json_string(stage)
              << ",\"termination\":" << json_string(termination)
              << ",\"model_validated\":" << (validated ? "true" : "false")
              << ",\"is_optimal\":false,\"direction\":" << json_string(solver.omt_direction)
              << ",\"error\":" << json_string(error) << "}\n";
    return exit_code;
}
}
