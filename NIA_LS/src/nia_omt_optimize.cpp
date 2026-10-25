#include "nia_omt_optimize.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include "nia_omt_repair.h"
#include "nia_omt_bilinear.h"
#include "nia_omt_neighborhood.h"
#include "nia_omt_difference.h"
#include "nia_omt_disjunctive.h"
#include "nia_omt_feasibility.h"
#include "nia_omt_large_target.h"
#include "nia_omt_unbounded.h"
#include "nia_omt_optimality.h"
#include "nia_linear_initialize.h"
#include <gmpxx.h>
#include <set>
#include <numeric>
#include <cmath>
#include <iomanip>
#include <sstream>
#include <unordered_map>
#include <functional>
#include <memory>
#include <thread>

namespace nia { namespace {
using Int = mpz_class;
using Assignment = std::vector<__int128_t>;
using Move = std::vector<std::pair<size_t,__int128_t>>;
Int integer(__int128_t v) { return Int(format_omt_integer(v)); }
std::string quote(const std::string& s) {
    std::ostringstream out; out << '"';
    for (unsigned char c:s) {
        if(c=='"'||c=='\\') out << '\\' << char(c);
        else if(c<32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else out << char(c);
    }
    out << '"'; return out.str();
}
Int power(const Int& x, unsigned p) { Int r; mpz_pow_ui(r.get_mpz_t(),x.get_mpz_t(),p); return r; }
Int monomial(const term& t,const Assignment& x) {
    Int r=1; for(auto& f:t.var_epxs) r*=power(integer(x[f.var_index]),f.exponent); return r;
}
// Independent full recomputation: no optimization delta caches or legacy
// floating point powers are trusted when accepting an incumbent.
Int validate(const ls_solver& s,const Assignment& x) {
    if(x.size()!=s._num_vars) throw std::runtime_error("invalid assignment size");
    std::vector<Int> terms; for(auto& t:s._terms) terms.push_back(monomial(t,x));
    for(size_t v=0;v<x.size();++v)
        if(!s._vars[v].is_nia && x[v]!=1 && x[v]!=-1) throw std::runtime_error("invalid Boolean assignment");
    for(size_t c=0;c<s._clauses.size();++c) {
        bool sat=false;
        for(int id:s._clauses[c].literals) {
            auto& l=s._lits[std::abs(id)]; bool truth;
            if(l.is_nia_lit) {
                Int d=integer(l.key); for(auto& ct:l.coff_terms) d+=integer(ct.coff)*terms[ct.term_idx];
                truth=l.is_equal?d==0:d<=0;
            } else truth=x[size_t(l.delta)]>0;
            if(truth==(id>0)) { sat=true; break; }
        }
        if(!sat) throw std::runtime_error("candidate failed exact validation at clause "+std::to_string(c));
    }
    Int objective=integer(s.omt_objective_constant);
    for(auto& ct:s.omt_objective_terms) objective+=integer(ct.coff)*terms[ct.term_idx];
    return objective;
}

struct State {
    ls_solver& s;
    Assignment x,lo,hi;
    std::vector<Int> terms,delta;
    std::vector<bool> truth;
    std::vector<int> sat;
    std::set<size_t> unsat;
    std::vector<std::vector<std::pair<size_t,Int>>> term_literals;
    std::vector<Int> objective_coeff;
    std::vector<unsigned> weight;
    std::set<size_t> objective_variables;
    struct Definition {size_t literal; std::vector<std::pair<size_t,Int>> targets; std::set<size_t> dependencies;};
    std::vector<Definition> definitions;
    struct GuardedDefinition {size_t literal; std::vector<int> guards;std::set<size_t> dependencies;std::vector<size_t> targets;};
    std::vector<GuardedDefinition> guarded_definitions;
    struct ConstantConditional {size_t target,guard;__int128_t when_true,when_false;std::vector<size_t> inputs;};
    std::vector<ConstantConditional> constant_conditionals;
    std::vector<std::vector<size_t>> conditional_readers;
    std::vector<size_t> conditional_producer,primary_variables;
    std::set<size_t> objective_search_variables;
    std::set<size_t> defined_variables;
    std::vector<size_t> unit_equalities;
    std::vector<int> unit_linear_inequalities;
    Int objective;
    uint64_t total_cost=0;
    double total_distance=0;
    bool minimize;
    uint64_t iterations=0;
    explicit State(ls_solver& solver):s(solver),lo(s._num_vars,-__int128_t((__uint128_t(1)<<127)-1)-1),
        hi(s._num_vars,__int128_t((__uint128_t(1)<<127)-1)),terms(s._terms.size()),delta(s._lits.size()),
        truth(s._lits.size()),sat(s._clauses.size()),term_literals(s._terms.size()),
        objective_coeff(s._terms.size()),weight(s._clauses.size(),1),minimize(s.omt_direction=="min") {
        for(size_t l=1;l<=s._num_lits;++l) if(s._lits[l].is_nia_lit)
            for(auto& ct:s._lits[l].coff_terms) term_literals[ct.term_idx].push_back({l,integer(ct.coff)});
        for(auto& ct:s.omt_objective_terms){objective_coeff[ct.term_idx]+=integer(ct.coff);for(auto f:s._terms[ct.term_idx].var_epxs)objective_variables.insert(f.var_index);}
        for(auto& c:s._clauses) if(c.literals.size()==1) {
            int id=c.literals[0]; auto& l=s._lits[std::abs(id)];
            if(id>0&&l.is_nia_lit&&l.is_equal) unit_equalities.push_back(id);
            if(l.is_nia_lit&&!l.is_equal){
                bool linear=true;
                for(auto ct:l.coff_terms)if(ct.coff){const auto& t=s._terms[ct.term_idx];
                    if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1){linear=false;break;}}
                if(linear)unit_linear_inequalities.push_back(id);
            }
            if(!l.is_nia_lit||l.coff_terms.size()!=1) continue;
            auto& ct=l.coff_terms[0]; auto& t=s._terms[ct.term_idx];
            if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1||ct.coff==0) continue;
            size_t v=t.var_epxs[0].var_index; Int a=integer(ct.coff),b=-integer(l.key),q;
            if(l.is_equal) { if(id<0)continue; if(!mpz_divisible_p(b.get_mpz_t(),a.get_mpz_t()))continue; q=b/a; }
            else {
                if(id<0){a=-a;b=-b-1;}
                if(a>0)mpz_fdiv_q(q.get_mpz_t(),b.get_mpz_t(),a.get_mpz_t());
                else mpz_cdiv_q(q.get_mpz_t(),b.get_mpz_t(),a.get_mpz_t());
            }
            __int128_t value; if(!parse_omt_integer(q.get_str(),value))continue;
            if(l.is_equal){lo[v]=hi[v]=value;}
            else if(a>0)hi[v]=std::min(hi[v],value);else lo[v]=std::max(lo[v],value);
        }
        for(size_t id:unit_equalities){
            const auto& l=s._lits[id];std::map<size_t,unsigned> occurrences;
            Definition def;def.literal=id;
            for(auto ct:l.coff_terms)for(auto f:s._terms[ct.term_idx].var_epxs){++occurrences[f.var_index];def.dependencies.insert(f.var_index);}
            for(auto ct:l.coff_terms){auto& t=s._terms[ct.term_idx];if(t.var_epxs.size()==1&&t.var_epxs[0].exponent==1&&occurrences[t.var_epxs[0].var_index]==1)def.targets.push_back({t.var_epxs[0].var_index,integer(ct.coff)});}
            std::sort(def.targets.begin(),def.targets.end(),[](const auto& a,const auto& b){return a.first>b.first;});
            if(!def.targets.empty()){defined_variables.insert(def.targets.front().first);definitions.push_back(std::move(def));}
        }
        for(const auto& clause:s._clauses){
            if(clause.bool_literals.empty()||clause.nia_literals.size()!=1||clause.nia_literals[0]<=0)continue;
            size_t id=clause.nia_literals[0];const auto& l=s._lits[id];if(!l.is_equal)continue;
            GuardedDefinition def;def.literal=id;def.guards=clause.bool_literals;
            std::map<size_t,unsigned> degrees;
            for(auto ct:l.coff_terms)for(auto f:s._terms[ct.term_idx].var_epxs){degrees[f.var_index]=std::max(degrees[f.var_index],unsigned(f.exponent));def.dependencies.insert(f.var_index);}
            for(auto p:degrees)if(p.second==1)def.targets.push_back(p.first);
            std::sort(def.targets.rbegin(),def.targets.rend());
            for(int guard:def.guards)def.dependencies.insert(size_t(s._lits[std::abs(guard)].delta));
            if(!def.targets.empty())guarded_definitions.push_back(std::move(def));
        }
        build_constant_conditionals();
    }
    // A complete pair (g OR x=a), (!g OR x=b) defines x exactly.
    // Unlike a general guarded equation, its selected constant needs no root
    // heuristic and g may itself be an arithmetic atom.
    void build_constant_conditionals(){
        struct Branches {bool present[2]={false,false};__int128_t value[2]={0,0};bool conflicting=false;};
        std::map<std::pair<size_t,size_t>,Branches> pairs;
        for(const auto& clause:s._clauses)if(clause.literals.size()==2){
            for(unsigned position=0;position<2;++position){
                const int signed_equality=clause.literals[position];
                if(signed_equality<=0)continue;
                const auto& equation=s._lits[size_t(signed_equality)];
                if(!equation.is_nia_lit||!equation.is_equal||equation.coff_terms.size()!=1)continue;
                const auto& coefficient=equation.coff_terms[0];const auto& term=s._terms[coefficient.term_idx];
                if(coefficient.coff==0||term.var_epxs.size()!=1||term.var_epxs[0].exponent!=1)continue;
                const size_t target=term.var_epxs[0].var_index;
                const Int numerator=-integer(equation.key),denominator=integer(coefficient.coff);
                if(!mpz_divisible_p(numerator.get_mpz_t(),denominator.get_mpz_t()))continue;
                __int128_t value;if(!parse_omt_integer(Int(numerator/denominator).get_str(),value))continue;
                const int guard=clause.literals[1-position];
                const size_t id=size_t(guard<0?-int64_t(guard):int64_t(guard));
                auto& pair=pairs[{target,id}];const unsigned side=guard>0?1:0;
                if(pair.present[side]&&pair.value[side]!=value)pair.conflicting=true;
                pair.present[side]=true;pair.value[side]=value;
            }
        }
        std::vector<ConstantConditional> raw;std::map<size_t,size_t> frequency;
        for(const auto& entry:pairs){
            const auto& pair=entry.second;if(pair.conflicting||!pair.present[0]||!pair.present[1])continue;
            const size_t target=entry.first.first,id=entry.first.second;const auto& guard=s._lits[id];
            std::set<size_t> inputs;
            if(guard.is_nia_lit){for(auto ct:guard.coff_terms)if(ct.coff)for(auto factor:s._terms[ct.term_idx].var_epxs)inputs.insert(factor.var_index);}
            else inputs.insert(size_t(guard.delta));
            if(inputs.count(target))continue;
            raw.push_back({target,id,pair.value[0],pair.value[1],std::vector<size_t>(inputs.begin(),inputs.end())});++frequency[target];
        }
        std::vector<ConstantConditional> unique;
        for(auto& definition:raw)if(frequency[definition.target]==1)unique.push_back(std::move(definition));
        std::map<size_t,size_t> producer;
        for(size_t i=0;i<unique.size();++i)producer[unique[i].target]=i;
        std::vector<std::vector<size_t>> successors(unique.size());std::vector<size_t> degree(unique.size(),0);
        for(size_t i=0;i<unique.size();++i)for(size_t input:unique[i].inputs){
            auto found=producer.find(input);if(found!=producer.end()){successors[found->second].push_back(i);++degree[i];}
        }
        std::set<size_t> ready;for(size_t i=0;i<degree.size();++i)if(!degree[i])ready.insert(i);
        while(!ready.empty()){
            const size_t i=*ready.begin();ready.erase(ready.begin());constant_conditionals.push_back(std::move(unique[i]));
            for(size_t next:successors[i])if(!--degree[next])ready.insert(next);
        }
        // Cycles and their dependents are conservatively left to normal search.
        conditional_readers.resize(s._num_vars);
        conditional_producer.assign(s._num_vars,SIZE_MAX);
        for(size_t i=0;i<constant_conditionals.size();++i){
            const auto& definition=constant_conditionals[i];conditional_producer[definition.target]=i;
            defined_variables.insert(definition.target);
            for(size_t input:definition.inputs)conditional_readers[input].push_back(i);
        }
        for(size_t v=0;v<s._num_vars;++v)if(conditional_producer[v]==SIZE_MAX)primary_variables.push_back(v);
        const auto priorities=search_inputs(std::vector<size_t>(objective_variables.begin(),objective_variables.end()));
        objective_search_variables.insert(priorities.begin(),priorities.end());
    }
    const std::vector<size_t>& search_variables()const{return primary_variables;}
    std::vector<size_t> search_inputs(const std::vector<size_t>& requested)const{
        std::vector<size_t> pending=requested;std::set<size_t> seen,inputs;
        while(!pending.empty()){
            const size_t v=pending.back();pending.pop_back();
            if(v>=conditional_producer.size())throw std::runtime_error("invalid decision variable");
            if(!seen.insert(v).second)continue;
            const size_t producer=conditional_producer[v];
            if(producer==SIZE_MAX)inputs.insert(v);
            else{
                const auto& dependencies=constant_conditionals[producer].inputs;
                pending.insert(pending.end(),dependencies.begin(),dependencies.end());
            }
        }
        return std::vector<size_t>(inputs.begin(),inputs.end());
    }
    std::vector<size_t> repair_variables(const std::vector<size_t>& requested,std::mt19937& rng)const{
        auto result=search_inputs(requested);
        if(result.empty()&&!primary_variables.empty())result.push_back(primary_variables[rng()%primary_variables.size()]);
        return result;
    }
    size_t project_constant_conditionals(const std::set<size_t>& inputs,const std::set<size_t>& fixed,
        std::map<size_t,__int128_t>& old,std::set<size_t>& touched){
        std::set<size_t> pending;
        for(size_t v:inputs)for(size_t reader:conditional_readers[v])pending.insert(reader);
        size_t writes=0,work=0;
        while(!pending.empty()&&work++<32768){
            const size_t i=*pending.begin();pending.erase(pending.begin());const auto& def=constant_conditionals[i];
            if(fixed.count(def.target))continue;
            const auto& guard=s._lits[def.guard];bool truth;
            if(guard.is_nia_lit){const Int value=polynomial(guard);truth=guard.is_equal?value==0:value<=0;}
            else truth=x[size_t(guard.delta)]>0;
            const __int128_t value=truth?def.when_true:def.when_false;
            if(value==x[def.target]||value<lo[def.target]||value>hi[def.target])continue;
            if(!old.count(def.target))old[def.target]=x[def.target];
            x[def.target]=value;touched.insert(def.target);++writes;
            for(size_t reader:conditional_readers[def.target])pending.insert(reader);
        }
        return writes;
    }
    bool better(const Int& a,const Int& b) const { return minimize?a<b:a>b; }
    void reset(const Assignment& values) {
        x=values; unsat.clear();
        for(size_t t=0;t<terms.size();++t)terms[t]=monomial(s._terms[t],x);
        for(size_t l=1;l<=s._num_lits;++l) {
            auto& a=s._lits[l];
            if(a.is_nia_lit) {
                delta[l]=integer(a.key);for(auto& ct:a.coff_terms)delta[l]+=integer(ct.coff)*terms[ct.term_idx];
                truth[l]=a.is_equal?delta[l]==0:delta[l]<=0;
            } else truth[l]=x[size_t(a.delta)]>0;
        }
        for(size_t c=0;c<sat.size();++c){sat[c]=0;for(int id:s._clauses[c].literals)sat[c]+=truth[std::abs(id)]==(id>0);if(!sat[c])unsat.insert(c);}
        objective=integer(s.omt_objective_constant);
        for(auto& ct:s.omt_objective_terms)objective+=integer(ct.coff)*terms[ct.term_idx];
        rescore();
    }
    Int polynomial(const lit& l) const {
        Int d=integer(l.key);for(auto& ct:l.coff_terms)d+=integer(ct.coff)*monomial(s._terms[ct.term_idx],x);return d;
    }
    // Project unit equalities after changing decision variables. Prefer a
    // remaining linear variable; never overwrite explicitly requested moves.
    Move project(Move move,bool guarded=true) {
        std::map<size_t,__int128_t> old;std::set<size_t> fixed;
        for(auto p:move){if(!old.count(p.first))old[p.first]=x[p.first];x[p.first]=p.second;fixed.insert(p.first);}
        std::set<size_t> touched=fixed,conditional_inputs=fixed;
        unsigned coefficient_builds=0,writes=0;
        for(int pass=0;pass<3;++pass) {
            bool change=false;
            for(const auto& def:definitions) {
                bool affected=false;for(size_t v:touched)if(def.dependencies.count(v)){affected=true;break;}
                if(!affected)continue;
                size_t chosen=s._num_vars;Int next;
                const auto& l=s._lits[def.literal];Int d=polynomial(l);if(d==0)continue;
                for(const auto& target:def.targets){size_t v=target.first;const Int& a=target.second;
                    if(fixed.count(v)||a==0||!mpz_divisible_p(d.get_mpz_t(),a.get_mpz_t()))continue;
                    Int q=integer(x[v])-d/a;__int128_t value;if(!parse_omt_integer(q.get_str(),value)||value<lo[v]||value>hi[v])continue;
                    chosen=v;next=q;break;
                }
                if(chosen<s._num_vars){__int128_t value;parse_omt_integer(next.get_str(),value);if(!old.count(chosen))old[chosen]=x[chosen];x[chosen]=value;touched.insert(chosen);conditional_inputs.insert(chosen);change=true;}
            }
            if(guarded&&coefficient_builds<64&&writes<16){
                std::set<size_t> pivoted;
                for(const auto& def:guarded_definitions){
                    if(coefficient_builds>=64||writes>=16)break;
                    bool affected=false;for(size_t v:touched)if(def.dependencies.count(v)){affected=true;break;}
                    if(!affected)continue;
                    bool inactive=false;
                    for(int id:def.guards)if((x[size_t(s._lits[std::abs(id)].delta)]>0)==(id>0)){inactive=true;break;}
                    if(inactive)continue;
                    const auto& l=s._lits[def.literal];if(polynomial(l)==0)continue;
                    for(size_t v:def.targets){
                        if(fixed.count(v)||pivoted.count(v)||lo[v]==hi[v])continue;
                        if(++coefficient_builds>64)break;
                        auto a=coefficients(l.coff_terms,integer(l.key),v);
                        if(a.size()!=2||a[1]==0)continue;
                        Int rhs=-a[0];if(!mpz_divisible_p(rhs.get_mpz_t(),a[1].get_mpz_t()))continue;
                        Int q=rhs/a[1];__int128_t value;if(!parse_omt_integer(q.get_str(),value)||value<lo[v]||value>hi[v])continue;
                        if(!old.count(v))old[v]=x[v];x[v]=value;touched.insert(v);conditional_inputs.insert(v);pivoted.insert(v);++writes;change=true;break;
                    }
                }
            }
            if(!conditional_inputs.empty()){
                if(project_constant_conditionals(conditional_inputs,fixed,old,touched))change=true;
                conditional_inputs.clear();
            }
            if(!change)break;
        }
        Move result;for(auto p:old){if(x[p.first]!=p.second)result.push_back({p.first,x[p.first]});x[p.first]=p.second;}return result;
    }
    struct Evaluation {
        Move move;
        std::vector<std::pair<size_t,Int>> terms,literals;
        std::vector<std::pair<size_t,bool>> truths;
        std::vector<std::pair<size_t,int>> clauses;
        Int objective;
        uint64_t cost=0;
        size_t violations=0;
        double distance=0;
    };
    double distance(size_t c,const std::map<size_t,Int>* changed=nullptr) const {
        double best=1e6;
        for(int id:s._clauses[c].literals){size_t i=std::abs(id);auto& l=s._lits[i];if(!l.is_nia_lit){best=std::min(best,1.0);continue;}
            Int d=delta[i];if(changed){auto p=changed->find(i);if(p!=changed->end())d=p->second;}
            if(id>0){if(l.is_equal)d=abs(d);else d=std::max(Int(0),d);}else{if(l.is_equal)d=(d==0?1:0);else d=std::max(Int(0),Int(Int(1)-d));}
            best=std::min(best,std::log1p(std::min(1e100,std::abs(d.get_d()))));
        }return best;
    }
    void rescore(){total_cost=0;total_distance=0;for(size_t c:unsat){total_cost+=weight[c];total_distance+=distance(c);}}
    Evaluation evaluate(Move move,bool projection=true,bool guarded=false) {
        Evaluation e;e.move=projection?project(std::move(move),guarded):std::move(move);e.objective=objective;
        std::set<size_t> affected_terms,affected_clauses,boolean_lits;
        Move old;
        for(auto p:e.move)if(p.second<lo[p.first]||p.second>hi[p.first]){e.cost=UINT64_MAX;e.violations=SIZE_MAX;return e;}
        for(auto p:e.move){
            old.push_back({p.first,x[p.first]});x[p.first]=p.second;
            for(auto& t:s._vars[p.first].term_exps)affected_terms.insert(t.term_idx);
            for(int c:s._vars[p.first].clause_idxs)affected_clauses.insert(c);
            if(!s._vars[p.first].is_nia)for(auto l:s._vars[p.first].literal_idxs)boolean_lits.insert(l);
        }
        std::map<size_t,Int> changes;
        for(size_t t:affected_terms){Int next=monomial(s._terms[t],x);Int diff=next-terms[t];e.terms.push_back({t,next});e.objective+=objective_coeff[t]*diff;
            for(auto& l:term_literals[t]){if(!changes.count(l.first))changes[l.first]=delta[l.first];changes[l.first]+=l.second*diff;}}
        std::map<size_t,bool> truths;
        for(auto& p:changes){auto& l=s._lits[p.first];truths[p.first]=l.is_equal?p.second==0:p.second<=0;e.literals.push_back(p);}
        for(size_t l:boolean_lits)truths[l]=x[size_t(s._lits[l].delta)]>0;
        for(auto p:truths)e.truths.push_back(p);
        e.violations=unsat.size();e.cost=total_cost;e.distance=total_distance;
        for(size_t c:affected_clauses){int count=0;for(int id:s._clauses[c].literals){auto p=truths.find(std::abs(id));bool t=p==truths.end()?truth[std::abs(id)]:p->second;count+=t==(id>0);}e.clauses.push_back({c,count});
            if(!sat[c]){--e.violations;e.cost-=weight[c];e.distance-=distance(c);}if(!count){++e.violations;e.cost+=weight[c];e.distance+=distance(c,&changes);}}
        for(auto p:old)x[p.first]=p.second;
        return e;
    }
    void apply(const Evaluation& e) {
        for(auto p:e.move)x[p.first]=p.second;
        for(auto& p:e.terms)terms[p.first]=p.second;
        for(auto& p:e.literals)delta[p.first]=p.second;
        for(auto p:e.truths)truth[p.first]=p.second;
        for(auto p:e.clauses){sat[p.first]=p.second;if(p.second)unsat.erase(p.first);else unsat.insert(p.first);}
        objective=e.objective;total_cost=e.cost;total_distance=e.distance;++iterations;
    }
    std::vector<Int> coefficients(const std::vector<coff_term>& list,const Int& constant,size_t v) const {
        std::vector<Int> a(1,constant);
        for(auto& ct:list){unsigned exponent=0;Int value=integer(ct.coff);
            for(auto f:s._terms[ct.term_idx].var_epxs){if(size_t(f.var_index)==v)exponent=f.exponent;else value*=power(integer(x[f.var_index]),f.exponent);}
            if(exponent>16)return {};if(a.size()<=exponent)a.resize(exponent+1);a[exponent]+=value;}
        while(a.size()>1&&a.back()==0)a.pop_back();return a;
    }
    void roots(const std::vector<Int>& a,std::set<__int128_t>& points,size_t v) const {
        auto insert=[&](const Int& q){for(int off=-2;off<=2;++off){Int n=q+off;__int128_t value;if(parse_omt_integer(n.get_str(),value)&&value>=lo[v]&&value<=hi[v])points.insert(value);}};
        if(a.size()==2&&a[1]!=0){Int q,n=-a[0];mpz_fdiv_q(q.get_mpz_t(),n.get_mpz_t(),a[1].get_mpz_t());insert(q);}
        if(a.size()==3&&a[2]!=0){Int d=a[1]*a[1]-4*a[2]*a[0];if(d>=0){Int sq;mpz_sqrt(sq.get_mpz_t(),d.get_mpz_t());for(int sign:{-1,1}){Int n=-a[1]+sign*sq,q,den=2*a[2];mpz_fdiv_q(q.get_mpz_t(),n.get_mpz_t(),den.get_mpz_t());insert(q);}}}
    }
    std::vector<__int128_t> candidates(size_t v,uint64_t radius,bool full) const {
        if(!s._vars[v].is_nia)return {-x[v]};
        std::set<__int128_t> points;
        auto insert=[&](Int p){__int128_t val;if(parse_omt_integer(p.get_str(),val)&&val>=lo[v]&&val<=hi[v]&&val!=x[v])points.insert(val);};
        insert(0);insert(-integer(x[v]));insert(integer(x[v])+1);insert(integer(x[v])-1);
        if(objective_variables.count(v)){insert(Int(1)<<40);insert(-(Int(1)<<40));}
        insert(integer(x[v])+Int(radius));insert(integer(x[v])-Int(radius));
        if(lo[v]>-__int128_t(1000000000000LL))insert(integer(lo[v]));if(hi[v]<__int128_t(1000000000000LL))insert(integer(hi[v]));
        auto obj=coefficients(s.omt_objective_terms,integer(s.omt_objective_constant),v);
        std::vector<Int> derivative;for(size_t i=1;i<obj.size();++i)derivative.push_back(obj[i]*i);roots(derivative,points,v);
        if(full)for(size_t l:s._vars[v].literal_idxs){auto& lit=s._lits[l];if(lit.is_nia_lit)roots(coefficients(lit.coff_terms,integer(lit.key),v),points,v);}
        points.erase(x[v]);return {points.begin(),points.end()};
    }
    // Probe the strict frontier of our own objective while jointly changing
    // two objective coordinates. Unequal changes matter for product objectives:
    // (3,7)->(5,4) improves area although no opposite unit exchange does so.
    std::vector<Move> frontier_probes(uint64_t radius,const std::function<bool()>& in_budget,size_t cap=24) {
        std::vector<Move> result;
        cap=std::min(cap,size_t(64));
        if(!cap||!unsat.empty()||objective_variables.size()<2||objective_variables.size()>8||!in_budget())return result;
        struct Probe {Move move;Int gap,distance;};
        std::vector<Probe> probes;std::set<Move> seen;
        const Int desired=objective+(minimize?-1:1);
        bool enough=false;
        for(size_t i:objective_variables){
            if(enough)break;if(!s._vars[i].is_nia)continue;
            for(size_t j:objective_variables){
                if(enough)break;if(j==i||!s._vars[j].is_nia)continue;
                std::set<__int128_t> unique_points;
                for(int step:{-1,1,-2,2,-3,3,-4,4}){
                    Int q=integer(x[i])+step;__int128_t value;
                    if(parse_omt_integer(q.get_str(),value)&&value>=lo[i]&&value<=hi[i])unique_points.insert(value);
                }
                for(int sign:{-1,1}){
                    Int q=integer(x[i])+sign*Int(std::min(uint64_t(16),radius));__int128_t value;
                    if(parse_omt_integer(q.get_str(),value)&&value>=lo[i]&&value<=hi[i]&&value!=x[i])unique_points.insert(value);
                }
                // A genuine domain endpoint can expose a completely new
                // shape beyond the local radius. Ignore the representation
                // limits used for an otherwise unbounded variable.
                const __int128_t maximum=__int128_t((__uint128_t(1)<<127)-1),minimum=-maximum-1;
                if(lo[i]!=minimum&&lo[i]!=x[i])unique_points.insert(lo[i]);
                if(hi[i]!=maximum&&hi[i]!=x[i])unique_points.insert(hi[i]);
                std::vector<__int128_t> points(unique_points.begin(),unique_points.end());
                std::stable_sort(points.begin(),points.end(),[&](auto a,auto b){return abs(integer(a)-integer(x[i]))<abs(integer(b)-integer(x[i]));});
                for(auto value:points){
                    if(!in_budget())return {};
                    const auto previous=x[i];x[i]=value;
                    std::vector<Int> a;
                    try{a=coefficients(s.omt_objective_terms,integer(s.omt_objective_constant),j);}
                    catch(...){x[i]=previous;throw;}
                    x[i]=previous;
                    if(a.empty())continue;a[0]-=desired;
                    std::set<__int128_t> partners;roots(a,partners,j);
                    for(auto partner:partners){
                        if(!in_budget())return {};
                        if(partner==x[j])continue;
                        auto e=evaluate({{i,value},{j,partner}});
                        if(e.cost==UINT64_MAX||!better(e.objective,objective)||e.move.empty()||!seen.insert(e.move).second)continue;
                        const Int distance=abs(integer(value)-integer(x[i]))+abs(integer(partner)-integer(x[j]));
                        probes.push_back({std::move(e.move),abs(e.objective-objective),distance});
                        if(probes.size()>=512){enough=true;break;}
                    }
                    if(enough)break;
                }
            }
        }
        if(!in_budget())return {};
        std::stable_sort(probes.begin(),probes.end(),[](const Probe& a,const Probe& b){return a.gap!=b.gap?a.gap<b.gap:a.distance<b.distance;});
        for(auto& probe:probes){result.push_back(std::move(probe.move));if(result.size()==cap)break;}
        if(!in_budget())return {};
        return result;
    }
    struct Pressure {int literal;Int shift,gain;};
    using Plateau=std::vector<Pressure>;
    // Freeze this field for one feasible plateau episode. It measures how far
    // objective-improving unit probes are blocked by hard linear inequalities.
    // Negative literals are integer complements: !(d<=0) means 1-d<=0.
    Plateau plateau_field(const std::function<bool()>& in_budget=[](){return true;}) {
        Plateau field;
        if(!unsat.empty())return field;
        for(size_t v:objective_search_variables){
            if(!s._vars[v].is_nia)continue;
            for(int direction:{-1,1}){
                if(!in_budget())return field;
                Int point=integer(x[v])+direction;__int128_t value;
                if(!parse_omt_integer(point.get_str(),value))continue;
                auto probe=evaluate({{v,value}});
                if(probe.cost==UINT64_MAX||!better(probe.objective,objective))continue;
                std::map<size_t,Int> changes(probe.literals.begin(),probe.literals.end());
                const Int gain=abs(probe.objective-objective);
                for(int id:unit_linear_inequalities){
                    if(!in_budget())return field;
                    size_t l=std::abs(id);auto p=changes.find(l);if(p==changes.end())continue;
                    Int shift=p->second-delta[l];if(id<0)shift=-shift;
                    if(shift>0)field.push_back({id,shift,gain});
                }
            }
        }
        return field;
    }
    Int pressure(const Plateau& field,const Evaluation* candidate=nullptr) const {
        std::map<size_t,Int> changes;
        if(candidate)changes.insert(candidate->literals.begin(),candidate->literals.end());
        Int score=0;
        for(const auto& term:field){
            size_t l=std::abs(term.literal);auto p=changes.find(l);
            Int q=p==changes.end()?delta[l]:p->second;
            if(term.literal<0)q=1-q;
            q+=term.shift;if(q>0)score+=term.gain*q;
        }
        return score;
    }
    bool neutral_step(const Plateau& field,Evaluation& selected,const std::function<bool()>& in_budget) {
        if(!unsat.empty()||field.empty()||!in_budget())return false;
        Int score=pressure(field);if(score==0)return false;
        bool found=false;
        for(size_t v:primary_variables){
            if(objective_variables.count(v))continue;
            for(auto point:candidates(v,1,false)){
                if(!in_budget())return false;
                auto e=evaluate({{v,point}});
                if(e.cost==UINT64_MAX||e.violations||e.objective!=objective||e.move.empty())continue;
                Int next=pressure(field,&e);
                if(next<score){score=next;selected=std::move(e);found=true;}
            }
        }
        return found;
    }
};

bool write_model(const ls_solver& s,const Assignment& x,const std::string& path,const std::function<bool()>& in_budget) {
    if(path.empty())return in_budget();const std::string temporary=path+".tmp";std::ofstream out(temporary);if(!out)throw std::runtime_error("cannot open model output");
    for(size_t v=0;v<x.size();++v){out<<s._vars[v].var_name<<'\t';if(s._vars[v].is_nia)out<<format_omt_integer(x[v]);else out<<(x[v]>0?"true":"false");out<<'\n';}out.close();if(!out)throw std::runtime_error("cannot write model output");if(!in_budget()){std::remove(temporary.c_str());return false;}if(std::rename(temporary.c_str(),path.c_str()))throw std::runtime_error("cannot publish model output");return true;
}
}

int run_omt_optimization(ls_solver& solver,const std::string& input,bool initialize_enabled,
    double timeout,double import_seconds,const std::string& model_output,const std::string& target,
    const std::string& large_target,const std::string& unbounded_threshold,
    const OmtOptimalityOptions& optimality_options) {
    const auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return import_seconds+std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    // Independent proof modes must not stop on a competitor-supplied target.
    Assignment best;Int best_value,target_value;bool has_best=false,has_target=!target.empty()&&unbounded_threshold.empty()&&!optimality_options.enabled;
    std::string termination="timeout",error;double best_time=0;uint64_t steps=0;
    OmtLargeTargetSchedule large_schedule(large_target,solver.omt_direction,timeout);
    uint64_t large_attempts=0,large_slices=0;
    double large_seconds=0;
    std::string large_status=large_schedule.enabled()?"waiting":"disabled";
    OmtUnboundedResult unbounded;
    unsigned unbounded_attempts=0;
    double unbounded_seconds=0,unbounded_time=0;
    std::string unbounded_status=unbounded_threshold.empty()?"disabled":"waiting";
    const Int proof_threshold=unbounded_threshold.empty()?Int(0):Int(unbounded_threshold,10);
    OmtOptimalityResult optimality;
    unsigned optimality_attempts=0;
    double optimality_seconds=0,optimality_time=0;
    bool has_checked_optimality=false;
    Int checked_optimality_value;
    std::string optimality_status=optimality_options.enabled?"waiting":"disabled";
    const std::string optimality_query=optimality_options.query_output.empty()?input+".optimality.smt2":optimality_options.query_output;
    if(has_target)target_value=Int(target,10);
    auto better=[&](const Int&a,const Int&b){return solver.omt_direction=="min"?a<b:a>b;};
    auto accept=[&](const Assignment& x){
        if(unbounded.proved||optimality.proved)return false; // Keep the certified model immutable.
        Int value=validate(solver,x);if(has_best&&!better(value,best_value))return false;
        if(elapsed()>=timeout||!write_model(solver,x,model_output,[&](){return elapsed()<timeout;}))return false;best=x;best_value=value;has_best=true;best_time=elapsed();
        large_schedule.accepted(value);
        std::cout<<std::setprecision(12)<<"{\"event\":\"incumbent\",\"status\":\"sat\",\"direction\":"<<quote(solver.omt_direction)
          <<",\"objective_value\":"<<quote(value.get_str())<<",\"elapsed_seconds\":"<<best_time
          <<",\"model_validated\":true,\"is_optimal\":false}"<<std::endl;
        if(!unbounded_threshold.empty()&&!unbounded_attempts&&elapsed()<timeout
           &&(solver.omt_direction=="min"?value<=-proof_threshold:value>=proof_threshold)){
            ++unbounded_attempts;
            const double proof_began=elapsed();
            const double allowance=std::min(5.0,std::min(timeout*.05,timeout-proof_began));
            try{
                unbounded=prove_omt_unbounded(solver,best,allowance);
                unbounded_status=unbounded.status;
            }catch(const std::exception& e){
                // This supplemental proof attempt never invalidates a feasible
                // incumbent or changes the established optimization trajectory.
                unbounded_status="unknown";
                std::cerr<<"omt_unbounded_probe="<<e.what()<<'\n';
            }
            unbounded_seconds=elapsed()-proof_began;
            if(unbounded.proved){
                if(elapsed()<timeout){
                    unbounded_time=elapsed();unbounded_status="proved";
                    std::cerr<<"omt_unbounded_proved queries="<<unbounded.queries<<'\n';
                }else{
                    unbounded.proved=false;unbounded.certificate_json="null";unbounded_status="budget";
                }
            }
        }
        return true;
    };
    auto beaten=[&](){return unbounded.proved||optimality.proved||(has_target&&has_best&&better(best_value,target_value));};
    auto stop_reason=[&](){return unbounded.proved?"unbounded_proved":(optimality.proved?"optimality_proved":"target_beaten");};
    auto check_optimality=[&](){
        if(!optimality_options.enabled||!has_best||beaten()
           ||(has_checked_optimality&&checked_optimality_value==best_value)
           ||elapsed()-best_time<optimality_options.stall_seconds||elapsed()>=timeout)return;
        has_checked_optimality=true;checked_optimality_value=best_value;
        ++optimality_attempts;
        const double proof_began=elapsed();
        const double allowance=std::min(optimality_options.timeout_seconds,timeout-proof_began);
        std::cerr<<"omt_optimality_probe best="<<best_value<<" seconds="<<allowance<<'\n';
        try{
            optimality=prove_omt_optimality(solver,best_value.get_str(),allowance,optimality_query);
            optimality_status=optimality.status;
        }catch(const std::exception& e){
            optimality.proved=false;optimality_status="error";optimality.reason=e.what();
        }
        optimality_seconds+=elapsed()-proof_began;
        if(optimality.proved){
            if(elapsed()<timeout){optimality_time=elapsed();optimality_status="proved";}
            else {optimality.proved=false;optimality_status="budget";}
        }
        std::cerr<<"omt_optimality_status="<<optimality_status;
        if(!optimality.reason.empty())std::cerr<<" reason="<<optimality.reason;
        std::cerr<<'\n';
    };
    try {
        if(solver.build_unsat)termination="preprocessing_unsat";
        else {
            std::unique_ptr<OmtFeasibilityContinuation> feasibility;
            if(initialize_enabled&&elapsed()<timeout){
                initial_model initial;double budget=std::min(10.0,(timeout-elapsed())*.1);
                if(make_progressive_initial_model(input,budget,initial)){
                    Assignment x(solver._num_vars,0);
                    for(size_t v=0;v<x.size();++v){auto p=initial.values.find(solver._vars[v].var_name);if(p==initial.values.end()){x[v]=solver._vars[v].is_nia?0:-1;continue;}
                        if(!solver._vars[v].is_nia)x[v]=p->second=="true"?1:-1;
                        else {std::string n=p->second;if(n.rfind("(- ",0)==0&&n.back()==')')n="-"+n.substr(3,n.size()-4);if(!parse_omt_integer(n,x[v]))throw std::runtime_error("initializer integer overflow");}}
                    try{accept(x);}catch(const std::exception& e){std::cerr<<"initializer_rejected="<<e.what()<<'\n';}
                }else solver.progressive_values=std::move(initial.values);
            }
            if(!has_best&&elapsed()<timeout){
                // Preserve the successful legacy first-feasible trajectory.
                // It is only a proposal source; full GMP validation still
                // gates publication. Strict-bound repair stays exact.
                feasibility=std::make_unique<OmtFeasibilityContinuation>(solver,false);
                const double remaining=timeout-elapsed();
                if(feasibility->resume_for(std::min(60.0,remaining*.2),remaining)==OmtFeasibilityContinuation::Status::sat){
                    Assignment x(solver._solution.begin(),solver._solution.begin()+solver._num_vars);
                    try{accept(x);}catch(const std::exception& e){
                        std::cerr<<"legacy_candidate_rejected="<<e.what()<<'\n';
                        feasibility=std::make_unique<OmtFeasibilityContinuation>(solver,true);
                    }
                }
            }
            if(beaten())termination=stop_reason();
            else if(elapsed()<timeout){
                State state(solver);
                Assignment initial=has_best?best:Assignment(solver._solution.begin(),solver._solution.begin()+solver._num_vars);
                if(initial.size()!=solver._num_vars)initial.assign(solver._num_vars,0);
                state.reset(initial);
                if(!has_best){
                    // A spread seed avoids rank-deficient all-zero starts in
                    // polynomial systems. Computed variables are projected.
                    Move spread;
                    for(size_t v=0;v<solver._num_vars;++v)if(!state.defined_variables.count(v)&&solver._vars[v].is_nia&&state.lo[v]>=0&&state.hi[v]<__int128_t(1000000000)){
                        Int q=integer(state.lo[v])+std::max(Int(1),Int((integer(state.hi[v])-integer(state.lo[v]))/std::max(size_t(1),solver._num_vars)));
                        __int128_t value;if(parse_omt_integer(q.get_str(),value)&&value<=state.hi[v])spread.push_back({v,value});
                    }
                    if(!spread.empty()){auto e=state.evaluate(spread);if(e.cost!=UINT64_MAX){state.apply(e);if(state.unsat.empty())accept(state.x);}}
                }
                if(state.unsat.empty()&&(!has_best||better(state.objective,best_value)))accept(state.x);
                uint64_t radius=1,stale=0,round=0;
                State::Plateau plateau;
                Assignment feasible_representative=has_best?best:Assignment();
                unsigned neutral_steps=0;
                double next_feasibility=elapsed()+.5;
                double next_repair=elapsed()+std::min(10.0,timeout*.1);
                // Give the established coordinate/bound search its initial
                // minute before spending time on supplemental operators.
                double next_bilinear=std::max(60.0,elapsed()+3.0);
                std::mt19937 supplemental_mt(solver.mt);
                std::unique_ptr<OmtBoundRepair> bound_repair;
                std::unique_ptr<OmtBoundRepair> large_repair;
                std::mt19937 large_mt(solver.mt);
                bool large_inactive=false;
                std::unique_ptr<OmtBilinearRepair> bilinear_repair;
                Int repair_incumbent;
                Int bilinear_incumbent;
                const auto& variables=state.search_variables();
                auto accelerate=[&](const Assignment& previous){
                    if(previous.size()!=best.size()||previous.empty())return;
                    std::vector<std::pair<size_t,Int>> direction;
                    for(size_t v=0;v<best.size();++v)if(solver._vars[v].is_nia&&!state.defined_variables.count(v)&&best[v]!=previous[v])
                        direction.push_back({v,integer(best[v])-integer(previous[v])});
                    if(direction.empty())return;
                    state.reset(best);
                    Int scale=2;
                    // Extrapolate the COMPLETE accepted direction, preserving
                    // same-sign coupled moves and recomputing definitions.
                    for(unsigned k=1;k<127&&elapsed()<timeout&&!beaten();++k,scale*=2){
                        Move ray;bool representable=true;
                        for(const auto& d:direction){Int q=integer(previous[d.first])+scale*d.second;__int128_t value;
                            if(!parse_omt_integer(q.get_str(),value)){representable=false;break;}ray.push_back({d.first,value});}
                        if(!representable)break;
                        auto e=state.evaluate(std::move(ray),true,true);
                        if(e.cost==UINT64_MAX||e.violations||!better(e.objective,best_value))break;
                        state.apply(e);
                        if(!accept(state.x))break;
                        ++steps;
                    }
                };
                while(elapsed()<timeout&&!beaten()&&(!variables.empty()||(optimality_options.enabled&&has_best))){
                    check_optimality();
                    if(elapsed()>=timeout||beaten())break;
                    if(variables.empty()){
                        // Preprocessing may eliminate every search coordinate.
                        // A constant incumbent still deserves a source-level proof.
                        std::this_thread::sleep_for(std::chrono::duration<double>(std::min(.01,timeout-elapsed())));
                        continue;
                    }
                    ++round;
                    if(!has_best&&feasibility&&elapsed()>=next_feasibility){
                        const auto status=feasibility->resume_for(std::min(5.0,timeout-elapsed()),timeout-elapsed());
                        if(status==OmtFeasibilityContinuation::Status::sat){
                            Assignment candidate(solver._solution.begin(),solver._solution.begin()+solver._num_vars);
                            bool accepted_candidate=true;
                            try{if(accept(candidate)){state.reset(best);feasible_representative=best;plateau.clear();neutral_steps=0;stale=0;}}
                            catch(const std::exception& e){std::cerr<<"legacy_candidate_rejected="<<e.what()<<'\n';accepted_candidate=false;}
                            if(accepted_candidate)feasibility.reset();
                            else feasibility=std::make_unique<OmtFeasibilityContinuation>(solver,true);
                        }else if(status==OmtFeasibilityContinuation::Status::exhausted)feasibility.reset();
                        next_feasibility=elapsed()+.5;
                        if(elapsed()>=timeout||beaten())break;
                    }
                    const double exploration_until=(!has_best&&feasibility)?std::min(timeout,next_feasibility):timeout;
                    if(!large_inactive&&large_schedule.ready(elapsed(),timeout-elapsed())){
                        const double probe_began=elapsed();
                        const double probe_until=probe_began+large_schedule.slice_seconds(timeout-probe_began);
                        bool probe_improved=false;
                        const Assignment previous=best;
                        if(!large_repair){
                            ++large_attempts;
                            large_repair=std::make_unique<OmtBoundRepair>(input,solver,best,best_value.get_str(),large_mt(),large_schedule.goal().get_str());
                            std::cerr<<"omt_large_target threshold="<<large_schedule.goal()<<" attempt="<<large_attempts<<'\n';
                        }
                        large_status="searching";
                        if(!large_repair->supported()){
                            large_inactive=true;large_status="unsupported";
                        }else if(elapsed()<probe_until){
                            ++large_slices;
                            Assignment repaired;
                            // The continuation's restart repair also observes
                            // the local slice deadline, not the whole case.
                            const double remaining=std::min(timeout,probe_until)-elapsed();
                            const bool found=large_repair->resume_for(remaining,remaining,repaired);
                            if(found&&elapsed()<timeout&&accept(repaired)){
                                state.reset(best);feasible_representative=best;probe_improved=true;
                                plateau.clear();neutral_steps=0;stale=0;
                            }
                            if(large_repair->status()==OmtBoundRepair::Status::exhausted){
                                large_inactive=true;large_status="exhausted";
                            }else if(!large_repair->supported()){
                                large_inactive=true;large_status="unsupported";
                            }
                        }
                        const double probe_ended=elapsed();
                        large_seconds+=probe_ended-probe_began;
                        large_schedule.charge(probe_ended-probe_began,probe_ended);
                        if(large_schedule.remaining_budget()<=0&&!large_inactive)large_status="budget";
                        // Accepted-direction acceleration is the existing main
                        // optimizer, outside the supplemental probe allowance.
                        if(probe_improved){accelerate(previous);feasible_representative=best;}
                        if(elapsed()>=timeout||beaten())break;
                    }
                    if(has_best&&timeout>=10&&elapsed()>=next_bilinear){
                        // Fix a complete color of a bilinear factor graph,
                        // then repair the full remaining linear problem.
                        // The helper retains private partial states between
                        // calls and receives only our own validated model.
                        if(!bilinear_repair||bilinear_incumbent!=best_value){
                            bilinear_repair=std::make_unique<OmtBilinearRepair>(solver,best,best_value.get_str(),state.lo,state.hi,supplemental_mt());
                            bilinear_incumbent=best_value;
                        }
                        if(bilinear_repair->supported()){
                            Assignment repaired;const Assignment previous=best;
                            const double remaining=timeout-elapsed();
                            if(bilinear_repair->resume_for(std::min(2.0,remaining*.2),remaining,repaired)&&elapsed()<timeout&&accept(repaired)){
                                state.reset(best);accelerate(previous);feasible_representative=best;plateau.clear();neutral_steps=0;stale=0;
                                bilinear_repair.reset();
                            }
                        }
                        next_bilinear=elapsed()+std::min(8.0,timeout*.1);
                        if(elapsed()>=timeout||beaten())break;
                    }
                    if(has_best&&timeout>=10&&elapsed()>=next_repair){
                        Assignment repaired;
                        Assignment previous=best;
                        if(!bound_repair||repair_incumbent!=best_value){
                            bound_repair=std::make_unique<OmtBoundRepair>(input,solver,best,best_value.get_str(),solver.mt());
                            repair_incumbent=best_value;
                        }
                        const double remaining=timeout-elapsed();
                        bool found=bound_repair->resume_for(std::min(5.0,remaining*.25),remaining,repaired);
                        if(found&&elapsed()<timeout&&accept(repaired)){
                            state.reset(best);accelerate(previous);feasible_representative=best;plateau.clear();neutral_steps=0;stale=0;
                            bound_repair.reset();
                        }
                        next_repair=elapsed()+std::min(5.0,timeout*.1);
                        if(elapsed()>=timeout||beaten())break;
                    }
                    bool feasible=state.unsat.empty();
                    std::vector<size_t> scan,repair_clauses;
                    if(feasible){scan=variables;std::shuffle(scan.begin(),scan.end(),solver.mt);std::stable_partition(scan.begin(),scan.end(),[&](size_t v){return state.objective_search_variables.count(v);});}
                    else {
                        size_t offset=solver.mt()%state.unsat.size();auto it=state.unsat.begin();std::advance(it,offset);
                        repair_clauses.push_back(*it);
                        for(int id:solver._clauses[*it].literals){auto& l=solver._lits[std::abs(id)];if(!l.is_nia_lit)scan.push_back(size_t(l.delta));else for(auto& ct:l.coff_terms)for(auto f:solver._terms[ct.term_idx].var_epxs)scan.push_back(f.var_index);}
                        scan=state.repair_variables(scan,solver.mt);
                    }
                    State::Evaluation selected;bool have=false,neutral=false;
                    std::vector<Move> linear_probes;
                    auto ranking=[&](const State::Evaluation& e){
                        uint64_t cost=e.cost;double distance=e.distance;
                        // Virtual strict bound uses ONLY our own incumbent.
                        // It guides repair away from returning to an old model.
                        if(has_best&&!better(e.objective,best_value)){
                            cost+=1+round/1000;
                            Int gap=abs(e.objective-best_value)+1;
                            distance+=1+std::log1p(std::min(1e100,gap.get_d()))*.01;
                        }
                        return std::make_pair(cost,distance);
                    };
                    auto choose=[&](State::Evaluation e){
                        if(e.move.empty()||e.cost==UINT64_MAX)return;
                        if(feasible&&e.violations){
                            if(linear_probes.size()<12&&better(e.objective,state.objective))linear_probes.push_back(std::move(e.move));
                            return;
                        }
                        if(feasible&&!better(e.objective,state.objective))return;
                        if(!feasible&&has_best&&better(state.objective,best_value)&&!better(e.objective,best_value))return;
                        auto a=ranking(e),b=have?ranking(selected):std::make_pair(UINT64_MAX,1e300);
                        if(!have||a.first<b.first||(a.first==b.first&&(a.second<b.second-1e-8||(std::abs(a.second-b.second)<1e-8&&better(e.objective,selected.objective))))){selected=std::move(e);have=true;}
                    };
                    for(size_t v:scan){
                        for(auto value:state.candidates(v,radius,true))choose(state.evaluate({{v,value}},true,!feasible));
                        // Fit the effective coordinate objective after equality
                        // projection; this also handles polynomial auxiliaries.
                        if(feasible&&solver._vars[v].is_nia){
                            int direction=integer(state.hi[v])-integer(state.x[v])>=2?1:-1;
                            Int p1=integer(state.x[v])+direction,p2=integer(state.x[v])+2*direction;
                            __int128_t v1,v2;
                            if(parse_omt_integer(p1.get_str(),v1)&&parse_omt_integer(p2.get_str(),v2)){
                                auto e1=state.evaluate({{v,v1}}),e2=state.evaluate({{v,v2}});
                                if(e1.cost!=UINT64_MAX&&e2.cost!=UINT64_MAX){
                                    Int a=(e2.objective-2*e1.objective+state.objective)/2;
                                    Int b=e1.objective-state.objective-a;
                                    if(a!=0){Int q,n=-b,den=2*a;mpz_fdiv_q(q.get_mpz_t(),n.get_mpz_t(),den.get_mpz_t());
                                        for(int d=-2;d<=2;++d){Int point=integer(state.x[v])+direction*(q+d);__int128_t value;if(parse_omt_integer(point.get_str(),value))choose(state.evaluate({{v,value}}));}}
                                }
                            }
                        }
                        if(elapsed()>=exploration_until)break;
                        if(feasible&&have&&scan.size()>100)break;
                    }
                    // A false product disequality can have several zero
                    // factors. Moving any one coordinate leaves its truth and
                    // repair distance unchanged; cross that plateau atomically.
                    if(!feasible&&elapsed()<exploration_until&&round%4==0&&(!have||selected.violations>=state.unsat.size())){
                        for(auto move:zero_product_moves(solver,state.x,state.lo,state.hi,repair_clauses,32)){
                            choose(state.evaluate(std::move(move),true,true));
                            if(elapsed()>=timeout)break;
                        }
                    }
                    // Coupled exchanges also cross discrete coordinate optima.
                    if(feasible&&!have&&variables.size()<=100){
                        unsigned count=0;
                        for(size_t i:scan){for(size_t j:scan){if(j<=i||!solver._vars[i].is_nia||!solver._vars[j].is_nia)continue;
                            for(int sign:{-1,1}){Int a=integer(state.x[i])+sign*Int(radius),b=integer(state.x[j])-sign*Int(radius);__int128_t va,vb;
                                if(parse_omt_integer(a.get_str(),va)&&parse_omt_integer(b.get_str(),vb))choose(state.evaluate({{i,va},{j,vb}}));}
                            if(++count>=1000||elapsed()>=timeout)break;}if(count>=1000||elapsed()>=timeout)break;}
                    }
                    if(feasible&&!have&&variables.size()<=100){
                        const double until=std::min(timeout,elapsed()+.5);
                        auto in_budget=[&](){return elapsed()<until;};
                        for(const auto& probe:linear_probes){
                            for(unsigned branch=0;branch<3&&in_budget();++branch){
                                auto move=difference_branch_move(solver,state.x,state.lo,state.hi,probe,{},in_budget,branch);
                                if(!move.empty()){
                                    auto candidate=state.evaluate(std::move(move),false);
                                    if(!candidate.violations)choose(std::move(candidate));
                                }
                            }
                            if(!in_budget())break;
                        }
                    }
                    // Follow the strict frontier of our own nonlinear
                    // objective. A container can need a wider AND shorter
                    // shape before any valid placement improves its area.
                    // Sparse invocation preserves the established operators'
                    // budget instead of rebuilding this graph every round.
                    std::vector<Move> frontier;
                    if(feasible&&!have&&variables.size()<=100&&elapsed()>=60&&round%8==0){
                        const double until=std::min(timeout,elapsed()+.15);
                        auto in_budget=[&](){return elapsed()<until;};
                        frontier=state.frontier_probes(radius,in_budget,24);
                        for(const auto& probe:frontier){
                            if(elapsed()>=timeout)break;
                            auto candidate=state.evaluate(probe,false);
                            if(!candidate.violations)choose(std::move(candidate));
                        }
                    }
                    // A negative cycle under the old branch choices does
                    // not rule out a new placement. Search alternative
                    // linear witnesses, including currently false branches,
                    // while keeping the requested objective coordinates.
                    if(feasible&&!have&&variables.size()<=100&&elapsed()>=60&&round%8==0){
                        std::vector<Move> probes;std::set<Move> unique;
                        for(size_t i=0;i<std::max(frontier.size(),linear_probes.size())&&probes.size()<24;++i){
                            if(i<frontier.size()&&unique.insert(frontier[i]).second)probes.push_back(frontier[i]);
                            if(probes.size()<24&&i<linear_probes.size()&&unique.insert(linear_probes[i]).second)probes.push_back(linear_probes[i]);
                        }
                        const double until=std::min(timeout,elapsed()+.25);
                        auto in_budget=[&](){return elapsed()<until;};
                        for(const auto& probe:probes){
                            if(!in_budget())break;
                            auto move=disjunctive_difference_move(solver,state.x,state.lo,state.hi,probe,{},in_budget,64,unsigned(round));
                            if(move.empty())continue;
                            auto candidate=state.evaluate(std::move(move),false);
                            // The linear helper can skip unsupported hard
                            // clauses; only exact full-formula feasibility
                            // authorizes choosing its proposed assignment.
                            if(!candidate.violations)choose(std::move(candidate));
                            if(have)break;
                        }
                    }
                    // Move objects within the current feasible container (or
                    // analogous linear slack) before attempting to shrink it.
                    // Keep the pressure field fixed so neutral moves cannot
                    // cycle, and never report them as new objective incumbents.
                    if(feasible&&!have&&variables.size()<=100&&neutral_steps<64&&elapsed()<timeout){
                        auto in_budget=[&](){return elapsed()<timeout;};
                        if(plateau.empty()&&neutral_steps==0)plateau=state.plateau_field(in_budget);
                        if(state.neutral_step(plateau,selected,in_budget)){have=true;neutral=true;}
                    }
                    if(elapsed()>=timeout)break;
                    if(have){
                        state.apply(selected);++steps;
                        if(neutral){
                            ++neutral_steps;radius=1;stale=0;
                            if(has_best&&state.objective==best_value)feasible_representative=state.x;
                        }else if(state.unsat.empty()){
                            plateau.clear();neutral_steps=0;
                            Assignment previous=best;
                            if((!has_best||better(state.objective,best_value))&&accept(state.x)){accelerate(previous);feasible_representative=best;stale=0;radius=std::min(uint64_t(1)<<40,radius*2);}
                            else ++stale;
                        }else ++stale;
                    }else ++stale;
                    if(elapsed()>=timeout)break;
                    if((feasible&&!have)||stale>300){
                        if(has_best)state.reset(feasible_representative.empty()?best:feasible_representative);
                        plateau.clear();neutral_steps=0;
                        // First try an improving objective kick with the least
                        // damage; otherwise diversify decision variables.
                        State::Evaluation kick;bool has_kick=false;
                        if(has_best&&round%3!=0)for(size_t v:state.objective_search_variables){
                            for(auto value:state.candidates(v,std::min(uint64_t(1)<<40,uint64_t(1)<<(round%41)),false)){
                                if(elapsed()>=timeout)break;
                                auto e=state.evaluate({{v,value}},true,true);if(e.cost==UINT64_MAX||!better(e.objective,best_value))continue;
                                if(!has_kick||e.violations<kick.violations||(e.violations==kick.violations&&e.distance<kick.distance)){kick=std::move(e);has_kick=true;}
                                // Switch a guarding Boolean together with the
                                // objective, then repair the activated branch.
                                std::set<size_t> guards;
                                for(int c:solver._vars[v].clause_idxs)for(int id:solver._clauses[c].bool_literals)guards.insert(size_t(solver._lits[std::abs(id)].delta));
                                unsigned tried=0;
                                for(size_t b:guards){if(elapsed()>=timeout)break;auto macro=state.evaluate({{v,value},{b,-state.x[b]}},true,true);
                                    if(macro.cost!=UINT64_MAX&&better(macro.objective,best_value)&&(!has_kick||macro.violations<kick.violations||(macro.violations==kick.violations&&macro.distance<kick.distance))){kick=std::move(macro);has_kick=true;}
                                    if(++tried>=8)break;}
                            }
                            if(elapsed()>=timeout)break;
                        }
                        if(elapsed()>=timeout)break;
                        if(has_kick)state.apply(kick);
                        const unsigned kicks=has_kick?0:1+solver.mt()%std::min(size_t(4),variables.size());
                        for(unsigned k=0;k<kicks;++k){if(elapsed()>=timeout)break;size_t v=variables[solver.mt()%variables.size()];auto points=state.candidates(v,1+solver.mt()%32,false);if(points.empty())continue;
                            auto e=state.evaluate({{v,points[solver.mt()%points.size()]}},true,true);if(e.cost!=UINT64_MAX&&elapsed()<timeout)state.apply(e);}
                        radius=1;stale=0;
                    }
                    if(!state.unsat.empty()&&round%32==0){for(size_t c:state.unsat)if(state.weight[c]<100000)++state.weight[c];state.rescore();}
                }
                if(beaten())termination=stop_reason();
            }
        }
    }catch(const std::exception& e){error=e.what();termination="error";}
    std::string status=has_best?"sat":(termination=="preprocessing_unsat"?"unsat":(error.empty()?"unknown":"error"));
    std::cout<<std::setprecision(12)<<"{\"event\":\"final\",\"status\":"<<quote(status)
        <<",\"direction\":"<<quote(solver.omt_direction)<<",\"objective_value\":"<<(has_best?quote(best_value.get_str()):"null")
        <<",\"elapsed_seconds\":"<<elapsed()<<",\"time_to_best_seconds\":"<<(has_best?std::to_string(best_time):"null")
        <<",\"model_validated\":"<<(has_best?"true":"false")<<",\"is_optimal\":"<<(optimality.proved?"true":"false")<<",\"steps\":"<<steps
        <<",\"termination\":"<<quote(termination)<<",\"error\":"<<quote(error)
        <<",\"large_target_magnitude\":"<<(large_schedule.enabled()?quote(large_schedule.magnitude().get_str()):"null")
        <<",\"large_target_attempts\":"<<large_attempts<<",\"large_target_slices\":"<<large_slices
        <<",\"large_target_seconds\":"<<large_seconds<<",\"large_target_reached\":"<<(large_schedule.reached()?"true":"false")
        <<",\"large_target_status\":"<<quote(large_schedule.reached()?"reached":large_status)
        <<",\"is_unbounded\":"<<(unbounded.proved?"true":"false")
        <<",\"unbounded_threshold\":"<<(unbounded_threshold.empty()?"null":quote(proof_threshold.get_str()))
        <<",\"unbounded_attempts\":"<<unbounded_attempts<<",\"unbounded_queries\":"<<unbounded.queries
        <<",\"unbounded_seconds\":"<<unbounded_seconds<<",\"unbounded_status\":"<<quote(unbounded_status)
        <<",\"time_to_unbounded_seconds\":";
    if(unbounded.proved)std::cout<<unbounded_time;else std::cout<<"null";
    std::cout<<",\"unbounded_certificate\":"<<(unbounded.proved?unbounded.certificate_json:"null")
        <<",\"prove_optimal\":"<<(optimality_options.enabled?"true":"false")
        <<",\"optimality_stall_seconds\":"<<optimality_options.stall_seconds
        <<",\"optimality_timeout_seconds\":"<<optimality_options.timeout_seconds
        <<",\"optimality_attempts\":"<<optimality_attempts
        <<",\"optimality_seconds\":"<<optimality_seconds
        <<",\"optimality_status\":"<<quote(optimality_status)
        <<",\"optimality_reason\":"<<quote(optimality.reason)
        <<",\"optimality_proved_value\":"<<(optimality.proved?quote(best_value.get_str()):"null")
        <<",\"time_to_optimality_seconds\":";
    if(optimality.proved)std::cout<<optimality_time;else std::cout<<"null";
    std::cout<<"}"<<std::endl;
    return error.empty()?0:2;
}
}
