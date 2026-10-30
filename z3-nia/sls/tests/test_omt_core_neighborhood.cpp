#include "../src/nia_omt_core_neighborhood.h"
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include <iostream>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <gmpxx.h>
#include <limits>
#include <string>
#include <vector>
#include <z3++.h>
namespace {
using Values = std::vector<__int128_t>;
int failures = 0;
void check(bool yes, const std::string& why) {
    if (!yes) { ++failures; std::cerr << "FAIL: " << why << '\n'; }
}
struct Fixture {
    nia::ls_solver native{17, 0, true};
    explicit Fixture(size_t count) {
        native.omt_loaded = true; native.omt_direction = "min";
        native._num_vars = count; native._num_lits = native._num_clauses = 0;
        native._vars.resize(count); native._lits.resize(1);
        native._solution.assign(count, 53);
        for (auto& variable : native._vars) { variable.is_nia = true; variable.var_name = "same-name"; }
    }
    int term(std::initializer_list<std::pair<int, int>> factors) {
        nia::term t{};
        for (auto factor : factors) { nia::var_exp power(factor.first); power.exponent = factor.second; t.var_epxs.push_back(power); }
        native._terms.push_back(t); return int(native._terms.size()-1);
    }
    int literal(__int128_t constant, std::vector<nia::coff_term> terms, bool equal=false) {
        nia::lit p{}; p.is_nia_lit = true; p.is_equal = equal; p.key = constant; p.coff_terms = std::move(terms);
        native._lits.push_back(p); native._num_lits = native._lits.size()-1; return int(native._num_lits);
    }
    void clause(std::vector<int> ids) {
        nia::clause c{}; c.literals=std::move(ids); native._clauses.push_back(c); native._num_clauses=native._clauses.size();
    }
    void bounds(int v, __int128_t lo, __int128_t hi) {
        int t=term({{v,1}}); clause({literal(lo,{{t,-1}})}); clause({literal(-hi,{{t,1}})});
    }
};
bool has(const std::vector<size_t>& values,size_t v){return std::find(values.begin(),values.end(),v)!=values.end();}
mpz_class integer(__int128_t value){return mpz_class(nia::format_omt_integer(value));}
mpz_class evaluate(const Fixture& f,__int128_t constant,const std::vector<nia::coff_term>& terms,const Values& x){
    mpz_class result=integer(constant);
    for(auto ct:terms){mpz_class product=integer(ct.coff);
        for(auto factor:f.native._terms[ct.term_idx].var_epxs){mpz_class power;mpz_pow_ui(power.get_mpz_t(),integer(x[factor.var_index]).get_mpz_t(),factor.exponent);product*=power;}
        result+=product;
    }return result;
}
bool exact_hard(const Fixture& f,const Values& x){
    if(x.size()!=f.native._num_vars)return false;
    for(const auto& clause:f.native._clauses){bool sat=false;
        for(int id:clause.literals){const auto& literal=f.native._lits[std::abs(id)];bool truth;
            if(literal.is_nia_lit){auto q=evaluate(f,literal.key,literal.coff_terms,x);truth=literal.is_equal?q==0:q<=0;}
            else truth=x[size_t(literal.delta)]==1;
            sat|=truth==(id>0);
        }if(!sat)return false;
    }return true;
}
nia::OmtCoreNeighborhood::Result finish(nia::OmtCoreNeighborhood& n) {
    nia::OmtCoreNeighborhood::Result answer;
    for (unsigned i=0;i<32 && !n.finished();++i) {
        answer=n.resume_for(1);
        if(answer.candidate_available) break;
    }
    return answer;
}
void joint_cubic_improvement() {
    Fixture f(3); for (int v=0;v<3;++v) f.bounds(v,0,1);
    for (int v=0;v<3;++v) f.native.omt_objective_terms.emplace_back(f.term({{v,1}}),1);
    f.native.omt_objective_terms.emplace_back(f.term({{0,1},{1,1},{2,1}}),-4);
    const auto rng=f.native.mt; const auto solution=f.native._solution;
    nia::OmtCoreNeighborhood n(f.native,{0,0,0},"0",7);
    const auto first=n.resume_for(1);
    check(!first.candidate_available && first.queries==1 && first.cores==1 && first.anchors>=1,
          "cubic first query keeps coordinate anchors and discovers a genuine UNSAT core");
    check(first.released_variables.size()==2 && !first.core_variables.empty(),
          "cubic first core releases a coordinate beyond its single initially free objective variable");
    nia::OmtCoreNeighborhood repeat(f.native,{0,0,0},"0",7);
    const auto repeated=repeat.resume_for(1);
    check(!first.released_variables.empty() && !repeated.released_variables.empty() &&
          first.released_variables.front()==repeated.released_variables.front(),
          "local RNG reproducibly selects the initial objective coordinate");
    auto r=first.candidate_available ? first : finish(n);
    check(r.candidate_available && r.status=="sat" && r.candidate==Values({1,1,1}) && r.candidate_value=="-1",
          "000 -> 111 crosses x+y+z-4xyz using a complete native strict model");
    check(f.native.mt==rng && f.native._solution==solution,"neighborhood does not mutate native RNG or assignment");
    nia::OmtCoreNeighborhood prefree(f.native,{0,0,0},"0",7,{0,1});
    const auto open=prefree.resume_for(1);
    check(open.candidate_available && open.candidate==Values({1,1,1}) && open.released_variables.size()==3,
          "all initially_free coordinates stay free and initial objective selection excludes them");
}
void core_releases_only_relevant_anchors() {
    Fixture f(3); f.bounds(0,0,1);
    f.clause({f.literal(0,{{f.term({{0,1}}),1},{f.term({{1,1}}),-1}},true)});
    f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood n(f.native,{1,1,77},"1",5);
    const auto first=n.resume_for(1);
    check(!first.candidate_available && first.queries==1 && first.cores==1 && !n.finished(),
          "restricted UNSAT releases anchors and cannot finish/prove an optimum");
    check(has(first.core_variables,1) && has(first.released_variables,0) && has(first.released_variables,1)
          && !has(first.released_variables,2),"core maps native indices and leaves irrelevant anchors fixed");
    const auto result=finish(n);
    check(result.candidate_available && result.candidate==Values({0,0,77}) && result.candidate_value=="0",
          "persistent solver drops a conflicting coordinate equality and finds the joint improvement");
    check(result.queries==1 && has(result.core_variables,1),"resume reports delta queries and cumulative core indices");
    Fixture at_optimum(1); at_optimum.bounds(0,0,1);
    at_optimum.native.omt_objective_terms={{at_optimum.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood done(at_optimum.native,{0},"0",5);
    auto end=finish(done);
    check(done.finished() && end.status=="exhausted" && !end.candidate_available,
          "even empty-assumption UNSAT only exhausts this neighborhood");
    check(done.resume_for(1).queries==0,"finished neighborhood never consumes another global solver slice");
}
void maximum_and_exact_boolean_cnf() {
    Fixture maximum(1); maximum.native.omt_direction="max"; maximum.bounds(0,-4,-2);
    maximum.native.omt_objective_terms={{maximum.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood up(maximum.native,{-3},"-3",11);
    auto r=finish(up);
    check(r.candidate_available && r.candidate==Values({-2}) && r.candidate_value=="-2","max uses a strict greater bound in original sign");
    Fixture f(3); f.native._vars[1].is_nia=false; f.bounds(0,0,1);
    nia::lit boolean{}; boolean.delta=1; f.native._lits.push_back(boolean); f.native._num_lits=f.native._lits.size()-1;
    const int b=int(f.native._num_lits);
    int x=f.term({{0,1}}),aux=f.term({{2,1}});
    f.clause({b,f.literal(-1,{{x,1}},true)});
    f.clause({-b,f.literal(0,{{x,1}},true)});
    f.clause({f.literal(-8,{{aux,1},{x,-1}},true)});
    f.clause({-f.literal(1,{{x,1}},true)});
    f.native.omt_objective_terms={{x,1}};
    nia::OmtCoreNeighborhood n(f.native,{1,-1,9},"1",7,{2});
    r=finish(n);
    check(r.candidate_available && r.candidate==Values({0,1,8}) && exact_hard(f,r.candidate),
          "complete disjunctive CNF, negated equalities, Bool -1/1 and free auxiliaries are exact");
    check(has(r.released_variables,2) && has(r.core_variables,1),"initially_free auxiliaries and Boolean cores map by native index");
}
void wide_coefficients_and_incumbents() {
    Fixture f(1);const __int128_t big=__int128_t(1)<<100;f.bounds(0,big-1,big);
    f.native.omt_objective_terms={{f.term({{0,2}}),big}};
    const mpz_class seed=integer(big)*integer(big)*integer(big);
    const mpz_class expected=integer(big)*integer(big-1)*integer(big-1);
    nia::OmtCoreNeighborhood n(f.native,{big},seed.get_str(),3);
    auto r=finish(n);
    check(r.candidate_available && r.candidate==Values({big-1}) && r.candidate_value==expected.get_str()
          && exact_hard(f,r.candidate),"128-bit values/coefficient and objective beyond 128 bits never narrow");
    __int128_t low=0;check(nia::parse_omt_integer("-170141183460469231731687303715884105728",low),"parse signed128 minimum");
    Fixture boundary(1);int x=boundary.term({{0,1}});boundary.native.omt_objective_terms={{x,1}};
    // low - x = 0 avoids negating signed128 minimum in native input.
    boundary.clause({boundary.literal(low,{{x,-1}},true)});
    nia::OmtCoreNeighborhood minimum(boundary.native,{low},nia::format_omt_integer(low),3);
    r=finish(minimum);
    check(minimum.finished() && r.status=="exhausted","signed128 minimum encodes exactly through decimal Z3 numerals");
}
void decimal_spelling_and_candidate_range() {
    Fixture f(1);f.bounds(0,0,10);f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood padded(f.native,{10},"+0010",1);auto r=finish(padded);
    check(r.candidate_available && exact_hard(f,r.candidate) && r.candidate[0]<10,
          "leading zeros and optional plus remain decimal rather than octal");
    __int128_t maximum=0;
    check(nia::parse_omt_integer("170141183460469231731687303715884105727",maximum),"parse signed128 maximum");
    Fixture wide(1);int x=wide.term({{0,1}});wide.clause({wide.literal(maximum,{{x,-1}})});
    wide.native.omt_objective_terms={{x,-1}};
    nia::OmtCoreNeighborhood outside(wide.native,{maximum},nia::format_omt_integer(-maximum),1);r=finish(outside);
    check(outside.finished() && r.status=="rejected" && !r.candidate_available && r.candidate.empty(),
          "arbitrary precision Z3 model outside signed128 assignment range is rejected without truncation");
}
void reject_invalid_seed_and_native() {
    Fixture f(1);f.bounds(0,0,1);f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    for(const auto& seed:std::vector<Values>{{},{2}}){nia::OmtCoreNeighborhood n(f.native,seed,"2",1);auto r=finish(n);
        check(n.finished() && r.status=="rejected" && r.queries==0 && !r.candidate_available,"invalid seed dimensions/hard CNF rejected before query");}
    for(const std::string best:{"99","0.5","0) (assert false)","", "--1"}){nia::OmtCoreNeighborhood n(f.native,{1},best,1);auto r=finish(n);
        check(n.finished() && r.status=="rejected" && r.queries==0,"incumbent objective must be exact decimal and equal the validated seed objective");}
    Fixture boolean(1);boolean.native._vars[0].is_nia=false;
    nia::OmtCoreNeighborhood bad_bool(boolean.native,{0},"0",1);auto r=finish(bad_bool);
    check(r.status=="rejected" && r.queries==0,"Boolean seed zero is invalid even when absent from CNF");
    f.native.omt_objective_terms[0].term_idx=std::numeric_limits<int>::max();
    nia::OmtCoreNeighborhood malformed(f.native,{1},"1",1);r=finish(malformed);
    check(r.status=="unsupported" && r.queries==0,"bad native factor/term index cannot enter Z3");
    Fixture signed_literal(1);signed_literal.clause({std::numeric_limits<int>::min()});
    nia::OmtCoreNeighborhood bad_id(signed_literal.native,{0},"0",1);r=finish(bad_id);
    check(r.status=="unsupported" && r.queries==0,"INT_MIN signed native literal rejected without overflow");
}
void unknown_preserves_anchors_and_stops() {
    Fixture f(3); for(int i=0;i<3;++i)f.bounds(i,0,1);
    f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    const auto rng=f.native.mt;
    {
        struct Limit {
            Limit(){z3::set_param("rlimit",1);}
            ~Limit(){z3::set_param("rlimit",0);}
        } limit;
        nia::OmtCoreNeighborhood n(f.native,{1,1,1},"1",19);
        const auto r=n.resume_for(1);
        check(r.status=="unknown" && r.queries==1 && n.finished(),
              "a real Z3 resource-limit unknown finishes this incumbent neighborhood");
        check(r.anchors==2 && r.released_variables==std::vector<size_t>{0}
              && r.core_variables.empty() && r.cores==0 && !r.candidate_available,
              "unknown has no conflict evidence and never releases another coordinate");
        check(n.resume_for(1).queries==0,
              "same incumbent after unknown cannot spend another query slice");
    }
    nia::OmtCoreNeighborhood next(f.native,{0,1,1},"0",19);
    const auto r=finish(next);
    check(r.queries==1 && next.finished() && r.status=="exhausted",
          "a new incumbent starts its own ordinary neighborhood after unknown");
    check(f.native.mt==rng,"unknown handling never consumes the SLS RNG");
}
void timeout_unknown_stops_without_releasing() {
    // The seed switch satisfies every clause. A strict x<0 query activates
    // the pigeonhole contradiction, which reliably exhausts a 1ms SAT slice.
    constexpr int pigeons=12,holes=11;
    const size_t anchor=1+pigeons*holes;
    Fixture f(anchor+1); f.bounds(0,-1,0);
    const int switch_lit=f.literal(0,{{f.term({{0,1}}),1}},true);
    f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    std::vector<size_t> free;
    std::vector<int> b(pigeons*holes);
    for(int i=0;i<pigeons*holes;++i){
        f.native._vars[1+i].is_nia=false;free.push_back(1+i);
        nia::lit literal{};literal.delta=1+i;f.native._lits.push_back(literal);
        b[i]=int(f.native._lits.size()-1);
    }
    f.native._num_lits=f.native._lits.size()-1;
    for(int p=0;p<pigeons;++p){std::vector<int> clause{switch_lit};
        for(int h=0;h<holes;++h)clause.push_back(b[p*holes+h]);f.clause(clause);}
    for(int h=0;h<holes;++h)for(int p=0;p<pigeons;++p)for(int q=p+1;q<pigeons;++q)
        f.clause({switch_lit,-b[p*holes+h],-b[q*holes+h]});
    Values seed(anchor+1,-1);seed[0]=seed[anchor]=0;
    nia::OmtCoreNeighborhood n(f.native,seed,"0",19,free,.002);
    bool saw_unknown=false;uint64_t queries=0;
    for(unsigned i=0;i<500 && !n.finished();++i){const auto r=n.resume_for(1);queries+=r.queries;
        check(!r.candidate_available && r.candidate.empty(),"timeout/unknown never becomes an improvement model");
        if(r.status=="unknown"){saw_unknown=true;check(!has(r.released_variables,anchor),"timeout unknown keeps the remaining coordinate assumption");}
    }
    check(saw_unknown && n.finished() && queries==1,"timeout unknown ends this neighborhood after exactly one query");
    check(n.resume_for(1).queries==0,"finished unknown neighborhood cannot consume subsequent global budget");
}
void initial_free_build_is_resumable() {
    Fixture f(20000);std::vector<size_t> free;
    for(size_t i=0;i<20000;++i)free.push_back(i);
    nia::OmtCoreNeighborhood n(f.native,Values(20000,0),"0",3,free,.00003);
    for(unsigned slice=0;slice<12;++slice){const auto r=n.resume_for(1);auto sorted=r.released_variables;
        std::sort(sorted.begin(),sorted.end());
        check(std::adjacent_find(sorted.begin(),sorted.end())==sorted.end() && sorted.size()<=20000,
              "resuming initial free coordinate construction never duplicates released indices");
    }
}
void bounded_resumption() {
    Fixture f(1);f.bounds(0,0,1);f.native.omt_objective_terms={{f.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood n(f.native,{1},"1",3);
    for(double seconds:{0.,-1.,1e-300,std::numeric_limits<double>::quiet_NaN(),std::numeric_limits<double>::infinity()}){
        auto r=n.resume_for(seconds);check(!r.candidate_available && r.candidate.empty() && r.queries==0 && r.status=="budget",
          "zero/tiny/invalid budget cannot return a fabricated partial model");}
    auto result=finish(n);check(result.candidate_available && result.candidate_value=="0","budget pauses retain usable construction state");
    auto done=n.resume_for(1);check(!done.candidate_available && done.queries==0,"a delivered candidate is never repeatedly published");
    Fixture many(2500);many.native.omt_objective_terms={{many.term({{0,1}}),1}};
    nia::OmtCoreNeighborhood sliced(many.native,Values(2500,0),"0",3,{},.002);
    const auto began=std::chrono::steady_clock::now();auto short_result=sliced.resume_for(99);
    const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    check(!short_result.candidate_available && short_result.status=="budget" && elapsed<.1,
          "configured millisecond slice includes incremental context and native construction");
    check(!sliced.finished() && short_result.queries==0,
          "construction-only slice expiration remains resumable rather than terminal unknown");
}
}
int main(){joint_cubic_improvement();core_releases_only_relevant_anchors();maximum_and_exact_boolean_cnf();
wide_coefficients_and_incumbents();decimal_spelling_and_candidate_range();reject_invalid_seed_and_native();bounded_resumption();unknown_preserves_anchors_and_stops();timeout_unknown_stops_without_releasing();initial_free_build_is_resumable();if(!failures)std::cout<<"PASS OMT core neighborhood\n";return failures?1:0;}
