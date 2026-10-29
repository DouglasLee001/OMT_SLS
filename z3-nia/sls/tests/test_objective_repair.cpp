#include "nia_omt_repair.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <unistd.h>

namespace {
void require(bool condition, const std::string& message) {
    if (!condition) { std::cerr << "FAIL: " << message << '\n'; std::exit(1); }
}
struct Fixture {
    std::string path;
    nia::ls_solver solver;
    Fixture(const std::string& input, unsigned id) :
        path("NIA_LS/tests/.build/repair-" + std::to_string(getpid()) + "-" + std::to_string(id) + ".nia"), solver(1, UINT64_MAX, true) {
        std::ofstream file(path); file << input; file.close();
        std::istringstream stream(input); std::string error;
        require(solver.load_omt_instance(stream, error), "fixture import: " + error);
    }
    ~Fixture() { std::remove(path.c_str()); }
};
std::string input(const std::string& direction, bool bounds=true, const std::string& coeff="1") {
    return "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 782b79\nDIRECTION " + direction + "\n"
        + (bounds ? "VARIABLES 2\n0 I 78\n1 I 79\nLITERALS 4\n1 L 1 1\n-1 1 0 1\n2 L -10 1\n1 1 0 1\n3 L 1 1\n-1 1 1 1\n4 L -10 1\n1 1 1 1\nCLAUSES 4\n1 1\n1 2\n1 3\n1 4\nOBJECTIVE 0 2\n1 1 0 1\n1 1 1 1\nEND\n"
                  : "VARIABLES 1\n0 I 78\nLITERALS 0\nCLAUSES 0\nOBJECTIVE 0 1\n" + coeff + " 1 0 1\nEND\n");
}

std::string continuation_input() {
    // The old assignment x=0 satisfies every guarded clause. The strict
    // min bound x<=-1 disables their shared x>=0 witness and exposes an
    // impossible four-corner Boolean problem. It reaches the real search
    // loop, rather than becoming build_unsat during import.
    return "NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
           "VARIABLES 3\n0 I 78\n1 B 62\n2 B 63\n"
           "LITERALS 3\n1 L 0 1\n-1 1 0 1\n2 B 1\n3 B 2\n"
           "CLAUSES 4\n3 1 2 3\n3 1 -2 3\n3 1 2 -3\n3 1 -2 -3\n"
           "OBJECTIVE 0 1\n1 1 0 1\nEND\n";
}

void continuation_trajectory(unsigned id,uint64_t restart_limit) {
    Fixture fixture(continuation_input(),id);
    require(!fixture.solver.build_unsat,"guarded continuation fixture reaches real search");
    fixture.solver.restart_repair_enabled=false;
    fixture.solver.restart_stall_limit=restart_limit;
    const std::vector<__int128_t> seed={0,-1,-1};
    const auto original_rng=fixture.solver.mt;
    nia::OmtBoundRepair sliced(fixture.path,fixture.solver,seed,"0",47);
    nia::OmtBoundRepair whole(fixture.path,fixture.solver,seed,"0",47);
    require(sliced.supported()&&whole.supported(),"strict-bound continuation supports the guarded fixture");
    require(std::remove(fixture.path.c_str())==0,"continuation fixture file removed after one-time construction");
    std::vector<__int128_t> result;
    require(!sliced.initialized()&&sliced.steps_completed()==0&&sliced.initialization_round()==0,
            "construction does not initialize or consume search steps");
    const auto initial_rng=sliced.random_state();
    require(!sliced.resume_steps(0,60,60,result)&&!sliced.initialized()&&sliced.random_state()==initial_rng,
            "zero-step bound continuation preserves initialization and RNG");
    require(!sliced.resume_for(0,60,result)&&!sliced.resume_for(60,0,result)&&!sliced.initialized(),
            "expired slice/global budgets never initialize strict-bound search");
    require(!sliced.resume_for(std::numeric_limits<double>::quiet_NaN(),60,result)
            &&!sliced.resume_for(60,std::numeric_limits<double>::quiet_NaN(),result)
            &&!sliced.initialized(),"NaN slice/global budgets never initialize strict-bound search");
    require(!sliced.resume_steps(1,60,60,result)&&result.empty(),"first impossible strict-bound slice pauses");
    require(sliced.initialized()&&sliced.steps_completed()==1,
            "first strict-bound slice executes exactly one retained legacy step");
    require(!sliced.resume_steps(7,60,60,result)&&sliced.steps_completed()==8,
            "second strict-bound slice accumulates steps rather than rebuilding search");
    require(!sliced.resume_steps(31,60,60,result)&&sliced.steps_completed()==39,
            "third strict-bound slice continues the existing trajectory");
    require(!whole.resume_steps(39,60,60,result)&&whole.steps_completed()==39,
            "uninterrupted strict-bound reference executes the same step budget");
    require(sliced.random_state()==whole.random_state(),"sliced strict-bound RNG matches uninterrupted search");
    require(sliced.random_state()!=initial_rng,
            "impossible strict-bound fixture performs real random search beyond initialization");
    require(sliced.initialization_round()==whole.initialization_round(),
            "slice boundaries add no reinitializations beyond genuine legacy restarts");
    if(restart_limit>1000)require(sliced.initialization_round()==1,
                                "repeated slices initialize exactly once without a legacy restart");
    else require(sliced.initialization_round()>1,"low stall limit exercises retained restart state");
    require(fixture.solver._num_lits==3&&fixture.solver._num_clauses==4
            &&fixture.solver.mt==original_rng&&seed==std::vector<__int128_t>({0,-1,-1}),
            "strict-bound continuation preserves original solver and own incumbent seed");
}

void finite_threshold_checks() {
    const std::string magnitude="10000000000";
    Fixture minimum(input("min",false),200);
    Fixture maximum(input("max",false),201);
    minimum.solver.restart_repair_enabled=false;
    maximum.solver.restart_repair_enabled=false;
    const auto original_rng=minimum.solver.mt;
    nia::OmtBoundRepair lower(minimum.path,minimum.solver,{0},"0",37,"-"+magnitude);
    nia::OmtBoundRepair upper(maximum.path,maximum.solver,{0},"0",41,magnitude);
    require(lower.supported()&&upper.supported(),"finite min/max cuts accept signed ten-billion keys");
    std::vector<__int128_t> result={123};
    require(!lower.resume_steps(0,1,1,result)&&result.empty()&&!lower.initialized(),
            "zero-step finite probe stays private and uninitialized");
    require(!lower.resume_for(0,1,result)&&!lower.resume_for(1,0,result)&&!lower.initialized(),
            "expired finite probe budgets cannot initialize search");
    require(!lower.resume_for(std::numeric_limits<double>::quiet_NaN(),1,result)
            &&!lower.resume_for(1,std::numeric_limits<double>::quiet_NaN(),result)
            &&!lower.initialized(),"NaN finite probe budgets cannot initialize search");
    require(lower.resume_steps(1000,1,1,result)&&result.size()==1&&result[0]<=-__int128_t(10000000000LL),
            "finite minimum crosses the legacy one-hundred-million move cap");
    require(upper.resume_steps(1000,1,1,result)&&result.size()==1&&result[0]>=__int128_t(10000000000LL),
            "finite maximum crosses the legacy one-hundred-million move cap");
    const std::string largest="170141183460469231731687303715884105727";
    __int128_t largest_value;
    require(nia::parse_omt_integer(largest,largest_value),"signed128 maximum threshold parses");
    nia::OmtBoundRepair largest_min(minimum.path,minimum.solver,{0},"0",17,"-"+largest);
    nia::OmtBoundRepair largest_max(maximum.path,maximum.solver,{0},"0",19,largest);
    require(largest_min.supported()&&largest_min.resume_steps(1000,1,1,result)
            &&result==std::vector<__int128_t>({-largest_value}),
            "ordinary finite minimum reaches negative signed128 maximum magnitude");
    require(largest_max.supported()&&largest_max.resume_steps(1000,1,1,result)
            &&result==std::vector<__int128_t>({largest_value}),
            "ordinary finite maximum reaches signed128 maximum without a special backend");
    require(minimum.solver.mt==original_rng&&!minimum.solver.wide_omt_moves&&minimum.solver._num_lits==0
            &&minimum.solver._num_clauses==0,"finite probe preserves source topology and RNG");

    Fixture scaled(input("min",false,"4294967296"),204);
    scaled.solver.restart_repair_enabled=false;
    nia::OmtBoundRepair wide_coefficient(scaled.path,scaled.solver,{0},"0",59,"-"+magnitude);
    require(wide_coefficient.supported()&&wide_coefficient.resume_steps(1000,1,1,result)
            &&result.size()==1&&result[0]*__int128_t(4294967296LL)<=-__int128_t(10000000000LL),
            "finite probe preserves wide coefficients rather than narrowing them to int");

    Fixture inclusive("NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
                      "VARIABLES 1\n0 I 78\nLITERALS 1\n1 L -10000000000 1\n"
                      "-1 1 0 1\nCLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n",205);
    inclusive.solver.restart_repair_enabled=false;
    nia::OmtBoundRepair at_boundary(inclusive.path,inclusive.solver,{0},"0",61,"-"+magnitude);
    require(at_boundary.supported()&&at_boundary.resume_steps(1000,1,1,result)
            &&result==std::vector<__int128_t>({-__int128_t(10000000000LL)}),
            "finite objective threshold includes equality at an attainable endpoint");

    // Moving x to -10^10 requires its auxiliary y=x*x to reach 10^20.
    // Both the old INT64 term filter and old variable-domain sentinel must
    // be absent from this private finite-target continuation.
    Fixture auxiliary("NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\n"
                      "VARIABLES 2\n0 I 78\n1 I 79\nLITERALS 1\n1 E 0 2\n"
                      "1 1 0 2\n-1 1 1 1\nCLAUSES 1\n1 1\n"
                      "OBJECTIVE 0 1\n1 1 0 1\nEND\n",202);
    auxiliary.solver.restart_repair_enabled=false;
    nia::OmtBoundRepair nonlinear(auxiliary.path,auxiliary.solver,{0,0},"0",43,"-"+magnitude);
    __int128_t square;
    require(nia::parse_omt_integer("100000000000000000000",square),"square auxiliary reference");
    require(nonlinear.supported()&&nonlinear.resume_steps(10000,2,2,result)&&result.size()==2
            &&result[0]<=-__int128_t(10000000000LL)&&result[1]>=square
            &&result[1]==result[0]*result[0],"finite probe repairs the complete nonlinear auxiliary constraint");

    Fixture bounded(input("min"),203);
    nia::OmtBoundRepair impossible(bounded.path,bounded.solver,{1,1},"2",47,"-"+magnitude);
    result={456};
    require(impossible.supported()&&!impossible.resume_for(.02,.02,result)&&result.empty(),
            "unreachable finite target cannot publish a partial assignment");
    nia::OmtBoundRepair wrong_seed(minimum.path,minimum.solver,{0},"1",53,"-"+magnitude);
    nia::OmtBoundRepair infeasible_seed(bounded.path,bounded.solver,{0,0},"0",53,"-"+magnitude);
    nia::OmtBoundRepair equal_cut(minimum.path,minimum.solver,{0},"0",53,"0");
    nia::OmtBoundRepair weaker_min(minimum.path,minimum.solver,{0},"0",53,"1");
    nia::OmtBoundRepair weaker_max(maximum.path,maximum.solver,{0},"0",53,"-1");
    nia::OmtBoundRepair overflow(minimum.path,minimum.solver,{0},"0",53,"-340282366920938463463374607431768211456");
    nia::OmtBoundRepair malformed(minimum.path,minimum.solver,{0},"0",53,"1e10");
    nia::OmtBoundRepair empty(minimum.path,minimum.solver,{0},"0",53,"");
    Fixture negation(input("max",false,"-170141183460469231731687303715884105728"),206);
    nia::OmtBoundRepair coefficient_overflow(negation.path,negation.solver,{0},"0",53,magnitude);
    require(!wrong_seed.supported()&&!infeasible_seed.supported()&&!equal_cut.supported()
            &&!weaker_min.supported()&&!weaker_max.supported()&&!overflow.supported()&&!malformed.supported()
            &&!empty.supported()&&!coefficient_overflow.supported(),
            "finite probe rejects invalid seeds, non-improving cuts and unrepresentable keys");
}
}
int main() {
    finite_threshold_checks();
    continuation_trajectory(100,UINT64_MAX);
    continuation_trajectory(101,3);
    std::vector<__int128_t> result;
    Fixture minimum(input("min"),1);
    nia::OmtBoundRepair retained_minimum(minimum.path,minimum.solver,{10,10},"20",17);
    require(retained_minimum.supported(),"retained minimum constructed");
    require(retained_minimum.resume_steps(100,std::numeric_limits<double>::infinity(),60,result),
            "step-sliced minimum reaches a validated strict improvement");
    const auto terminal_steps=retained_minimum.steps_completed();
    const auto terminal_rng=retained_minimum.random_state();
    const auto terminal_model=result;
    require(retained_minimum.status()==nia::OmtBoundRepair::Status::sat
            &&retained_minimum.resume_for(1,60,result)&&result==terminal_model
            &&retained_minimum.steps_completed()==terminal_steps
            &&retained_minimum.random_state()==terminal_rng,
            "validated SAT is terminal and preserves the retained search trajectory");
    nia::OmtBoundRepair invalid_minimum(minimum.path,minimum.solver,{10,10},"19",17);
    require(!invalid_minimum.supported()&&invalid_minimum.status()==nia::OmtBoundRepair::Status::unsupported,
            "unsupported construction does not throw or masquerade as an active search");
    require(!invalid_minimum.resume_for(1,60,result)&&result.empty()
            &&!invalid_minimum.resume_steps(100,60,60,result)
            &&invalid_minimum.steps_completed()==0&&!invalid_minimum.initialized(),
            "unsupported repair remains a zero-step fast failure on repeated resumes");
    require(nia::repair_omt_bound(minimum.path,minimum.solver,{10,10},"20",1.0,17,result),"min bound repaired");
    require(result.size()==2 && result[0]>=1 && result[0]<=10 && result[1]>=1 && result[1]<=10 && result[0]+result[1]<20,"min candidate meets hard constraints and strict bound");
    require(minimum.solver._num_lits==4 && minimum.solver._num_clauses==4,"original solver not modified");
    Fixture maximum(input("max"),2);
    require(nia::repair_omt_bound(maximum.path,maximum.solver,{1,1},"2",1.0,19,result),"max bound repaired");
    require(result.size()==2 && result[0]>=1 && result[0]<=10 && result[1]>=1 && result[1]<=10 && result[0]+result[1]>2,"max candidate meets hard constraints and strict bound");
    require(!nia::repair_omt_bound(minimum.path,minimum.solver,{1,1},"2",.02,23,result) && result.empty(),"impossible minimum does not return false success");
    require(!nia::repair_omt_bound(maximum.path,maximum.solver,{10,10},"20",.02,23,result) && result.empty(),"impossible maximum does not return false success");
    Fixture objective_only(input("min",false),3);
    require(objective_only.solver._vars[0].var_lit_terms.empty(),"objective-only fixture starts with no occurrences");
    require(nia::repair_omt_bound(objective_only.path,objective_only.solver,{10},"10",1.0,29,result) && result.size()==1 && result[0]<10,"objective-only variable linked to bound");
    require(!nia::repair_omt_bound(objective_only.path,objective_only.solver,{10},"9",1.0,29,result),"incorrect incumbent rejected");
    __int128_t low,high;
    require(nia::parse_omt_integer("-170141183460469231731687303715884105728",low),"minimum128 fixture");
    require(nia::parse_omt_integer("170141183460469231731687303715884105727",high),"maximum128 fixture");
    require(!nia::repair_omt_bound(objective_only.path,objective_only.solver,{low},nia::format_omt_integer(low),1.0,31,result),"bound key overflow rejected");
    Fixture coefficient(input("max",false,"-170141183460469231731687303715884105728"),4);
    require(!nia::repair_omt_bound(coefficient.path,coefficient.solver,{0},"0",1.0,31,result),"negated coefficient overflow rejected");
    Fixture narrow(input("min",false,"4294967296"),5);
    require(!nia::repair_omt_bound(narrow.path,narrow.solver,{0},"0",1.0,31,result),"legacy int coefficient narrowing rejected");
    require(!nia::repair_omt_bound(objective_only.path,objective_only.solver,{__int128_t(1)<<40},"1099511627776",1.0,31,result),"legacy int key narrowing rejected");
    require(!nia::repair_omt_bound(minimum.path,minimum.solver,{0,0},"0",1.0,31,result),"infeasible seed rejected");
    require(!nia::repair_omt_bound(minimum.path,minimum.solver,{10,10},"20",0,31,result),"zero budget rejected");
    std::cout << "native objective bound repair checks passed\n";
}
