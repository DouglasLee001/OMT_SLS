#pragma once

#include "nia_ls.h"
#include <gmpxx.h>
#include <cstdint>
#include <set>

namespace nia {

// Absolute assignment changes for an atomic escape from a zero product.
// These are candidates: the caller still scores all hard clauses exactly.
inline std::vector<std::vector<std::pair<size_t,__int128_t>>> zero_product_moves(
    const ls_solver& solver,const std::vector<__int128_t>& assignment,
    const std::vector<__int128_t>& lo,const std::vector<__int128_t>& hi,
    const std::vector<size_t>& unsat_clauses,size_t maxmoves) {
    using Move=std::vector<std::pair<size_t,__int128_t>>;
    std::vector<Move> result;
    const size_t limit=std::min(maxmoves,size_t(32));
    if(!limit||assignment.size()!=solver._num_vars||lo.size()<assignment.size()||
       hi.size()<assignment.size()||solver._vars.size()<assignment.size())return result;

    auto integer=[](__int128_t value) {
        const bool negative=value<0;
        __uint128_t magnitude=static_cast<__uint128_t>(value);
        if(negative)magnitude=__uint128_t(0)-magnitude;
        mpz_class exact;
        mpz_import(exact.get_mpz_t(),1,1,sizeof(magnitude),0,0,&magnitude);
        if(negative)mpz_neg(exact.get_mpz_t(),exact.get_mpz_t());
        return exact;
    };
    std::vector<mpz_class> values(solver._terms.size());
    std::vector<unsigned char> computed(solver._terms.size(),0);
    auto term_value=[&](size_t id)->const mpz_class* {
        if(id>=solver._terms.size())return nullptr;
        if(computed[id])return computed[id]==1?&values[id]:nullptr;
        bool zero=false;
        for(const auto& factor:solver._terms[id].var_epxs) {
            if(factor.exponent<=0||factor.var_index<0||
               size_t(factor.var_index)>=assignment.size()||
               !solver._vars[factor.var_index].is_nia) {
                computed[id]=2;return nullptr;
            }
            zero|=assignment[factor.var_index]==0;
        }
        mpz_class value=zero?0:1;
        if(!zero)for(const auto& factor:solver._terms[id].var_epxs) {
            mpz_class base=integer(assignment[factor.var_index]),power;
            mpz_pow_ui(power.get_mpz_t(),base.get_mpz_t(),unsigned(factor.exponent));
            value*=power;
        }
        values[id]=std::move(value);computed[id]=1;return &values[id];
    };

    std::set<int> considered_literals;
    std::set<size_t> considered_terms;
    std::set<Move> unique;
    for(size_t clause:unsat_clauses) {
        if(clause>=solver._clauses.size())continue;
        for(int signed_id:solver._clauses[clause].literals) {
            if(!signed_id||!considered_literals.insert(signed_id).second)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            if(id>=solver._lits.size())continue;
            const auto& literal=solver._lits[id];
            if(!literal.is_nia_lit)continue;
            mpz_class delta=integer(literal.key);
            bool supported=true;
            for(const auto& ct:literal.coff_terms) {
                if(!ct.coff)continue;
                const auto* value=term_value(size_t(ct.term_idx));
                if(!value){supported=false;break;}
                delta+=integer(ct.coff)*(*value);
            }
            if(!supported)continue;
            const bool truth=literal.is_equal?delta==0:delta<=0;
            if(truth==(signed_id>0))continue;

            for(const auto& ct:literal.coff_terms) {
                if(!ct.coff||!considered_terms.insert(size_t(ct.term_idx)).second)continue;
                const auto& term=solver._terms[size_t(ct.term_idx)];
                std::set<size_t> zero_variables;
                for(const auto& factor:term.var_epxs)
                    if(assignment[factor.var_index]==0)zero_variables.insert(size_t(factor.var_index));
                if(zero_variables.size()<2||zero_variables.size()>6)continue;
                std::vector<size_t> variables;
                std::vector<std::vector<__int128_t>> options;
                size_t combinations=1;
                bool movable=true;
                for(size_t v:zero_variables) {
                    std::vector<__int128_t> next;
                    const __int128_t positive=std::max(lo[v],__int128_t(1));
                    const __int128_t negative=std::min(hi[v],__int128_t(-1));
                    if(positive<=hi[v])next.push_back(positive);
                    if(negative>=lo[v])next.push_back(negative);
                    if(next.empty()){movable=false;break;}
                    variables.push_back(v);combinations*=next.size();options.push_back(std::move(next));
                }
                if(!movable)continue;
                for(size_t combination=0;combination<combinations;++combination) {
                    // Cover both uniform directions before truncating mixed signs.
                    size_t choices=combination==0?0:combination==1?combinations-1:combination-1;
                    Move move;
                    for(size_t i=0;i<variables.size();++i) {
                        move.push_back({variables[i],options[i][choices%options[i].size()]});
                        choices/=options[i].size();
                    }
                    if(unique.insert(move).second)result.push_back(std::move(move));
                    if(result.size()==limit)return result;
                }
            }
        }
    }
    return result;
}
}
