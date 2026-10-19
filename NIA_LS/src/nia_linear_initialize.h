#pragma once
#include <map>
#include <string>
namespace nia {
struct initial_model {
    std::map<std::string,std::string> values;
    double seconds=0.0;
    unsigned bounded_variables=0;
};
// Returns only a complete assignment checked against the original converted CNF.
// Failure/timeout of a bounded subproblem is not a proof of unsatisfiability.
bool make_linear_initial_model(const std::string& path,double budget_seconds,initial_model& result);
}

namespace nia {
// The last/best SAT prefix survives later UNSAT, UNKNOWN or budget exhaustion.
// A true return still requires every original CNF clause to hold.
bool make_progressive_initial_model(const std::string& path,double budget_seconds,initial_model& best);
}
