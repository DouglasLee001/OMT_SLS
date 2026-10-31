#pragma once

#include "nia_ls.h"
#include "nia_omt_input.h"
#include <gmpxx.h>
#include <functional>
#include <limits>
#include <map>
#include <set>

namespace nia {
namespace omt_slack_detail {
using Integer=mpz_class;
using Move=std::vector<std::pair<size_t,__int128_t>>;
constexpr size_t max_bits=65536;
inline Integer integer(__int128_t value) {return Integer(format_omt_integer(value),10);}
inline bool bounded(const Integer& value) {
    return mpz_sizeinbase(value.get_mpz_t(),2)<=max_bits;
}
inline bool bounded_power(Integer& result,const Integer& base,unsigned exponent) {
    if(!bounded(base))return false;
    if(!exponent){result=1;return true;}
    if(base==0){result=0;return true;}
    if(mpz_cmpabs_ui(base.get_mpz_t(),1)==0){result=base<0&&(exponent&1)?-1:1;return true;}
    const size_t bits=mpz_sizeinbase(base.get_mpz_t(),2);
    if(exponent>max_bits/bits)return false;
    mpz_pow_ui(result.get_mpz_t(),base.get_mpz_t(),exponent);
    return bounded(result);
}
inline bool bounded_multiply(Integer& result,const Integer& value) {
    if(!bounded(result)||!bounded(value))return false;
    if(result==0||value==0){result=0;return true;}
    if(value==1)return true;
    if(value==-1){result=-result;return true;}
    if(result==1){result=value;return true;}
    if(result==-1){result=-value;return true;}
    if(mpz_sizeinbase(result.get_mpz_t(),2)>max_bits-mpz_sizeinbase(value.get_mpz_t(),2))return false;
    result*=value;return bounded(result);
}
inline bool bounded_add(Integer& result,const Integer& value) {
    if(!bounded(result)||!bounded(value))return false;
    if(mpz_sgn(result.get_mpz_t())==mpz_sgn(value.get_mpz_t())&&result!=0&&
       std::max(mpz_sizeinbase(result.get_mpz_t(),2),mpz_sizeinbase(value.get_mpz_t(),2))==max_bits)return false;
    result+=value;return bounded(result);
}
struct Row {
    bool computed=false,supported=false,derived=false;
    Integer old_value=0,probe_value=0;
    std::set<size_t> variables;
};
}

// Propose one extra integer coordinate to restore a formerly true arithmetic
// witness broken by the raw primary probe. This is an incomplete neighborhood,
// not a feasibility check: every full move still needs caller projection,
// exact evaluation of the original formula, and the true objective check.
// In particular, no objective coordinate or previously projected cost is frozen.
inline std::vector<std::vector<std::pair<size_t,__int128_t>>> omt_slack_repair_moves(
    const ls_solver& solver,const std::vector<__int128_t>& assignment,
    const std::vector<__int128_t>& lo,const std::vector<__int128_t>& hi,
    const std::vector<std::pair<size_t,__int128_t>>& primary,
    const std::set<size_t>& derived,const std::function<bool()>& in_budget,
    size_t cap=16) {
    using namespace omt_slack_detail;
    std::vector<Move> result;
    bool cancelled=false;
    auto available=[&](){
        if(!cancelled&&in_budget&&!in_budget())cancelled=true;
        return !cancelled;
    };
    auto arithmetic=[&](bool supported){if(!supported)cancelled=true;return supported;};
    const size_t count=assignment.size();
    if(!available()||!cap||primary.empty()||count!=solver._num_vars||
       solver._vars.size()<count||lo.size()<count||hi.size()<count)return {};
    std::map<size_t,__int128_t> fixed;
    auto probe=assignment;
    for(const auto& item:primary) {
        if(!available()||item.first>=count||!solver._vars[item.first].is_nia||
           item.second<lo[item.first]||item.second>hi[item.first])return {};
        const auto inserted=fixed.emplace(item.first,item.second);
        if(!inserted.second&&inserted.first->second!=item.second)return {};
        probe[item.first]=item.second;
    }
    for(size_t variable:derived)if(variable>=count)return {};
    for(size_t variable=0;variable<count;++variable)
        if(!available()||lo[variable]>hi[variable])return {};

    // Bound arithmetic traversal as well as publication. This allowance only
    // truncates a neighborhood; budget cancellation discards all proposals.
    const size_t work_cap=cap>std::numeric_limits<size_t>::max()/64?
        std::numeric_limits<size_t>::max():cap*64;
    size_t work=0;
    auto step=[&](){
        if(!available()||work>=work_cap)return false;
        ++work;return true;
    };
    std::map<size_t,Row> rows;
    auto row_for=[&](size_t id)->const Row* {
        if(id>=solver._lits.size())return nullptr;
        auto& row=rows[id];
        if(row.computed)return row.supported?&row:nullptr;
        row.computed=true;
        const auto& literal=solver._lits[id];
        if(!literal.is_nia_lit)return nullptr;
        row.old_value=row.probe_value=integer(literal.key);
        for(const auto& ct:literal.coff_terms) {
            if(!step())return nullptr;
            if(!ct.coff)continue;
            if(ct.term_idx<0||size_t(ct.term_idx)>=solver._terms.size())return nullptr;
            Integer old_term=integer(ct.coff),probe_term=old_term;
            for(const auto& factor:solver._terms[ct.term_idx].var_epxs) {
                if(!step()||factor.exponent<0||factor.var_index<0||
                   size_t(factor.var_index)>=count||!solver._vars[factor.var_index].is_nia)return nullptr;
                if(!factor.exponent)continue;
                const size_t variable=size_t(factor.var_index);
                row.variables.insert(variable);row.derived|=derived.count(variable)>0;
                Integer power;
                if(!arithmetic(bounded_power(power,integer(assignment[variable]),factor.exponent))||
                   !arithmetic(bounded_multiply(old_term,power))||
                   !arithmetic(bounded_power(power,integer(probe[variable]),factor.exponent))||
                   !arithmetic(bounded_multiply(probe_term,power)))return nullptr;
            }
            if(!arithmetic(bounded_add(row.old_value,old_term))||
               !arithmetic(bounded_add(row.probe_value,probe_term)))return nullptr;
        }
        row.supported=true;return &row;
    };
    auto truth=[&](size_t id,const Integer& value) {
        return solver._lits[id].is_equal?value==0:value<=0;
    };
    auto affine=[&](size_t id,size_t variable,Integer& a,Integer& c) {
        a=0;c=integer(solver._lits[id].key);
        for(const auto& ct:solver._lits[id].coff_terms) {
            if(!step())return false;
            if(!ct.coff)continue;
            Integer coefficient=integer(ct.coff);
            unsigned degree=0;
            for(const auto& factor:solver._terms[ct.term_idx].var_epxs) {
                if(!step())return false;
                if(size_t(factor.var_index)==variable) {
                    // Only whether the total degree exceeds one matters.
                    degree=std::min(2u,degree+unsigned(std::min(factor.exponent,2)));
                } else {
                    Integer power;
                    if(!arithmetic(bounded_power(power,integer(probe[factor.var_index]),factor.exponent))||
                       !arithmetic(bounded_multiply(coefficient,power)))return false;
                }
            }
            if(coefficient==0)continue;
            if(degree>1)return false;
            if(!arithmetic(bounded_add(degree?a:c,coefficient)))return false;
        }
        return a!=0;
    };
    auto publish=[&](size_t variable,const Integer& value) {
        if(!available()||!arithmetic(bounded(value))||result.size()>=cap||
           value<integer(lo[variable])||value>integer(hi[variable]))return;
        __int128_t native;
        if(!parse_omt_integer(value.get_str(),native)||native==probe[variable])return;
        auto coordinates=fixed;coordinates.emplace(variable,native);
        Move move(coordinates.begin(),coordinates.end());
        if(std::find(result.begin(),result.end(),move)==result.end())result.push_back(std::move(move));
    };
    // Solve a*b+c<=0 exactly over the caller's native search domain. Publish
    // the closest legal integer and adjacent integers at the clipped boundary.
    auto half_line=[&](size_t variable,const Integer& a,const Integer& c) {
        Integer lower=integer(lo[variable]),upper=integer(hi[variable]),boundary,numerator=-c;
        if(a>0) {
            mpz_fdiv_q(boundary.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());
            if(boundary<upper)upper=boundary;
        } else {
            mpz_cdiv_q(boundary.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());
            if(boundary>lower)lower=boundary;
        }
        if(!arithmetic(bounded(boundary)))return;
        if(lower>upper)return;
        Integer nearest=integer(probe[variable]);
        if(nearest<lower)nearest=lower;if(nearest>upper)nearest=upper;
        publish(variable,nearest);
        if(nearest>lower) {
            Integer next=nearest;if(!arithmetic(bounded_add(next,-1)))return;
            publish(variable,next);
        }
        if(nearest<upper) {
            Integer next=nearest;if(!arithmetic(bounded_add(next,1)))return;
            publish(variable,next);
        }
    };

    for(const auto& clause:solver._clauses) {
        if(result.size()>=cap||!step())break;
        bool satisfied=false;
        std::vector<std::pair<int,const Row*>> witnesses;
        for(int signed_id:clause.literals) {
            if(!step())break;
            if(!signed_id)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            if(id>=solver._lits.size())continue;
            const auto& literal=solver._lits[id];
            if(!literal.is_nia_lit) {
                if(literal.delta>=0&&literal.delta<__int128_t(count)&&
                   (probe[size_t(literal.delta)]>0)==(signed_id>0))satisfied=true;
                continue;
            }
            const auto* row=row_for(id);
            if(!row)continue;
            if(truth(id,row->probe_value)==(signed_id>0))satisfied=true;
            if(!row->derived&&truth(id,row->old_value)==(signed_id>0)&&
               truth(id,row->probe_value)!=(signed_id>0))witnesses.push_back({signed_id,row});
        }
        if(satisfied)continue;
        for(const auto& witness:witnesses) {
            const int signed_id=witness.first;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            for(size_t variable:witness.second->variables) {
                if(result.size()>=cap||!step())break;
                if(fixed.count(variable)||derived.count(variable))continue;
                Integer a,c;
                if(!affine(id,variable,a,c))continue;
                if(solver._lits[id].is_equal&&signed_id>0) {
                    Integer numerator=-c,value;
                    if(mpz_divisible_p(numerator.get_mpz_t(),a.get_mpz_t())) {
                        mpz_divexact(value.get_mpz_t(),numerator.get_mpz_t(),a.get_mpz_t());
                        publish(variable,value);
                    }
                } else if(solver._lits[id].is_equal) {
                    Integer negative_side=c,positive_side=-c;
                    if(!arithmetic(bounded_add(negative_side,1))||
                       !arithmetic(bounded_add(positive_side,1)))return {};
                    half_line(variable,a,negative_side);half_line(variable,-a,positive_side);
                } else if(signed_id>0)half_line(variable,a,c);
                else {
                    Integer complement=-c;if(!arithmetic(bounded_add(complement,1)))return {};
                    half_line(variable,-a,complement);
                }
            }
        }
    }
    return available()?result:std::vector<Move>{};
}
}
