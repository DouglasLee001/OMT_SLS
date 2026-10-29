#pragma once

#include "nia_omt_difference.h"
#include <algorithm>
#include <cstdint>
#include <deque>
#include <limits>
#include <string>

namespace nia {
struct DisjunctiveMoveStatistics {
    size_t graph_checks=0;
    size_t unique_states=0;
    bool cancelled=false;
};

namespace omt_disjunctive_detail {
using Integer=omt_difference_detail::Integer;
using Edge=omt_difference_detail::Edge;
struct Alternative {
    std::vector<Edge> edges;
    Integer displacement=0;
    std::string key;
};
struct Group {std::vector<Alternative> alternatives;};
struct ReasonedEdge {Edge edge;size_t group;};
inline uint64_t hash(uint64_t value) {
    value+=0x9e3779b97f4a7c15ULL;
    value=(value^(value>>30))*0xbf58476d1ce4e5b9ULL;
    value=(value^(value>>27))*0x94d049bb133111ebULL;
    return value^(value>>31);
}
inline std::string canonicalize(std::vector<Edge>& edges) {
    std::sort(edges.begin(),edges.end(),[](const Edge& a,const Edge& b) {
        if(a.from!=b.from)return a.from<b.from;
        if(a.to!=b.to)return a.to<b.to;
        return a.bound<b.bound;
    });
    edges.erase(std::unique(edges.begin(),edges.end(),[](const Edge& a,const Edge& b) {
        return a.from==b.from&&a.to==b.to&&a.bound==b.bound;
    }),edges.end());
    std::string result;
    for(const auto& edge:edges)
        result+=std::to_string(edge.from)+":"+std::to_string(edge.to)+":"+edge.bound.get_str()+";";
    return result;
}
}

// A bounded candidate search over disjunctive integer difference constraints.
// Old-false linear witnesses may replace old-true witnesses. Negative cycles
// identify which choices to change; they are never reported as original UNSAT.
// Unsupported hard clauses are left to the caller's full exact validation.
// The probe and all objective coordinates stay fixed. No reference target or
// opponent assignment is accepted by this API.
inline std::vector<std::pair<size_t,__int128_t>> disjunctive_difference_move(
    const ls_solver& solver,const std::vector<__int128_t>& assignment,
    const std::vector<__int128_t>& lo,const std::vector<__int128_t>& hi,
    std::vector<std::pair<size_t,__int128_t>> requested,std::set<size_t> fixed,
    const std::function<bool()>& in_budget,size_t max_states=64,unsigned branch_seed=0,
    DisjunctiveMoveStatistics* statistics=nullptr) {
    using namespace omt_disjunctive_detail;
    using Move=std::vector<std::pair<size_t,__int128_t>>;
    using Choice=std::vector<uint32_t>;
    const size_t none=std::numeric_limits<size_t>::max(),count=assignment.size(),source=count;
    DisjunctiveMoveStatistics local;
    auto& stats=statistics?*statistics:local;stats={};
    auto available=[&]() {
        if(stats.cancelled)return false;
        if(in_budget&&!in_budget())stats.cancelled=true;
        return !stats.cancelled;
    };
    max_states=std::min(max_states,size_t(256));
    if(!available()||!max_states||count!=solver._num_vars||solver._vars.size()<count||
       lo.size()<count||hi.size()<count||requested.empty())return {};

    std::map<size_t,__int128_t> requested_values;
    for(const auto& next:requested) {
        if(!available()||next.first>=count||!solver._vars[next.first].is_nia||
           next.second<lo[next.first]||next.second>hi[next.first])return {};
        const auto inserted=requested_values.emplace(next.first,next.second);
        if(!inserted.second&&inserted.first->second!=next.second)return {};
        fixed.insert(next.first);
    }
    for(const auto& coefficient:solver.omt_objective_terms) {
        if(!available()||coefficient.term_idx<0||size_t(coefficient.term_idx)>=solver._terms.size())return {};
        if(!coefficient.coff)continue;
        for(const auto& factor:solver._terms[coefficient.term_idx].var_epxs) {
            if(factor.var_index<0||size_t(factor.var_index)>=count)return {};
            fixed.insert(size_t(factor.var_index));
        }
    }
    for(size_t variable:fixed)if(variable>=count)return {};

    std::vector<Integer> old(count+1),initial(count+1);
    std::vector<ReasonedEdge> base;
    for(size_t variable=0;variable<count;++variable) {
        if(!available()||lo[variable]>hi[variable])return {};
        old[variable]=omt_difference_detail::integer(assignment[variable]);
        initial[variable]=old[variable];
        if(!solver._vars[variable].is_nia)continue;
        base.push_back({{source,variable,omt_difference_detail::integer(hi[variable])},none});
        base.push_back({{variable,source,-omt_difference_detail::integer(lo[variable])},none});
        const auto requested=requested_values.find(variable);
        if(requested!=requested_values.end())initial[variable]=omt_difference_detail::integer(requested->second);
        if(fixed.count(variable)) {
            base.push_back({{source,variable,initial[variable]},none});
            base.push_back({{variable,source,-initial[variable]},none});
        }
    }

    std::vector<omt_difference_detail::LinearLiteral> cached(solver._lits.size());
    auto linear_literal=[&](size_t id)->const omt_difference_detail::LinearLiteral* {
        if(id>=solver._lits.size())return nullptr;
        auto& row=cached[id];
        if(row.computed)return row.supported?&row:nullptr;
        row.computed=true;
        const auto& literal=solver._lits[id];
        if(!literal.is_nia_lit)return nullptr;
        row.constant=omt_difference_detail::integer(literal.key);
        for(const auto& coefficient:literal.coff_terms) {
            if(!available())return nullptr;
            if(!coefficient.coff)continue;
            if(coefficient.term_idx<0||size_t(coefficient.term_idx)>=solver._terms.size())return nullptr;
            const auto& term=solver._terms[coefficient.term_idx];
            if(term.var_epxs.empty())row.constant+=omt_difference_detail::integer(coefficient.coff);
            else {
                if(term.var_epxs.size()!=1||term.var_epxs[0].exponent!=1)return nullptr;
                const int variable=term.var_epxs[0].var_index;
                if(variable<0||size_t(variable)>=count||!solver._vars[variable].is_nia)return nullptr;
                row.coefficients[size_t(variable)]+=omt_difference_detail::integer(coefficient.coff);
            }
        }
        for(auto it=row.coefficients.begin();it!=row.coefficients.end();) {
            if(it->second==0)it=row.coefficients.erase(it);else ++it;
        }
        row.supported=true;return &row;
    };

    std::vector<Group> groups;
    size_t clause_number=0,supported_edges=0;
    for(const auto& clause:solver._clauses) {
        if(!available())return {};
        const size_t original_clause=clause_number++;
        bool always_true=false;
        for(int signed_id:clause.literals) {
            if(!available())return {};
            if(!signed_id)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            if(id>=solver._lits.size())return {};
            const auto& literal=solver._lits[id];
            if(literal.is_nia_lit)continue;
            if(literal.delta<0||size_t(literal.delta)>=count)return {};
            if((assignment[size_t(literal.delta)]>0)==(signed_id>0)){always_true=true;break;}
        }
        if(always_true)continue;
        Group group;
        std::set<std::string> unique_alternatives;
        auto append=[&](std::vector<Edge> edges) {
            if(edges.empty()){always_true=true;return;}
            Alternative alternative;alternative.key=canonicalize(edges);
            if(!unique_alternatives.insert(alternative.key).second)return;
            alternative.edges=std::move(edges);
            for(const auto& edge:alternative.edges) {
                const Integer deficit=old[edge.to]-old[edge.from]-edge.bound;
                if(deficit>0)alternative.displacement+=deficit;
            }
            group.alternatives.push_back(std::move(alternative));
        };
        for(int signed_id:clause.literals) {
            if(!available())return {};
            if(!signed_id)continue;
            const size_t id=signed_id<0?size_t(-int64_t(signed_id)):size_t(signed_id);
            const auto* linear=linear_literal(id);
            if(!linear)continue;
            auto coefficients=linear->coefficients;
            Integer constant=linear->constant;
            std::vector<Edge> edges;
            const bool equal=solver._lits[id].is_equal;
            if(equal&&signed_id>0) {
                if(!omt_difference_detail::edges_for_row(coefficients,constant,source,edges))continue;
                for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                if(!omt_difference_detail::edges_for_row(coefficients,-constant,source,edges))continue;
                append(std::move(edges));
            } else if(equal) {
                // !(d=0) is the disjunction d<=-1 OR d>=1 over integers.
                if(omt_difference_detail::edges_for_row(coefficients,constant+1,source,edges))append(std::move(edges));
                edges.clear();
                for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                if(omt_difference_detail::edges_for_row(coefficients,1-constant,source,edges))append(std::move(edges));
            } else {
                if(signed_id<0) {
                    constant=1-constant;
                    for(auto& coefficient:coefficients)coefficient.second=-coefficient.second;
                }
                if(omt_difference_detail::edges_for_row(coefficients,constant,source,edges))append(std::move(edges));
            }
        }
        if(!available())return {};
        if(always_true||group.alternatives.empty())continue;
        std::stable_sort(group.alternatives.begin(),group.alternatives.end(),[&](const Alternative& a,const Alternative& b) {
            if((a.displacement==0)!=(b.displacement==0))return a.displacement==0;
            if(a.displacement!=b.displacement)return a.displacement<b.displacement;
            if(!branch_seed)return false;
            auto rank=[&](const Alternative& alternative) {
                uint64_t value=hash(uint64_t(branch_seed)^hash(original_clause));
                for(unsigned char c:alternative.key)value=hash(value^c);
                return value;
            };
            return rank(a)<rank(b);
        });
        for(const auto& alternative:group.alternatives)supported_edges+=alternative.edges.size();
        if(group.alternatives.size()==1) {
            for(const auto& edge:group.alternatives[0].edges)base.push_back({edge,none});
        } else {
            if(group.alternatives.size()>std::numeric_limits<uint32_t>::max())return {};
            groups.push_back(std::move(group));
        }
    }
    if(!supported_edges||!available())return {};

    const size_t max_frontier=max_states*4,max_unique=max_states*16;
    std::deque<Choice> frontier;
    std::set<Choice> visited;
    Choice initial_choice(groups.size(),0);
    frontier.push_back(initial_choice);visited.insert(initial_choice);stats.unique_states=1;
    while(!frontier.empty()&&stats.graph_checks<max_states) {
        if(!available())return {};
        const Choice choice=std::move(frontier.front());frontier.pop_front();
        std::vector<ReasonedEdge> edges=base;
        for(size_t group=0;group<groups.size();++group) {
            if(!available())return {};
            for(const auto& edge:groups[group].alternatives[choice[group]].edges)edges.push_back({edge,group});
        }
        ++stats.graph_checks;
        auto potential=initial;
        std::vector<size_t> predecessor(count+1,none);
        size_t changed=none;
        bool converged=false;
        for(size_t pass=0;pass<=count;++pass) {
            changed=none;
            for(size_t ei=0;ei<edges.size();++ei) {
                if(!available())return {};
                const auto& edge=edges[ei].edge;
                const Integer next=potential[edge.from]+edge.bound;
                if(potential[edge.to]>next){potential[edge.to]=next;predecessor[edge.to]=ei;changed=edge.to;}
            }
            if(changed==none){converged=true;break;}
        }
        if(converged) {
            Move result;
            for(size_t variable=0;variable<count;++variable) {
                if(!available())return {};
                if(!solver._vars[variable].is_nia)continue;
                const Integer normalized=potential[variable]-potential[source];
                if(normalized<omt_difference_detail::integer(lo[variable])||
                   normalized>omt_difference_detail::integer(hi[variable]))return {};
                __int128_t value;
                if(!parse_omt_integer(normalized.get_str(),value))return {};
                const auto requested=requested_values.find(variable);
                if(fixed.count(variable)&&value!=(requested==requested_values.end()?assignment[variable]:requested->second))return {};
                if(value!=assignment[variable])result.push_back({variable,value});
            }
            return available()?result:Move{};
        }
        // Walk into the negative cycle, then retain its actual choice reasons.
        size_t vertex=changed;
        for(size_t step=0;step<=count;++step) {
            if(!available()||vertex==none||predecessor[vertex]==none)return {};
            vertex=edges[predecessor[vertex]].edge.from;
        }
        const size_t cycle_start=vertex;
        std::set<size_t> conflict;
        size_t cycle_size=0;
        do {
            if(!available()||predecessor[vertex]==none||++cycle_size>count+1)return {};
            const auto& reason=edges[predecessor[vertex]];
            if(reason.group!=none)conflict.insert(reason.group);
            vertex=reason.edge.from;
        } while(vertex!=cycle_start);
        // A cycle containing only true bounds, fixed probes and unconditional
        // rows has no layout choice to repair in this candidate subproblem.
        for(size_t group:conflict) {
            if(!available())return {};
            for(size_t alternative=0;alternative<groups[group].alternatives.size();++alternative) {
                if(!available())return {};
                if(alternative==choice[group])continue;
                if(frontier.size()>=max_frontier||visited.size()>=max_unique)break;
                Choice next=choice;next[group]=uint32_t(alternative);
                if(visited.insert(next).second){frontier.push_back(std::move(next));++stats.unique_states;}
            }
        }
    }
    return {};
}
}
