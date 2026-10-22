// Pure arithmetic candidate tests; no solver CLI or benchmark execution.
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_omt_difference.h"
#include <cstdlib>
#include <functional>
#include <iostream>
#include <set>

#include "../src/nia_omt_disjunctive.h"

namespace {
using Values=std::vector<__int128_t>;
using Move=std::vector<std::pair<size_t,__int128_t>>;
int failures=0;
void check(bool okay,const char* description) {
    if(!okay){std::cerr<<"FAIL: "<<description<<'\n';++failures;}
}
mpz_class integer(__int128_t value) {return mpz_class(nia::format_omt_integer(value),10);}
Values applied(Values x,const Move& move) {for(auto p:move)x[p.first]=p.second;return x;}
bool hard(const nia::ls_solver& solver,const Values& x) {
    for(const auto& clause:solver._clauses) {
        bool satisfied=false;
        for(int id:clause.literals) {
            const auto& literal=solver._lits[std::abs(id)];bool truth;
            if(!literal.is_nia_lit)truth=x[size_t(literal.delta)]>0;
            else {
                mpz_class delta=integer(literal.key);
                for(const auto& ct:literal.coff_terms) {
                    mpz_class value=integer(ct.coff);
                    for(auto factor:solver._terms[ct.term_idx].var_epxs) {
                        mpz_class power,base=integer(x[factor.var_index]);
                        mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),factor.exponent);value*=power;
                    }
                    delta+=value;
                }
                truth=literal.is_equal?delta==0:delta<=0;
            }
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
        solver._num_vars=count;solver._vars.resize(count);solver._lits.resize(1);
        for(auto& variable:solver._vars)variable.is_nia=true;
    }
    int literal(__int128_t key,const std::vector<std::pair<size_t,__int128_t>>& coefficients,
                bool equality=false) {
        nia::lit literal{};literal.is_nia_lit=true;literal.is_equal=equality;
        literal.key=key;literal.delta=123;literal.is_true=-1;
        for(auto coefficient:coefficients) {
            nia::term term{};term.var_epxs.emplace_back(int(coefficient.first));term.value=719;
            int index=int(solver._terms.size());solver._terms.push_back(term);
            literal.coff_terms.emplace_back(index,coefficient.second);
        }
        int id=int(solver._lits.size());solver._lits.push_back(literal);
        solver._num_lits=solver._lits.size()-1;return id;
    }
    void clause(std::vector<int> literals) {
        nia::clause clause{};clause.literals=std::move(literals);
        solver._clauses.push_back(clause);solver._num_clauses=solver._clauses.size();
    }
    Move move(Move requested,size_t max_states=64,unsigned branch_seed=0,
              nia::DisjunctiveMoveStatistics* statistics=nullptr,
              const std::function<bool()>& budget=[](){return true;},
              std::set<size_t> fixed={}) const {
        return nia::disjunctive_difference_move(solver,x,lo,hi,std::move(requested),
                                               std::move(fixed),budget,max_states,branch_seed,statistics);
    }
};

void moving_false_branch_shifts_whole_chain(bool negative,bool equality,bool duplicate=false) {
    Fixture f(4);f.x={4,3,2,1};f.lo={1,0,0,0};f.hi={4,3,3,3};
    for(size_t v=1;v<4;++v) {
        if(negative)f.clause({-f.literal(0,{{v-1,2},{v,-2}})});
        else f.clause({f.literal(1,{{v,1},{v-1,-1}})});
    }
    int old_branch,new_branch=f.literal(-2,{{1,1}});
    if(equality) {
        old_branch=-f.literal(0,{{3,1}},true);
        f.clause({f.literal(-1,{{2,1},{3,-1}},true)});
    } else if(negative)old_branch=-f.literal(0,{{3,1}});
    else old_branch=f.literal(1,{{3,-1}});
    f.clause(duplicate?std::vector<int>{old_branch,old_branch,new_branch,new_branch}
                      :std::vector<int>{old_branch,new_branch});
    check(hard(f.solver,f.x),"chain starts feasible with only the old OR branch true");
    for(unsigned branch=0;branch<3;++branch)
        check(nia::difference_branch_move(f.solver,f.x,f.lo,f.hi,{{0,3}},{},[](){return true;},branch).empty(),
              "old-true-branch closure cannot improve this chain");
    nia::DisjunctiveMoveStatistics statistics;
    auto move=f.move({{0,3}},64,0,&statistics);
    check(!move.empty(),"disjunctive closure changes an old-false branch and shifts the whole chain");
    if(!move.empty()) {
        const auto x=applied(f.x,move);
        check(x==Values({3,2,1,0})&&hard(f.solver,x),"changed topology candidate satisfies every original chain clause");
    }
    check(statistics.graph_checks>1&&statistics.graph_checks<=64,
          "topology repair explores more than the old branch and respects its graph budget");
    check(statistics.unique_states>=statistics.graph_checks&&statistics.unique_states<=1024,
          "visited states are deduplicated and bounded");
    check(f.x==Values({4,3,2,1}),"disjunctive preview preserves the original assignment");
    for(const auto& term:f.solver._terms)check(term.value==719,"disjunctive preview preserves term caches");
    for(size_t l=1;l<f.solver._lits.size();++l)check(f.solver._lits[l].delta==123,"disjunctive preview preserves literal caches");
    nia::DisjunctiveMoveStatistics limited;
    check(f.move({{0,3}},1,0,&limited).empty()&&limited.graph_checks==1,
          "one-graph budget does not silently search a replacement topology");
    check(f.move({{0,3}},64,0,nullptr,[](){return true;},{0,3}).empty(),
          "fixed chain endpoint prevents a false improvement");
}

void joint_product_objective_and_new_orientation() {
    // Two fixed-orientation items 3x4 and 2x3. The vertical layout has area21;
    // the horizontal layout has area20, requiring W3,H7 -> W5,H4 together.
    Fixture f(6);f.x={3,7,0,0,0,4};f.lo={3,4,0,0,0,0};f.hi={5,7,5,7,5,7};
    const int widths[]={3,2},heights[]={4,3};
    for(size_t item=0;item<2;++item) {
        const size_t vx=2+2*item,vy=vx+1;
        f.clause({f.literal(widths[item],{{vx,1},{0,-1}})});
        f.clause({f.literal(heights[item],{{vy,1},{1,-1}})});
    }
    f.clause({f.literal(3,{{2,1},{4,-1}}),f.literal(2,{{4,1},{2,-1}}),
              f.literal(4,{{3,1},{5,-1}}),f.literal(3,{{5,1},{3,-1}})});
    nia::term product{};product.var_epxs.emplace_back(0);product.var_epxs.emplace_back(1);product.value=719;
    int index=int(f.solver._terms.size());f.solver._terms.push_back(product);
    f.solver.omt_objective_terms.emplace_back(index,1);
    check(hard(f.solver,f.x),"product fixture old vertical layout is feasible");
    check(nia::difference_branch_move(f.solver,f.x,f.lo,f.hi,{{0,5},{1,4}},{},[](){return true;}).empty(),
          "retaining old vertical topology cannot fit the improving aspect ratio");
    auto move=f.move({{0,5},{1,4}});
    check(!move.empty(),"disjunctive joint probe changes aspect ratio and orientation");
    if(!move.empty()) {
        const auto x=applied(f.x,move);
        check(x[0]==5&&x[1]==4&&x[0]*x[1]==20&&hard(f.solver,x),
              "joint product candidate preserves the fixed probe and improves area21 to20");
    }
    // H is an objective coordinate and may not drift even if omitted by caller.
    auto impossible=f.move({{0,3},{1,4}});
    check(impossible.empty(),"fixed objective coordinates rule out changing H or W to hide an infeasible probe");
}

void synchronized_old_true_choices_are_independent() {
    Fixture f(3);f.x={1,1,1};f.lo={0,0,0};f.hi={1,1,1};
    const int xr=f.literal(0,{{1,1},{0,-1}}),yr=f.literal(0,{{2,1},{0,-1}});
    f.clause({xr});f.clause({yr});
    f.clause({xr,f.literal(1,{{1,-1}})});
    f.clause({f.literal(1,{{2,-1}}),yr});
    for(unsigned branch=0;branch<3;++branch)
        check(nia::difference_branch_move(f.solver,f.x,f.lo,f.hi,{{0,0}},{},[](){return true;},branch).empty(),
              "globally synchronized old-true branch variants miss the mixed assignment");
    auto move=f.move({{0,0}});
    check(!move.empty()&&hard(f.solver,applied(f.x,move)),
          "per-clause search finds the mixed old-true choice without a global variant lock");
}

void bounds_budget_and_dedup() {
    Fixture f(2);f.x={2,1};f.lo={0,0};f.hi={3,3};
    f.clause({f.literal(1,{{1,1},{0,-1}})});
    check(f.move({{0,-1}}).empty(),"out-of-bound probe is rejected");
    check(f.move({{0,1},{0,2}}).empty(),"contradictory requested values are rejected");
    nia::DisjunctiveMoveStatistics statistics;
    check(f.move({{0,1}},0,0,&statistics).empty()&&statistics.graph_checks==0,
          "zero graph budget performs no graph search");
    check(f.move({{0,1}},64,0,&statistics,[](){return false;}).empty()
          &&statistics.cancelled,"expired wall budget rejects all candidates");
    unsigned calls=10;
    auto budget=[&](){if(!calls)return false;--calls;return true;};
    check(f.move({{0,1}},64,0,&statistics,budget).empty()&&statistics.cancelled,
          "mid-construction wall cancellation never publishes a partial candidate");
    size_t callbacks=0;
    auto counting_budget=[&](){++callbacks;return true;};
    check(!f.move({{0,1}},64,0,nullptr,counting_budget).empty(),
          "successful candidate establishes a complete callback trace");
    size_t remaining=callbacks-1;
    auto publication_budget=[&](){if(!remaining)return false;--remaining;return true;};
    check(f.move({{0,1}},64,0,&statistics,publication_budget).empty()&&statistics.cancelled,
          "budget cancellation at the final publication check discards the complete candidate");
    Fixture duplicated(2);duplicated.x=f.x;duplicated.lo=f.lo;duplicated.hi=f.hi;
    const int duplicate=duplicated.literal(1,{{1,1},{0,-1}});
    duplicated.clause({duplicate,duplicate});
    nia::DisjunctiveMoveStatistics one,two;
    auto a=f.move({{0,1}},64,0,&one),b=duplicated.move({{0,1}},64,0,&two);
    check(!a.empty()&&a==b&&one.graph_checks==two.graph_checks&&one.unique_states==two.unique_states,
          "duplicate alternatives do not create duplicate branch assignments");
}

void exhaustive_small_disjunctions() {
    uint64_t random=151;
    auto next=[&](){random=random*2862933555777941757ULL+3037000493ULL;return random;};
    for(unsigned sample=0;sample<120;++sample) {
        Fixture f(3);f.lo={-1,-1,-1};f.hi={1,1,1};
        for(auto& coordinate:f.x)coordinate=int(next()%3)-1;
        for(unsigned clause=0;clause<3;++clause) {
            const size_t a=next()%3,b=next()%3;
            const int coefficient=int(next()%2)+1;
            std::vector<std::pair<size_t,__int128_t>> first={{a,coefficient}};
            if(a!=b)first.push_back({b,-coefficient});
            const __int128_t value=coefficient*(f.x[a]-(a!=b?f.x[b]:0));
            const int true_literal=f.literal(-value-int(next()%3),first);
            const size_t c=next()%3,d=next()%3;
            std::vector<std::pair<size_t,__int128_t>> other={{c,1}};
            if(c!=d)other.push_back({d,-1});
            const bool equal=next()%3==0;
            const int alternate=f.literal(int(next()%5)-2,other,equal);
            f.clause({true_literal,(next()%2)?alternate:-alternate});
        }
        check(hard(f.solver,f.x),"small random disjunctive system starts feasible");
        for(int target=-1;target<=1;++target) {
            if(target==f.x[0])continue;
            bool feasible=false;
            for(int a=-1;a<=1;++a)for(int b=-1;b<=1;++b)
                feasible|=hard(f.solver,Values({target,a,b}));
            nia::DisjunctiveMoveStatistics statistics;
            auto candidate=f.move({{0,target}},256,sample,&statistics);
            check((!candidate.empty())==feasible,
                  "bounded disjunctive search agrees with exhaustive small integer feasibility");
            check(statistics.graph_checks<=256&&statistics.unique_states<=4096,
                  "exhaustive comparison respects graph and visited-state limits");
            if(!candidate.empty()) {
                auto x=applied(f.x,candidate);
                check(x[0]==target&&hard(f.solver,x),"every comparison candidate satisfies all original disjunctions");
                for(size_t v=0;v<x.size();++v)
                    check(x[v]>=f.lo[v]&&x[v]<=f.hi[v],"comparison candidates respect every true bound");
            }
        }
    }
}
}

int main() {
    moving_false_branch_shifts_whole_chain(false,false);
    moving_false_branch_shifts_whole_chain(true,false);
    moving_false_branch_shifts_whole_chain(false,true);
    moving_false_branch_shifts_whole_chain(false,false,true);
    joint_product_objective_and_new_orientation();synchronized_old_true_choices_are_independent();
    bounds_budget_and_dedup();exhaustive_small_disjunctions();
    if(failures)return 1;
    std::cout<<"optimization disjunctive-difference checks passed\n";
}
