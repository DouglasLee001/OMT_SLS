#pragma once

#include "nia_ls.h"
#include "nia_omt_input.h"
#include <gmpxx.h>
#include <functional>
#include <map>
#include <set>

namespace nia {
namespace omt_difference_detail {
using Integer=mpz_class;
using Move=std::vector<std::pair<size_t,__int128_t>>;
inline Integer integer(__int128_t value) {
    return Integer(format_omt_integer(value),10);
}
struct Edge {
    size_t from,to;
    Integer bound;
};
struct LinearLiteral {
    bool computed=false,supported=false;
    Integer constant=0,delta=0;
    std::map<size_t,Integer> coefficients;
};

// A row is constant + sum(a_v*x_v) <= 0. Integer division must round down:
// 2*x-2*y+1<=0 is x-y<=-1, rather than x-y<=0.
inline bool edges_for_row(const std::map<size_t,Integer>& coefficients,
                          const Integer& constant,size_t source,
                          std::vector<Edge>& edges) {
    if(coefficients.empty())return constant<=0;
    if(coefficients.size()>2)return false;
    size_t positive=source,negative=source;
    Integer magnitude=0;
    for(const auto& coefficient:coefficients) {
        if(coefficient.second>0) {
            if(positive!=source)return false;
            positive=coefficient.first;
        } else {
            if(negative!=source||coefficient.second==0)return false;
            negative=coefficient.first;
        }
        const Integer absolute=abs(coefficient.second);
        if(magnitude!=0&&magnitude!=absolute)return false;
        magnitude=absolute;
    }
    if(magnitude==0)return false;
    Integer bound,numerator=-constant;
    mpz_fdiv_q(bound.get_mpz_t(),numerator.get_mpz_t(),magnitude.get_mpz_t());
    edges.push_back({negative,positive,std::move(bound)});
    return true;
}
}

// Preserve one currently true difference-constraint witness per supported
// clause, then repair a fixed probe jointly. This only creates a candidate:
// nonlinear or other unsupported clauses still require the caller's exact
// full evaluation. No historical objective target enters this operator.
//
// Edges encode x_to <= x_from + bound. The source potential may also relax;
// subtracting its final value preserves all unary bounds and fixed probes.
inline std::vector<std::pair<size_t,__int128_t>> difference_branch_move(
    const ls_solver& solver,const std::vector<__int128_t>& assignment,
    const std::vector<__int128_t>& lo,const std::vector<__int128_t>& hi,
    std::vector<std::pair<size_t,__int128_t>> requested,std::set<size_t> fixed,
    const std::function<bool()>& in_budget,unsigned branch_variant=0) {
    using namespace omt_difference_detail;
    const size_t count=assignment.size(),source=count;
    bool cancelled=false;
    auto available=[&](){
        if(cancelled)return false;
        if(in_budget&&!in_budget())cancelled=true;
        return !cancelled;
    };
    if(!available()||count!=solver._num_vars||solver._vars.size()<count||
       lo.size()<count||hi.size()<count||requested.empty())return {};

    std::map<size_t,__int128_t> requested_values;
    for(const auto& next:requested) {
        if(!available()||next.first>=count||!solver._vars[next.first].is_nia||
           next.second<lo[next.first]||next.second>hi[next.first])return {};
        auto inserted=requested_values.emplace(next.first,next.second);
        if(!inserted.second&&inserted.first->second!=next.second)return {};
        fixed.insert(next.first);
    }
    // Preserve the complete effective objective probe, including any aliases
    // supplied by the caller, rather than allowing closure to undo its value.
    for(const auto& coefficient:solver.omt_objective_terms) {
        if(!available()||coefficient.term_idx<0||size_t(coefficient.term_idx)>=solver._terms.size())return {};
        if(!coefficient.coff)continue;
        for(const auto& factor:solver._terms[coefficient.term_idx].var_epxs) {
            if(factor.var_index<0||size_t(factor.var_index)>=count)return {};
            fixed.insert(size_t(factor.var_index));
        }
    }
    for(size_t variable:fixed)if(variable>=count)return {};

    std::vector<LinearLiteral> cached(solver._lits.size());
    auto linear_literal=[&](size_t id)->const LinearLiteral* {
        if(id>=solver._lits.size())return nullptr;
        auto& row=cached[id];
        if(row.computed)return row.supported?&row:nullptr;
        row.computed=true;
        const auto& literal=solver._lits[id];
        if(!literal.is_nia_lit)return nullptr;
        row.constant=integer(literal.key);
        for(const auto& coefficient:literal.coff_terms) {
            if(!available())return nullptr;
            if(!coefficient.coff)continue;
            if(coefficient.term_idx<0||size_t(coefficient.term_idx)>=solver._terms.size())return nullptr;
            const auto& term=solver._terms[coefficient.term_idx];
            if(term.var_epxs.empty())row.constant+=integer(coefficient.coff);
            else {
                if(term.var_epxs.size()!=1||term.var_epxs[0].exponent!=1)return nullptr;
                const int variable=term.var_epxs[0].var_index;
                if(variable<0||size_t(variable)>=count||!solver._vars[variable].is_nia)return nullptr;
                row.coefficients[size_t(variable)]+=integer(coefficient.coff);
            }
        }
        row.delta=row.constant;
        for(auto it=row.coefficients.begin();it!=row.coefficients.end();) {
            if(it->second==0)it=row.coefficients.erase(it);
            else {row.delta+=it->second*integer(assignment[it->first]);++it;}
        }
        row.supported=true;return &row;
    };

    std::vector<Edge> edges;
    size_t selected_edges=0;
    for(const auto& clause:solver._clauses) {
        if(!available())return {};
        bool boolean_witness=false;
        for(int signed_id:clause.literals) {
            if(!available())return {};
            if(!signed_id)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            if(id>=solver._lits.size())return {};
            const auto& literal=solver._lits[id];
            if(literal.is_nia_lit)continue;
            if(literal.delta<0||size_t(literal.delta)>=count)return {};
            if((assignment[size_t(literal.delta)]>0)==(signed_id>0)){boolean_witness=true;break;}
        }
        if(boolean_witness)continue; // Boolean assignments are never changed.
        std::vector<std::vector<Edge>> alternatives;
        for(int signed_id:clause.literals) {
            if(!available())return {};
            if(!signed_id)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            const auto* literal=linear_literal(id);
            if(!literal)continue;
            const bool equal=solver._lits[id].is_equal;
            const bool truth=equal?literal->delta==0:literal->delta<=0;
            if(truth!=(signed_id>0))continue;
            auto coefficients=literal->coefficients;
            Integer constant=literal->constant;
            std::vector<Edge> alternative;
            if(equal&&signed_id>0) {
                if(!edges_for_row(coefficients,constant,source,alternative))continue;
                for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                if(!edges_for_row(coefficients,-constant,source,alternative))continue;
            } else {
                // A negative equality keeps the strict side occupied by the
                // current assignment. All complements use integer distance 1.
                if(signed_id<0&&(!equal||literal->delta>0)) {
                    constant=1-constant;
                    for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                } else if(equal)++constant;
                if(!edges_for_row(coefficients,constant,source,alternative))continue;
            }
            alternatives.push_back(std::move(alternative));
        }
        if(!alternatives.empty()) {
            const auto& chosen=alternatives[branch_variant%alternatives.size()];
            selected_edges+=chosen.size();edges.insert(edges.end(),chosen.begin(),chosen.end());
        }
    }
    if(!selected_edges||!available())return {};

    std::vector<Integer> potential(count+1);
    for(size_t variable=0;variable<count;++variable) {
        if(!available()||lo[variable]>hi[variable])return {};
        potential[variable]=integer(assignment[variable]);
        if(!solver._vars[variable].is_nia)continue;
        // Exact true bounds, including the full signed 128-bit endpoints.
        edges.push_back({source,variable,integer(hi[variable])});
        edges.push_back({variable,source,-integer(lo[variable])});
        auto requested=requested_values.find(variable);
        if(requested!=requested_values.end())potential[variable]=integer(requested->second);
        if(fixed.count(variable)) {
            const Integer value=potential[variable];
            edges.push_back({source,variable,value});
            edges.push_back({variable,source,-value});
        }
    }
    // Bellman--Ford with a feasible old point as the initial potential. A
    // change in the N-th pass means the chosen branch contains a negative cycle.
    bool converged=false;
    for(size_t pass=0;pass<=count;++pass) {
        bool changed=false;
        for(const auto& edge:edges) {
            if(!available())return {};
            const Integer next=potential[edge.from]+edge.bound;
            if(potential[edge.to]>next){potential[edge.to]=next;changed=true;}
        }
        if(!changed){converged=true;break;}
    }
    if(!converged||!available())return {};
    Move result;
    for(size_t variable=0;variable<count;++variable) {
        if(!available())return {};
        if(!solver._vars[variable].is_nia)continue;
        const Integer normalized=potential[variable]-potential[source];
        if(normalized<integer(lo[variable])||normalized>integer(hi[variable]))return {};
        __int128_t value;
        if(!parse_omt_integer(normalized.get_str(),value))return {};
        auto requested=requested_values.find(variable);
        if(fixed.count(variable)&&value!=(requested==requested_values.end()?assignment[variable]:requested->second))return {};
        if(value!=assignment[variable])result.push_back({variable,value});
    }
    return available()?result:Move{};
}
}
