// Exact arithmetic regressions from native bound-repair cache divergences.
// These are fixed critical moves, never benchmark searches or repair solvers.
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_feasibility.h"
#include "../src/nia_omt_legacy_arithmetic.h"
#include <gmpxx.h>
#include <cstdlib>
#include <iostream>
#include <sstream>

namespace {
using Int = mpz_class;
void check(bool condition,const std::string& message) {
    if(!condition){std::cerr<<"FAIL: "<<message<<'\n';std::exit(1);}
}
Int integer(__int128_t value){return Int(nia::format_omt_integer(value),10);}
Int monomial(const nia::term& term,const std::vector<__int128_t>& values){
    Int result=1;
    for(const auto& factor:term.var_epxs){
        Int power,base=integer(values[factor.var_index]);
        mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),factor.exponent);result*=power;
    }
    return result;
}
void exact_caches(const nia::ls_solver& solver,const std::string& context){
    for(size_t t=0;t<solver._terms.size();++t)
        check(integer(solver._terms[t].value)==monomial(solver._terms[t],solver._solution),
              context+": term cache "+std::to_string(t)+" matches GMP");
    for(size_t l=1;l<=solver._num_lits;++l){
        const auto& literal=solver._lits[l];if(!literal.is_nia_lit)continue;
        Int delta=integer(literal.key);
        for(const auto& ct:literal.coff_terms)delta+=integer(ct.coff)*monomial(solver._terms[ct.term_idx],solver._solution);
        check(integer(literal.delta)==delta,context+": literal delta matches GMP");
        check((literal.is_true>0)==(literal.is_equal?delta==0:delta<=0),context+": literal truth matches GMP");
    }
}
std::string input(unsigned variables,const std::string& literal){
    std::string text="NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 78\nDIRECTION min\nVARIABLES "+std::to_string(variables)+"\n0 I 78\n";
    if(variables==2)text+="1 I 79\n";
    return text+"LITERALS 1\n1 "+literal+"\nCLAUSES 1\n1 1\nOBJECTIVE 0 1\n1 1 0 1\nEND\n";
}
void load(nia::ls_solver& solver,const std::string& text,const std::vector<__int128_t>& seed){
    std::istringstream stream(text);std::string error;
    check(solver.load_omt_instance(stream,error),"arithmetic fixture imports: "+error);
    solver.restart_repair_enabled=false;
    solver.restart_seed=seed;solver.initialization_round=1;
    solver.last_op_var=UINT64_MAX;solver.last_op_value=0;
    solver._step=1;solver._outer_layer_step=1;
    solver.start=std::chrono::steady_clock::now();
    solver.initialize();
}
void equality_scoring_and_update(){
    nia::omt_legacy_arithmetic::Scope scope;
    const long long point=(1LL<<27)+1;
    const Int wide_point=integer(point);
    const Int offset=-wide_point*wide_point;
    const std::string constant=offset.get_str();
    nia::ls_solver solver(1,100,true);
    load(solver,input(1,"E "+constant+" 1\n1 1 0 2"),{point-1});
    exact_caches(solver,"large-offset initialization");
    check(solver.critical_score(0,1)==1,"exact candidate score recognizes the large-offset quadratic equality");
    solver.critical_move(0,1);
    exact_caches(solver,"large-offset equality move");
    check(solver.unsat_clauses->empty(),"exact quadratic equality move satisfies its clause");
}
void traced_464_move(){
    nia::omt_legacy_arithmetic::Scope scope;
    nia::ls_solver solver(1,100,true);
    load(solver,input(1,"L 996371614137490 2\n-130979348 1 0 1\n39 1 0 2"),{-9482842});
    check(integer(solver._lits[1].delta)==Int("5745475479966102"),"464 traced initial delta");
    solver.critical_move(0,-7462455);
    check(integer(solver._lits[1].delta)==Int("14414436093173997"),"464 exact integer sum retains the low bit above 2^53");
    exact_caches(solver,"464 traced move");
    solver.critical_move(0,7462455);
    exact_caches(solver,"464 reverse move has no accumulated rounding drift");
}
void cofactor_and_product(){
    nia::omt_legacy_arithmetic::Scope scope;
    const __int128_t cofactor=(__int128_t(1)<<53)+1;
    nia::ls_solver solver(1,100,true);
    load(solver,input(2,"L 0 1\n1 2 0 2 1 1"),{2,cofactor});
    exact_caches(solver,"cofactor initialization");
    check(solver.coff_in_term(0,0,2)==cofactor,"exact cofactor division preserves integers above 2^53");
    solver.critical_move(0,1);
    exact_caches(solver,"squared term times large integer cofactor");
}
void traced_255_product(){
    nia::omt_legacy_arithmetic::Scope scope;
    nia::ls_solver solver(1,100,true);
    load(solver,input(2,"L 0 1\n9 2 0 2 1 1"),{0,3374});
    solver.critical_move(0,-16037267);
    check(integer(solver._terms[0].value)==Int("867772329366021086"),
          "255 traced squared term times cofactor retains every integer bit");
    exact_caches(solver,"255 traced product move");
}
bool generates(const std::string& literal,__int128_t destination,__int128_t initial=0,int signed_literal=1){
    nia::ls_solver solver(1,100,true);
    load(solver,input(1,literal),{initial});
    std::vector<nia::ls_solver::raw_critical_operation> operations;
    solver.capturing_critical_operations=&operations;
    int count=0;solver.generate_operations_from_false_lit(false,signed_literal,count);
    solver.capturing_critical_operations=nullptr;
    for(const auto& operation:operations)if(operation.first==0&&operation.second==destination)return true;
    return false;
}
void small_concave_candidates(){
    nia::omt_legacy_arithmetic::Scope scope;
    check(generates("L 2 1\n-1 1 0 2",2),
          "concave small quadratic generates a satisfying integer exterior point");
    check(generates("L 4 1\n-1 1 0 2",-1,2,-1),
          "negative concave literal generates a strictly satisfying interior point");
    check(generates("E 0 2\n-1 1 0 1\n1 1 0 2",2,0,-1),
          "negated small equality escapes two neighboring integer roots");
}
void wide_linear_coefficient(){
    nia::omt_legacy_arithmetic::Scope scope;
    check(generates("E -7696581394432 1\n1099511627776 1 0 1",7),
          "wide linear coefficient generates its exact integer root without int narrowing");
}
void wide_quadratic_discriminant(){
    nia::omt_legacy_arithmetic::Scope scope;
    check(generates("E -100001 2\n100000 1 0 1\n1 1 0 2",1),
          "wide quadratic discriminant generates its exact integer root without int overflow");
}
void wide_candidate_truths(){
    nia::omt_legacy_arithmetic::Scope scope;
    for(const __int128_t scale:{__int128_t(1),__int128_t(1)<<40})
    for(int a:{-3,-1,1,3})for(int b:{-5,0,5})for(int c:{-7,0,7})
        for(bool equality:{false,true}) {
            nia::ls_solver solver(1,100,true);
            const std::string literal=std::string(equality?"E ":"L ")+nia::format_omt_integer(c*scale)
                +" 2\n"+nia::format_omt_integer(b*scale)+" 1 0 1\n"
                +nia::format_omt_integer(a*scale)+" 1 0 2";
            load(solver,input(1,literal),{0});
            const bool old_truth=equality?c==0:c<=0;
            const int signed_literal=old_truth?-1:1;
            std::vector<nia::ls_solver::raw_critical_operation> operations;
            solver.capturing_critical_operations=&operations;
            int count=0;solver.generate_operations_from_false_lit(false,signed_literal,count);
            solver.capturing_critical_operations=nullptr;
            bool exists=false;
            for(int x=-30;x<=30;++x){const int delta=a*x*x+b*x+c;exists|=(equality?delta==0:delta<=0)!=old_truth;}
            check(!operations.empty()==exists,"wide signed quadratic candidates cover exhaustive small-domain feasibility");
            for(const auto& operation:operations){
                const Int x=integer(operation.second),delta=a*x*x+b*x+c;
                check((equality?delta==0:delta<=0)!=old_truth,"every wide candidate satisfies the requested signed literal exactly");
            }
        }
}
void legacy_scope_is_unchanged(){
    check(!nia::omt_legacy_arithmetic::enabled,"exact scope restores the default policy");
    nia::ls_solver solver(1,100,true);
    load(solver,input(1,"L 996371614137490 2\n-130979348 1 0 1\n39 1 0 2"),{-9482842});
    solver.critical_move(0,-7462455);
    check(integer(solver._lits[1].delta)==Int("14414436093173996"),"default legacy arithmetic retains its original expression and result");
    {nia::omt_legacy_arithmetic::Scope outer;
        check(nia::omt_legacy_arithmetic::enabled,"scope enables exact arithmetic");
        {nia::omt_legacy_arithmetic::Scope inner(false);check(!nia::omt_legacy_arithmetic::enabled,"nested scope can restore legacy arithmetic");}
        check(nia::omt_legacy_arithmetic::enabled,"nested scope restores the outer exact policy");}
    check(!nia::omt_legacy_arithmetic::enabled,"outer scope restores the default legacy policy");
}
void overflow_stops_only_continuation(){
    nia::ls_solver solver(1,100,true);std::istringstream stream(input(1,"L 0 1\n1 1 0 128"));std::string error;
    check(solver.load_omt_instance(stream,error),"overflow fixture imports");
    solver.restart_seed={2};solver.initialization_round=1;
    const auto rng=solver.mt;
    nia::OmtFeasibilityContinuation continuation(solver);
    check(continuation.resume_steps(1,60,60)==nia::OmtFeasibilityContinuation::Status::exhausted,
          "checked overflow exhausts the optional legacy continuation instead of reporting SAT");
    check(solver.mt==rng&&!nia::omt_legacy_arithmetic::enabled,"overflow restores caller RNG and arithmetic policy");
}
void continuation_initialization_policy(){
    using Continuation=nia::OmtFeasibilityContinuation;
    const __int128_t point=(__int128_t(1)<<27)+1;
    const Int exact_square("18014398777917441"),rounded_square("18014398777917440");
    for(bool outer_exact:{false,true})for(bool requested_exact:{false,true}){
        nia::omt_legacy_arithmetic::Scope outer(outer_exact);
        nia::ls_solver solver(73,1,true);std::istringstream stream(input(1,"L 0 1\n1 1 0 2"));std::string error;
        check(solver.load_omt_instance(stream,error),"initialization-policy fixture imports");
        solver.restart_repair_enabled=false;solver.restart_seed={point};solver.initialization_round=1;
        solver.start=std::chrono::steady_clock::now()-std::chrono::seconds(3);solver._cutoff=17;
        const auto rng=solver.mt;const auto started=solver.start;const double cutoff=solver._cutoff;
        Continuation continuation(solver,requested_exact);
        check(continuation.resume_steps(1,60,60)==Continuation::Status::exhausted&&continuation.steps_completed()==0,
              "max_step=1 continuation performs initialization without any local-search move");
        check(solver._solution[0]==point,"initialization preserves the chosen exact integer seed");
        check(integer(solver._terms[0].value)==(requested_exact?exact_square:rounded_square),
              requested_exact?"exact continuation initializes the quadratic cache without rounding":"legacy continuation preserves the old rounded quadratic cache");
        if(requested_exact)exact_caches(solver,"explicit exact initialization policy");
        check(solver.mt==rng&&solver.start==started&&solver._cutoff==cutoff,
              "each arithmetic policy restores the caller RNG and timing scope");
        check(nia::omt_legacy_arithmetic::enabled==outer_exact,"each arithmetic policy restores its enclosing exact/legacy scope");
        const auto terminal_rng=continuation.random_state();
        check(continuation.resume_steps(1,60,60)==Continuation::Status::exhausted&&continuation.random_state()==terminal_rng&&solver.mt==rng,
              "terminal initialization-only continuation consumes no further RNG");
    }
    check(!nia::omt_legacy_arithmetic::enabled,"initialization policies leave the global default unchanged");
}
}
int main(int argc,char** argv){
    const std::string selected=argc>1?argv[1]:"all";
    if(selected=="all"||selected=="scoring")equality_scoring_and_update();
    if(selected=="all"||selected=="464")traced_464_move();
    if(selected=="all"||selected=="cofactor")cofactor_and_product();
    if(selected=="all"||selected=="255")traced_255_product();
    if(selected=="all"||selected=="wide_linear")wide_linear_coefficient();
    if(selected=="all"||selected=="wide_quadratic")wide_quadratic_discriminant();
    if(selected=="all"||selected=="wide_truth")wide_candidate_truths();
    if(selected=="all"||selected=="small_concave")small_concave_candidates();
    if(selected=="all"||selected=="default")legacy_scope_is_unchanged();
    if(selected=="all"||selected=="overflow")overflow_stops_only_continuation();
    if(selected=="all"||selected=="continuation_policy")continuation_initialization_policy();
    std::cout<<"PASS optimization-only exact legacy arithmetic\n";
}
