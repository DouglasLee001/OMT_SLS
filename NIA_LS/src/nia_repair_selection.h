#pragma once
#include <algorithm>
#include <cstddef>
#include <set>
#include <utility>
#include <vector>

namespace nia {
// Mutable priority order with exact marginal gains. Only neighbors of changed
// terms and conflicts are updated after an anchor is selected.
class RepairAnchorSelector {
    struct Key {
        unsigned conflicts,gain;size_t rank,variable;
        bool operator<(const Key& b)const {
            if(conflicts!=b.conflicts)return conflicts<b.conflicts;
            if(gain!=b.gain)return gain>b.gain;
            return rank<b.rank;
        }
    };
    const std::vector<unsigned>& conflicts;
    const std::vector<bool>& zero;
    const std::vector<bool>& can_move;
    const std::vector<std::vector<size_t>>& clauses;
    const std::vector<std::vector<std::pair<size_t,unsigned>>>& factors;
    size_t offset;
    std::vector<unsigned> degree,gain,remaining;
    std::vector<bool> anchored,blocked;
    std::vector<std::vector<size_t>> memberships;
    std::vector<std::vector<std::pair<size_t,unsigned>>> terms;
    std::set<Key> queue;
    Key key(size_t v)const{return {conflicts[v],gain[v],(v+gain.size()-offset)%gain.size(),v};}
    bool contributes(size_t v,unsigned exponent,unsigned d)const{return d>1 && (zero[v] || d<=exponent+1);}
public:
    size_t gain_updates=0;
    RepairAnchorSelector(const std::vector<unsigned>& conflict_counts,const std::vector<bool>& zeros,
                        const std::vector<bool>& movable,const std::vector<std::vector<size_t>>& violated,
                        const std::vector<std::vector<std::pair<size_t,unsigned>>>& variable_terms,
                        const std::vector<unsigned>& degrees,size_t rotation)
        :conflicts(conflict_counts),zero(zeros),can_move(movable),clauses(violated),factors(variable_terms),offset(rotation),
         degree(degrees),gain(conflict_counts.size(),0),remaining(violated.size(),0),
         anchored(conflict_counts.size(),false),blocked(conflict_counts.size(),false),
         memberships(conflict_counts.size()),terms(degrees.size()){
        for(size_t v=0;v<gain.size();++v)for(auto item:factors[v]){
            terms[item.first].push_back({v,item.second});
            gain[v]+=contributes(v,item.second,degree[item.first]);
        }
        for(size_t j=0;j<clauses.size();++j)for(size_t v:clauses[j])if(movable[v]){
            memberships[v].push_back(j);++remaining[j];
        }
        for(size_t v=0;v<gain.size();++v){
            for(size_t j:memberships[v])if(remaining[j]<=1){blocked[v]=true;break;}
            if(!blocked[v])queue.insert(key(v));
        }
    }
    size_t take(){
        if(queue.empty())return gain.size();
        size_t selected=queue.begin()->variable;queue.erase(queue.begin());anchored[selected]=true;
        for(size_t j:memberships[selected])if(--remaining[j]==1){
            for(size_t v:clauses[j])if(!anchored[v] && !blocked[v] &&
                can_move[v]){
                queue.erase(key(v));blocked[v]=true;
            }
        }
        for(auto item:factors[selected]){
            unsigned old=degree[item.first];unsigned next=zero[selected]?0:(old>=item.second?old-item.second:0);
            degree[item.first]=next;
            if(old==next)continue;
            for(auto neighbor:terms[item.first]){
                size_t v=neighbor.first;if(anchored[v] || blocked[v])continue;
                ++gain_updates;
                bool was=contributes(v,neighbor.second,old),now=contributes(v,neighbor.second,next);
                if(was==now)continue;
                queue.erase(key(v));if(now)++gain[v];else --gain[v];queue.insert(key(v));
            }
        }
        return selected;
    }
};
}
