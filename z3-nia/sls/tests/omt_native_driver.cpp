#include "nia_ls.h"
#include "nia_omt_input.h"

#include <cstdlib>
#include <iostream>
#include <sstream>

namespace {
void require(bool condition, const std::string& message) {
    if (!condition) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}

const std::string prefix =
    "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\n"
    "ORIGINAL_OBJECTIVE 78\nDIRECTION min\n";

std::string simple(const std::string& objective) {
    return prefix + "VARIABLES 1\n0 I 78\nLITERALS 0\nCLAUSES 0\nOBJECTIVE "
        + objective + "\nEND\n";
}

void rejected(const std::string& input, const std::string& label) {
    nia::ls_solver solver(1, 0, true);
    std::istringstream stream(input);
    std::string error;
    require(!solver.load_omt_instance(stream, error), label + " was accepted");
    require(!error.empty() && !solver.omt_loaded, label + " did not report an error");
}
}

int main() {
    const std::string minimum = "-170141183460469231731687303715884105728";
    const std::string maximum = "170141183460469231731687303715884105727";
    const std::string large = "1267650600228229401496703205376"; // 2^100
    __int128_t low, high, big;
    require(nia::parse_omt_integer(minimum, low), "signed minimum parse");
    require(nia::parse_omt_integer(maximum, high), "signed maximum parse");
    require(nia::parse_omt_integer(large, big), "large coefficient parse");
    require(nia::format_omt_integer(low) == minimum, "signed minimum formatting");
    require(nia::format_omt_integer(high) == maximum, "signed maximum formatting");
    __int128_t ignored;
    require(!nia::parse_omt_integer("170141183460469231731687303715884105728", ignored), "positive overflow");
    require(!nia::parse_omt_integer("-170141183460469231731687303715884105729", ignored), "negative overflow");
    require(!nia::parse_omt_integer("1.0", ignored), "non-integer input");

    nia::ls_solver solver(1, 0, true);
    const std::string input = prefix
        + "VARIABLES 4\n0 I 78\n1 I 6f6e6c79\n2 B 62\n3 I 617578\n"
          "LITERALS 2\n1 E 0 2\n" + large + " 1 0 1\n-1 1 3 1\n"
          "2 B 2\nCLAUSES 2\n1 1\n1 2\n"
          "OBJECTIVE 7 2\n3 1 0 5\n" + large + " 1 1 1\nEND\n";
    std::istringstream stream(input);
    std::string error;
    require(solver.load_omt_instance(stream, error), "valid mixed input: " + error);
    require(solver.omt_loaded && solver.omt_direction == "min", "objective metadata");
    require(solver._num_vars == 4 && solver._num_lits == 2 && solver._num_clauses == 2, "input dimensions");
    require(solver.name2var.at("only") == 1 && solver._vars[1].var_lit_terms.empty(), "objective-only variable preserved");
    require(solver._lits[1].coff_terms[0].coff == big, "128-bit literal coefficient preserved");
    require(solver._vars[0].var_lit_terms[0].coff == big, "128-bit occurrence coefficient preserved");
    require(solver._terms[solver.omt_objective_terms[0].term_idx].var_epxs[0].exponent == 5, "degree-five objective preserved");
    require(solver._vars[0].upper_bound == nia::max_int && solver._vars[0].low_bound == -nia::max_int, "no hard-bound approximation");
    require(solver._score_clause_occurrences.size() == 1 && solver._vars[1].term_exps.size() == 1, "occurrence indexes");
    std::vector<__int128_t> assignment{2, 1, 1, 0};
    __int128_t value = 0;
    require(solver.evaluate_omt_objective(assignment, value, error), "objective evaluation: " + error);
    require(value == big + 103, "exact fifth-power and large-coefficient value");
    require(!solver.evaluate_omt_objective(std::vector<__int128_t>{2}, value, error), "missing assignment rejected");
    assignment[0] = __int128_t(1) << 30;
    require(!solver.evaluate_omt_objective(assignment, value, error), "evaluation overflow rejected");

    nia::ls_solver constant(1, 0, true);
    std::istringstream constant_stream(simple(minimum + " 0"));
    require(constant.load_omt_instance(constant_stream, error), "minimum objective constant");
    require(constant.evaluate_omt_objective(std::vector<__int128_t>{0}, value, error) && value == low, "minimum constant evaluation");

    nia::ls_solver edge(1, 0, true);
    std::istringstream edge_stream(simple("0 1\n-1 1 0 127"));
    require(edge.load_omt_instance(edge_stream, error), "negative power boundary input");
    require(edge.evaluate_omt_objective(std::vector<__int128_t>{2}, value, error) && value == low, "negative 2^127 evaluation");

    nia::ls_solver wide(1, 0, true);
    std::istringstream wide_stream(simple("0 1\n1 1 0 1"));
    require(wide.load_omt_instance(wide_stream, error), "wide-domain objective input");
    require(wide.evaluate_omt_objective(std::vector<__int128_t>{big}, value, error) && value == big,
        "objective evaluator ignores legacy domain sentinels");

    rejected(simple("0 1\n1 1 999 1"), "undefined objective variable");
    rejected(simple("0 1\n170141183460469231731687303715884105728 1 0 1"), "coefficient overflow");
    rejected(simple("0 1\n1 1 0 0"), "zero exponent");
    rejected(simple("0 0") + "extra\n", "trailing data");
    rejected(prefix + "VARIABLES 2\n0 I 78\n0 I 79\n", "duplicate variable id");
    rejected(prefix + "VARIABLES 2\n0 I 78\n1 I 78\n", "duplicate variable name");
    rejected(prefix + "VARIABLES 2147483615\n2147483614 I 78\n", "huge sparse variable id");
    rejected(prefix + "VARIABLES 1\n0 I 78\nLITERALS 2147483615\n2147483615 L 0 0\n", "huge sparse literal id");
    rejected(prefix + "VARIABLES 1\n0 I c080\n", "noncanonical UTF-8");
    rejected(prefix + "VARIABLES 1\n0 I 00\n", "embedded NUL name");
    std::string unsat = simple("0 0");
    unsat.replace(unsat.find("STATUS ready"), 12, "STATUS unsat");
    rejected(unsat, "unsat without empty clause");
    unsat.replace(unsat.find("CLAUSES 0"), 9, "CLAUSES 1\n0");
    nia::ls_solver contradiction(1, 0, true);
    std::istringstream unsat_stream(unsat);
    require(contradiction.load_omt_instance(unsat_stream, error) && contradiction.build_unsat, "explicit empty-clause contradiction");
    std::cout << "native OMT import checks passed\n";
}
