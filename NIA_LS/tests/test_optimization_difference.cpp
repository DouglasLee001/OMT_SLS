// Pure native arithmetic/graph checks. No solver CLI or benchmark run.
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include <gmpxx.h>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <map>
#include <set>

#include "../src/nia_omt_difference.h"

namespace {
using Values=std::vector<__int128_t>;
using Move=std::vector<std::pair<size_t,__int128_t>>;
int failures=0;
void check(bool okay,const char* message) {
    if(!okay){std::cerr<<"FAIL: "<<message<<'\n';++failures;}
}
mpz_class integer(__int128_t value) {
    return mpz_class(nia::format_omt_integer(value),10);
}
Values applied(Values values,const Move& move) {
    for(const auto& next:move)values[next.first]=next.second;
    return values;
}
mpz_class polynomial(const nia::ls_solver& solver,const nia::lit& literal,const Values& x) {
    mpz_class result=integer(literal.key);
    for(const auto& ct:literal.coff_terms) {
        mpz_class value=integer(ct.coff);
        for(const auto& f:solver._terms[ct.term_idx].var_epxs) {
            mpz_class power,base=integer(x[f.var_index]);
            mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),unsigned(f.exponent));
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
            const auto& literal=solver._lits[std::abs(id)];
            bool truth;
            if(literal.is_nia_lit) {
                auto delta=polynomial(solver,literal,x);
                truth=literal.is_equal?delta==0:delta<=0;
            } else truth=x[size_t(literal.delta)]>0;
            if(truth==(id>0)){satisfied=true;break;}
        }
        if(!satisfied)return false;
    }
    return true;
}
struct Fixture {
    nia::ls_solver solver{1,0,true};
    Values x,lo,hi;
    explicit Fixture(size_t count):x(count,0),lo(count,-10),hi(count,10) {
        solver._num_vars=count;solver._vars.resize(count);
        for(auto& variable:solver._vars)variable.is_nia=true;
        solver._lits.resize(1);
        solver.omt_direction="min";
    }
    int literal(__int128_t key,const std::vector<std::pair<size_t,__int128_t>>& coefficients,
                bool equality=false) {
        nia::lit literal{};literal.is_nia_lit=true;literal.is_equal=equality;
        literal.key=key;literal.delta=123;literal.is_true=-1;
        for(auto coefficient:coefficients) {
            nia::term term{};term.var_epxs.emplace_back(int(coefficient.first));
            const int term_index=int(solver._terms.size());solver._terms.push_back(term);
            literal.coff_terms.emplace_back(term_index,coefficient.second);
        }
        const int id=int(solver._lits.size());solver._lits.push_back(literal);
        solver._num_lits=solver._lits.size()-1;return id;
    }
    void clause(std::vector<int> literals) {
        nia::clause clause{};clause.literals=std::move(literals);
        solver._clauses.push_back(clause);solver._num_clauses=solver._clauses.size();
    }
    Move move(Move requested,std::set<size_t> fixed={0},unsigned variant=0,
              const std::function<bool()>& budget=[](){return true;}) const {
        return nia::difference_branch_move(solver,x,lo,hi,std::move(requested),
                                           std::move(fixed),budget,variant);
    }
};

Fixture chain(bool negative=false,bool scaled=false) {
    // Widths 1,1,2 prevent the last item from teleporting into the unit gap
    // at the left. Compaction requires shifting the entire current OR order.
    const int widths[]={0,1,1,2};
    Fixture fixture(4);fixture.x={5,1,2,3};fixture.lo={1,0,0,0};fixture.hi={5,4,4,3};
    for(size_t v=1;v<4;++v) {
        if(negative)fixture.clause({-fixture.literal(2-2*widths[v],{{0,2},{v,-2}})});
        else fixture.clause({fixture.literal(scaled?2*widths[v]-1:widths[v],
                                             {{v,scaled?2:1},{0,scaled?-2:-1}})});
    }
    for(size_t i=1;i<4;++i)for(size_t j=i+1;j<4;++j) {
        int a,b;
        if(negative) {
            a=-fixture.literal(2-2*widths[i],{{j,2},{i,-2}});
            b=-fixture.literal(2-2*widths[j],{{i,2},{j,-2}});
        } else {
            const int coefficient=scaled?2:1;
            a=fixture.literal(scaled?2*widths[i]-1:widths[i],{{i,coefficient},{j,-coefficient}});
            b=fixture.literal(scaled?2*widths[j]-1:widths[j],{{j,coefficient},{i,-coefficient}});
        }
        fixture.clause({a,b});
    }
    return fixture;
}

void full_or_chain(bool negative,bool scaled) {
    auto fixture=chain(negative,scaled);
    check(hard(fixture.solver,fixture.x),"OR-chain starts feasible");
    auto pressure=[&](const Values& values) {
        __int128_t result=0;
        for(size_t v=1;v<4;++v)result+=std::max(__int128_t(0),values[v]+(v==3?3:2)-values[0]);
        return result;
    };
    for(size_t v=0;v<4;++v)for(int value=int(fixture.lo[v]);value<=int(fixture.hi[v]);++value) {
        auto trial=fixture.x;trial[v]=value;
        check(!hard(fixture.solver,trial)||trial[0]>=fixture.x[0],
              "no single coordinate strictly improves the OR-chain objective");
        if(v)check(!hard(fixture.solver,trial)||pressure(trial)>=pressure(fixture.x),
              "no feasible single neutral move lowers direct containment pressure");
    }
    const auto before=fixture.x;
    const auto move=fixture.move({{0,4}});
    check(!move.empty(),"difference closure generates a joint OR-chain compaction");
    if(!move.empty()) {
        auto after=applied(fixture.x,move);
        check(after==Values({4,0,1,2}),"closure shifts all three touching objects atomically");
        check(hard(fixture.solver,after),"joint compaction satisfies every original OR clause exactly");
    }
    check(fixture.x==before,"closure preserves source assignment");
    for(size_t l=1;l<fixture.solver._lits.size();++l)
        check(fixture.solver._lits[l].delta==123,"closure preserves literal caches");
}

void bounds_fixed_and_budget() {
    auto fixture=chain();
    check(fixture.move({{0,0}}).empty(),"out-of-bound requested objective is rejected");
    check(fixture.move({{0,4},{0,3}}).empty(),"conflicting duplicate requested coordinates are rejected");
    check(fixture.move({{0,4}},{0,1}).empty(),"fixed predecessor prevents an invalid compaction");
    fixture.lo[1]=1;
    check(fixture.move({{0,4}}).empty(),"true lower bound exposes a negative cycle instead of a partial model");
    fixture.lo[1]=0;
    check(fixture.move({{0,4}},{0},0,[](){return false;}).empty(),
          "budget exhausted at entry produces no candidate");
    unsigned remaining=10;
    auto budget=[&](){if(!remaining)return false;--remaining;return true;};
    check(fixture.move({{0,4}},{0},0,budget).empty(),
          "budget exhausted during construction or closure produces no candidate");
    check(fixture.x==Values({5,1,2,3}),"all failure paths preserve source assignment");
}

void equality_and_disequality() {
    Fixture equal(3);equal.x={3,2,2};equal.lo={0,0,0};equal.hi={4,4,4};
    equal.clause({equal.literal(1,{{1,1},{0,-1}})});
    equal.clause({equal.literal(0,{{1,1},{2,-1}},true)});
    auto move=equal.move({{0,2}});
    check(!move.empty(),"positive equality propagates both directions");
    if(!move.empty())check(hard(equal.solver,applied(equal.x,move)),"positive equality candidate is exact");
    for(int sign:{-1,1}) {
        Fixture unequal(2);unequal.x={3,sign>0?2:4};unequal.lo={0,0};unequal.hi={5,5};
        const int inequality=unequal.literal(0,{{0,1},{1,-1}},true);
        unequal.clause({-inequality});
        auto candidate=unequal.move({{0,sign>0?2:4}});
        check(!candidate.empty(),"negative equality keeps its currently satisfied strict side");
        if(!candidate.empty())check(hard(unequal.solver,applied(unequal.x,candidate)),
                                   "integer disequality branch stays exactly true");
    }
}

void boolean_and_branch_variants() {
    Fixture fixture(3);fixture.x={3,2,1};fixture.lo={0,0,-1};fixture.hi={4,4,1};
    fixture.solver._vars[2].is_nia=false;
    nia::lit boolean{};boolean.is_nia_lit=false;boolean.delta=2;
    const int b=int(fixture.solver._lits.size());fixture.solver._lits.push_back(boolean);
    fixture.clause({b,fixture.literal(0,{{0,1}})});
    fixture.clause({fixture.literal(1,{{1,1},{0,-1}})});
    auto move=fixture.move({{0,2}});
    check(!move.empty(),"unchanged true Boolean witnesses do not impose their false linear alternative");
    if(!move.empty()) {
        auto after=applied(fixture.x,move);
        check(after[2]==1&&hard(fixture.solver,after),"source normalization leaves Booleans unchanged");
    }
    Fixture branch(2);branch.x={3,1};branch.lo={0,0};branch.hi={4,4};
    const int first=branch.literal(0,{{1,1},{0,-1}});
    const int second=branch.literal(0,{{0,-1},{1,1}});
    branch.clause({first,second});
    for(unsigned variant=0;variant<4;++variant) {
        auto candidate=branch.move({{0,2}},{0},variant);
        check(!candidate.empty(),"branch selection variants retain a currently satisfied witness");
        if(!candidate.empty())check(hard(branch.solver,applied(branch.x,candidate)),"branch variant result is exact");
    }
    Fixture choices(2);choices.x={3,1};choices.lo={0,0};choices.hi={5,5};
    choices.clause({choices.literal(2,{{1,1},{0,-1}}),choices.literal(-3,{{0,1}})});
    check(!choices.move({{0,4}},{0,1},0).empty(),"one currently true OR branch permits the fixed probe");
    check(choices.move({{0,4}},{0,1},1).empty(),"a different currently true branch can conflict with the fixed probe");
}

void combined_coefficients_and_large_exact_values() {
    Fixture duplicate(2);duplicate.x={3,2};duplicate.lo={0,0};duplicate.hi={4,4};
    duplicate.clause({duplicate.literal(1,{{1,3},{1,-2},{0,-1}})});
    auto move=duplicate.move({{0,2}});
    check(!move.empty(),"repeated variable coefficients are combined before difference recognition");
    if(!move.empty())check(hard(duplicate.solver,applied(duplicate.x,move)),"combined coefficient candidate is exact");
    const __int128_t large=__int128_t(1)<<100;
    Fixture exact(2);exact.x={large+3,large+2};exact.lo={large,large};exact.hi={large+4,large+4};
    exact.clause({exact.literal(1,{{1,large},{0,-large}})});
    auto candidate=exact.move({{0,large+2}});
    check(!candidate.empty(),"GMP closure handles products and potentials beyond signed 128-bit intermediates");
    if(!candidate.empty())check(hard(exact.solver,applied(exact.x,candidate)),"large exact difference candidate is feasible");
    const __int128_t maximum=__int128_t((__uint128_t(1)<<127)-1),minimum=-maximum-1;
    Fixture endpoints(2);endpoints.x={maximum-1,maximum-2};
    endpoints.lo={minimum,minimum};endpoints.hi={maximum,maximum};
    endpoints.clause({endpoints.literal(1,{{1,1},{0,-1}})});
    auto edge=endpoints.move({{0,maximum-2}});
    check(!edge.empty(),"full signed 128-bit bound endpoints remain representable in graph arithmetic");
    if(!edge.empty())check(hard(endpoints.solver,applied(endpoints.x,edge)),"endpoint candidate is exactly feasible");
}

void fixed_objective_and_bidirectional_source() {
    Fixture fixture(3);fixture.x={3,2,1};fixture.lo={0,0,0};fixture.hi={5,5,5};
    // Keep x2 fixed automatically because it occurs in the objective, even
    // when the caller's fixed set omits it. The lower-bound source may relax.
    nia::term objective{};objective.var_epxs.emplace_back(2);
    int term=int(fixture.solver._terms.size());fixture.solver._terms.push_back(objective);
    fixture.solver.omt_objective_terms.emplace_back(term,1);
    fixture.clause({fixture.literal(0,{{0,1},{1,-1}})});
    fixture.x={2,2,1};
    auto move=fixture.move({{0,4}},{});
    check(!move.empty(),"source normalization can propagate an increase through difference rows");
    if(!move.empty()) {
        const auto after=applied(fixture.x,move);
        check(after[0]==4&&after[1]>=4&&after[2]==1&&hard(fixture.solver,after),
              "normalization preserves both requested coordinate and automatic objective coordinates");
    }
    fixture.x[2]=2;
    fixture.clause({fixture.literal(0,{{1,1},{2,-1}})});
    check(fixture.move({{0,4}},{}).empty(),"automatic objective freeze detects conflicting difference constraints");
}

void exhaustive_small_difference_systems() {
    // Compare graph closure with independent exhaustive feasibility, including
    // lower/upper rows and integer complements with non-divisible coefficients.
    uint64_t random=173;
    auto next=[&](){random=random*6364136223846793005ULL+1442695040888963407ULL;return random;};
    for(unsigned sample=0;sample<240;++sample) {
        Fixture fixture(3);fixture.lo={-2,-2,-2};fixture.hi={2,2,2};
        for(auto& coordinate:fixture.x)coordinate=int(next()%5)-2;
        for(unsigned row=0;row<6;++row) {
            size_t a=next()%3,b=next()%3;
            const int magnitude=int(next()%3)+1;
            std::vector<std::pair<size_t,__int128_t>> coefficients;
            coefficients.push_back({a,magnitude});
            if(b!=a)coefficients.push_back({b,-magnitude});
            const __int128_t current=magnitude*(fixture.x[a]-(b!=a?fixture.x[b]:0));
            const int slack=int(next()%4);
            if(next()%2) {
                // Positive row chosen true at the old assignment.
                const int literal=fixture.literal(-current-slack,coefficients);
                fixture.clause({literal});
            } else {
                // Its negative form has integer-complement boundary 1.
                for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                const int literal=fixture.literal(current+slack+1,coefficients);
                fixture.clause({-literal});
            }
        }
        check(hard(fixture.solver,fixture.x),"generated comparison system starts feasible");
        for(int target=-2;target<=2;++target) {
            if(target==fixture.x[0])continue;
            bool feasible=false;
            for(int x1=-2;x1<=2;++x1)for(int x2=-2;x2<=2;++x2)
                feasible|=hard(fixture.solver,Values({target,x1,x2}));
            const auto candidate=fixture.move({{0,target}});
            check(!candidate.empty()==feasible,"difference closure agrees with exhaustive bounded integer feasibility");
            if(!candidate.empty()) {
                const auto after=applied(fixture.x,candidate);
                check(after[0]==target&&hard(fixture.solver,after),"every exhaustive-comparison candidate is exactly feasible");
                for(size_t v=0;v<after.size();++v)
                    check(after[v]>=fixture.lo[v]&&after[v]<=fixture.hi[v],"every candidate respects true bounds");
            }
        }
    }
}
}

int main() {
    full_or_chain(false,false);full_or_chain(false,true);full_or_chain(true,false);
    bounds_fixed_and_budget();equality_and_disequality();boolean_and_branch_variants();
    combined_coefficients_and_large_exact_values();fixed_objective_and_bidirectional_source();
    exhaustive_small_difference_systems();
    if(failures)return 1;
    std::cout<<"optimization difference-branch checks passed\n";
}
