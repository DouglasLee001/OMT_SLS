#include "nia_import.h"
#include <z3++.h>
#include <gmpxx.h>
#include <algorithm>
#include <cctype>
#include <climits>
#include <map>
#include <set>
#include <sstream>
#include <utility>
#include <vector>

namespace nia { namespace embedded {
namespace {

constexpr std::size_t term_budget = 1024;
using Monomial = std::vector<std::pair<std::size_t, int>>;

struct Form {
    bool atom = false;
    std::string text;
    std::vector<Form> children;
};

std::vector<std::string> tokens(std::string const& source) {
    std::vector<std::string> out;
    std::size_t i = 0;
    while (i < source.size()) {
        unsigned char ch = static_cast<unsigned char>(source[i]);
        if (std::isspace(ch)) { ++i; continue; }
        if (ch == ';') {
            auto end = source.find('\n', i);
            i = end == std::string::npos ? source.size() : end + 1;
        } else if (ch == '(' || ch == ')') {
            out.push_back(source.substr(i++, 1));
        } else if (ch == '|') {
            auto end = source.find('|', i + 1);
            if (end == std::string::npos) throw ImportError("unterminated quoted SMT-LIB symbol");
            out.push_back(source.substr(i, end + 1 - i));
            i = end + 1;
        } else if (ch == '"') {
            auto start = i++;
            bool closed = false;
            while (i < source.size()) {
                if (source[i] != '"') { ++i; continue; }
                if (i + 1 < source.size() && source[i + 1] == '"') { i += 2; continue; }
                ++i;
                out.push_back(source.substr(start, i - start));
                closed = true;
                break;
            }
            if (!closed) throw ImportError("unterminated SMT-LIB string");
        } else {
            auto start = i;
            while (i < source.size() && !std::isspace(static_cast<unsigned char>(source[i])) &&
                   source[i] != '(' && source[i] != ')' && source[i] != ';') ++i;
            out.push_back(source.substr(start, i - start));
        }
    }
    return out;
}

std::vector<Form> commands(std::string const& source) {
    std::vector<Form> top;
    // A stack of unfinished lists avoids pointers invalidated by vector growth.
    std::vector<std::vector<Form>> stack;
    auto append = [&](Form form) {
        (stack.empty() ? top : stack.back()).push_back(std::move(form));
    };
    for (auto const& token : tokens(source)) {
        if (token == "(") stack.emplace_back();
        else if (token == ")") {
            if (stack.empty()) throw ImportError("unmatched closing SMT-LIB parenthesis");
            Form form;
            form.children = std::move(stack.back());
            stack.pop_back();
            append(std::move(form));
        } else append(Form{true, token, {}});
    }
    if (!stack.empty()) throw ImportError("unclosed SMT-LIB parenthesis");
    return top;
}

std::string render(Form const& form) {
    if (form.atom) return form.text;
    std::string out = "(";
    bool first = true;
    for (auto const& child : form.children) {
        if (!first) out += ' ';
        first = false;
        out += render(child);
    }
    return out + ")";
}

struct Query { std::string text, direction, objective; };
Query query(std::string const& source) {
    Query out;
    bool has_objective = false;
    unsigned checks = 0;
    std::set<std::string> const accepted = {"declare-const", "declare-fun", "define-fun", "define-fun-rec",
        "define-funs-rec", "declare-sort", "define-sort", "assert", "set-logic"};
    for (auto form : commands(source)) {
        if (form.atom || form.children.empty() || !form.children[0].atom)
            throw ImportError("expected top-level SMT-LIB commands");
        auto const command = form.children[0].text;
        if (command == "exit") break;
        bool get = command.compare(0, 4, "get-") == 0;
        if (checks && command != "echo" && command != "set-info" && !get)
            throw ImportError("command after check-sat would change the query state: " + command);
        if (command == "minimize" || command == "maximize") {
            if (has_objective || form.children.size() != 2)
                throw ImportError("expected exactly one scalar minimize or maximize objective");
            has_objective = true;
            out.objective = render(form.children[1]);
            out.direction = command == "minimize" ? "min" : "max";
            form.children[0].text = "minimize";
            out.text += render(form) + '\n';
        } else if (command == "check-sat") {
            if (form.children.size() != 1 || ++checks > 1)
                throw ImportError("only a single nonincremental OMT query is supported");
        } else if (command == "set-option" || command == "set-info" || command == "echo" || get) {
            continue;
        } else if (accepted.count(command)) out.text += render(form) + '\n';
        else throw ImportError("unsupported SMT-LIB command: " + command);
    }
    if (!has_objective) throw ImportError("expected exactly one scalar minimize or maximize objective");
    return out;
}

std::string hex(std::string const& text) {
    if (text.empty()) return "-";
    static char const digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(text.size() * 2);
    for (unsigned char c : text) { out += digits[c >> 4]; out += digits[c & 15]; }
    return out;
}
std::string name(z3::func_decl const& declaration) {
    std::ostringstream out;
    out << declaration.name();
    return out.str();
}

struct Variables {
    std::vector<std::pair<char, std::string>> items;
    std::map<unsigned, std::size_t> by_decl;
    std::set<std::string> used_names, reserved_names;

    void reserve(std::vector<z3::expr> expressions) {
        std::set<unsigned> seen;
        while (!expressions.empty()) {
            auto expression = expressions.back();
            expressions.pop_back();
            if (!seen.insert(expression.id()).second) continue;
            if (expression.is_quantifier()) throw ImportError("quantifiers are not supported: expected QF_NIA");
            if (!expression.is_app()) throw ImportError("bound or non-application expressions are not supported");
            if (expression.decl().decl_kind() == Z3_OP_UNINTERPRETED) {
                if (expression.num_args()) throw ImportError("uninterpreted function is not supported: " + name(expression.decl()));
                if (!expression.is_int() && !expression.is_bool())
                    throw ImportError("unsupported variable sort: " + expression.get_sort().to_string());
                reserved_names.insert(name(expression.decl()));
            }
            for (unsigned i = 0; i < expression.num_args(); ++i) expressions.push_back(expression.arg(i));
        }
    }

    std::size_t add(z3::expr const& expression) {
        auto declaration = expression.decl();
        auto found = by_decl.find(declaration.id());
        if (found != by_decl.end()) return found->second;
        if (expression.num_args() || declaration.decl_kind() != Z3_OP_UNINTERPRETED)
            throw ImportError("expected a scalar Int or Bool variable");
        if (!expression.is_int() && !expression.is_bool())
            throw ImportError("unsupported variable sort: " + expression.get_sort().to_string());
        std::string exported = name(declaration);
        if (used_names.count(exported)) {
            auto stem = exported;
            std::size_t suffix = 0;
            while (used_names.count(exported) || reserved_names.count(exported))
                exported = stem + "!nia_omt_" + std::to_string(suffix++);
        }
        auto index = items.size();
        items.emplace_back(expression.is_int() ? 'I' : 'B', exported);
        used_names.insert(exported);
        by_decl.emplace(declaration.id(), index);
        return index;
    }

    std::size_t auxiliary() {
        auto index = items.size(), suffix = index;
        std::string exported = "__nia_omt_aux_" + std::to_string(suffix);
        while (used_names.count(exported) || reserved_names.count(exported))
            exported = "__nia_omt_aux_" + std::to_string(++suffix);
        items.emplace_back('I', exported);
        used_names.insert(exported);
        return index;
    }
};

// The map gives exact sorted canonical keys. The order vector separately
// retains Python dict insertion/deletion order for arithmetic and aliasing.
struct Polynomial {
    std::map<Monomial, mpz_class> coefficients;
    std::vector<Monomial> order;
    std::size_t size() const { return coefficients.size(); }
    mpz_class get(Monomial const& m) const {
        auto found = coefficients.find(m);
        return found == coefficients.end() ? mpz_class(0) : found->second;
    }
    void set(Monomial const& m, mpz_class const& value) {
        auto found = coefficients.find(m);
        if (value == 0) {
            if (found != coefficients.end()) {
                coefficients.erase(found);
                order.erase(std::find(order.begin(), order.end(), m));
            }
        } else {
            if (found == coefficients.end()) order.push_back(m);
            coefficients[m] = value;
        }
    }
    std::string key() const {
        std::ostringstream out;
        for (auto const& term : coefficients) {
            out << term.second << ':';
            for (auto const& factor : term.first) out << factor.first << '^' << factor.second << ',';
            out << ';';
        }
        return out.str();
    }
    bool nonconstant() const {
        return !coefficients.empty() && !coefficients.rbegin()->first.empty();
    }
};
Polynomial constant(mpz_class const& value) {
    Polynomial out;
    out.set({}, value);
    return out;
}
Polynomial variable(std::size_t index) {
    Polynomial out;
    out.set({{index, 1}}, 1);
    return out;
}

struct Polynomials {
    Variables& variables;
    std::map<unsigned, Polynomial> cache;
    std::vector<Polynomial> definitions;
    std::map<std::string, Polynomial> aliases;
    explicit Polynomials(Variables& variables) : variables(variables) {}

    Polynomial alias(Polynomial const& polynomial) {
        if (polynomial.size() <= 1) return polynomial;
        auto key = polynomial.key();
        auto found = aliases.find(key);
        if (found != aliases.end()) return found->second;
        auto index = variables.auxiliary();
        auto replacement = variable(index);
        Polynomial definition;
        for (auto const& m : polynomial.order) definition.set(m, -polynomial.get(m));
        definition.set({{index, 1}}, 1);
        definitions.push_back(std::move(definition));
        aliases.emplace(std::move(key), replacement);
        return replacement;
    }

    Polynomial add(Polynomial const& left, Polynomial const& right, int factor = 1) {
        auto result = left;
        for (auto const& m : right.order) result.set(m, result.get(m) + factor * right.get(m));
        if (result.size() > term_budget) result = alias(result);
        return result;
    }

    Polynomial multiply(Polynomial left, Polynomial right) {
        if (!left.size() || !right.size()) return {};
        if (left.size() > term_budget / right.size()) {
            left = alias(left);
            right = alias(right);
        }
        Polynomial result;
        for (auto const& a : left.order) for (auto const& b : right.order) {
            std::map<std::size_t, int> powers(a.begin(), a.end());
            for (auto const& factor : b) {
                int old = powers[factor.first];
                if (old > INT_MAX - factor.second)
                    throw ImportError("a polynomial exponent exceeds signed 32-bit range");
                powers[factor.first] = old + factor.second;
            }
            Monomial m(powers.begin(), powers.end());
            result.set(m, result.get(m) + left.get(a) * right.get(b));
        }
        return result;
    }

    Polynomial of(z3::expr const& expression) {
        auto cached = cache.find(expression.id());
        if (cached != cache.end()) return cached->second;
        Polynomial result;
        if (expression.is_numeral() && expression.is_int()) {
            result = constant(mpz_class(Z3_get_numeral_string(expression.ctx(), expression), 10));
        } else if (!expression.is_app()) {
            throw ImportError("non-polynomial or quantified arithmetic is not supported");
        } else {
            auto kind = expression.decl().decl_kind();
            if (kind == Z3_OP_UNINTERPRETED) {
                if (!expression.is_int()) throw ImportError("expected an integer arithmetic variable: " + expression.to_string());
                result = variable(variables.add(expression));
            } else if (kind == Z3_OP_ADD) {
                for (unsigned i = 0; i < expression.num_args(); ++i) result = add(result, of(expression.arg(i)));
            } else if (kind == Z3_OP_UMINUS) {
                auto argument = of(expression.arg(0));
                for (auto const& m : argument.order) result.set(m, -argument.get(m));
            } else if (kind == Z3_OP_SUB) {
                result = of(expression.arg(0));
                for (unsigned i = 1; i < expression.num_args(); ++i) result = add(result, of(expression.arg(i)), -1);
            } else if (kind == Z3_OP_MUL) {
                result = constant(1);
                for (unsigned i = 0; i < expression.num_args(); ++i) result = multiply(result, of(expression.arg(i)));
            } else if (kind == Z3_OP_POWER) {
                auto exponent = expression.arg(1);
                if (!exponent.is_numeral() || !exponent.is_int())
                    throw ImportError("only nonnegative integer polynomial exponents are supported");
                mpz_class power(Z3_get_numeral_string(exponent.ctx(), exponent), 10);
                if (power < 0 || power > INT_MAX) throw ImportError("only nonnegative integer polynomial exponents are supported");
                unsigned remaining = static_cast<unsigned>(power.get_ui());
                result = constant(1);
                auto factor = of(expression.arg(0));
                while (remaining) {
                    if (remaining & 1) result = multiply(result, factor);
                    remaining >>= 1;
                    if (remaining) factor = multiply(factor, factor);
                }
            } else throw ImportError("unsupported non-polynomial arithmetic operator: " + name(expression.decl()));
        }
        cache.emplace(expression.id(), result);
        return result;
    }
};

struct Preprocessed {
    std::vector<z3::expr> cnf;
    z3::expr objective;
    bool unsat = false;
    explicit Preprocessed(z3::expr objective) : objective(std::move(objective)) {}
};
Preprocessed preprocess(z3::context& context, std::vector<z3::expr> const& hard,
                        z3::expr const& objective, ImportOptions const& options) {
    // Exactly the Python marker construction, including the fresh Bool name.
    z3::expr fresh(context, Z3_mk_fresh_const(context, "omt_marker_name", context.bool_sort()));
    Z3_sort domain[] = {context.int_sort()};
    z3::func_decl marker(context, Z3_mk_func_decl(context, Z3_get_decl_name(context, fresh.decl()),
                                               1, domain, context.bool_sort()));
    z3::goal goal(context);
    for (auto const& expression : hard) goal.add(expression);
    goal.add(marker(objective));
    z3::params simplify_params(context);
    simplify_params.set("som", false);
    simplify_params.set("mul_to_power", false);
    simplify_params.set("arith_lhs", true);
    simplify_params.set("blast_distinct", true);
    simplify_params.set("factor_nonlinear", false);
    auto simplify = z3::with(z3::tactic(context, "simplify"), simplify_params);
    z3::params no_factor(context);
    no_factor.set("factor_nonlinear", false);
    std::vector<z3::tactic> stages{simplify};
    if (options.preprocess) {
        z3::tactic solve_eqs(context, "solve-eqs");
        if (options.keep_definitions) {
            z3::params keep(context);
            keep.set("theory_solver", false);
            solve_eqs = z3::with(solve_eqs, keep);
        }
        stages.push_back(z3::with(z3::tactic(context, "propagate-values"), no_factor));
        stages.push_back(z3::with(solve_eqs & simplify, no_factor));
    }
    stages.emplace_back(context, "elim-term-ite");
    stages.push_back(simplify);
    z3::expr marked(context);
    for (auto const& stage : stages) {
        auto applied = stage(goal);
        if (applied.size() != 1) throw ImportError("preprocessing unexpectedly split the input into several goals");
        goal = applied[0];
        if (goal.inconsistent()) {
            Preprocessed result(context.int_val(0));
            result.unsat = true;
            return result;
        }
        unsigned count = 0;
        for (unsigned i = 0; i < goal.size(); ++i) {
            auto atom = goal[i];
            if (atom.is_app() && z3::eq(atom.decl(), marker)) { marked = atom; ++count; }
        }
        if (count != 1) throw ImportError("objective marker was lost or duplicated during preprocessing");
    }
    Preprocessed result(marked.arg(0));
    z3::goal plain(context);
    for (unsigned i = 0; i < goal.size(); ++i) {
        auto atom = goal[i];
        if (!(atom.is_app() && z3::eq(atom.decl(), marker))) plain.add(atom);
    }
    auto applied = z3::with(z3::tactic(context, "tseitin-cnf"), no_factor)(plain);
    if (applied.size() != 1) throw ImportError("CNF conversion unexpectedly produced several goals");
    auto cnf = applied[0];
    for (unsigned i = 0; i < cnf.size(); ++i) result.cnf.push_back(cnf[i]);
    result.unsat = cnf.inconsistent();
    return result;
}

std::string i128(mpz_class const& value) {
    static mpz_class const minimum("-170141183460469231731687303715884105728", 10);
    static mpz_class const maximum("170141183460469231731687303715884105727", 10);
    if (value < minimum || value > maximum)
        throw ImportError("integer coefficient or constant exceeds signed 128-bit range: " + value.get_str());
    return value.get_str();
}
void write_polynomial(std::ostream& out, Polynomial const& polynomial) {
    std::size_t terms = polynomial.size() - polynomial.coefficients.count({});
    out << i128(polynomial.get({})) << ' ' << terms << '\n';
    for (auto const& term : polynomial.coefficients) {
        if (term.first.empty()) continue;
        out << i128(term.second) << ' ' << term.first.size();
        for (auto const& factor : term.first) out << ' ' << factor.first << ' ' << factor.second;
        out << '\n';
    }
}

struct Literal { char kind; std::size_t variable_index; Polynomial polynomial; };
struct Encoder {
    Variables& variables;
    Polynomials& polynomials;
    std::vector<Literal> literals;
    std::map<std::string, int> literal_ids;
    std::vector<std::vector<int>> clauses;
    Encoder(Variables& v, Polynomials& p) : variables(v), polynomials(p) {}

    int intern(char kind, Polynomial const& polynomial, std::size_t variable_index = 0) {
        std::string key = std::string(1, kind) + (kind == 'B' ? std::to_string(variable_index) : polynomial.key());
        auto found = literal_ids.find(key);
        if (found != literal_ids.end()) return found->second;
        if (literals.size() >= INT_MAX) throw ImportError("too many native literals");
        int index = static_cast<int>(literals.size() + 1);
        literal_ids.emplace(std::move(key), index);
        literals.push_back({kind, variable_index, polynomial});
        return index;
    }

    // Integer 0 and a separate truth flag distinguish constant False from a literal.
    struct Atom { int literal = 0; bool truth = false; };
    Atom atom_literal(z3::expr atom) {
        int sign = 1;
        while (atom.is_not()) { sign = -sign; atom = atom.arg(0); }
        if (atom.is_true()) return {0, sign > 0};
        if (atom.is_false()) return {0, sign < 0};
        if (atom.is_const() && atom.decl().decl_kind() == Z3_OP_UNINTERPRETED) {
            if (!atom.is_bool()) throw ImportError("expected Boolean CNF atom");
            return {sign * intern('B', {}, variables.add(atom)), false};
        }
        if (!atom.is_app() || atom.num_args() != 2) throw ImportError("unsupported CNF atom: " + atom.to_string());
        auto kind = atom.decl().decl_kind();
        auto left = atom.arg(0), right = atom.arg(1);
        if ((kind != Z3_OP_LE && kind != Z3_OP_GE && kind != Z3_OP_LT && kind != Z3_OP_GT && kind != Z3_OP_EQ)
                || !left.is_int() || !right.is_int()) throw ImportError("unsupported CNF atom: " + atom.to_string());
        if (kind == Z3_OP_GE || kind == Z3_OP_GT) std::swap(left, right);
        // Evaluate left before right; C++ argument evaluation order must not
        // decide native variable indices or polynomial auxiliary numbering.
        auto left_polynomial = polynomials.of(left);
        auto right_polynomial = polynomials.of(right);
        auto value = polynomials.add(left_polynomial, right_polynomial, -1);
        if (kind == Z3_OP_LT || kind == Z3_OP_GT) value = polynomials.add(value, constant(1));
        char literal_kind = kind == Z3_OP_EQ ? 'E' : 'L';
        if (!value.nonconstant()) {
            auto c = value.get({});
            bool truth = literal_kind == 'E' ? c == 0 : c <= 0;
            return {0, sign > 0 ? truth : !truth};
        }
        return {sign * intern(literal_kind, value), false};
    }

    void encode(std::vector<z3::expr> const& cnf, bool unsat) {
        if (unsat) { clauses.emplace_back(); return; }
        for (auto const& expression : cnf) {
            std::vector<z3::expr> atoms;
            if (expression.is_or()) for (unsigned i = 0; i < expression.num_args(); ++i) atoms.push_back(expression.arg(i));
            else atoms.push_back(expression);
            std::vector<int> clause;
            bool tautology = false;
            for (auto const& atom : atoms) {
                auto result = atom_literal(atom);
                if (!result.literal) {
                    if (result.truth) { tautology = true; break; }
                    continue;
                }
                if (std::find(clause.begin(), clause.end(), -result.literal) != clause.end()) { tautology = true; break; }
                if (std::find(clause.begin(), clause.end(), result.literal) == clause.end()) clause.push_back(result.literal);
            }
            if (!tautology) clauses.push_back(std::move(clause));
        }
    }
};

} // namespace

std::string convert_text(std::string const& source, std::string const& source_name, ImportOptions const& options) {
    try {
        auto parsed = query(source);
        // No process-wide Z3 settings are changed. This context and all tactic
        // parameters belong exclusively to this one import operation.
        z3::context context;
        z3::optimize optimizer(context);
        optimizer.from_string(parsed.text.c_str());
        auto objectives = optimizer.objectives();
        if (objectives.size() != 1) throw ImportError("expected one arithmetic optimization objective");
        auto objective = objectives[0];
        if (!objective.is_int()) throw ImportError("objective must have integer sort, found " + objective.get_sort().to_string());
        auto assertions = optimizer.assertions();
        std::vector<z3::expr> hard;
        for (unsigned i = 0; i < assertions.size(); ++i) hard.push_back(assertions[i]);
        Variables variables;
        auto reserved = hard;
        reserved.push_back(objective);
        variables.reserve(std::move(reserved));
        auto rewritten = preprocess(context, hard, objective, options);
        reserved = rewritten.cnf;
        reserved.push_back(rewritten.objective);
        variables.reserve(std::move(reserved));
        Polynomials polynomials(variables);
        Polynomial objective_polynomial;
        if (!(options.preprocess && options.keep_definitions)) objective_polynomial = polynomials.of(rewritten.objective);
        Encoder encoder(variables, polynomials);
        encoder.encode(rewritten.cnf, rewritten.unsat);
        if (options.preprocess && options.keep_definitions) objective_polynomial = polynomials.of(rewritten.objective);
        for (auto const& definition : polynomials.definitions) encoder.clauses.push_back({encoder.intern('E', definition)});
        bool unsat = std::any_of(encoder.clauses.begin(), encoder.clauses.end(), [](std::vector<int> const& c) { return c.empty(); });
        std::ostringstream out;
        out << "NIA_LS_OMT 1\nSTATUS " << (unsat ? "unsat" : "ready") << "\nSOURCE " << hex(source_name)
            << "\nORIGINAL_OBJECTIVE " << hex(parsed.objective) << "\nDIRECTION " << parsed.direction
            << "\nVARIABLES " << variables.items.size() << '\n';
        for (std::size_t i = 0; i < variables.items.size(); ++i)
            out << i << ' ' << variables.items[i].first << ' ' << hex(variables.items[i].second) << '\n';
        out << "LITERALS " << encoder.literals.size() << '\n';
        for (std::size_t i = 0; i < encoder.literals.size(); ++i) {
            auto const& literal = encoder.literals[i];
            out << i + 1 << ' ' << literal.kind << ' ';
            if (literal.kind == 'B') out << literal.variable_index << '\n';
            else write_polynomial(out, literal.polynomial);
        }
        out << "CLAUSES " << encoder.clauses.size() << '\n';
        for (auto const& clause : encoder.clauses) {
            out << clause.size();
            for (int literal : clause) out << ' ' << literal;
            out << '\n';
        }
        out << "OBJECTIVE ";
        write_polynomial(out, objective_polynomial);
        out << "END\n";
        return out.str();
    } catch (z3::exception const& error) {
        throw ImportError("Z3 parsing or preprocessing failed: " + std::string(error.msg()));
    }
}

} }
