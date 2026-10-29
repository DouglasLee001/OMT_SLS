#include "../src/nia_omt_cutset.h"
#include <algorithm>
#include <chrono>
#include <limits>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void finite_bilinear_unsat() {
    z3::context c; auto x=c.int_const("x"),y=c.int_const("y");
    z3::expr_vector f(c); f.push_back(x>=2);f.push_back(x<=4);
    f.push_back(y>=3);f.push_back(y<=7);f.push_back(x*y<6);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"finite bilinear cover must prove UNSAT");
}
void restores_fixed_symbols_in_sat_model() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),cost=c.int_const("cost");
    auto b=c.bool_const("branch");z3::expr_vector f(c);
    f.push_back(x>=-2);f.push_back(x<=2);f.push_back(y>=1);f.push_back(y<=3);
    f.push_back(cost==x*y);f.push_back(cost<-2);f.push_back(b||(x==2));
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::sat&&r.model,"finite SAT leaf must return its model");
    for(auto e:f)check(r.model->eval(e,true).is_true(),"SAT source model must satisfy every original assertion");
    check(r.model->eval(x,true).is_numeral()&&r.model->eval(y,true).is_numeral(),"fixed source symbols must have values");
}
void simplex_pruning_preserves_large_cartesian_cover() {
    z3::context c;z3::expr_vector vars(c),f(c);auto sum=c.int_val(0),objective=c.int_val(0);
    for(unsigned i=0;i<10;++i){vars.push_back(c.int_const(("n"+std::to_string(i)).c_str()));
        f.push_back(vars[i]>=0);f.push_back(vars[i]<=5);sum=sum+vars[i];}
    for(unsigned i=0;i<10;++i)for(unsigned j=i+1;j<10;++j)objective=objective+vars[i]*vars[j];
    f.push_back(sum==5);f.push_back(objective>10);
    const auto r=nia::solve_omt_finite_cutset(c,f,20);
    check(r.applicable&&r.answer==z3::unsat,"sum-pruned finite cover must prove the multi-affine optimum");
    check(r.branches<=2002,"linear sum pruning must retain a 6^9 raw cover with only 2002 feasible prefixes");
}
void strict_negative_bounds_are_rounded_exactly() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y");z3::expr_vector f(c);
    f.push_back(2*x>-7);f.push_back(3*x<-2);f.push_back(y==1);f.push_back(x*x<1);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"strict bounds with negative division must round outward correctly");
}
void disjunction_does_not_give_unconditional_bounds() {
    z3::context c;auto x=c.int_const("x");auto b=c.bool_const("b");z3::expr_vector f(c);
    f.push_back((x<=1)||b);f.push_back((x>=0)||b);f.push_back(x*x>4);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(!r.applicable&&r.answer==z3::unknown,"disjunctive bounds cannot restrict the cover");
}
void unbounded_factor_can_remain_linear() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y");z3::expr_vector f(c);
    f.push_back(x>=1);f.push_back(x<=2);f.push_back(y>=3);f.push_back(x*y<3);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"bounded cutset may leave an unbounded linear variable");
}
void exhausted_budget_is_never_a_proof() {
    z3::context c;auto x=c.int_const("x");z3::expr_vector f(c);
    f.push_back(x>=0);f.push_back(x<=2);f.push_back(x*x<0);
    const auto r=nia::solve_omt_finite_cutset(c,f,0);
    check(r.answer==z3::unknown&&!r.model,"zero allowance cannot authorize UNSAT or SAT");
}
void degree_cover_handles_powers_and_three_factors() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");
    z3::expr_vector f(c);for(auto v:{x,y,z}){f.push_back(v>=1);f.push_back(v<=2);}
    f.push_back(x*y*z<1);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"three-factor terms require enough fixed variables to become linear");
    z3::expr_vector g(c);g.push_back(x>=1);g.push_back(x<=2);g.push_back(y>=1);g.push_back(x*y*y<1);
    const auto s=nia::solve_omt_finite_cutset(c,g,5);
    check(!s.applicable&&s.answer==z3::unknown,"fixing x does not eliminate the unbounded square in x*y*y");
}
void accepts_persistent_infinite_allowance() {
    z3::context c;auto x=c.int_const("x");z3::expr_vector f(c);
    f.push_back(x>=-2);f.push_back(x<=2);f.push_back(x*x<0);
    const auto r=nia::solve_omt_finite_cutset(c,f,std::numeric_limits<double>::infinity());
    check(r.applicable&&r.answer==z3::unsat,"persistent parent-controlled workers accept infinite internal allowance");
}
void negative_boundary_sat_is_not_pruned() {
    z3::context c;auto x=c.int_const("x");z3::expr_vector f(c);
    f.push_back(!(2*x<=-5));f.push_back(x<0);f.push_back(x*x>3);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::sat&&r.model,"negated strict bounds must retain the feasible negative endpoint");
    check(r.model->eval(x,true).get_numeral_int()==-2,"negative endpoint must be restored exactly");
}
void polynomial_covers_match_independent_enumeration() {
    for(int seed=0;seed<16;++seed){
        const int a=seed%5-2,b=seed%7-3,d=seed%3-1,e=seed%4-2;
        int best=-100000;
        for(int x=0;x<=2;++x)for(int y=0;y<=2;++y)for(int z=0;z<=2;++z)
            best=std::max(best,a*x*x+b*x*y+d*y*z+e*x*y*z);
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");
        auto value=a*x*x+b*x*y+d*y*z+e*x*y*z;
        for(bool satisfiable:{false,true}){
            z3::expr_vector f(c);for(auto v:{x,y,z}){f.push_back(v>=0);f.push_back(v<=2);}
            f.push_back(satisfiable?value>=best:value>best);
            const auto r=nia::solve_omt_finite_cutset(c,f,5);
            if(!r.applicable||r.answer!=(satisfiable?z3::sat:z3::unsat))
                std::cerr<<"seed="<<seed<<" sat="<<satisfiable<<" expected="<<best
                         <<" answer="<<r.answer<<" applicable="<<r.applicable<<" reason="<<r.reason<<'\n';
            check(r.applicable&&r.answer==(satisfiable?z3::sat:z3::unsat),"finite polynomial cover must agree with exhaustive integer truth");
            if(r.model)for(auto atom:f)check(r.model->eval(atom,true).is_true(),"enumeration SAT model must satisfy original polynomial");
        }
    }
}
void linear_propagation_matches_independent_enumeration() {
    for(int seed=0;seed<48;++seed){
        const int a=seed%5-2,b=(seed/2)%5-2,d=(seed/3)%5-2,rhs=seed%7-3;
        int best=-100000;
        for(int x=-2;x<=2;++x)for(int y=-2;y<=2;++y)for(int z=-2;z<=2;++z)
            if(a*x+b*y+d*z<rhs&&x-y>=-1&&((y+z<=1)||(x>0)))best=std::max(best,x*x-x*y+y*z);
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");
        for(bool at_optimum:{false,true}){
            z3::expr_vector f(c);for(auto v:{x,y,z}){f.push_back(v>=-2);f.push_back(v<=2);}
            f.push_back(a*x+b*y+d*z<rhs);f.push_back(x-y>=-1);f.push_back((y+z<=1)||(x>0));
            auto objective=x*x-x*y+y*z;f.push_back(at_optimum?objective>=best:objective>best);
            const auto r=nia::solve_omt_finite_cutset(c,f,5);
            const bool sat=at_optimum&&best!=-100000;
            if(!r.applicable||r.answer!=(sat?z3::sat:z3::unsat))
                std::cerr<<"linear seed="<<seed<<" optimum="<<at_optimum<<" answer="<<r.answer<<" reason="<<r.reason<<'\n';
            check(r.applicable&&r.answer==(sat?z3::sat:z3::unsat),"mixed-sign linear pruning must match exhaustive integer truth");
            if(r.model)for(auto atom:f)check(r.model->eval(atom,true).is_true(),"pruned SAT model must satisfy every original row");
        }
    }
}
void propagated_domains_need_no_explicit_upper_bounds() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");z3::expr_vector f(c);
    for(auto v:{x,y,z})f.push_back(v>=0);
    f.push_back(x+y+z==4);f.push_back(x*y+x*z+y*z>5);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"entailed sum bounds must supply the finite cutset domains");
}
void large_exact_endpoints_do_not_overflow() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y");
    auto base=c.int_val("1000000000000000000000000000000");z3::expr_vector f(c);
    f.push_back(x>=base);f.push_back(x<=base+2);f.push_back(y>=-1);f.push_back(y<=1);f.push_back((x-base)*y>2);
    const auto r=nia::solve_omt_finite_cutset(c,f,5);
    check(r.applicable&&r.answer==z3::unsat,"small domains at arbitrary-precision endpoints stay exact");
}
void expensive_binary_cover_falls_back() {
    z3::context c;z3::expr_vector vars(c),f(c);auto objective=c.int_val(0);
    for(unsigned i=0;i<80;++i){vars.push_back(c.int_const(("b"+std::to_string(i)).c_str()));f.push_back(vars[i]>=0);f.push_back(vars[i]<=1);}
    for(unsigned i=1;i<80;++i)objective=objective+vars[i-1]*vars[i];
    f.push_back(objective>0);
    const auto r=nia::solve_omt_finite_cutset(c,f,2);
    check(!r.applicable&&r.answer==z3::unknown,"an impractical binary cover must fall back before proof traversal");
}
void one_sided_bound_cycle_returns_promptly() {
    z3::context c;auto x=c.int_const("x"),y=c.int_const("y");z3::expr_vector f(c);
    f.push_back(x<=0);f.push_back(x-y<=-1);f.push_back(y-x<=0);f.push_back(x*x>1);
    const auto began=std::chrono::steady_clock::now();
    const auto r=nia::solve_omt_finite_cutset(c,f,2);
    const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    check(!r.applicable&&r.answer==z3::unknown&&elapsed<1,"one-sided propagation cycle must not consume the proof allowance");
}
}
int main(){try{
    finite_bilinear_unsat();restores_fixed_symbols_in_sat_model();
    simplex_pruning_preserves_large_cartesian_cover();strict_negative_bounds_are_rounded_exactly();
    disjunction_does_not_give_unconditional_bounds();unbounded_factor_can_remain_linear();
    exhausted_budget_is_never_a_proof();
    degree_cover_handles_powers_and_three_factors();accepts_persistent_infinite_allowance();
    negative_boundary_sat_is_not_pruned();polynomial_covers_match_independent_enumeration();
    linear_propagation_matches_independent_enumeration();propagated_domains_need_no_explicit_upper_bounds();
    large_exact_endpoints_do_not_overflow();
    expensive_binary_cover_falls_back();one_sided_bound_cycle_returns_promptly();
    std::cout<<"finite cutset proof tests passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
