#include "nia_omt_repair.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include "nia_omt_feasibility.h"
#include <gmpxx.h>
#include <climits>
#include <cmath>
#include <stdexcept>

namespace nia {
namespace {
mpz_class integer(__int128_t value) { return mpz_class(format_omt_integer(value),10); }

class ExactCandidate {
    const ls_solver& problem;
    const std::vector<__int128_t>& values;
    std::vector<mpz_class> terms;
    std::vector<bool> computed;
    const mpz_class& term_value(size_t id) {
        if (!computed.at(id)) {
            mpz_class value=1;
            for (const auto& factor:problem._terms.at(id).var_epxs) {
                mpz_class base=integer(values.at(factor.var_index)),power;
                mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),factor.exponent);
                value*=power;
            }
            terms[id]=std::move(value);computed[id]=true;
        }
        return terms[id];
    }
public:
    ExactCandidate(const ls_solver& solver,const std::vector<__int128_t>& assignment)
        :problem(solver),values(assignment),terms(solver._terms.size()),computed(terms.size(),false) {}
    bool hard() {
        if(values.size()!=problem._num_vars)return false;
        for(size_t i=0;i<values.size();++i)
            if(!problem._vars[i].is_nia && values[i]!=-1 && values[i]!=1)return false;
        for(const auto& clause:problem._clauses) {
            bool satisfied=false;
            for(int id:clause.literals) {
                const auto& literal=problem._lits.at(std::abs(id));bool truth;
                if(literal.is_nia_lit) {
                    mpz_class delta=integer(literal.key);
                    for(const auto& ct:literal.coff_terms)delta+=integer(ct.coff)*term_value(ct.term_idx);
                    truth=literal.is_equal?delta==0:delta<=0;
                } else truth=values.at(size_t(literal.delta))>0;
                if(truth==(id>0)){satisfied=true;break;}
            }
            if(!satisfied)return false;
        }
        return true;
    }
    mpz_class objective() {
        mpz_class value=integer(problem.omt_objective_constant);
        for(const auto& ct:problem.omt_objective_terms)value+=integer(ct.coff)*term_value(ct.term_idx);
        return value;
    }
};

void release_search_arrays(ls_solver& solver) {
    delete solver.unsat_clauses;solver.unsat_clauses=nullptr;
    delete solver.sat_clause_with_false_literal;solver.sat_clause_with_false_literal=nullptr;
    delete solver.contain_bool_unsat_clauses;solver.contain_bool_unsat_clauses=nullptr;
    delete solver.false_lit_occur;solver.false_lit_occur=nullptr;
    delete solver.var_in_long_term;solver.var_in_long_term=nullptr;
}

void rebuild_search_state(ls_solver& solver) {
    release_search_arrays(solver);
    solver.var_in_long_term=new Array(int(solver._num_vars+solver._additional_len));
    solver._num_opt=0;solver._num_nia_lits=0;solver._num_bool_lits=0;
    for(auto& v:solver._vars) {
        v.clause_idxs.clear();v.bool_var_in_pos_clause.clear();v.var_lit_terms.clear();
        v.literal_idxs.clear();v.term_exps.clear();v.scoring_coffs.clear();v.scoring_coffs_valid=false;
    }
    solver.term_appear.assign(solver._terms.size()+solver._additional_len,false);
    solver.lit_appear.assign(solver._lits.size(),false);
    for(size_t t=0;t<solver._terms.size();++t) {
        const auto& term=solver._terms[t];solver.term_appear[t]=true;
        for(const auto& factor:term.var_epxs) {
            solver._vars.at(factor.var_index).term_exps.emplace_back(t,factor.exponent);
            if(factor.exponent>=2 || term.var_epxs.size()>2)solver.var_in_long_term->insert_element(factor.var_index);
        }
    }
    for(size_t id=1;id<=solver._num_lits;++id) {
        auto& literal=solver._lits[id];literal.var_lit_terms.clear();solver.lit_appear[id]=true;
        if(!literal.is_nia_lit) {
            ++solver._num_bool_lits;solver._vars.at(size_t(literal.delta)).literal_idxs.push_back(id);continue;
        }
        ++solver._num_nia_lits;
        for(const auto& ct:literal.coff_terms)for(auto factor:solver._terms.at(ct.term_idx).var_epxs) {
            if(++solver._num_opt>uint64_t((INT_MAX-32)/3))throw std::runtime_error("too many repair operations");
            var_lit_term occurrence(factor,ct.term_idx,id,ct.coff);
            literal.var_lit_terms.push_back(occurrence);
            solver._vars[factor.var_index].var_lit_terms.push_back(occurrence);
            solver._vars[factor.var_index].literal_idxs.push_back(id);
        }
        std::sort(literal.var_lit_terms.begin(),literal.var_lit_terms.end(),[](const var_lit_term& a,const var_lit_term& b) {
            return a.var_idx<b.var_idx || (a.var_idx==b.var_idx && a.term_idx<b.term_idx);
        });
    }
    for(size_t c=0;c<solver._clauses.size();++c) {
        auto& clause=solver._clauses[c];clause.nia_literals.clear();clause.bool_literals.clear();
        clause.weight=1;clause.sat_count=0;clause.watch_lit_idx=0;
        for(int id:clause.literals) {
            const auto& literal=solver._lits.at(std::abs(id));
            if(literal.is_nia_lit) {
                clause.nia_literals.push_back(id);
                for(const auto& ct:literal.coff_terms)for(const auto& factor:solver._terms.at(ct.term_idx).var_epxs)
                    solver._vars[factor.var_index].clause_idxs.push_back(c);
            } else {
                clause.bool_literals.push_back(id);
                auto& var=solver._vars.at(size_t(literal.delta));
                var.clause_idxs.push_back(c);var.bool_var_in_pos_clause.push_back(id>0);
            }
        }
    }
    for(auto& v:solver._vars) {
        std::sort(v.literal_idxs.begin(),v.literal_idxs.end());
        v.literal_idxs.erase(std::unique(v.literal_idxs.begin(),v.literal_idxs.end()),v.literal_idxs.end());
        if(v.is_nia) {
            std::sort(v.clause_idxs.begin(),v.clause_idxs.end());
            v.clause_idxs.erase(std::unique(v.clause_idxs.begin(),v.clause_idxs.end()),v.clause_idxs.end());
        }
    }
    solver._tmp_vars=solver._vars;solver._resolution_vars=solver._vars;
    solver.best_found_cost=int(solver._num_clauses);
    solver.make_space(); // Reallocates the four released clause/literal Arrays and scoring index.
    solver._pre_value_1.assign(solver._num_vars+solver._additional_len,INT32_MAX);
    solver._pre_value_2.assign(solver._num_vars+solver._additional_len,INT32_MAX);
}

bool legacy_integer(const mpz_class& value,__int128_t& destination) {
    // Critical-operation generation still narrows its coefficients and key to
    // int. This optional fallback declines such input; the GMP engine remains
    // responsible for wider objective polynomials.
    return value>=INT_MIN && value<=INT_MAX && parse_omt_integer(value.get_str(),destination);
}
}

struct OmtBoundRepair::Impl {
    const ls_solver& original;
    std::unique_ptr<ls_solver> augmented;
    std::unique_ptr<OmtFeasibilityContinuation> continuation;
    mpz_class incumbent;
    mpz_class threshold;
    bool finite_probe=false;
    bool minimize=false;
    Status state=Status::unsupported;
    std::vector<__int128_t> accepted;

    Impl(const std::string& original_native_path,const ls_solver& source,
         const std::vector<__int128_t>& seed,const std::string& incumbent_exact,unsigned random_seed,
         const std::string* finite_threshold=nullptr)
        :original(source) {
        if(!original.omt_loaded||seed.size()!=original._num_vars)
            throw std::runtime_error("invalid original problem or incumbent size");
        if(incumbent_exact.empty() || incumbent_exact.find_first_not_of("-0123456789")!=std::string::npos)
            throw std::runtime_error("invalid incumbent integer");
        incumbent=mpz_class(incumbent_exact,10);
        finite_probe=finite_threshold!=nullptr;
        if(finite_probe) {
            const auto& text=*finite_threshold;
            const size_t begin=!text.empty()&&text.front()=='-'?1:0;
            if(begin==text.size()||text.find_first_not_of("0123456789",begin)!=std::string::npos)
                throw std::runtime_error("invalid finite threshold integer");
            threshold=mpz_class(text,10);
        }
        ExactCandidate prior(original,seed);
        if(!prior.hard()||prior.objective()!=incumbent)
            throw std::runtime_error("incumbent fails exact original validation");
        augmented.reset(new ls_solver(int(random_seed),UINT64_MAX,true));
        auto& repaired=*augmented;
        std::ifstream file(original_native_path);std::string error;
        if(!file||!repaired.load_omt_instance(file,error)||repaired.build_unsat)
            throw std::runtime_error("cannot import bound-repair original: "+error);
        if(repaired._num_vars!=original._num_vars||repaired.omt_direction!=original.omt_direction)
            throw std::runtime_error("bound-repair original topology mismatch");
        for(size_t v=0;v<original._num_vars;++v)
            if(repaired._vars[v].var_name!=original._vars[v].var_name||repaired._vars[v].is_nia!=original._vars[v].is_nia)
                throw std::runtime_error("bound-repair original variable mismatch");
        minimize=repaired.omt_direction=="min";
        if(!minimize&&repaired.omt_direction!="max")throw std::runtime_error("unsupported objective direction");
        if(finite_probe&&(minimize?threshold>=incumbent:threshold<=incumbent))
            throw std::runtime_error("finite threshold does not strictly improve own incumbent");
        if(repaired._num_lits>=uint64_t(INT_MAX-32)||repaired._num_clauses>=uint64_t(INT_MAX-32))
            throw std::runtime_error("too many bound-repair literals or clauses");
        lit bound{};bound.is_nia_lit=true;bound.is_equal=false;bound.lits_index=int(repaired._num_lits+1);
        mpz_class key=integer(repaired.omt_objective_constant)-(finite_probe?threshold:incumbent);
        if(!minimize)key=-key;
        if(!finite_probe)key+=1;
        auto bound_integer=[&](const mpz_class& value,__int128_t& destination) {
            return finite_probe?parse_omt_integer(value.get_str(),destination):legacy_integer(value,destination);
        };
        if(!bound_integer(key,bound.key))throw std::runtime_error(finite_probe?
            "finite bound key exceeds signed 128-bit range":"bound key unsupported by legacy int arithmetic");
        for(const auto& ct:repaired.omt_objective_terms) {
            mpz_class coefficient=integer(ct.coff);if(!minimize)coefficient=-coefficient;
            __int128_t value;
            if(!bound_integer(coefficient,value))throw std::runtime_error(finite_probe?
                "finite bound coefficient exceeds signed 128-bit range":"bound coefficient unsupported by legacy int arithmetic");
            bound.coff_terms.emplace_back(ct.term_idx,value);
        }
        if(bound.coff_terms.empty())throw std::runtime_error("constant objective has no strict bound-repair moves");
        if(finite_probe) {
            repaired.wide_omt_moves=true;
            const __int128_t maximum=__int128_t((__uint128_t(1)<<127)-1),minimum=-maximum-1;
            // These old sentinels are search restrictions, not hard clauses.
            // Keep all original clauses and widen only this private proposal
            // engine; its checked arithmetic may still decline large terms.
            for(auto& variable:repaired._vars)if(variable.is_nia) {
                variable.low_bound=minimum;variable.upper_bound=maximum;
            }
        }
        ++repaired._num_lits;
        repaired._lits.resize(repaired._num_lits+repaired._additional_len+1);
        repaired._lits[bound.lits_index]=std::move(bound);
        clause unit{};unit.literals.push_back(int(repaired._num_lits));repaired._clauses.push_back(std::move(unit));
        repaired._num_clauses=repaired._clauses.size();
        rebuild_search_state(repaired);
        repaired.restart_repair_enabled=original.restart_repair_enabled;
        repaired.equality_protection=original.equality_protection;
        repaired.restart_stall_limit=original.restart_stall_limit;
        repaired.restart_repair_seconds=original.restart_repair_seconds;
        // The continuation applies the caller's remaining global deadline on
        // each resume; a short slice must not discard this normal allowance.
        repaired.restart_repair_budget=original.restart_repair_budget;
        repaired.restart_seed=seed;repaired.initialization_round=1;
        repaired.last_op_var=UINT64_MAX;repaired.last_op_value=0;
        continuation.reset(new OmtFeasibilityContinuation(repaired));
        state=Status::paused;
    }
};

OmtBoundRepair::OmtBoundRepair(const std::string& path,const ls_solver& original,
                             const std::vector<__int128_t>& seed,const std::string& exact,unsigned random_seed) {
    try {impl_.reset(new Impl(path,original,seed,exact,random_seed));}
    catch(const std::exception& exception) {
        std::cerr<<"omt_bound_repair unsupported="<<exception.what()<<'\n';
    }
}
OmtBoundRepair::OmtBoundRepair(const std::string& path,const ls_solver& original,
                             const std::vector<__int128_t>& seed,const std::string& exact,unsigned random_seed,
                             const std::string& finite_threshold) {
    try {impl_.reset(new Impl(path,original,seed,exact,random_seed,&finite_threshold));}
    catch(const std::exception& exception) {
        std::cerr<<"omt_finite_probe unsupported="<<exception.what()<<'\n';
    }
}
OmtBoundRepair::~OmtBoundRepair()=default;
OmtBoundRepair::OmtBoundRepair(OmtBoundRepair&&) noexcept=default;
OmtBoundRepair& OmtBoundRepair::operator=(OmtBoundRepair&&) noexcept=default;
bool OmtBoundRepair::supported() const {return impl_&&impl_->state!=Status::unsupported;}
OmtBoundRepair::Status OmtBoundRepair::status() const {return impl_?impl_->state:Status::unsupported;}
bool OmtBoundRepair::initialized() const {return impl_&&impl_->continuation&&impl_->continuation->initialized();}
uint64_t OmtBoundRepair::steps_completed() const {return impl_&&impl_->continuation?impl_->continuation->steps_completed():0;}
uint64_t OmtBoundRepair::initialization_round() const {
    return impl_&&impl_->augmented?impl_->augmented->initialization_round-1:0;
}
std::mt19937 OmtBoundRepair::random_state() const {
    return impl_&&impl_->continuation?impl_->continuation->random_state():std::mt19937(0);
}

bool OmtBoundRepair::resume_steps(uint64_t max_steps,double slice,double global_remaining,
                                 std::vector<__int128_t>& result) {
    result.clear();
    const auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    auto remaining=[&](){return global_remaining-elapsed();};
    if(!supported()||impl_->state==Status::exhausted||max_steps==0||!(slice>0)||!(global_remaining>0))return false;
    try {
        if(impl_->state==Status::sat) {
            if(remaining()<=0)return false;
            result=impl_->accepted;
            if(remaining()<=0){result.clear();return false;}
            return true;
        }
        const double global=remaining(),allotted=slice-elapsed();
        if(!(global>0)||!(allotted>0))return false;
        const auto state=impl_->continuation->resume_steps(max_steps,allotted,global);
        if(state==OmtFeasibilityContinuation::Status::exhausted){impl_->state=Status::exhausted;return false;}
        if(state!=OmtFeasibilityContinuation::Status::sat||remaining()<=0)return false;
        const auto& repaired=*impl_->augmented;
        std::vector<__int128_t> candidate(repaired._solution.begin(),repaired._solution.begin()+repaired._num_vars);
        ExactCandidate augmented_exact(repaired,candidate),original_exact(impl_->original,candidate);
        if(!augmented_exact.hard()||!original_exact.hard())
            throw std::runtime_error("bound candidate fails exact hard validation");
        const mpz_class objective=original_exact.objective();
        if(impl_->minimize?objective>=impl_->incumbent:objective<=impl_->incumbent)
            throw std::runtime_error("bound candidate does not strictly improve own incumbent");
        if(impl_->finite_probe&&(impl_->minimize?objective>impl_->threshold:objective<impl_->threshold))
            throw std::runtime_error("bound candidate misses finite objective threshold");
        if(remaining()<=0)return false;
        impl_->accepted=std::move(candidate);
        impl_->state=Status::sat;
        result=impl_->accepted;
        if(remaining()<=0){result.clear();return false;}
        return true;
    } catch(const std::exception& exception) {
        impl_->state=Status::unsupported;
        std::cerr<<"omt_bound_repair unsupported="<<exception.what()<<'\n';
        result.clear();return false;
    }
}

bool OmtBoundRepair::resume_for(double slice,double global_remaining,std::vector<__int128_t>& result) {
    return resume_steps(UINT64_MAX,slice,global_remaining,result);
}

bool repair_omt_bound(const std::string& path,const ls_solver& original,
                      const std::vector<__int128_t>& seed,const std::string& exact,
                      double budget,unsigned random_seed,std::vector<__int128_t>& result) {
    result.clear();
    if(!original.omt_loaded||!std::isfinite(budget)||budget<=0||seed.size()!=original._num_vars)return false;
    const auto began=std::chrono::steady_clock::now();
    auto remaining=[&](){return budget-std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    OmtBoundRepair repair(path,original,seed,exact,random_seed);
    const double left=remaining();
    if(left<=0||!repair.resume_for(left,left,result)||remaining()<=0){result.clear();return false;}
    return true;
}
}
