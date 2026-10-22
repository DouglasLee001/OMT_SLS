// Small in-process semantic checks; no benchmark or external solver calls.
#include "../src/nia_ls.h"
#include "../src/nia_omt_input.h"
#include "../src/nia_lia_sls.h"
#include <gmpxx.h>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <map>
#include <sstream>
#include <limits>
#include <set>

#include "../src/nia_omt_bilinear.h"

namespace {
using Values=std::vector<__int128_t>;
int failures=0;
void check(bool okay,const char* text){if(!okay){++failures;std::cerr<<"FAIL: "<<text<<'\n';}}
mpz_class integer(__int128_t x){return mpz_class(nia::format_omt_integer(x),10);}
mpz_class polynomial(const nia::ls_solver& s,const nia::lit& p,const Values& x){
 mpz_class r=integer(p.key);
 for(const auto& ct:p.coff_terms){mpz_class t=integer(ct.coff);
  for(auto f:s._terms[ct.term_idx].var_epxs){mpz_class q;mpz_pow_ui(q.get_mpz_t(),integer(x[f.var_index]).get_mpz_t(),f.exponent);t*=q;}r+=t;}
 return r;
}
bool hard(const nia::ls_solver& s,const Values& x){
 for(const auto& c:s._clauses){bool sat=false;
  for(int id:c.literals){auto& p=s._lits[std::abs(id)];bool truth;
   if(p.is_nia_lit){auto q=polynomial(s,p,x);truth=p.is_equal?q==0:q<=0;}
   else truth=x[size_t(p.delta)]>0;
   sat|=truth==(id>0);}
  if(!sat)return false;}
 return true;
}
std::string snapshot(const nia::ls_solver& s){
 std::ostringstream out;out<<s._num_vars<<' '<<s._num_lits<<' '<<s._num_clauses<<' '<<s.omt_direction;
 for(auto& v:s._vars)out<<v.var_name<<' '<<v.is_nia;
 for(auto& t:s._terms){out<<nia::format_omt_integer(t.value);for(auto f:t.var_epxs)out<<f.var_index<<':'<<f.exponent<<',';}
 for(auto& l:s._lits){out<<l.is_nia_lit<<l.is_equal<<nia::format_omt_integer(l.key)<<nia::format_omt_integer(l.delta)<<l.is_true;for(auto ct:l.coff_terms)out<<ct.term_idx<<':'<<nia::format_omt_integer(ct.coff)<<',';}
 for(auto& c:s._clauses){out<<c.weight<<':'<<c.sat_count;for(int l:c.literals)out<<l<<',';}
 for(auto x:s._solution)out<<nia::format_omt_integer(x)<<',';
 return out.str();
}
struct Fixture {
 nia::ls_solver solver{7,0,true};Values seed,lo,hi;
 explicit Fixture(size_t count):seed(count,0),lo(count,-10),hi(count,10){
  solver._num_vars=count;solver._vars.resize(count);solver._lits.resize(1);solver.omt_direction="min";
  for(size_t v=0;v<count;++v){solver._vars[v].is_nia=true;solver._vars[v].var_name="v"+std::to_string(v);}
  solver._solution.assign(count,23);
  nia::term t{};t.var_epxs.emplace_back(0);solver._terms.push_back(t);solver.omt_objective_terms.emplace_back(0,1);
 }
 int literal(__int128_t key,const std::vector<std::pair<__int128_t,std::vector<std::pair<int,int>>>>& terms,bool equal=false){
  nia::lit p{};p.is_nia_lit=true;p.is_equal=equal;p.key=key;p.delta=123;p.is_true=-1;
  for(auto item:terms){nia::term t{};t.value=456;for(auto f:item.second){nia::var_exp ve(f.first);ve.exponent=f.second;t.var_epxs.push_back(ve);}int id=solver._terms.size();solver._terms.push_back(t);p.coff_terms.emplace_back(id,item.first);}
  solver._lits.push_back(p);solver._num_lits=solver._lits.size()-1;return solver._num_lits;
 }
 void clause(std::vector<int> ids){nia::clause c{};c.literals=std::move(ids);c.weight=19;c.sat_count=9;solver._clauses.push_back(c);solver._num_clauses=solver._clauses.size();}
 void bounds(size_t v,__int128_t low,__int128_t high){lo[v]=low;hi[v]=high;clause({literal(low,{{-1,{{int(v),1}}}})});clause({literal(-high,{{1,{{int(v),1}}}})});}
};

void coupled(bool maximize,bool huge=false,bool negative=false){
 Fixture f(3);f.solver.omt_direction=maximize?"max":"min";
 const __int128_t required=huge?(__int128_t(1)<<70):3;
 f.bounds(0,maximize?0:-1,maximize?1:0);
 f.bounds(1,negative?-1:0,negative?0:1);f.bounds(2,0,huge?required+5:10);
 int at_seed=f.literal(0,{{1,{{0,1}}}},true);
 int product=f.literal(required,{{negative?1:-1,{{1,1},{2,1}}}});
 f.clause({at_seed,product});
 check(hard(f.solver,f.seed),"coupled fixture begins at its own feasible incumbent");
 auto probe=f.seed;probe[0]=maximize?1:-1;
 check(!hard(f.solver,probe),"objective move alone cannot cross the zero product");
 for(size_t v=1;v<3;++v){auto one=probe;one[v]=1;check(!hard(f.solver,one),"either zero factor alone still blocks the objective probe");}
 const auto before=snapshot(f.solver);const auto oldseed=f.seed;const auto rng=f.solver.mt;
 nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,17);
 check(repair.supported(),"quadratic bipartite repair is supported");Values result;
 bool found=repair.resume_steps(24,3,3,result);
 check(found,"alternating block repair escapes a zero product using both coefficient and multiplier");
 if(found){check(result.size()==3&&hard(f.solver,result),"returned candidate satisfies the complete original hard formula");check(maximize?result[0]>0:result[0]<0,"candidate strictly improves its own min/max objective");for(size_t v=0;v<3;++v)check(result[v]>=f.lo[v]&&result[v]<=f.hi[v],"returned assignment respects exact caller bounds");check(result[1]!=0&&result[2]!=0,"both zero factors change jointly");if(huge)check(result[2]>__int128_t(INT64_MAX),"free LIA group can reach values beyond legacy int64 domains");}
 check(f.seed==oldseed&&snapshot(f.solver)==before&&f.solver.mt==rng,"source seed, topology, literal caches and caller RNG remain unchanged");
}

void no_partial_or_late_publication(){
 Fixture f(3);f.bounds(0,-1,0);f.bounds(1,0,0);f.bounds(2,0,10);
 int zero=f.literal(0,{{1,{{0,1}}}},true);int nonzero=f.literal(1,{{-1,{{1,1},{2,1}}}});f.clause({zero,nonzero});
 nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,5);Values result{42};
 check(!repair.resume_for(0,1,result)&&result==Values({42}),"zero slice cannot publish or change output");
 check(!repair.resume_for(1,0,result)&&result==Values({42}),"expired global budget cannot publish output");
 check(!repair.resume_steps(3,.05,.05,result)&&result==Values({42}),"an infeasible strict probe never publishes a partial candidate");
 check(!repair.resume_for(std::numeric_limits<double>::quiet_NaN(),1,result)&&result==Values({42}),"NaN budget cannot publish output");
}

void general_polynomial_escape(){
 for(int mode=0;mode<3;++mode){Fixture f(4);int zero=f.literal(0,{{1,{{0,1}}}},true);
  f.bounds(0,-1,0);for(size_t v=1;v<4;++v)f.bounds(v,0,1);
  if(mode==0){for(auto edge:std::vector<std::pair<int,int>>{{1,2},{2,3},{3,1}})f.clause({zero,f.literal(1,{{-1,{{edge.first,1},{edge.second,1}}}})});}
  if(mode==1)f.clause({zero,f.literal(1,{{-1,{{1,1},{2,1},{3,1}}}})});
  if(mode==2)f.clause({zero,f.literal(1,{{-1,{{1,2},{2,1}}}})});
  check(hard(f.solver,f.seed),"general polynomial escape starts from its own feasible incumbent");
  Values probe=f.seed;probe[0]=-1;check(!hard(f.solver,probe),"a pure objective probe cannot cross the general polynomial zero plateau");
  const auto before=snapshot(f.solver);const auto seed=f.seed;const auto rng=f.solver.mt;
  nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,9);Values result{77};
  check(repair.supported(),"odd-cycle, cubic and square formulas have a fixed-set linearization fallback");
  const bool found=repair.resume_steps(96,3,3,result);
  check(found,"general polynomial repair jointly escapes an actual hard-constraint zero plateau");
  if(found){check(hard(f.solver,result)&&result[0]<0,"general fallback returns only exact original feasible strict improvements");for(size_t v=0;v<result.size();++v)check(result[v]>=f.lo[v]&&result[v]<=f.hi[v],"general fallback obeys the true caller bounds");}
  check(f.seed==seed&&snapshot(f.solver)==before&&f.solver.mt==rng,"general fallback leaves original formula, own seed and caller RNG unchanged");
 }
}

void general_polynomial_objective_and_fixed_factors(){
 for(int mode=0;mode<2;++mode){Fixture f(3);f.solver.omt_direction="max";for(size_t v=0;v<3;++v)f.bounds(v,0,1);
  nia::term t{};nia::var_exp first(0);first.exponent=mode?1:2;t.var_epxs.push_back(first);
  if(mode){t.var_epxs.emplace_back(1);t.var_epxs.emplace_back(2);}
  const size_t id=f.solver._terms.size();f.solver._terms.push_back(t);f.solver.omt_objective_terms.clear();f.solver.omt_objective_terms.emplace_back(id,1);
  nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,21);Values result;
  check(repair.supported(),"nonlinear higher-degree objectives participate in fallback covers");
  const bool found=repair.resume_steps(96,3,3,result);check(found,"general fallback can strictly improve square and cubic max objectives");
  if(found)check(hard(f.solver,result)&&result[0]>0&&(!mode||(result[1]>0&&result[2]>0)),"nonlinear objective candidate is evaluated exactly");
 }
 for(bool zero: {false,true}){Fixture f(3);f.bounds(0,-1,0);f.seed[1]=zero?0:2;f.bounds(1,f.seed[1],f.seed[1]);f.bounds(2,0,1);
  int ownzero=f.literal(0,{{1,{{0,1}}}},true);
  if(zero)f.clause({f.literal(0,{{1,{{1,1},{2,1000}}}},true)});
  else f.clause({ownzero,f.literal(1,{{-1,{{1,65},{2,1}}}})});
  nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,11);Values result;
  check(repair.supported(),"true fixed high powers and zero factors are eliminated before the residual degree cap");
  const bool found=repair.resume_steps(16,2,2,result);check(found,"fixed-factor simplification preserves an exact feasible escape");
  if(found)check(hard(f.solver,result)&&result[0]<0,"fixed-factor fallback does not drop a nonzero term or invent a candidate");
 }
 for(int mode=0;mode<3;++mode){Fixture f(2);
  if(mode==0)f.clause({f.literal(0,{{1,{{1,65}}}},true)});
  if(mode==1)f.clause({f.literal(0,{{1,{{1,0}}}},true)});
  if(mode==2)f.clause({f.literal(0,{{1,{{2,1}}}},true)});
  nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,0);Values result{71};
  check(!repair.supported()&&!repair.resume_steps(1,1,1,result)&&result==Values({71}),"residual degree overflow and malformed factors conservatively decline without output mutation");
 }
}

void polynomial_cover_properties(){
 using Factors=nia::bilinear_detail::MonomialFactors;
 auto validate=[&](const std::vector<Factors>& terms,const Values& lo,const Values& hi,const std::vector<size_t>& objective,unsigned seed){
  std::mt19937 rng(seed),repeat(seed);const auto covers=nia::bilinear_detail::polynomial_cover_portfolio(terms,lo,hi,objective,rng);
  check(!covers.empty()&&covers.size()<=32,"general polynomial has a nonempty bounded linearizing cover portfolio");
  check(covers==nia::bilinear_detail::polynomial_cover_portfolio(terms,lo,hi,objective,repeat)&&rng==repeat,"general covers and private RNG are reproducible");
  std::set<std::vector<size_t>> unique;
  for(const auto& cover:covers){std::set<size_t> fixed(cover.begin(),cover.end());check(fixed.size()==cover.size()&&unique.insert(cover).second,"general covers have no duplicate variables or repeated fixed sets");
   for(size_t v:cover)check(v<lo.size()&&lo[v]!=hi[v],"generic cover anchors only valid nonconstant variables");
   for(const auto& term:terms){bool zero=false;unsigned free_degree=0;for(auto factor:term){if(lo[factor.first]==hi[factor.first])zero|=lo[factor.first]==0;else if(!fixed.count(factor.first))free_degree+=factor.second;}check(zero||free_degree<=1,"every monomial linearizes after fixing its cover and actual constant domains");}
  }return covers;
 };
 Values lo(6,0),hi(6,1);
 const auto covers=validate({{{0,1},{1,1}},{{1,1},{2,1}},{{2,1},{0,1}},{{0,2},{1,1},{2,1}},{{3,1},{4,1},{5,1}}},lo,hi,{4},17);
 if(!covers.empty())check(std::find(covers[0].begin(),covers[0].end(),4)==covers[0].end(),"the first greedy cover prefers to leave a feasible objective variable free");
 std::mt19937 data(884);
 for(unsigned sample=0;sample<40;++sample){std::vector<Factors> terms;Values lower(6,-1),upper(6,1);if(sample%3==0)lower[sample%6]=upper[sample%6]=sample%2;
  for(unsigned t=0;t<5;++t){Factors f;for(size_t v=0;v<6;++v)if(data()%3==0)f.push_back({v,1+data()%3});terms.push_back(f);}validate(terms,lower,upper,{sample%6},sample);
 }
 std::mt19937 rng(1);check(nia::bilinear_detail::polynomial_cover_portfolio({{{0,65}}},lo,hi,{},rng).empty(),"cover helper rejects an unfixed factor beyond the supported exponent cap");
 check(nia::bilinear_detail::polynomial_cover_portfolio({{{0,33},{1,32}}},lo,hi,{},rng).empty(),"cover helper rejects total residual degree overflow");
 check(nia::bilinear_detail::polynomial_cover_portfolio({{{0,0}}},lo,hi,{},rng).empty(),"cover helper rejects nonpositive powers");
 check(nia::bilinear_detail::polynomial_cover_portfolio({{{6,1}}},lo,hi,{},rng).empty(),"cover helper rejects out-of-range factors");
 lo[0]=hi[0]=0;check(!nia::bilinear_detail::polynomial_cover_portfolio({{{0,1},{1,1000}}},lo,hi,{},rng).empty(),"a true zero factor eliminates an otherwise oversized monomial");
 check(nia::bilinear_detail::polynomial_cover_portfolio({},lo,hi,{},rng,0).empty(),"general cover construction respects a zero portfolio budget");
}
void own_reference_validation(){
 Fixture f(1);f.bounds(0,-1,0);Values result{33};
 nia::OmtBilinearRepair wrong(f.solver,f.seed,"1",f.lo,f.hi,0);
 check(!wrong.supported()&&!wrong.resume_steps(1,.1,.1,result)&&result==Values({33}),"declared incumbent must equal the exact objective of its own seed");
 nia::OmtBilinearRepair valid(f.solver,f.seed,"0",f.lo,f.hi,0);
 check(valid.supported()&&valid.resume_steps(2,1,1,result)&&result==Values({-1}),"a linear objective improves with no historical target or opponent input");
}
void finite_block_cartesian_coverage(){
 for(auto widths:std::vector<std::vector<unsigned>>{{3,9},{3,3,3}}){
  Values seed(widths.size(),0),lo(widths.size(),0),hi;std::vector<size_t> vars;uint64_t product=1;
  for(size_t v=0;v<widths.size();++v){hi.push_back(widths[v]-1);vars.push_back(v);product*=widths[v];}
  std::set<Values> seen;
  for(uint64_t variant=1;variant<=product+2;++variant){auto candidate=nia::bilinear_detail::finite_block_trial(seed,lo,hi,vars,variant);seen.insert(candidate);for(size_t v=0;v<widths.size();++v)check(candidate[v]>=lo[v]&&candidate[v]<=hi[v],"finite block enumeration respects each actual domain");}
  check(seen.size()==product,"finite block covers the complete heterogeneous Cartesian product before rotating");
  check(seen.count(hi)==1,"finite block includes the all-upper-bound tuple");
  check(seed==Values(widths.size(),0),"finite block enumeration leaves its seed unchanged");
 }
}
void huge_finite_budget(){
 Fixture f(1);f.bounds(0,-1,0);Values result;
 nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,0);
 check(repair.resume_steps(1,1e300,1e300,result)&&result==Values({-1}),"huge positive finite budgets saturate without overflowing the clock");
}
void boolean_and_signed_equality(){
 Fixture f(4);f.bounds(0,-1,0);f.bounds(1,0,1);f.bounds(2,0,1);
 f.solver._vars[3].is_nia=false;f.seed[3]=-1;f.lo[3]=-1;f.hi[3]=1;
 nia::lit boolean{};boolean.is_nia_lit=false;boolean.delta=3;
 f.solver._lits.push_back(boolean);f.solver._num_lits=f.solver._lits.size()-1;const int b=f.solver._num_lits;
 const int ownzero=f.literal(0,{{1,{{0,1}}}},true);
 const int productzero=f.literal(0,{{1,{{1,1},{2,1}}}},true);
 f.clause({ownzero,b});f.clause({-b,-productzero});
 nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,1);Values result;
 const bool found=repair.resume_steps(24,3,3,result);
 check(found,"native Boolean guards and a negated polynomial equality permit repair");
 if(found)check(hard(f.solver,result)&&result[0]<0&&result[3]==1&&result[1]*result[2]!=0,"signed equality and Boolean truth are validated against source semantics");
}
void exact_objective_expressions(){
 Fixture product(2);product.solver.omt_direction="max";
 product.bounds(0,0,1);product.bounds(1,0,1);
 nia::term term{};term.var_epxs.emplace_back(0);term.var_epxs.emplace_back(1);
 const size_t id=product.solver._terms.size();product.solver._terms.push_back(term);
 product.solver.omt_objective_terms.clear();product.solver.omt_objective_terms.emplace_back(id,1);
 nia::OmtBilinearRepair nonlinear(product.solver,product.seed,"0",product.lo,product.hi,4);Values result;
 check(nonlinear.supported()&&nonlinear.resume_steps(24,3,3,result)&&hard(product.solver,result)&&result[0]*result[1]>0,"a bilinear objective participates in the cover and its strict linear subproblem");
 Fixture wide(1);wide.bounds(0,-1,0);
 __int128_t minimum;check(nia::parse_omt_integer("-170141183460469231731687303715884105728",minimum),"exact-objective boundary fixture parses");
 wide.solver.omt_objective_constant=minimum;
 nia::OmtBilinearRepair exact(wide.solver,wide.seed,nia::format_omt_integer(minimum),wide.lo,wide.hi,0);
 result.clear();check(exact.resume_steps(2,1,1,result)&&result==Values({-1}),"GMP objective cuts can improve below the signed 128-bit objective range");
}
void bounded_neutral_history(){
 nia::bilinear_detail::PrivateNeutralHistory history(2);history.reset(Values({-1,0}));
 check(history.retain(Values({-1,1}),false,true),"a distinct equal-score partial crosses the old strict-score plateau");
 check(!history.retain(Values({-1,0}),false,true),"neutral history rejects a short cycle to the initial state");
 check(!history.retain(Values({-1,1}),false,true),"neutral history rejects an identical repeated partial");
 check(!history.retain(Values({-1,7}),false,false),"a worse score or different own-bound state cannot enter neutral history");
 check(history.retain(Values({-1,2}),false,true),"a second novel neutral state fits the epoch budget");
 check(!history.retain(Values({-1,3}),false,true),"neutral acceptance stops at the configured per-level bound");
 check(history.retain(Values({-1,4}),true,false),"strict score improvement starts a fresh epoch");
 check(history.retain(Values({-1,0}),false,true),"strict improvement clears prior-level novelty history and budget");
 check(history.neutral_acceptances()==3,"neutral counters persist across score-improvement epochs");
}
void production_neutral_across_calls(){
 Fixture f(4);f.bounds(0,-1,0);f.bounds(1,0,1);f.bounds(2,0,1);
 f.solver._vars[3].is_nia=false;f.seed[3]=-1;f.lo[3]=-1;f.hi[3]=1;
 nia::lit boolean{};boolean.is_nia_lit=false;boolean.delta=3;f.solver._lits.push_back(boolean);f.solver._num_lits=f.solver._lits.size()-1;const int b=f.solver._num_lits;
 const int ownzero=f.literal(0,{{1,{{0,1}}}},true),productzero=f.literal(0,{{1,{{1,1},{2,1}}}},true);
 f.clause({ownzero,b});f.clause({ownzero,-b});f.clause({ownzero,productzero});
 check(hard(f.solver,f.seed),"plateau fixture starts from its own feasible source model");
 nia::OmtBilinearRepair repair(f.solver,f.seed,"0",f.lo,f.hi,7);Values result{81};
 check(!repair.resume_steps(4,1,1,result)&&result==Values({81}),"infeasible own strict probe stays private during first round slice");
 const auto neutrals=repair.neutral_moves_retained();
 check(neutrals>0,"production class retains a different partial on its one-unsat log(2) plateau");
 check(!repair.resume_steps(4,1,1,result)&&result==Values({81}),"a second call cannot publish a neutral partial as SAT");
 check(repair.rounds_completed()==8&&repair.neutral_moves_retained()>=neutrals,"round and novelty state persist across resume calls");
}
std::pair<bool,bool> seeded_lia_choice(unsigned seed,bool explicit_seed){
 z3::context context;auto a=context.bool_const("seed_a"),b=context.bool_const("seed_b");
 auto solver=explicit_seed?std::make_unique<nia::LiaSolver>(context,seed):std::make_unique<nia::LiaSolver>(context);
 solver->add(a||b);solver->add(!a||!b);solver->set("timeout",1000);
 const auto status=solver->check();check(status==z3::sat,"seed fixture has an exactly one Boolean native SLS solution");
 if(status!=z3::sat)return {};
 auto model=solver->get_model();return {model.eval(a,true).is_true(),model.eval(b,true).is_true()};
}
void explicit_lia_seed(){
 const unsigned previous=nia::lia_seed;nia::lia_seed=83;
 check(seeded_lia_choice(0,false)==seeded_lia_choice(83,true),"default constructor retains the global lia_seed behavior");
 std::set<std::pair<bool,bool>> choices;
 for(unsigned seed=0;seed<16;++seed){const auto first=seeded_lia_choice(seed,true);check(first==seeded_lia_choice(seed,true),"an explicit native SLS seed reproduces its search choice");choices.insert(first);}
 check(choices.size()>1,"different explicit SLS seeds can escape a repeated deterministic prefix");
 check(nia::lia_seed==83,"explicit solver seeds do not overwrite global lia_seed");nia::lia_seed=previous;
}
void complete_mixed_component_covers(){
 using Component=nia::bilinear_detail::BipartiteComponent;
 std::vector<Component> components(2);components[0].colors[0]={0};components[0].colors[1]={1,2};components[1].colors[0]={3};components[1].colors[1]={4};
 std::mt19937 rng(37),repeat(37);
 const auto covers=nia::bilinear_detail::component_cover_portfolio(components,rng);
 check(covers==nia::bilinear_detail::component_cover_portfolio(components,repeat)&&rng==repeat,"component cover portfolio is reproducible from its own RNG");
 check(covers==std::vector<std::vector<size_t>>({{0,3},{1,2,4},{1,2,3},{0,4}}),"legacy bipartite portfolio keeps its exact ordered cover prefix");
 std::mt19937 prefix_rng=rng;check(prefix_rng()==597971307u&&prefix_rng()==1993286476u&&prefix_rng()==2250930427u&&prefix_rng()==828048348u,"legacy bipartite portfolio keeps its exact RNG state");
 check(covers.size()<=32&&covers.size()>=4,"two components include both mixed orientations in the bounded portfolio");
 std::set<std::set<size_t>> patterns;
 for(const auto& cover:covers){std::set<size_t> fixed(cover.begin(),cover.end());patterns.insert(fixed);
  check(fixed.size()==cover.size(),"a component cover never duplicates anchored variables");
  for(auto edge:std::vector<std::pair<size_t,size_t>>{{0,1},{0,2},{3,4}})check(fixed.count(edge.first)||fixed.count(edge.second),"every nonlinear edge remains covered under mixed component orientation");
  for(const auto& component:components){bool class0=true,class1=true;for(size_t v:component.colors[0]){class0&=fixed.count(v);class1&=!fixed.count(v);}for(size_t v:component.colors[1]){class1&=fixed.count(v);class0&=!fixed.count(v);}check(class0||class1,"each complete component fixes exactly one whole color class");}
 }
 check(covers.size()>=2&&std::set<size_t>(covers[0].begin(),covers[0].end())==std::set<size_t>({0,3})&&std::set<size_t>(covers[1].begin(),covers[1].end())==std::set<size_t>({1,2,4}),"the first two covers retain both original global orientations");
 check(patterns.count({0,4})&&patterns.count({1,2,3}),"mixed covers expose both combinations of free groups unavailable to global flips");
 std::mt19937 small(9);check(nia::bilinear_detail::component_cover_portfolio(components,small,2).size()==2,"cover portfolio obeys its explicit construction bound");
}
void wide_anchor_minimum(bool centered,bool negative=false){
 Fixture f(2);f.bounds(0,-100,100);f.bounds(1,-100,100);
 const __int128_t root=centered?(negative?-33:33):0;
 f.seed={root+1,root+2};
 f.solver._terms[0].var_epxs[0].exponent=2;
 f.solver.omt_objective_constant=root*root;
 if(root){nia::term linear{};linear.var_epxs.emplace_back(0);const size_t id=f.solver._terms.size();f.solver._terms.push_back(linear);f.solver.omt_objective_terms.emplace_back(id,-2*root);}
 f.clause({f.literal(-1,{{1,{{1,1}}},{-1,{{0,1}}}},true)});
 check(hard(f.solver,f.seed),"wide-anchor fixture has its own exact feasible incumbent");
 nia::OmtBilinearRepair repair(f.solver,f.seed,"1",f.lo,f.hi,17);Values result{77};
 const bool found=repair.resume_steps(24,3,3,result);
 check(repair.supported()&&found,"wide-anchor portfolio reaches zero and an incumbent-centered quadratic minimum");
 if(found)check(result==Values({root,root+1})&&hard(f.solver,result),"centered anchor improvement repairs the coupled free variable and satisfies all original constraints");
}
void anchored_probe_contract(){
 const Values seed={2,1,3,7,-40},incumbent={2,1,3,30,90};
 const Values lo={-2,-1,0,-1000,-1000},hi={2,1,3,1000,1000};
 const std::vector<size_t> finite={0,1,2},wide={3,4};
 for(uint64_t old=1;old<=320;++old){
  auto expected=nia::bilinear_detail::finite_block_trial(seed,lo,hi,finite,old);
  const size_t v=wide[((old-1)/2)%wide.size()];
  mpz_class q=old%2?1:-1;q*=mpz_class(1)<<std::min(uint64_t(80),(old-1)/2/wide.size());
  q=std::max(integer(lo[v]),std::min(integer(hi[v]),q));
  check(nia::parse_omt_integer(q.get_str(),expected[v]),"legacy probe expectation remains representable");
  const uint64_t added=(old-1)/8*11+(old-1)%8+1;
  check(nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,finite,wide,added)==expected,"wide-anchor additions preserve every legacy absolute and finite-block probe in order");
  check(nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,finite,{},old)==nia::bilinear_detail::finite_block_trial(seed,lo,hi,finite,old),"a finite-only cover preserves the original complete Cartesian sequence");
 }
 auto zero=nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,{},wide,9);
 auto plus=nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,{},wide,10);
 auto minus=nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,{},wide,11);
 check(zero[3]==0&&plus[3]==31&&minus[3]==29&&plus[4]==seed[4],"supplemental probes use the own incumbent while preserving other private working coordinates");
 check(nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,{},wide,21)[4]==91&&nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,{},wide,32)[3]==32,"incumbent probes visit every wide anchor before doubling their radius");
 __int128_t low,high;nia::parse_omt_integer("-170141183460469231731687303715884105728",low);nia::parse_omt_integer("170141183460469231731687303715884105727",high);
 Values full_lo={low},full_hi={high},big={__int128_t(1)<<100};
 check(nia::bilinear_detail::anchored_trial(big,big,full_lo,full_hi,{},{0},10)[0]==big[0]+1&&nia::bilinear_detail::anchored_trial(big,big,full_lo,full_hi,{},{0},11)[0]==big[0]-1,"incumbent neighbors remain exact above the int64 and double ranges");
 check(nia::bilinear_detail::anchored_trial(Values{high},Values{high},full_lo,full_hi,{},{0},10)==Values{high}&&nia::bilinear_detail::anchored_trial(Values{low},Values{low},full_lo,full_hi,{},{0},11)==Values{low},"signed int128 endpoints clamp without overflowing a centered addition");
 const auto last=nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,finite,wide,UINT64_MAX);
 for(size_t v=0;v<last.size();++v)check(last[v]>=lo[v]&&last[v]<=hi[v],"the largest variant retains true domain bounds");
 check(nia::bilinear_detail::anchored_trial(seed,{},lo,hi,finite,wide,10)==seed&&nia::bilinear_detail::anchored_trial(seed,incumbent,lo,hi,finite,{99},10)==seed,"invalid probe dimensions and indices leave the private assignment unchanged");
}
}
int main(){coupled(false);coupled(true);coupled(false,true);coupled(false,false,true);no_partial_or_late_publication();general_polynomial_escape();general_polynomial_objective_and_fixed_factors();polynomial_cover_properties();own_reference_validation();finite_block_cartesian_coverage();huge_finite_budget();boolean_and_signed_equality();exact_objective_expressions();bounded_neutral_history();production_neutral_across_calls();explicit_lia_seed();complete_mixed_component_covers();wide_anchor_minimum(false);wide_anchor_minimum(true);wide_anchor_minimum(true,true);anchored_probe_contract();if(failures)return 1;std::cout<<"PASS native alternating bilinear repair semantic checks\n";}
