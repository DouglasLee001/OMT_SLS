#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include <gmpxx.h>
#include <cstdlib>
#include <iostream>
#include <set>

#include "../src/nia_omt_neighborhood.h"

namespace {
using Values=std::vector<__int128_t>;
using Move=std::vector<std::pair<size_t,__int128_t>>;
int failures=0;
void check(bool okay,const char* description) {
    if(!okay){std::cerr<<"FAIL: "<<description<<'\n';++failures;}
}
mpz_class integer(__int128_t value) {
    return mpz_class(nia::format_omt_integer(value),10);
}
mpz_class evaluate(const nia::ls_solver& solver,const nia::lit& literal,const Values& x) {
    mpz_class result=integer(literal.key);
    for(const auto& ct:literal.coff_terms) {
        mpz_class value=integer(ct.coff);
        for(const auto& f:solver._terms[ct.term_idx].var_epxs) {
            mpz_class power,base=integer(x[f.var_index]);
            mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),f.exponent);
            value*=power;
        }
        result+=value;
    }
    return result;
}
bool hard(const nia::ls_solver& solver,const Values& x) {
    for(const auto& clause:solver._clauses) {
        bool sat=false;
        for(int signed_id:clause.literals) {
            const auto& literal=solver._lits[std::abs(signed_id)];
            bool truth;
            if(literal.is_nia_lit) {
                mpz_class d=evaluate(solver,literal,x);
                truth=literal.is_equal?d==0:d<=0;
            } else truth=x[size_t(literal.delta)]>0;
            if(truth==(signed_id>0)){sat=true;break;}
        }
        if(!sat)return false;
    }
    return true;
}
Values applied(Values x,const Move& move) {
    for(const auto& entry:move)x[entry.first]=entry.second;
    return x;
}
struct Fixture {
    nia::ls_solver solver{1,0,true};
    Values x,lo,hi;
    Fixture(size_t count,const std::vector<std::pair<int,int>>& factors,
            __int128_t lower=0,__int128_t upper=1)
        :x(count,0),lo(count,lower),hi(count,upper) {
        solver._num_vars=count;solver._vars.resize(count);
        for(auto& var:solver._vars)var.is_nia=true;
        nia::term product{};
        for(auto f:factors){nia::var_exp power(f.first);power.exponent=f.second;product.var_epxs.push_back(power);}
        product.value=719;solver._terms.push_back(product);
        solver._lits.resize(2);
        auto& literal=solver._lits[1];literal.is_nia_lit=true;
        literal.is_equal=true;literal.key=0;literal.delta=123;literal.is_true=-1;
        literal.coff_terms.emplace_back(0,1);
        nia::clause required{};required.literals={-1};solver._clauses.push_back(required);
        bounds();
    }
    void bounds() {
        solver._clauses.resize(1);solver._lits.resize(2);solver._terms.resize(1);
        for(size_t v=0;v<x.size();++v) {
            nia::term t{};t.var_epxs.emplace_back(int(v));
            int ti=int(solver._terms.size());solver._terms.push_back(t);
            for(int direction:{-1,1}) {
                nia::lit l{};l.is_nia_lit=true;l.is_equal=false;
                l.key=direction<0?lo[v]:-hi[v];l.coff_terms.emplace_back(ti,direction);
                int id=int(solver._lits.size());solver._lits.push_back(l);
                nia::clause c{};c.literals={id};solver._clauses.push_back(c);
            }
        }
        solver._num_lits=solver._lits.size()-1;solver._num_clauses=solver._clauses.size();
    }
    std::vector<Move> moves(size_t limit=32,const std::vector<size_t>& clauses={0}) const {
        return nia::zero_product_moves(solver,x,lo,hi,clauses,limit);
    }
};

void atomic_escape_satisfies_actual_constraints() {
    Fixture f(3,{{0,1},{1,1},{2,1}});
    check(!hard(f.solver,f.x),"zero product starts infeasible");
    for(size_t v=0;v<3;++v){auto single=f.x;single[v]=1;check(!hard(f.solver,single),"no single-coordinate change satisfies xyz!=0");}
    const auto before=f.x;auto moves=f.moves();
    check(!moves.empty(),"atomic zero-product candidate exists");
    for(const auto& move:moves){check(move.size()==3,"all three zero factors change together");check(hard(f.solver,applied(f.x,move)),"atomic candidate satisfies actual product and bound clauses");}
    check(f.x==before,"candidate generation preserves source assignment");
    check(f.solver._terms[0].value==719&&f.solver._lits[1].delta==123,"candidate generation preserves search caches");
}
void bounds_and_nonzero_factors() {
    Fixture forced(2,{{0,1},{1,1}});forced.hi[0]=0;forced.bounds();
    check(forced.moves().empty(),"a bound forcing one factor to zero prevents an invalid partial move");
    Fixture clamped(2,{{0,1},{1,1}},2,5);auto moves=clamped.moves();
    check(!moves.empty(),"positive bounds choose bounded nonzero values beyond one");
    for(const auto& move:moves)check(hard(clamped.solver,applied(clamped.x,move)),"clamped nonzero values satisfy actual bounds");
    Fixture negative(2,{{0,1},{1,1}},-5,-2);moves=negative.moves();
    check(!moves.empty(),"negative-only bounds choose bounded nonzero values");
    for(const auto& move:moves)check(hard(negative.solver,applied(negative.x,move)),"negative candidates respect bounds");
    Fixture retained(3,{{0,1},{1,1},{2,1}});retained.x[2]=7;retained.lo[2]=retained.hi[2]=7;retained.bounds();
    moves=retained.moves();check(!moves.empty(),"two zero factors escape beside a nonzero factor");
    for(const auto& move:moves){check(move.size()==2,"nonzero factor is absent from the move");check(applied(retained.x,move)[2]==7&&hard(retained.solver,applied(retained.x,move)),"existing nonzero factor is retained");}
}
void powers_and_signs() {
    Fixture powers(2,{{0,2},{0,1},{1,3}});auto moves=powers.moves();
    check(!moves.empty(),"powers and repeated factors still admit atomic escape");
    for(const auto& move:moves)check(move.size()==2&&hard(powers.solver,applied(powers.x,move)),"one assignment per distinct zero variable handles powers");
    Fixture one(1,{{0,4}});check(one.moves().empty(),"one powered zero variable is not mistaken for several zero factors");
    Fixture satisfied(2,{{0,1},{1,1}});satisfied.solver._clauses[0].literals={1};
    check(satisfied.moves().empty(),"a true positive equality produces no escape candidates");
    satisfied.solver._lits[1].is_equal=false;
    check(satisfied.moves().empty(),"a true signed positive inequality is skipped");
    satisfied.solver._clauses[0].literals={-1};
    check(!satisfied.moves().empty(),"a false signed negative inequality is considered");
    satisfied.solver._clauses[0].literals={1};satisfied.solver._lits[1].key=1;satisfied.solver._lits[1].coff_terms[0].coff=-1;
    check(!satisfied.moves().empty(),"a false positive inequality is considered");
    Fixture invalid_power(2,{{0,0},{1,1}});check(invalid_power.moves().empty(),"nonpositive powers are not proposed");
}
void limits_and_duplicates() {
    Fixture f(3,{{0,1},{1,1},{2,1}},-1,1);
    f.solver._lits[1].coff_terms.emplace_back(0,1);
    auto moves=f.moves(32,{0,0,0});std::set<Move> unique(moves.begin(),moves.end());
    check(moves.size()==8&&unique.size()==8,"duplicate term occurrences and clauses produce unique moves");
    check(f.moves(0).empty(),"zero candidate budget is honored");
    check(f.moves(2).size()==2,"requested candidate budget is honored");
    Fixture six(6,{{0,1},{1,1},{2,1},{3,1},{4,1},{5,1}},-1,1);
    auto six_moves=six.moves(1000);
    check(six_moves.size()==32,"global candidate count is capped at thirty-two");
    Move all_negative;for(size_t v=0;v<6;++v)all_negative.push_back({v,-1});
    check(std::find(six_moves.begin(),six_moves.end(),all_negative)!=six_moves.end(),
        "bounded sign enumeration includes the uniform negative direction");
    Fixture seven(7,{{0,1},{1,1},{2,1},{3,1},{4,1},{5,1},{6,1}});
    check(seven.moves().empty(),"terms with more than six zero variables are skipped atomically");
    check(f.moves(32,{}).empty(),"no supplied unsatisfied clauses produces no candidates");
}
void exact_signed_literal_evaluation() {
    Fixture f(3,{{0,1},{1,1}});f.x[2]=__int128_t(1)<<20;f.lo[2]=f.hi[2]=f.x[2];f.bounds();
    nia::term square{};nia::var_exp z(2);z.exponent=2;square.var_epxs.push_back(z);
    int id=int(f.solver._terms.size());f.solver._terms.push_back(square);
    f.solver._lits[1].coff_terms.emplace_back(id,__int128_t(1)<<100);
    f.solver._lits[1].coff_terms.emplace_back(id,-(__int128_t(1)<<100));
    check(!hard(f.solver,f.x),"large cancelling terms leave the disequality false");
    auto moves=f.moves();check(!moves.empty(),"signed literal truth uses exact arithmetic beyond 128-bit intermediate products");
    for(const auto& move:moves)check(hard(f.solver,applied(f.x,move)),"large cancellation candidates satisfy actual clauses exactly");
}
}

int main() {
    atomic_escape_satisfies_actual_constraints();bounds_and_nonzero_factors();
    powers_and_signs();limits_and_duplicates();exact_signed_literal_evaluation();
    if(failures)return 1;
    std::cout<<"optimization zero-product neighborhood checks passed\n";
}
