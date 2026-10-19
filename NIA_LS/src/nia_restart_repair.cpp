#include "nia_linearize.h"
#include "nia_lia_sls.h"
#include <chrono>
#include <iostream>

#include "nia_ls.h"
#include "nia_repair_selection.h"
#include <numeric>
#include <unordered_map>
#include <unordered_set>

namespace nia {
// Exact scoring is independent of the search's incremental literal caches.
uint64_t ls_solver::assignment_cost(const std::vector<__int128_t>& assignment,
                                    std::vector<unsigned>* conflicts,
                                    std::vector<std::vector<size_t>>* violated_variables) const {
    if(assignment.size()!=_num_vars)return UINT64_MAX;
    if(conflicts)conflicts->assign(_num_vars,0);
    if(violated_variables)violated_variables->clear();
    for(size_t i=0;i<_num_vars;++i){
        if(_vars[i].is_nia){
            if(assignment[i]<_vars[i].low_bound || assignment[i]>_vars[i].upper_bound)return UINT64_MAX;
        }else if(assignment[i]!=1 && assignment[i]!=-1)return UINT64_MAX;
    }
    std::vector<__int128_t> terms(_terms.size(),0);
    for(size_t i=0;i<_terms.size();++i){
        if(!term_appear[i])continue;
        __int128_t value=1;
        for(const auto& ve:_terms[i].var_epxs){
            if(ve.var_index<0 || size_t(ve.var_index)>=_num_vars || ve.exponent<0)return UINT64_MAX;
            for(int k=0;k<ve.exponent;++k)
                if(__builtin_mul_overflow(value,assignment[ve.var_index],&value))return UINT64_MAX;
        }
        terms[i]=value;
    }
    uint64_t unsat=0;
    for(const auto& clause:_clauses){
        bool satisfied=false;
        for(int index:clause.literals){
            const auto& l=_lits[std::abs(index)];bool truth;
            if(l.is_nia_lit){
                __int128_t delta=l.key;
                for(const auto& ct:l.coff_terms){
                    __int128_t product;
                    if(__builtin_mul_overflow(ct.coff,terms[ct.term_idx],&product) ||
                       __builtin_add_overflow(delta,product,&delta))return UINT64_MAX;
                }
                truth=l.is_equal?delta==0:delta<=0;
            }else truth=assignment[l.delta]>0;
            if(truth==(index>0)){satisfied=true;break;}
        }
        if(satisfied)continue;
        ++unsat;
        if(conflicts || violated_variables){
            std::set<size_t> involved;
            for(int index:clause.literals){
                const auto& l=_lits[std::abs(index)];
                if(!l.is_nia_lit)involved.insert(size_t(l.delta));
                else for(const auto& ct:l.coff_terms)
                    for(const auto& ve:_terms[ct.term_idx].var_epxs)involved.insert(ve.var_index);
            }
            if(conflicts)for(auto v:involved)++(*conflicts)[v];
            if(violated_variables)violated_variables->emplace_back(involved.begin(),involved.end());
        }
    }
    return unsat;
}

// A fresh context owns every fixed equality and finite-domain restriction.
// Only the resulting assignment (or the previous best on failure) survives.
void ls_solver::prepare_restart_repair(){
    ++restart_repair_calls;
    auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    restart_seed=restart_best_assignment;
    if(restart_seed.size()!=_num_vars)
        restart_seed.assign(_solution.begin(),_solution.begin()+_num_vars);
    std::vector<unsigned> conflicts;
    std::vector<std::vector<size_t>> violated_variables;
    uint64_t before=UINT64_MAX,after=UINT64_MAX;bool reference_scored=false;
    unsigned fixed=0,bounded=0,widened=0,sparse=0;bool installed=false,attempted=false;
    restart_repair_last_fixed=0;restart_repair_widened=0;restart_repair_sparse=0;
    restart_repair_fixed_variables.clear();
    if(restart_repair_domain_edges.size()!=_num_vars)restart_repair_domain_edges.assign(_num_vars,0);
    const char* reason="budget";
    const char* phase="score_reference";
    double selection_seconds=0,build_seconds=0,search_seconds=0;
    auto finish=[&](){
        double seconds=elapsed();restart_repair_spent+=seconds;
        if(attempted){
            if(installed)restart_repair_failures=0;
            else if(restart_repair_failures<UINT32_MAX)++restart_repair_failures;
        }
        restart_repair_last_fixed=fixed;restart_repair_widened=widened;restart_repair_sparse=sparse;
        std::cerr<<"restart_repair round="<<restart_repair_calls<<" status="<<reason
                 <<" fixed="<<fixed<<" free="<<(_num_vars-fixed)<<" bounded="<<bounded
                 <<" sparse="<<sparse<<" widened="<<widened<<" failures="<<restart_repair_failures
                 <<" before="<<(reference_scored?std::to_string(before):"skipped")
                 <<" after="<<(reference_scored?std::to_string(after):"skipped")<<" installed="<<installed
                 <<" phase="<<phase<<" selection_seconds="<<selection_seconds
                 <<" build_seconds="<<build_seconds<<" search_seconds="<<search_seconds
                 <<" seconds="<<seconds<<" spent="<<restart_repair_spent<<"\n";
    };
    double budget=std::min(restart_repair_seconds,restart_repair_budget-restart_repair_spent);
    budget=std::min(budget,_cutoff-TimeElapsed());
    if(budget<=0){phase="budget_gate";finish();return;}
    before=assignment_cost(restart_seed,&conflicts,&violated_variables);after=before;reference_scored=true;
    if(before==UINT64_MAX){
        restart_seed.clear();reason="invalid_reference";finish();return;
    }
    attempted=true;
    if(elapsed()>=budget*0.6){reason="preparation_budget";finish();return;}
    // Reserve at least 40% of this call for LIA search. Optional selection
    // and distant-point discovery have tighter caps within preparation.
    const double preparation_limit=budget*0.6;
    phase="create_context";
    try{
        z3::context c;
        auto numeral=[&](__int128_t value){return c.int_val(print_128(value).c_str());};
        std::vector<z3::expr> vars,reference_values;
        std::unordered_map<unsigned,size_t> variable_indices;
        vars.reserve(_num_vars);reference_values.reserve(_num_vars);
        for(size_t i=0;i<_num_vars;++i){
            std::string name="restart_v"+std::to_string(i);
            vars.push_back(_vars[i].is_nia?c.int_const(name.c_str()):c.bool_const(name.c_str()));
            reference_values.push_back(_vars[i].is_nia?numeral(restart_seed[i]):c.bool_val(restart_seed[i]>0));
            variable_indices.emplace(vars.back().id(),i);
        }
        const size_t offset=_num_vars?(restart_repair_calls-1)%_num_vars:0;
        const unsigned target_fixed=unsigned(_num_vars/(restart_repair_failures>=2?4:2));
        std::vector<bool> anchored(_num_vars,false),movable(_num_vars,false),zero(_num_vars,false);
        for(size_t i=0;i<_num_vars;++i){
            movable[i]=!_vars[i].is_nia || _vars[i].low_bound<_vars[i].upper_bound;
            zero[i]=restart_seed[i]==0;
        }
        std::vector<std::vector<std::pair<size_t,unsigned>>> factor_terms(_num_vars);
        std::vector<unsigned> free_degree(_terms.size(),0);
        for(size_t t=0;t<_terms.size();++t)if(term_appear[t])
            for(const auto& ve:_terms[t].var_epxs){
                free_degree[t]+=ve.exponent;factor_terms[ve.var_index].push_back({t,unsigned(ve.exponent)});
            }
        LiaSolver solver(c);
        phase="select_anchors";double selection_begin=elapsed();
        const double selection_limit=std::min(preparation_limit,selection_begin+std::min(0.05,budget*0.1));
        RepairAnchorSelector selector(conflicts,zero,movable,violated_variables,factor_terms,free_degree,offset);
        for(unsigned k=0;k<target_fixed && elapsed()<selection_limit;++k){
            size_t i=selector.take();if(i==_num_vars)break;
            anchored[i]=true;++fixed;restart_repair_fixed_variables.push_back(i);
            solver.add(vars[i]==reference_values[i]);
        }
        selection_seconds=elapsed()-selection_begin;
        if(elapsed()>=preparation_limit){reason="preparation_budget";finish();return;}
        phase="build_residual";double build_begin=elapsed();
        for(size_t i=0;i<_num_vars;++i)
            solver.prefer(vars[i],reference_values[i]);
        Seeder seed(c,1);
        // Domains for any remaining nonlinear factors are centered at the
        // previous best rather than zero; original bounds are always asserted.
        for(size_t i=0;i<_num_vars;++i)if(_vars[i].is_nia){
            solver.add(vars[i]>=numeral(_vars[i].low_bound));
            solver.add(vars[i]<=numeral(_vars[i].upper_bound));
            const __int128_t limit=INT64_MAX/4;
            int64_t lo=int64_t(std::max(-limit,std::min(limit,_vars[i].low_bound)));
            int64_t hi=int64_t(std::max(-limit,std::min(limit,_vars[i].upper_bound)));
            seed.bounds.emplace(vars[i].id(),std::make_pair(lo,hi));
            seed.preferred.emplace(vars[i].id(),int64_t(std::max(-limit,std::min(limit,restart_seed[i]))));
        }
        // Widen only a small number of free nonlinear factors. Most domains
        // stay at radius one, limiting nested ITE expansion.
        if(restart_repair_failures){
            std::vector<bool> nonlinear(_num_vars,false);
            for(size_t t=0;t<_terms.size();++t){
                if(!term_appear[t])continue;
                unsigned degree=0;
                for(const auto& ve:_terms[t].var_epxs)if(!anchored[ve.var_index])degree+=ve.exponent;
                if(degree>1)for(const auto& ve:_terms[t].var_epxs)
                    if(!anchored[ve.var_index])nonlinear[ve.var_index]=true;
            }
            std::vector<size_t> expand;
            for(size_t i=0;i<_num_vars;++i)
                if(nonlinear[i] && _vars[i].is_nia && _vars[i].low_bound<_vars[i].upper_bound &&
                   (conflicts[i] || restart_repair_domain_edges[i]))expand.push_back(i);
            std::stable_sort(expand.begin(),expand.end(),[&](size_t a,size_t b){
                if(conflicts[a]!=conflicts[b])return conflicts[a]>conflicts[b];
                if(restart_repair_domain_edges[a]!=restart_repair_domain_edges[b])
                    return restart_repair_domain_edges[a]>restart_repair_domain_edges[b];
                return (a+_num_vars-offset)%_num_vars<(b+_num_vars-offset)%_num_vars;
            });
            const size_t cap=std::min(size_t(4),std::max(size_t(1),(_num_vars-fixed+7)/8));
            for(size_t k=0;k<std::min(cap,expand.size());++k)
                seed.radii.emplace(vars[expand[k]].id(),restart_repair_failures>=2?4:2);
        }
        std::vector<z3::expr> terms(_terms.size(),c.int_val(0));
        for(size_t i=0;i<_terms.size();++i){
            if(!term_appear[i])continue;
            if(elapsed()>=preparation_limit){reason="preparation_budget";build_seconds=elapsed()-build_begin;finish();return;}
            z3::expr value=c.int_val(1);
            for(const auto& ve:_terms[i].var_epxs)
                for(int k=0;k<ve.exponent;++k)value=value*(_vars[ve.var_index].is_nia && anchored[ve.var_index]?reference_values[ve.var_index]:vars[ve.var_index]);
            terms[i]=value.simplify();
        }
        std::map<int,z3::expr> literals;
        z3::expr_vector original(c);
        for(const auto& clause:_clauses){
            if(elapsed()>=preparation_limit){reason="preparation_budget";build_seconds=elapsed()-build_begin;finish();return;}
            z3::expr_vector disj(c);
            for(int index:clause.literals){
                int id=std::abs(index);auto it=literals.find(id);
                if(it==literals.end()){
                    const auto& l=_lits[id];z3::expr literal=c.bool_val(false);
                    if(l.is_nia_lit){
                        z3::expr delta=numeral(l.key);
                        for(const auto& ct:l.coff_terms)delta=delta+numeral(ct.coff)*terms[ct.term_idx];
                        literal=l.is_equal?delta==0:delta<=0;
                    }else literal=anchored[size_t(l.delta)]?reference_values[size_t(l.delta)]:vars[size_t(l.delta)];
                    it=literals.emplace(id,literal.simplify()).first;
                }
                disj.push_back(index>0?it->second:!it->second);
            }
            original.push_back(z3::mk_or(disj).simplify());
        }
        std::set<unsigned> seen;
        for(auto clause:original){seed.units(clause);seed.count(clause,seen);}
        // After two failures, try a bounded sparse domain containing nearby
        // values and distant linear critical points from the residual clauses.
        const double critical_limit=std::min(preparation_limit,elapsed()+std::min(0.02,budget*0.04));
        if(restart_repair_failures>=2)for(const auto& radius:seed.radii){
            if(elapsed()>=critical_limit)break;
            auto pos=std::find_if(vars.begin(),vars.end(),[&](const z3::expr& v){return v.id()==radius.first;});
            size_t index=size_t(pos-vars.begin());auto variable=*pos;
            auto bound=seed.bounds.at(variable.id());
            if(bound.second-bound.first<=8)continue;
            int64_t center=std::max(bound.first,std::min(bound.second,seed.preferred.at(variable.id())));
            std::set<int64_t> points;
            for(int d=-1;d<=1;++d)points.insert(std::max(bound.first,std::min(bound.second,center+d)));
            std::vector<z3::expr> pending;for(auto clause:original)pending.push_back(clause);
            std::set<unsigned> seen;unsigned atoms=0;bool distant=false;
            while(!pending.empty() && atoms<64 && points.size()<9){
                if(elapsed()>=critical_limit)break;
                auto atom=pending.back();pending.pop_back();
                if(!seen.insert(atom.id()).second)continue;
                auto kind=atom.decl().decl_kind();
                if(atom.num_args()==2 && atom.arg(0).is_int() && atom.arg(1).is_int() &&
                   (kind==Z3_OP_EQ || kind==Z3_OP_LE || kind==Z3_OP_GE || kind==Z3_OP_LT || kind==Z3_OP_GT)){
                    ++atoms;
                    // Substitute only leaves of this atom. Passing all n
                    // variables to every small atom rebuilt a large map.
                    z3::expr_vector other_vars(c),other_values(c);
                    std::vector<z3::expr> leaves{atom};std::unordered_set<unsigned> leaf_seen;
                    while(!leaves.empty()){
                        auto leaf=leaves.back();leaves.pop_back();
                        if(!leaf_seen.insert(leaf.id()).second)continue;
                        auto found=variable_indices.find(leaf.id());
                        if(found!=variable_indices.end()){
                            if(found->second!=index){other_vars.push_back(leaf);other_values.push_back(reference_values[found->second]);}
                        }else for(unsigned k=0;k<leaf.num_args();++k)leaves.push_back(leaf.arg(k));
                    }
                    auto reduced=atom.substitute(other_vars,other_values).simplify();
                    LiaPreprocessor::Linear row;
                    if(LiaPreprocessor::difference(reduced,row) && row.coefficients.size()==1 &&
                       row.coefficients.begin()->first==variable.id()){
                        mpz_class root;auto coeff=row.coefficients.begin()->second;
                        mpz_class rhs=-row.constant;mpz_fdiv_q(root.get_mpz_t(),rhs.get_mpz_t(),coeff.get_mpz_t());
                        if(root>=bound.first && root<=bound.second){
                            int64_t value=root.get_si();distant|=value<center-4 || value>center+4;
                            for(int d=-1;d<=1 && points.size()<9;++d)
                                points.insert(std::max(bound.first,std::min(bound.second,value+d)));
                        }
                    }
                }else for(unsigned k=0;k<atom.num_args();++k)pending.push_back(atom.arg(k));
            }
            if(distant)seed.discrete.emplace(variable.id(),std::vector<int64_t>(points.begin(),points.end()));
        }
        for(auto clause:original){
            if(elapsed()>=preparation_limit){reason="preparation_budget";build_seconds=elapsed()-build_begin;finish();return;}
            solver.add(seed.visit(clause));
        }
        for(const auto& domain:seed.domains)solver.add(domain.second);
        bounded=seed.domains.size();
        for(const auto& entry:seed.radii)widened+=seed.domains.count(entry.first);
        for(const auto& entry:seed.discrete)sparse+=seed.domains.count(entry.first);
        build_seconds=elapsed()-build_begin;
        double remaining=budget-elapsed();
        if(remaining<=0){finish();return;}
        solver.set("timeout",std::max(1u,unsigned(1000*remaining)));
        phase="lia_search";double search_begin=elapsed();
        auto status=solver.check();search_seconds=elapsed()-search_begin;
        phase="validate_candidate";
        if(status!=z3::sat && !solver.candidate_available()){reason="unknown";finish();return;}
        auto model=solver.get_model();
        for(auto clause:original)
            if(status==z3::sat && !model.eval(clause,true).simplify().is_true()){
                reason="rejected";finish();return;
            }
        std::vector<__int128_t> candidate=restart_seed;
        for(size_t i=0;i<_num_vars;++i){
            auto value=model.eval(vars[i],true).simplify();
            if(!_vars[i].is_nia){candidate[i]=value.is_true()?1:-1;continue;}
            // Avoid lossy floating conversions and accept the full signed
            // 128-bit range supported by the search's assignment vector.
            std::string number=Z3_get_numeral_string(c,value);size_t pos=0;
            bool negative=!number.empty() && number[0]=='-';if(negative)++pos;
            __uint128_t magnitude=0,limit=(__uint128_t(1)<<127)-(negative?0:1);
            if(pos==number.size())throw std::runtime_error("empty repair numeral");
            for(;pos<number.size();++pos){
                unsigned digit=unsigned(number[pos]-'0');
                if(digit>9 || magnitude>(limit-digit)/10)throw std::runtime_error("repair numeral overflow");
                magnitude=magnitude*10+digit;
            }
            candidate[i]=negative?-__int128_t(magnitude-1)-1:__int128_t(magnitude);
        }
        // Track repeated contact with an artificial domain edge. True bounds
        // cannot be widened and do not count as evidence of a narrow domain.
        for(size_t i=0;i<_num_vars;++i){
            if(!seed.domains.count(vars[i].id())){restart_repair_domain_edges[i]=0;continue;}
            auto options=seed.domain_values(vars[i]);
            auto d=std::make_pair(options.front(),options.back());auto original_bounds=seed.bounds.at(vars[i].id());
            bool edge=(candidate[i]==d.first && d.first>original_bounds.first) ||
                      (candidate[i]==d.second && d.second<original_bounds.second);
            if(edge){if(restart_repair_domain_edges[i]<UINT32_MAX)++restart_repair_domain_edges[i];}
            else restart_repair_domain_edges[i]=0;
        }
        after=assignment_cost(candidate);
        if(after<before || after==0){
            restart_seed=std::move(candidate);installed=true;++restart_repair_installed;reason=after==0?"sat":"improved";
        }else reason="rejected";
        finish();
    }catch(const std::exception& error){
        std::cerr<<"restart_repair_error="<<error.what()<<"\n";
        reason="unsupported";finish();
    }
}
}
