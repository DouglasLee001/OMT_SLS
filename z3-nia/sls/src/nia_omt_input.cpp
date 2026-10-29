#include "nia_omt_input.h"
#include "nia_ls.h"

#include <climits>
#include <cstdint>
#include <limits>
#include <set>
#include <stdexcept>
#include <utility>

namespace nia {

bool parse_omt_integer(const std::string& text, __int128_t& value) {
    if (text.empty()) return false;
    size_t pos = 0;
    bool negative = false;
    if (text[pos] == '-' || text[pos] == '+') negative = text[pos++] == '-';
    if (pos == text.size()) return false;
    const __uint128_t sign_bit = __uint128_t(1) << 127;
    const __uint128_t limit = negative ? sign_bit : sign_bit - 1;
    __uint128_t magnitude = 0;
    for (; pos < text.size(); ++pos) {
        const unsigned char ch = text[pos];
        if (ch < '0' || ch > '9') return false;
        const unsigned digit = ch - '0';
        if (magnitude > (limit - digit) / 10) return false;
        magnitude = magnitude * 10 + digit;
    }
    if (!negative) value = static_cast<__int128_t>(magnitude);
    else if (magnitude == 0) value = 0;
    else value = -static_cast<__int128_t>(magnitude - 1) - 1;
    return true;
}

std::string format_omt_integer(__int128_t value) {
    if (value == 0) return "0";
    const bool negative = value < 0;
    __uint128_t magnitude = negative ? -__uint128_t(value) : __uint128_t(value);
    std::string result;
    while (magnitude != 0) {
        result.push_back(char('0' + magnitude % 10));
        magnitude /= 10;
    }
    if (negative) result.push_back('-');
    std::reverse(result.begin(), result.end());
    return result;
}

namespace {

struct InputTerm {
    __int128_t coefficient;
    std::vector<std::pair<int, int> > factors;
};

struct InputPolynomial {
    __int128_t constant = 0;
    std::vector<InputTerm> terms;
};

struct InputLiteral {
    char kind = 0;
    int boolean_variable = -1;
    InputPolynomial polynomial;
};

struct InputVariable {
    bool integer = false;
    std::string name;
};

struct OmtReader {
    std::istream& input;
    std::vector<InputVariable> variables;
    std::vector<InputLiteral> literals;
    std::vector<std::vector<int> > clauses;
    InputPolynomial objective;
    std::string source, original_objective, direction;
    bool unsat = false;

    explicit OmtReader(std::istream& stream) : input(stream) {}

    std::string token(const char* what) {
        std::string result;
        if (!(input >> result)) throw std::runtime_error(std::string("missing ") + what);
        return result;
    }

    void expect(const char* expected) {
        const auto actual = token(expected);
        if (actual != expected)
            throw std::runtime_error(std::string("expected ") + expected + ", got " + actual);
    }

    __int128_t integer(const char* what) {
        __int128_t result;
        const auto text = token(what);
        if (!parse_omt_integer(text, result))
            throw std::runtime_error(std::string("invalid signed 128-bit integer for ") + what);
        return result;
    }

    int count(const char* what, int maximum = INT_MAX - 32) {
        const auto result = integer(what);
        if (result < 0 || result > maximum)
            throw std::runtime_error(std::string("out-of-range ") + what);
        return static_cast<int>(result);
    }

    static bool utf8(const std::string& value) {
        for (size_t i = 0; i < value.size();) {
            const unsigned char first = value[i++];
            if (first == 0) return false;
            if (first < 0x80) continue;
            unsigned code, remaining, minimum;
            if (first >= 0xc2 && first <= 0xdf) { code = first & 0x1f; remaining = 1; minimum = 0x80; }
            else if (first >= 0xe0 && first <= 0xef) { code = first & 0x0f; remaining = 2; minimum = 0x800; }
            else if (first >= 0xf0 && first <= 0xf4) { code = first & 0x07; remaining = 3; minimum = 0x10000; }
            else return false;
            if (remaining > value.size() - i) return false;
            for (unsigned j = 0; j < remaining; ++j) {
                const unsigned char continuation = value[i++];
                if ((continuation & 0xc0) != 0x80) return false;
                code = (code << 6) | (continuation & 0x3f);
            }
            if (code < minimum || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return false;
        }
        return true;
    }

    std::string text(const char* what, bool allow_empty) {
        const auto encoded = token(what);
        if (encoded == "-") {
            if (allow_empty) return "";
            throw std::runtime_error(std::string("empty ") + what);
        }
        if (encoded.size() % 2 != 0)
            throw std::runtime_error(std::string("invalid hexadecimal ") + what);
        auto digit = [](char ch) -> int {
            if (ch >= '0' && ch <= '9') return ch - '0';
            if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
            if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
            return -1;
        };
        std::string decoded;
        decoded.reserve(encoded.size() / 2);
        for (size_t i = 0; i < encoded.size(); i += 2) {
            const int high = digit(encoded[i]), low = digit(encoded[i + 1]);
            if (high < 0 || low < 0) throw std::runtime_error(std::string("invalid hexadecimal ") + what);
            decoded.push_back(char(high * 16 + low));
        }
        if (decoded.empty() || !utf8(decoded))
            throw std::runtime_error(std::string("invalid UTF-8 ") + what);
        return decoded;
    }

    int variable_id(const char* what, bool integer_sort) {
        const int result = count(what);
        if (static_cast<size_t>(result) >= variables.size())
            throw std::runtime_error(std::string("unknown variable in ") + what);
        if (variables[result].integer != integer_sort)
            throw std::runtime_error(std::string("wrong variable sort in ") + what);
        return result;
    }

    InputPolynomial polynomial() {
        InputPolynomial result;
        result.constant = integer("polynomial constant");
        const int term_count = count("term count");
        for (int i = 0; i < term_count; ++i) {
            InputTerm term;
            term.coefficient = integer("term coefficient");
            const int factors = count("factor count");
            if (factors == 0) throw std::runtime_error("a nonconstant term must have a factor");
            std::map<int, int> powers;
            for (int j = 0; j < factors; ++j) {
                const int variable = variable_id("polynomial factor", true);
                const int exponent = count("factor exponent", INT_MAX);
                if (exponent == 0) throw std::runtime_error("factor exponent must be positive");
                if (powers[variable] > INT_MAX - exponent)
                    throw std::runtime_error("combined factor exponent is out of range");
                powers[variable] += exponent;
            }
            term.factors.assign(powers.begin(), powers.end());
            if (term.coefficient != 0) result.terms.push_back(std::move(term));
        }
        return result;
    }

    void read() {
        expect("NIA_LS_OMT");
        if (token("format version") != "1") throw std::runtime_error("unsupported OMT input version");
        expect("STATUS");
        const auto status = token("status");
        if (status != "ready" && status != "unsat") throw std::runtime_error("invalid OMT status");
        unsat = status == "unsat";
        expect("SOURCE"); source = text("source", true);
        expect("ORIGINAL_OBJECTIVE"); original_objective = text("original objective", false);
        expect("DIRECTION"); direction = token("objective direction");
        if (direction != "min" && direction != "max") throw std::runtime_error("invalid objective direction");

        expect("VARIABLES");
        const int variable_count = count("variable count");
        std::set<std::string> names;
        for (int i = 0; i < variable_count; ++i) {
            const int id = count("variable id");
            if (id != i)
                throw std::runtime_error("variable ids must be consecutive from zero, without duplicates");
            const auto sort = token("variable sort");
            if (sort != "I" && sort != "B") throw std::runtime_error("unsupported variable sort");
            auto name = text("variable name", false);
            if (!names.insert(name).second) throw std::runtime_error("duplicate variable name");
            // Grow only for actual declarations, never a sparse, untrusted id.
            variables.push_back(InputVariable{sort == "I", std::move(name)});
        }

        expect("LITERALS");
        const int literal_count = count("literal count");
        literals.resize(1); // zero is never a signed clause literal.
        for (int i = 0; i < literal_count; ++i) {
            const int id = count("literal id");
            if (id != i + 1)
                throw std::runtime_error("literal ids must be consecutive from one, without duplicates");
            const auto kind = token("literal kind");
            InputLiteral literal;
            if (kind == "B") {
                literal.kind = 'B';
                literal.boolean_variable = variable_id("Boolean literal", false);
            } else if (kind == "L" || kind == "E") {
                literal.kind = kind[0];
                literal.polynomial = polynomial();
            } else throw std::runtime_error("unsupported literal kind");
            literals.push_back(std::move(literal));
        }

        expect("CLAUSES");
        const int clause_count = count("clause count");
        bool empty_clause = false;
        for (int i = 0; i < clause_count; ++i) {
            const int length = count("clause length");
            std::vector<int> clause;
            for (int j = 0; j < length; ++j) {
                const auto index = integer("signed literal id");
                if (index == 0 || index < -__int128_t(literal_count) || index > literal_count)
                    throw std::runtime_error("unknown signed literal id");
                clause.push_back(static_cast<int>(index));
            }
            if (clause.empty()) empty_clause = true;
            clauses.push_back(std::move(clause));
        }
        if (unsat && !empty_clause) throw std::runtime_error("unsat status requires an empty clause");
        expect("OBJECTIVE"); objective = polynomial();
        expect("END");
        std::string extra;
        if (input >> extra) throw std::runtime_error("trailing data after END");
        if (input.bad()) throw std::runtime_error("I/O failure reading OMT input");
    }
};

}

bool ls_solver::load_omt_instance(std::istream& input, std::string& error) {
    error.clear();
    if (omt_loaded || !_vars.empty() || !_lits.empty() || !_clauses.empty() || !_terms.empty() ||
        unsat_clauses || var_in_long_term) {
        error = "OMT input requires a fresh solver instance";
        return false;
    }
    try {
        OmtReader reader(input);
        reader.read();

        // No satisfiability-only elimination is run here.  In particular, hard
        // bounds remain exact literals and objective-only variables survive.
        _num_vars = reader.variables.size();
        _num_lits = reader.literals.size() - 1;
        _num_clauses = reader.clauses.size();
        _num_nia_vars = 0;
        _num_nia_lits = 0;
        _num_bool_lits = 0;
        _num_opt = 0;
        _vars.reserve(_num_vars);
        for (size_t i = 0; i < reader.variables.size(); ++i) {
            variable v{};
            v.var_name = reader.variables[i].name;
            v.is_nia = reader.variables[i].integer;
            name2var.emplace(v.var_name, i);
            if (v.is_nia) { nia_var_vec.push_back(i); ++_num_nia_vars; }
            else bool_var_vec.push_back(i);
            _vars.push_back(std::move(v));
        }

        auto coefficients = [&](const InputPolynomial& polynomial) {
            std::vector<coff_term> result;
            result.reserve(polynomial.terms.size());
            for (const auto& raw : polynomial.terms) {
                term monomial{};
                for (const auto& power : raw.factors) {
                    var_exp factor(power.first);
                    factor.exponent = power.second;
                    monomial.var_epxs.push_back(factor);
                }
                if (_terms.size() >= static_cast<size_t>(INT_MAX - 32))
                    throw std::runtime_error("too many distinct terms");
                result.emplace_back(static_cast<int>(transfer_term_to_idx(monomial)), raw.coefficient);
            }
            return result;
        };

        _lits.resize(_num_lits + _additional_len + 1);
        lit_appear.assign(_lits.size(), false);
        for (size_t i = 1; i < reader.literals.size(); ++i) {
            const auto& raw = reader.literals[i];
            lit literal{};
            literal.lits_index = i;
            if (raw.kind == 'B') {
                literal.key = 1;
                literal.delta = raw.boolean_variable;
                _vars[raw.boolean_variable].literal_idxs.push_back(i);
                ++_num_bool_lits;
            } else {
                literal.is_nia_lit = true;
                literal.is_equal = raw.kind == 'E';
                literal.key = raw.polynomial.constant;
                literal.coff_terms = coefficients(raw.polynomial);
                for (const auto& coefficient : literal.coff_terms) {
                    const auto& monomial = _terms[coefficient.term_idx];
                    _num_opt += monomial.var_epxs.size();
                    if (_num_opt > static_cast<uint64_t>((INT_MAX - 32) / 3))
                        throw std::runtime_error("too many arithmetic move entries");
                    for (const auto& power : monomial.var_epxs) {
                        var_exp factor = power;
                        var_lit_term occurrence(factor, coefficient.term_idx, i, coefficient.coff);
                        literal.var_lit_terms.push_back(occurrence);
                        _vars[power.var_index].var_lit_terms.push_back(occurrence);
                        _vars[power.var_index].literal_idxs.push_back(i);
                    }
                }
                std::sort(literal.var_lit_terms.begin(), literal.var_lit_terms.end(),
                    [](const var_lit_term& a, const var_lit_term& b) {
                        return a.var_idx < b.var_idx || (a.var_idx == b.var_idx && a.term_idx < b.term_idx);
                    });
                ++_num_nia_lits;
            }
            _lits[i] = std::move(literal);
            lit_appear[i] = true;
        }

        omt_objective_constant = reader.objective.constant;
        omt_objective_terms = coefficients(reader.objective);
        for (const auto& coefficient : omt_objective_terms) {
            _num_opt += _terms[coefficient.term_idx].var_epxs.size();
            if (_num_opt > static_cast<uint64_t>((INT_MAX - 32) / 3))
                throw std::runtime_error("too many objective move entries");
        }
        term_appear.assign(_terms.size() + _additional_len, false);
        var_in_long_term = new Array(static_cast<int>(_num_vars + _additional_len));
        for (size_t i = 0; i < _terms.size(); ++i) {
            term_appear[i] = true;
            for (const auto& power : _terms[i].var_epxs) {
                _vars[power.var_index].term_exps.emplace_back(i, power.exponent);
                if (power.exponent >= 2 || _terms[i].var_epxs.size() > 2)
                    var_in_long_term->insert_element(power.var_index);
            }
        }

        _clauses.reserve(_num_clauses);
        build_unsat = reader.unsat;
        for (size_t i = 0; i < reader.clauses.size(); ++i) {
            clause c{};
            c.literals = reader.clauses[i];
            if (c.literals.empty()) build_unsat = true;
            for (int signed_id : c.literals) {
                const auto& literal = _lits[std::abs(signed_id)];
                if (literal.is_nia_lit) {
                    c.nia_literals.push_back(signed_id);
                    for (const auto& coefficient : literal.coff_terms)
                        for (const auto& power : _terms[coefficient.term_idx].var_epxs)
                            _vars[power.var_index].clause_idxs.push_back(i);
                } else {
                    c.bool_literals.push_back(signed_id);
                    _vars[literal.delta].clause_idxs.push_back(i);
                    _vars[literal.delta].bool_var_in_pos_clause.push_back(signed_id > 0);
                }
            }
            _clauses.push_back(std::move(c));
        }
        for (auto& v : _vars) {
            std::sort(v.literal_idxs.begin(), v.literal_idxs.end());
            v.literal_idxs.erase(std::unique(v.literal_idxs.begin(), v.literal_idxs.end()), v.literal_idxs.end());
            // Boolean occurrence polarity remains aligned with clause_idxs.
            if (v.is_nia) {
                std::sort(v.clause_idxs.begin(), v.clause_idxs.end());
                v.clause_idxs.erase(std::unique(v.clause_idxs.begin(), v.clause_idxs.end()), v.clause_idxs.end());
            }
        }

        // Identity reconstruction maps keep inspection/model consumers safe;
        // the importer has not introduced an eliminated-variable convention.
        _tmp_vars = _vars;
        _resolution_vars = _vars;
        for (size_t i = 0; i < _vars.size(); ++i) {
            name2resolution_var.emplace(_vars[i].var_name, i);
            if (_vars[i].is_nia) name2tmp_var.emplace(_vars[i].var_name, i);
            fa.push_back(i);
            fa_coff.push_back(1);
        }
        use_pbs = false;
        is_idl = false;
        has_unidentified_lits = false;
        best_found_cost = static_cast<int>(_num_clauses);
        make_space();
        // Legacy set_pre_value narrows exact bounds to int; no such heuristic
        // conversion belongs in a lossless OMT import.
        _pre_value_1.assign(_num_vars + _additional_len, INT32_MAX);
        _pre_value_2.assign(_num_vars + _additional_len, INT32_MAX);
        omt_source = std::move(reader.source);
        omt_original_objective = std::move(reader.original_objective);
        omt_direction = std::move(reader.direction);
        omt_loaded = true;
        return true;
    } catch (const std::exception& exception) {
        error = exception.what();
        return false;
    }
}

bool ls_solver::evaluate_omt_objective(const std::vector<__int128_t>& assignment,
                                     __int128_t& value, std::string& error) const {
    error.clear();
    if (!omt_loaded) { error = "no OMT objective has been loaded"; return false; }
    if (assignment.size() < _num_vars) { error = "objective assignment is missing variables"; return false; }
    // The legacy variable-bound sentinels are search heuristics, not imported
    // logical restrictions.  Evaluate the supplied assignment independently.
    auto magnitude = [](__int128_t number) -> __uint128_t {
        return number < 0 ? -__uint128_t(number) : __uint128_t(number);
    };
    __int128_t result = omt_objective_constant;
    for (const auto& coefficient : omt_objective_terms) {
        const auto& monomial = _terms[coefficient.term_idx];
        bool negative = coefficient.coff < 0, zero = coefficient.coff == 0;
        for (const auto& factor : monomial.var_epxs) {
            if (assignment[factor.var_index] == 0) zero = true;
            if (assignment[factor.var_index] < 0 && factor.exponent % 2 != 0) negative = !negative;
        }
        if (zero) continue;
        const __uint128_t sign_bit = __uint128_t(1) << 127;
        const __uint128_t limit = negative ? sign_bit : sign_bit - 1;
        __uint128_t product = magnitude(coefficient.coff);
        if (product > limit) { error = "objective term exceeds signed 128-bit range"; return false; }
        for (const auto& factor : monomial.var_epxs) {
            __uint128_t base = magnitude(assignment[factor.var_index]);
            unsigned exponent = factor.exponent;
            while (exponent != 0) {
                if (exponent & 1) {
                    if (base != 0 && product > limit / base) {
                        error = "objective multiplication exceeds signed 128-bit range";
                        return false;
                    }
                    product *= base;
                }
                exponent >>= 1;
                if (exponent != 0) {
                    if (base != 0 && base > limit / base) {
                        error = "objective power exceeds signed 128-bit range";
                        return false;
                    }
                    base *= base;
                }
            }
        }
        const __int128_t term_value = !negative ? static_cast<__int128_t>(product)
            : -static_cast<__int128_t>(product - 1) - 1;
        if (__builtin_add_overflow(result, term_value, &result)) {
            error = "objective sum exceeds signed 128-bit range";
            return false;
        }
    }
    value = result;
    return true;
}

}
