#pragma once
#include <z3++.h>
#include <algorithm>
#include <map>
#include <set>
#include <vector>

struct Seeder {
    z3::context& c;
    int radius;
    std::map<unsigned,int> radii;
    std::map<unsigned,std::vector<int64_t>> discrete;
    std::map<unsigned,std::pair<int64_t,int64_t>> bounds;
    std::map<unsigned,unsigned> uses;
    std::map<unsigned,z3::expr> memo;
    std::map<unsigned,z3::expr> domains;
    std::map<unsigned,int64_t> preferred;
    Seeder(z3::context& ctx,int r):c(ctx),radius(r){}
    bool variable(const z3::expr& e) {return e.is_const() && e.is_int() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED;}
    bool number(const z3::expr& e,int64_t& n) {return e.is_numeral_i64(n);}
    void count(const z3::expr& e,std::set<unsigned>& seen) {
        if(!seen.insert(e.id()).second)return;
        if(e.is_app() && e.decl().decl_kind()==Z3_OP_MUL){
            for(unsigned i=0;i<e.num_args();++i)if(variable(e.arg(i)))++uses[e.arg(i).id()];
        }
        for(unsigned i=0;i<e.num_args();++i)count(e.arg(i),seen);
    }
    void units(const z3::expr& e) {
        if(e.is_and()){for(unsigned i=0;i<e.num_args();++i)units(e.arg(i));return;}
        if(!e.is_app() || e.num_args()!=2)return;
        auto k=e.decl().decl_kind();
        if(k!=Z3_OP_LE && k!=Z3_OP_GE && k!=Z3_OP_LT && k!=Z3_OP_GT && k!=Z3_OP_EQ)return;
        z3::expr v=e.arg(0),n=e.arg(1);int64_t value;
        bool reversed=false;
        if(!variable(v)){v=e.arg(1);n=e.arg(0);reversed=true;}
        if(!variable(v) || !number(n.simplify(),value) || value<-1000000000 || value>1000000000)return;
        if(!bounds.count(v.id()))bounds.emplace(v.id(),std::make_pair(INT64_MIN/4,INT64_MAX/4));
        auto& b=bounds.at(v.id());
        if(k==Z3_OP_EQ){b.first=std::max(b.first,value);b.second=std::min(b.second,value);return;}
        bool lower=(k==Z3_OP_GE || k==Z3_OP_GT)^reversed;
        if(k==Z3_OP_GT || k==Z3_OP_LT)value+=lower?1:-1;
        if(lower)b.first=std::max(b.first,value);else b.second=std::min(b.second,value);
    }
    std::pair<int64_t,int64_t> domain(const z3::expr& v) {
        auto it=bounds.find(v.id());
        int64_t lo=it==bounds.end()?INT64_MIN/4:it->second.first;
        int64_t hi=it==bounds.end()?INT64_MAX/4:it->second.second;
        if(hi-lo<=8 && hi>=lo)return {lo,hi};
        auto p=preferred.find(v.id());
        int64_t center=std::max(lo,std::min(hi,p==preferred.end()?int64_t(0):p->second));
        auto chosen_radius=radii.find(v.id());
        int local_radius=chosen_radius==radii.end()?radius:chosen_radius->second;
        return {std::max(lo,center-local_radius),std::min(hi,center+local_radius)};
    }
    std::vector<int64_t> domain_values(const z3::expr& v){
        auto sparse=discrete.find(v.id());
        if(sparse!=discrete.end()){
            auto bound=bounds.find(v.id());std::set<int64_t> values;
            for(auto value:sparse->second)
                if(bound==bounds.end() || (value>=bound->second.first && value<=bound->second.second))values.insert(value);
            return {values.begin(),values.end()};
        }
        auto d=domain(v);std::vector<int64_t> values;
        for(int64_t value=d.first;value<=d.second;++value)values.push_back(value);
        return values;
    }
    z3::expr multiply(std::vector<z3::expr> factors) {
        if(factors.empty())return c.int_val(1);
        if(factors.size()==1)return visit(factors[0]);
        size_t chosen=factors.size();double best=-1;
        for(size_t i=0;i<factors.size();++i){
            if(!variable(factors[i]))continue;
            auto options=domain_values(factors[i]);
            if(options.empty())throw std::runtime_error("empty bounded initialization domain");
            auto it=bounds.find(factors[i].id());
            bool bounded=it!=bounds.end() && it->second.second-it->second.first<=8;
            double score=(bounded?1000000.0:0.0)+
                ((radii.count(factors[i].id()) || discrete.count(factors[i].id()))?10000.0:0.0)+uses[factors[i].id()]*100.0/options.size();
            if(score>best){best=score;chosen=i;}
        }
        if(chosen==factors.size())throw std::runtime_error("non-atomic nonlinear factor");
        z3::expr x=factors[chosen];factors.erase(factors.begin()+chosen);
        auto options=domain_values(x);
        if(options.empty())throw std::runtime_error("empty bounded initialization domain");
        if(discrete.count(x.id())){
            z3::expr_vector allowed(c);for(auto value:options)allowed.push_back(x==c.int_val(value));
            domains.emplace(x.id(),z3::mk_or(allowed));
        }else domains.emplace(x.id(),x>=c.int_val(options.front()) && x<=c.int_val(options.back()));
        z3::expr other=multiply(factors);
        z3::expr result=c.int_val(options.back())*other;
        for(size_t k=options.size()-1;k>0;--k){
            int64_t value=options[k-1];result=z3::ite(x==c.int_val(value),c.int_val(value)*other,result);
        }
        return result;
    }
    z3::expr visit(const z3::expr& e) {
        auto it=memo.find(e.id());if(it!=memo.end())return it->second;
        if(!e.is_app())throw std::runtime_error("quantified input");
        auto k=e.decl().decl_kind();
        z3::expr result=e;
        if(k==Z3_OP_MUL){
            std::vector<z3::expr> factors;z3::expr scale=c.int_val(1);
            for(unsigned i=0;i<e.num_args();++i){
                z3::expr a=e.arg(i).simplify();int64_t n;
                if(number(a,n))scale=scale*a;else factors.push_back(a);
            }
            result=scale*multiply(factors);
        }else if(k==Z3_OP_POWER){
            int64_t n;
            if(!number(e.arg(1),n) || n<0 || n>4)throw std::runtime_error("unsupported power");
            std::vector<z3::expr> f(n,e.arg(0));result=multiply(f);
        }else if(k==Z3_OP_IDIV || k==Z3_OP_MOD || k==Z3_OP_REM || k==Z3_OP_DIV){
            throw std::runtime_error("non-polynomial input");
        }else if(e.num_args()){
            z3::expr_vector args(c);for(unsigned i=0;i<e.num_args();++i)args.push_back(visit(e.arg(i)));
            result=e.decl()(args);
        }
        memo.emplace(e.id(),result);return result;
    }
};
