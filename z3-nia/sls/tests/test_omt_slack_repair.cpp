// Isolated native candidate semantics. No solver CLI or benchmark run.
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_difference.h"
#include <gmpxx.h>

// Record an unsafe request before GMP executes it. This makes the huge-exponent
// regression fail promptly on the unguarded implementation without allocating
// an enormous integer or relying on a process timeout.
unsigned unsafe_power_requests=0;
void slack_test_power(mpz_ptr result,mpz_srcptr base,unsigned long exponent) {
    if(mpz_cmpabs_ui(base,1)>0&&exponent>=65536) {
        ++unsafe_power_requests;mpz_set_ui(result,0);return;
    }
    mpz_pow_ui(result,base,exponent);
}
#ifndef OMT_SLACK_BASELINE
#pragma push_macro("mpz_pow_ui")
#undef mpz_pow_ui
#define mpz_pow_ui slack_test_power
#include "../src/nia_omt_slack_repair.h"
#pragma pop_macro("mpz_pow_ui")
#endif
#include <functional>
#include <iostream>
#include <limits>
#include <set>

namespace {
using Values=std::vector<__int128_t>;
using Move=std::vector<std::pair<size_t,__int128_t>>;
using Terms=std::vector<std::pair<__int128_t,std::vector<std::pair<int,int>>>>;
int failures=0;
void check(bool okay,const char* message) {
    if(!okay){std::cerr<<"FAIL: "<<message<<'\n';++failures;}
}
mpz_class integer(__int128_t value) {return mpz_class(nia::format_omt_integer(value),10);}
Values applied(Values values,const Move& move) {
    for(const auto& item:move)values[item.first]=item.second;
    return values;
}
mpz_class polynomial(const nia::ls_solver& solver,const nia::lit& literal,const Values& x) {
    mpz_class result=integer(literal.key);
    for(const auto& ct:literal.coff_terms) {
        mpz_class value=integer(ct.coff);
        for(auto factor:solver._terms[ct.term_idx].var_epxs) {
            mpz_class power;
            mpz_pow_ui(power.get_mpz_t(),integer(x[factor.var_index]).get_mpz_t(),factor.exponent);
            value*=power;
        }
        result+=value;
    }
    return result;
}
bool hard(const nia::ls_solver& solver,const Values& x) {
    for(const auto& clause:solver._clauses) {
        bool satisfied=false;
        for(int id:clause.literals) {
            const auto& literal=solver._lits[size_t(id<0?-int64_t(id):id)];
            bool truth;
            if(literal.is_nia_lit) {
                const auto value=polynomial(solver,literal,x);
                truth=literal.is_equal?value==0:value<=0;
            } else truth=x[size_t(literal.delta)]>0;
            satisfied|=truth==(id>0);
        }
        if(!satisfied)return false;
    }
    return true;
}
struct Fixture {
    nia::ls_solver solver{17,0,true};
    Values x,lo,hi;
    explicit Fixture(size_t count):x(count,0),lo(count,-10),hi(count,10) {
        solver._num_vars=count;solver._vars.resize(count);solver._lits.resize(1);
        for(auto& variable:solver._vars)variable.is_nia=true;
    }
    int literal(__int128_t key,const Terms& terms,bool equal=false) {
        nia::lit literal{};literal.is_nia_lit=true;literal.is_equal=equal;
        literal.key=key;literal.delta=123;literal.is_true=-1;
        for(const auto& item:terms) {
            nia::term term{};term.value=456;
            for(auto factor:item.second) {
                nia::var_exp exponent(factor.first);exponent.exponent=factor.second;
                term.var_epxs.push_back(exponent);
            }
            int index=int(solver._terms.size());solver._terms.push_back(term);
            literal.coff_terms.emplace_back(index,item.first);
        }
        int id=int(solver._lits.size());solver._lits.push_back(literal);
        solver._num_lits=solver._lits.size()-1;return id;
    }
    int linear(__int128_t key,const Move& coefficients,bool equal=false) {
        Terms terms;
        for(auto item:coefficients)terms.push_back({item.second,{{int(item.first),1}}});
        return literal(key,terms,equal);
    }
    void clause(std::vector<int> ids) {
        nia::clause clause{};clause.literals=std::move(ids);
        solver._clauses.push_back(clause);solver._num_clauses=solver._clauses.size();
    }
    std::vector<Move> moves(const Move& primary,const std::set<size_t>& derived={},
                          const std::function<bool()>& budget=[](){return true;},size_t cap=16) const {
#ifdef OMT_SLACK_BASELINE
        std::set<size_t> fixed=derived;
        for(auto item:primary)fixed.insert(item.first);
        auto move=nia::difference_branch_move(solver,x,lo,hi,primary,fixed,budget);
        return move.empty()?std::vector<Move>{}:std::vector<Move>{move};
#else
        return nia::omt_slack_repair_moves(solver,x,lo,hi,primary,derived,budget,cap);
#endif
    }
};
bool contains(const Fixture& fixture,const std::vector<Move>& moves,const Values& target) {
    for(const auto& move:moves)if(applied(fixture.x,move)==target)return true;
    return false;
}
mpz_class gram(const Values& values,bool second) {
    const auto x=integer(values[0]),y=integer(values[1]);
    return second?mpz_class(75*x*x+36*x*y+42*y*y):mpz_class(35*x*x+48*x*y+70*y*y);
}
void gram_slice(bool second) {
    Fixture fixture(3);
    fixture.x=second?Values{3,58,148227}:Values{21,5,22225};
    fixture.lo={0,0,0};fixture.hi=second?Values{207,69,100000000}:Values{29,30,100000000};
    const int a=second?3:20,b=second?9:19,lower=second?529:499,upper=second?623:587;
    fixture.clause({fixture.linear(lower,{{0,-a},{1,-b}})});
    fixture.clause({fixture.linear(-upper,{{0,a},{1,b}})});
    fixture.clause({fixture.linear(second?849:615,{{0,second?-16:-34},{1,second?-18:-33}})});
    fixture.clause({fixture.literal(0,{{second?75:35,{{0,2}}},{second?36:48,{{0,1},{1,1}}},
                                      {second?42:70,{{1,2}}},{-1,{{2,1}}}},true)});
    nia::term objective{};objective.var_epxs.emplace_back(2);
    fixture.solver.omt_objective_terms.emplace_back(fixture.solver._terms.size(),1);
    fixture.solver._terms.push_back(objective);
    auto projected=[&](Values values){values[2]=__int128_t(gram(values,second).get_si());return values;};
    check(hard(fixture.solver,fixture.x),"Gram slice starts feasible with its own projected cost");
    for(size_t v=0;v<2;++v)for(__int128_t value=fixture.lo[v];value<=fixture.hi[v];++value) {
        auto trial=fixture.x;trial[v]=value;trial=projected(trial);
        check(!hard(fixture.solver,trial)||gram(trial,second)>=gram(fixture.x,second),
              "Gram slice has no feasible single-coordinate improvement");
    }
    for(int distance=-207;distance<=207;++distance) {
        auto trial=fixture.x;trial[0]+=distance;trial[1]-=distance;
        if(trial[0]<fixture.lo[0]||trial[0]>fixture.hi[0]||trial[1]<fixture.lo[1]||trial[1]>fixture.hi[1])continue;
        trial=projected(trial);
        check(!hard(fixture.solver,trial)||gram(trial,second)>=gram(fixture.x,second),
              "Gram slice has no feasible equal/opposite improvement");
    }
    const Move primary={{second?size_t(0):size_t(1),0}};
    auto difference=nia::difference_branch_move(fixture.solver,fixture.x,fixture.lo,fixture.hi,
                                               primary,{primary[0].first,2},[](){return true;});
    check(difference.empty(),"existing equal-coefficient difference repair cannot make the Gram move");
    const auto before=fixture.x;const auto rng=fixture.solver.mt;
    auto moves=fixture.moves(primary,{2});
    const Values target=second?Values{0,59,148227}:Values{25,0,22225};
    check(contains(fixture,moves,target),second?"slack repair proposes x=0,y=59 on second Gram slice":
                                               "slack repair proposes x=25,y=0 on first Gram slice");
    bool improved=false;
    for(const auto& move:moves) {
        auto trial=applied(fixture.x,move);
        check(trial[primary[0].first]==0&&trial[2]==fixture.x[2],"candidate preserves raw primary and leaves cost for caller projection");
        trial=projected(trial);
        improved|=hard(fixture.solver,trial)&&gram(trial,second)<gram(fixture.x,second);
    }
    check(improved,"caller projection and exact original formula validate a strict Gram improvement");
    check(fixture.x==before&&fixture.solver.mt==rng,"slack candidates preserve assignment and caller RNG");
    for(size_t l=1;l<fixture.solver._lits.size();++l)
        check(fixture.solver._lits[l].delta==123,"candidate generation preserves literal caches");
}
void signed_thresholds() {
    for(int coefficient:{-3,3})for(bool negative:{false,true}) {
        Fixture f(2);f.x={1,0};
        const int id=f.linear(negative?-4:5,{{0,negative?5:-5},{1,coefficient}});
        f.clause({negative?-id:id});
        check(hard(f.solver,f.x),"signed integer threshold begins feasible");
        auto moves=f.moves({{0,0}});
        const int target=negative?(coefficient>0?2:-2):(coefficient>0?-2:2);
        check(contains(f,moves,{0,target}),"signed ceil/floor and strict complement select nearest legal integer");
        for(auto move:moves)check(hard(f.solver,applied(f.x,move)),"each threshold proposal satisfies its repaired witness");
    }
    Fixture strict(2);strict.x={1,0};
    strict.clause({-strict.linear(0,{{0,3},{1,3}})});
    check(contains(strict,strict.moves({{0,0}}),{0,1}),"strict complement excludes its exactly divisible zero boundary");
}
void equalities() {
    for(int offset:{5,6}) {
        Fixture f(2);f.x={1,0};f.clause({f.linear(offset,{{0,-offset},{1,-3}},true)});
        auto moves=f.moves({{0,0}});
        check(offset==6?contains(f,moves,{0,2}):moves.empty(),"positive equality requires exact integer divisibility");
    }
    Fixture f(2);f.x={1,0};f.clause({-f.linear(0,{{0,5},{1,3}},true)});
    auto moves=f.moves({{0,0}});
    check(contains(f,moves,{0,-1})&&contains(f,moves,{0,1}),"negated equality proposes nearest integers on both strict sides");
}
void projected_coefficients_and_unsupported_rows() {
    Fixture f(2);f.x={2,2};
    f.clause({f.literal(6,{{-2,{{0,1},{1,1}}}})});
    check(contains(f,f.moves({{0,1}}),{1,3}),"bilinear coefficient is recomputed after fixing the primary probe");
    Fixture zero(2);zero.x={1,1};zero.clause({zero.literal(1,{{-1,{{0,1},{1,1}}}})});
    check(zero.moves({{0,0}}).empty(),"zero probe coefficient cannot repair a broken witness");
    Fixture square(2);square.x={1,0};square.clause({square.literal(1,{{-1,{{0,1}}},{-1,{{1,2}}}})});
    check(square.moves({{0,0}}).empty(),"non-affine compensation rows fall back without a claim");
    Fixture derived(3);derived.x={1,0,5};
    derived.clause({derived.linear(0,{{0,-5},{1,-3},{2,1}})});
    check(derived.moves({{0,0}},{2}).empty(),"rows containing derived cost cannot constrain compensation using stale cost");
    Fixture direct(2);direct.x={1,0};direct.clause({direct.linear(5,{{0,-5},{1,-3}})});
    nia::term term{};term.var_epxs.emplace_back(1);direct.solver._terms.push_back(term);
    direct.solver.omt_objective_terms.emplace_back(direct.solver._terms.size()-1,1);
    check(contains(direct,direct.moves({{0,0}}),{0,2}),"compensating variable may affect the true objective");
}
void clauses_bounds_and_cancellation() {
    Fixture f(3);f.x={1,0,1};f.solver._vars[2].is_nia=false;
    nia::lit boolean{};boolean.delta=2;int b=f.solver._lits.size();f.solver._lits.push_back(boolean);
    const int row=f.linear(5,{{0,-5},{1,-3}});f.clause({b,row});
    check(f.moves({{0,0}}).empty(),"a preserved Boolean clause witness needs no arithmetic compensation");
    f.x[2]=-1;
    check(contains(f,f.moves({{0,0}}),{0,2,-1}),"broken arithmetic witness is repaired when Boolean witness is false");
    f.lo[1]=3;
    check(contains(f,f.moves({{0,0}}),{0,3,-1}),"native lower bound clips a feasible half-line threshold");
    f.hi[1]=1;
    check(f.moves({{0,0}}).empty(),"empty native compensation domain publishes no proposal");
    f.lo[1]=-10;f.hi[1]=10;
    check(f.moves({{0,11}}).empty(),"out-of-bounds primary is rejected");
    check(f.moves({{0,0},{0,1}}).empty(),"conflicting primary requests are rejected");
    check(f.moves({{0,0},{1,0}}).empty(),"all primary coordinates stay fixed");
    check(f.moves({{0,0}},{1}).empty(),"derived variables cannot serve as compensation variables");
    check(f.moves({{0,0}},{},[](){return false;}).empty(),"entry cancellation publishes nothing");
    size_t calls=0;auto counted=[&](){++calls;return true;};
    check(!f.moves({{0,0}},{},counted).empty(),"counted search produces a candidate");
    size_t remaining=calls-1;
    check(f.moves({{0,0}},{},[&](){return remaining?--remaining,true:false;}).empty(),
          "late cancellation discards every accumulated proposal");
    check(f.moves({{0,0}},{},[](){return true;},0).empty(),"zero candidate cap returns no proposals");
    check(f.moves({{0,0}},{},[](){return true;},1).size()<=1,"candidate cap bounds publication");
    Fixture alternatives(2);alternatives.x={1,0};
    alternatives.clause({alternatives.linear(5,{{0,-5},{1,-3}}),
                         alternatives.linear(5,{{0,-5},{1,3}})});
    auto choices=alternatives.moves({{0,0}});
    check(contains(alternatives,choices,{0,2})&&contains(alternatives,choices,{0,-2}),
          "old true OR witnesses supply independent candidate branches");
}
void large_exact_values() {
    const __int128_t huge=__int128_t(1)<<100;
    Fixture f(2);f.x={huge,huge+1};f.lo={huge,huge};f.hi={huge+10,huge+10};
    f.clause({f.linear(1,{{0,huge},{1,-huge}})});
    auto moves=f.moves({{0,huge+2}});
    check(contains(f,moves,{huge+2,huge+3}),"GMP products and exact integer thresholds exceed 64/128-bit intermediates");
    for(auto move:moves)check(hard(f.solver,applied(f.x,move)),"large native values satisfy exact arithmetic witness");
}
void traversal_cap() {
    Fixture f(2);f.x={1,1};f.solver._vars[1].is_nia=false;
    nia::lit boolean{};boolean.delta=1;f.solver._lits.push_back(boolean);
    for(unsigned i=0;i<1024;++i)f.clause({1});
    size_t calls=0;
    auto moves=f.moves({{0,0}},{},[&](){++calls;return true;},1);
    check(moves.empty()&&calls<200,"candidate cap also bounds traversal when every clause stays true");
}
void arithmetic_resource_limits() {
    Fixture huge(3);huge.x={1,0,1};huge.lo[2]=1;huge.hi[2]=2;
    huge.clause({huge.literal(4,{{-5,{{0,1}}},{-3,{{1,1}}},
                               {1,{{2,std::numeric_limits<int>::max()}}}})});
    const auto before=huge.x;const auto rng=huge.solver.mt;
    const auto unsafe_before=unsafe_power_requests;
    auto declined=huge.moves({{0,0},{2,2}});
    check(unsafe_power_requests==unsafe_before,"huge native exponent is declined before an unsafe GMP power call");
    check(declined.empty(),"exceeding arithmetic bit allowance publishes no partial candidate");
    check(huge.x==before&&huge.solver.mt==rng,"arithmetic resource rejection preserves assignment and RNG");
    for(int base:{-1,0,1}) {
        Fixture unit(3);unit.x={1,0,base};
        unit.clause({unit.literal(5-base,{{-5,{{0,1}}},{-3,{{1,1}}},
                                        {1,{{2,std::numeric_limits<int>::max()}}}})});
        auto moves=unit.moves({{0,0},{2,base}});
        check(contains(unit,moves,{0,2,base}),"huge exponents of zero and signed unit bases remain supported");
    }
    Fixture bounded(3);bounded.x={1,0,2};
    bounded.clause({bounded.literal(5,{{-5,{{0,1}}},{-3,{{1,1}}},
                                      {1,{{2,30000}}},{-1,{{2,30000}}}})});
    check(contains(bounded,bounded.moves({{0,0},{2,2}}),{0,2,2}),
          "large powers within the bit allowance and exact cancellation remain supported");
    Fixture product(5);product.x={1,0,2,2,2};
    product.clause({product.linear(5,{{0,-5},{1,-3}})});
    product.clause({product.literal(0,{{-1,{{2,30000},{3,30000},{4,30000}}}})});
    check(product.moves({{0,0}}).empty(),"oversized products discard earlier proposals within the same search");
    Fixture sum(3);sum.x={1,0,2};
    sum.clause({sum.linear(5,{{0,-5},{1,-3}})});
    sum.clause({sum.literal(0,{{-1,{{2,32767},{2,32767}}},
                             {-1,{{2,32767},{2,32767}}},
                             {-1,{{2,32767},{2,32767}}},
                             {-1,{{2,32767},{2,32767}}}})});
    check(sum.moves({{0,0}}).empty(),"oversized polynomial sums discard earlier proposals");
}
}
int main(int argc,char** argv) {
    gram_slice(false);gram_slice(true);
    if(argc>1&&std::string(argv[1])=="--gram-only")return failures?1:0;
    signed_thresholds();equalities();projected_coefficients_and_unsupported_rows();
    clauses_bounds_and_cancellation();large_exact_values();traversal_cap();arithmetic_resource_limits();
    if(failures)return 1;
    std::cout<<"slack repair native candidate checks passed\n";
}
