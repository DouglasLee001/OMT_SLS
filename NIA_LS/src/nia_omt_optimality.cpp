#include "nia_omt_optimality.h"
#include "nia_ls.h"
#include <z3++.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <vector>

namespace nia {
namespace {
using Clock = std::chrono::steady_clock;
struct Expired {};
struct Budget {
    Clock::time_point start = Clock::now();
    double allowance;
    explicit Budget(double seconds) : allowance(seconds) {}
    double elapsed() const { return std::chrono::duration<double>(Clock::now() - start).count(); }
    double remaining() const { return allowance - elapsed(); }
    void check() const { if (!(remaining() > 0)) throw Expired{}; }
};

std::string read_source(const std::string& path, const Budget& budget) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open original SMT source: " + path);
    std::string source;
    char buffer[65536];
    while (input) {
        budget.check();
        input.read(buffer, sizeof(buffer));
        source.append(buffer, size_t(input.gcount()));
    }
    if (!input.eof()) throw std::runtime_error("cannot read original SMT source");
    return source;
}

// Mirror the converter's SMT-LIB lexical rules, including comments, quoted
// symbols and doubled quotes in strings. No source command is executed here.
std::vector<std::string> tokens(const std::string& source, const Budget& budget) {
    std::vector<std::string> result;
    for (size_t at = 0; at < source.size();) {
        budget.check();
        const unsigned char ch = static_cast<unsigned char>(source[at]);
        if (std::isspace(ch)) { ++at; continue; }
        if (ch == ';') {
            do { ++at; if ((at & 4095) == 0) budget.check(); }
            while (at < source.size() && source[at] != '\n');
            continue;
        }
        const size_t begin = at++;
        if (ch == '(' || ch == ')') {
            result.emplace_back(1, char(ch));
            continue;
        }
        if (ch == '|' || ch == '"') {
            bool closed = false;
            while (at < source.size()) {
                if ((at & 4095) == 0) budget.check();
                if (source[at++] != char(ch)) continue;
                if (ch == '"' && at < source.size() && source[at] == '"') { ++at; continue; }
                closed = true;
                break;
            }
            if (!closed) throw std::runtime_error("unterminated SMT-LIB quoted symbol/string");
        } else {
            while (at < source.size() && !std::isspace(static_cast<unsigned char>(source[at]))
                   && source[at] != '(' && source[at] != ')' && source[at] != ';') {
                ++at;
                if ((at & 4095) == 0) budget.check();
            }
        }
        result.push_back(source.substr(begin, at - begin));
    }
    return result;
}

struct Form {
    bool list = false;
    std::string atom;
    std::vector<Form> children;
};
Form expression(const std::vector<std::string>& input, size_t& at,
                const Budget& budget, unsigned depth = 0) {
    budget.check();
    if (at >= input.size() || input[at] == ")") throw std::runtime_error("malformed SMT-LIB expression");
    if (depth > 1024) throw std::runtime_error("SMT-LIB expression nesting is too deep");
    Form result;
    if (input[at] != "(") { result.atom = input[at++]; return result; }
    result.list = true;
    ++at;
    while (at < input.size() && input[at] != ")")
        result.children.push_back(expression(input, at, budget, depth + 1));
    if (at == input.size()) throw std::runtime_error("unclosed SMT-LIB parenthesis");
    ++at;
    return result;
}
std::vector<Form> forms(const std::string& source, const Budget& budget) {
    auto input = tokens(source, budget);
    std::vector<Form> result;
    size_t at = 0;
    while (at < input.size()) result.push_back(expression(input, at, budget));
    return result;
}
void render(const Form& form, std::ostream& out, const Budget& budget) {
    budget.check();
    if (!form.list) { out << form.atom; return; }
    out << '(';
    for (size_t i = 0; i < form.children.size(); ++i) {
        if (i) out << ' ';
        render(form.children[i], out, budget);
    }
    out << ')';
}
std::string rendered(const Form& form, const Budget& budget) {
    std::ostringstream text;
    render(form, text, budget);
    return text.str();
}

std::string safe_query_source(const ls_solver& solver, const std::string& source, const Budget& budget) {
    const std::set<std::string> accepted = {"declare-const", "declare-fun", "define-fun", "define-fun-rec",
        "define-funs-rec", "declare-sort", "define-sort", "assert", "set-logic"};
    auto expected = forms(solver.omt_original_objective, budget);
    if (expected.size() != 1) throw std::runtime_error("missing or malformed original objective metadata");
    const auto expected_objective = rendered(expected[0], budget);
    std::ostringstream query;
    unsigned objectives = 0, checks = 0;
    for (const auto& form : forms(source, budget)) {
        budget.check();
        if (!form.list || form.children.empty() || form.children[0].list)
            throw std::runtime_error("expected top-level SMT-LIB commands");
        const auto& command = form.children[0].atom;
        const bool output_command = command.compare(0, 4, "get-") == 0;
        if (command == "exit") {
            if (form.children.size() != 1) throw std::runtime_error("malformed exit command");
            break;
        }
        if (checks && command != "echo" && command != "set-info" && !output_command)
            throw std::runtime_error("command changes query state after check-sat: " + command);
        if (command == "minimize" || command == "maximize") {
            if (++objectives != 1 || form.children.size() != 2)
                throw std::runtime_error("expected exactly one scalar objective");
            const std::string direction = command == "minimize" ? "min" : "max";
            if (direction != solver.omt_direction)
                throw std::runtime_error("original objective direction differs from native metadata");
            const auto objective = rendered(form.children[1], budget);
            if (objective != expected_objective)
                throw std::runtime_error("original objective differs from native metadata");
            // Normalize only the parser command. The extracted objective stays
            // in its original sign for both min and max, just as in the converter.
            query << "(minimize " << objective << ")\n";
        } else if (command == "check-sat") {
            if (++checks > 1 || form.children.size() != 1)
                throw std::runtime_error("only one nonincremental check-sat is supported");
        } else if (command == "set-option" || command == "set-info" || command == "echo" || output_command) {
            // In particular never let source :timeout/:rlimit/:opt.* options
            // override this probe, or execute source queries and output commands.
        } else if (accepted.count(command)) {
            render(form, query, budget);
            query << '\n';
        } else {
            throw std::runtime_error("unsupported SMT-LIB command: " + command);
        }
    }
    if (objectives != 1) throw std::runtime_error("expected exactly one original objective");
    return query.str();
}

void exact_integer(const std::string& value) {
    size_t at = !value.empty() && value[0] == '-' ? 1 : 0;
    if (at == value.size()) throw std::runtime_error("incumbent must be an exact decimal integer");
    for (; at < value.size(); ++at)
        if (value[at] < '0' || value[at] > '9')
            throw std::runtime_error("incumbent must be an exact decimal integer");
}
void save_query(const std::string& output, const std::string& source, const std::string& text) {
    if (output.empty()) throw std::runtime_error("proof query output path is required");
    std::error_code error;
    if (std::filesystem::equivalent(output, source, error) && !error)
        throw std::runtime_error("proof query output would overwrite original source");
    std::ofstream file(output, std::ios::binary | std::ios::trunc);
    if (!file) throw std::runtime_error("cannot write proof query: " + output);
    file << text;
    file.close();
    if (!file) throw std::runtime_error("cannot finish writing proof query: " + output);
}
}

OmtOptimalityResult prove_omt_optimality(const ls_solver& solver, const std::string& best,
                                       double seconds, const std::string& query_output) {
    OmtOptimalityResult result;
    Budget budget(seconds);
    if (!std::isfinite(seconds) || seconds <= 0) {
        result.status = "budget";
        return result;
    }
    try {
        budget.check();
        if (!solver.omt_loaded || (solver.omt_direction != "min" && solver.omt_direction != "max"))
            throw std::runtime_error("missing native optimization metadata");
        exact_integer(best);
        auto original = read_source(solver.omt_source, budget);
        auto filtered = safe_query_source(solver, original, budget);
        budget.check();
        {
            z3::context context;
            z3::optimize parser(context);
            parser.from_string(filtered.c_str()); // Parsing only; never Optimize.check().
            budget.check();
            const auto objectives = parser.objectives();
            if (objectives.size() != 1 || !objectives[0].is_int())
                throw std::runtime_error("original source must have one integer objective");
            z3::solver query(context);
            query.add(parser.assertions()); // Full original Boolean/arithmetic hard formula.
            const auto bound = context.int_val(best.c_str());
            query.add(solver.omt_direction == "min" ? objectives[0] < bound : objectives[0] > bound);
            budget.check();
            const auto smt = query.to_smt2();
            save_query(query_output, solver.omt_source, smt);
            budget.check();
            const double milliseconds = std::floor(budget.remaining() * 1000);
            if (milliseconds < 1) throw Expired{};
            query.set("timeout", unsigned(std::min(milliseconds, double(std::numeric_limits<unsigned>::max()))));
            ++result.queries;
            const auto answer = query.check();
            budget.check();
            if (answer == z3::unsat) { result.proved = true; result.status = "proved"; }
            else if (answer == z3::sat) result.status = "sat";
            else { result.status = "unknown"; result.reason = query.reason_unknown(); }
        } // Include model/context construction and destruction in the allowance.
        budget.check();
    } catch (const Expired&) {
        result.proved = false;
        result.status = "budget";
        result.reason = "finite optimality probe exhausted its time allowance";
    } catch (const z3::exception& error) {
        result.proved = false;
        result.status = "error";
        result.reason = error.msg();
    } catch (const std::exception& error) {
        result.proved = false;
        result.status = "error";
        result.reason = error.what();
    }
    result.seconds = budget.elapsed();
    if (result.proved && result.seconds >= seconds) {
        result.proved = false;
        result.status = "budget";
        result.reason = "finite optimality proof completed after its allowance";
    }
    return result;
}
}
