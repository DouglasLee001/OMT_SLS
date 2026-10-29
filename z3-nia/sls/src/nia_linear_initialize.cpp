#include <z3++.h>
#include <algorithm>
#include <chrono>
#include <iostream>
#include <map>
#include <set>
#include <unordered_map>
#include <vector>

#include "nia_linearize.h"
#include "nia_lia_sls.h"

#include "nia_linear_initialize.h"
#include "nia_ls.h"
#include "nia_omt_input.h"
#include <fstream>
#include <sstream>
#include <cctype>
#include <cmath>

// Read the full converted CNF independently of the local-search simplifier.
// This lets the seed model be checked before variable/clause elimination.
struct ConvertedSeedInput {
    z3::context& c;
    std::map<std::string,z3::expr> vars;
    std::map<int,z3::expr> lits, raw_lits;
    std::map<std::string,z3::expr> auxiliary_symbols;
    z3::expr_vector clauses, validation_clauses;
    ConvertedSeedInput(z3::context& ctx):c(ctx),clauses(ctx),validation_clauses(ctx){}
    z3::expr var(const std::string& name,bool boolean){
        auto it=vars.find(name);
        if(it!=vars.end()){
            if(it->second.is_bool()!=boolean)throw std::runtime_error("ambiguous variable sort");
            return it->second;
        }
        z3::expr e=boolean?c.bool_const(name.c_str()):c.int_const(name.c_str());
        vars.emplace(name,e);return e;
    }
    std::vector<std::string> tokens(const std::string& text){
        std::vector<std::string> result;std::string part;
        for(char ch:text){
            if(std::isspace(static_cast<unsigned char>(ch)) || ch=='(' || ch==')'){
                if(!part.empty()){result.push_back(part);part.clear();}
                if(ch=='(' || ch==')')result.push_back(std::string(1,ch));
            }else part+=ch;
        }
        if(!part.empty())result.push_back(part);
        return result;
    }
    z3::expr parse(const std::vector<std::string>& ts,size_t& pos,bool boolean=false){
        if(pos>=ts.size())throw std::runtime_error("truncated literal");
        std::string token=ts[pos++];
        if(token!="("){
            if(token=="true" || token=="false")return c.bool_val(token=="true");
            size_t offset=token[0]=='-' || token[0]=='+' ? 1 : 0;
            bool numeral=offset<token.size();
            for(size_t i=offset;i<token.size();++i)numeral&=std::isdigit(static_cast<unsigned char>(token[i]))!=0;
            if(numeral)return c.int_val(token.c_str());
            if(token==")")throw std::runtime_error("unexpected closing parenthesis");
            return var(token,boolean);
        }
        if(pos>=ts.size())throw std::runtime_error("missing operator");
        std::string op=ts[pos++];
        bool bool_args=op=="and" || op=="or" || op=="not" || op=="=>";
        z3::expr_vector args(c);
        while(pos<ts.size() && ts[pos]!=")")args.push_back(parse(ts,pos,bool_args));
        if(pos==ts.size())throw std::runtime_error("unclosed expression");
        ++pos;
        if(op=="+")return z3::sum(args);
        if(op=="*"){z3::expr e=c.int_val(1);for(auto a:args)e=e*a;return e.simplify();}
        if(op=="-"){
            if(args.empty())throw std::runtime_error("empty subtraction");
            z3::expr e=args[0];if(args.size()==1)return -e;
            for(unsigned i=1;i<args.size();++i)e=e-args[i];return e;
        }
        if(op=="and")return z3::mk_and(args);
        if(op=="or")return z3::mk_or(args);
        if(op=="not" && args.size()==1)return !args[0];
        if(args.size()==2){
            if(op=="=")return args[0]==args[1];
            if(op=="<=")return args[0]<=args[1];
            if(op==">=")return args[0]>=args[1];
            if(op=="<")return args[0]<args[1];
            if(op==">")return args[0]>args[1];
            if(op=="=>")return z3::implies(args[0],args[1]);
        }
        throw std::runtime_error("unsupported seed operator: "+op);
    }
    // The native OMT reader already validates identifiers, sorts and exact
    // integer coefficients. Rebuild precisely its hard CNF for the existing
    // initializer; the optimization objective imposes no feasibility clause.
    void read_native(std::istream& in){
        nia::ls_solver native;
        std::string error;
        if(!native.load_omt_instance(in,error))
            throw std::runtime_error("cannot read native seed input: "+error);
        std::vector<z3::expr> symbols;
        symbols.reserve(native._vars.size());
        for(const auto& variable:native._vars)
            symbols.push_back(var(variable.var_name,!variable.is_nia));
        std::vector<z3::expr> terms;
        terms.reserve(native._terms.size());
        for(const auto& term:native._terms){
            z3::expr value=c.int_val(1);
            for(const auto& factor:term.var_epxs){
                z3::expr power=c.int_val(1),base=symbols.at(factor.var_index);
                unsigned exponent=static_cast<unsigned>(factor.exponent);
                while(exponent){
                    if(exponent&1)power=power*base;
                    exponent>>=1;
                    if(exponent)base=base*base;
                }
                value=value*power;
            }
            terms.push_back(value);
        }
        for(uint64_t id=1;id<=native._num_lits;++id){
            const auto& literal=native._lits.at(id);
            z3::expr expression=c.bool_val(false);
            if(literal.is_nia_lit){
                z3::expr polynomial=c.int_val(nia::format_omt_integer(literal.key).c_str());
                for(const auto& coefficient:literal.coff_terms)
                    polynomial=polynomial+c.int_val(nia::format_omt_integer(coefficient.coff).c_str())
                                           *terms.at(coefficient.term_idx);
                expression=literal.is_equal?polynomial==0:polynomial<=0;
            }else expression=symbols.at(static_cast<size_t>(literal.delta));
            raw_lits.emplace(static_cast<int>(id),expression);
            lits.emplace(static_cast<int>(id),expression.simplify());
        }
        for(const auto& clause:native._clauses){
            z3::expr_vector disj(c),raw_disj(c);
            for(int index:clause.literals){
                const auto& expression=lits.at(std::abs(index));
                const auto& raw=raw_lits.at(std::abs(index));
                disj.push_back(index>0?expression:!expression);
                raw_disj.push_back(index>0?raw:!raw);
            }
            clauses.push_back(z3::mk_or(disj).simplify());
            validation_clauses.push_back(z3::mk_or(raw_disj));
        }
    }
    void read(const std::string& path){
        std::ifstream in(path);
        std::string header;
        if(!(in>>header))throw std::runtime_error("cannot read seed input");
        in.clear();in.seekg(0);
        if(header=="NIA_LS_OMT"){read_native(in);return;}
        uint64_t count;
        if(!(in>>count))throw std::runtime_error("cannot read seed input");
        std::string line;std::getline(in,line);
        while(std::getline(in,line) && line!="0"){
            auto ts=tokens(line);if(ts.empty())continue;
            int id=std::stoi(ts[0]);size_t pos=1;
            if(pos>=ts.size())throw std::runtime_error("missing literal body");
            if(ts[pos]=="or" || ts[pos]=="if"){
                if(ts.size()<3)throw std::runtime_error("unsupported auxiliary literal");
                z3::expr symbol=c.bool_const(ts[2].c_str());
                raw_lits.emplace(id,symbol);
                if(ts.size()==3){lits.emplace(id,var(ts[2],true));}
                else{
                    z3::expr_vector refs(c);
                    for(size_t i=3;i<ts.size();++i){
                        int index=std::stoi(ts[i]);auto it=lits.find(std::abs(index));
                        if(it==lits.end())throw std::runtime_error("forward auxiliary reference");
                        refs.push_back(index>0?it->second:!it->second);
                    }
                    // Recover the defined gate instead of asking the linear
                    // solver to rediscover it from its CNF encoding. The model
                    // exports the gate's evaluated value, so every original
                    // CNF clause is still checked under a complete assignment.
                    z3::expr definition=c.bool_val(false);
                    if(ts[pos]=="or")definition=z3::mk_or(refs);
                    else if(refs.size()==3)definition=z3::ite(refs[0],refs[1],refs[2]);
                    else throw std::runtime_error("invalid auxiliary conditional");
                    if(!vars.emplace(ts[2],definition).second)throw std::runtime_error("duplicate auxiliary name");
                    auxiliary_symbols.emplace(ts[2],symbol);
                    lits.emplace(id,definition);
                }
            }else{
                z3::expr e=parse(ts,pos,ts[pos]!="(");
                if(pos!=ts.size() || !e.is_bool())throw std::runtime_error("invalid literal");
                lits.emplace(id,e.simplify());
                raw_lits.emplace(id,e.simplify());
            }
        }
        if(!(in>>count))throw std::runtime_error("missing clause count");
        for(uint64_t i=0;i<count;++i){
            std::string token;if(!(in>>token) || token!="(")throw std::runtime_error("missing clause");
            z3::expr_vector disj(c),raw_disj(c);bool closed=false;
            while(in>>token){
                if(token==")"){closed=true;break;}
                int index=std::stoi(token);auto it=lits.find(std::abs(index));
                if(it==lits.end())throw std::runtime_error("unknown literal");
                disj.push_back(index>0?it->second:!it->second);
                const auto& raw=raw_lits.at(std::abs(index));
                raw_disj.push_back(index>0?raw:!raw);
            }
            if(!closed)throw std::runtime_error("unclosed clause");
            clauses.push_back(z3::mk_or(disj).simplify());
            validation_clauses.push_back(z3::mk_or(raw_disj));
        }
    }
};

namespace nia {
bool make_linear_initial_model(const std::string& path,double budget_seconds,initial_model& result){
    auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    try{
        z3::context c;ConvertedSeedInput input(c);input.read(path);
        Seeder seed(c,1);std::set<unsigned> seen;
        for(auto e:input.clauses){seed.units(e);seed.count(e,seen);}
        LiaSolver solver(c);
        for(auto e:input.clauses)solver.add(seed.visit(e));
        for(auto& d:seed.domains)solver.add(d.second);
        if(elapsed()>=budget_seconds)return false;
        solver.set("timeout",unsigned(1000*(budget_seconds-elapsed())));
        auto status=solver.check();
        std::cerr<<"linear_initialization_status="<<status<<" elapsed="<<elapsed()<<" bounded_variables="<<seed.domains.size()<<"\n";
        if(status!=z3::sat)return false;
        auto model=solver.get_model();
        // Validate against every original CNF clause, not its linearized form
        // or the local-search parser's possibly simplified representation.
        z3::expr_vector gate_symbols(c),gate_values(c);
        for(const auto& entry:input.auxiliary_symbols){
            gate_symbols.push_back(entry.second);
            gate_values.push_back(model.eval(input.vars.at(entry.first),true));
        }
        for(auto e:input.validation_clauses){
            if(!gate_symbols.empty())e=e.substitute(gate_symbols,gate_values);
            if(!model.eval(e,true).simplify().is_true())return false;
        }
        initial_model candidate;
        for(const auto& entry:input.vars)candidate.values.emplace(entry.first,model.eval(entry.second,true).to_string());
        candidate.seconds=elapsed();candidate.bounded_variables=seed.domains.size();
        result=std::move(candidate);
        return true;
    }catch(const std::exception& e){
        std::cerr<<"linear_initialization=skipped reason="<<e.what()<<"\n";
        return false;
    }
}
}

namespace nia {
bool make_progressive_initial_model(const std::string& path,double budget_seconds,initial_model& best){
    auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    unsigned checks=0,best_cost=UINT32_MAX,best_prefix=0;
    const char* reason="budget";
    auto finish=[&](bool complete){
        best.seconds=elapsed();
        std::cerr<<"progressive_stage status="<<reason<<" checks="<<checks
                 <<" candidate="<<(!best.values.empty())<<" candidate_unsat="<<best_cost
                 <<" best_prefix="<<best_prefix<<" seconds="<<best.seconds<<"\n";
        return complete;
    };
    try{
        z3::context c;ConvertedSeedInput input(c);input.read(path);
        Seeder seed(c,1);std::set<unsigned> seen;
        for(auto e:input.clauses){seed.units(e);seed.count(e,seen);}
        z3::expr_vector linear(c);
        for(auto e:input.clauses){if(elapsed()>=budget_seconds)return finish(false);linear.push_back(seed.visit(e));}
        LiaSolver solver(c);
        for(auto& d:seed.domains)solver.add(d.second);
        const unsigned total=linear.size();
        unsigned added=0,target=std::min(total,std::max(1u,total/16));
        while(elapsed()<budget_seconds){
            while(added<target)solver.add(linear[added++]);
            double remaining=budget_seconds-elapsed();
            if(remaining<=0)break;
            // Short prefix checks reserve most of the budget for the final check.
            unsigned ms=unsigned(1000*remaining);
            if(target<total)ms=std::min(ms,500u);
            solver.set("timeout",std::max(1u,ms));
            ++checks;auto status=solver.check();
            if(status==z3::sat || solver.candidate_available()){
                auto model=solver.get_model();
                z3::expr_vector symbols(c),values(c);
                for(const auto& entry:input.auxiliary_symbols){
                    symbols.push_back(entry.second);
                    values.push_back(model.eval(input.vars.at(entry.first),true));
                }
                unsigned cost=0;
                for(auto clause:input.validation_clauses){
                    if(!symbols.empty())clause=clause.substitute(symbols,values);
                    if(!model.eval(clause,true).simplify().is_true())++cost;
                }
                if(cost<best_cost){
                    initial_model candidate;
                    for(const auto& entry:input.vars)
                        candidate.values.emplace(entry.first,model.eval(entry.second,true).to_string());
                    candidate.bounded_variables=seed.domains.size();
                    best=std::move(candidate);best_cost=cost;best_prefix=target;
                }
                if(cost==0){reason="sat";return finish(true);}
            }
            // SLS UNKNOWN can expose its best assignment for the handoff.
            if(target==total){reason="unknown";return finish(false);}
            target=std::min(total,std::max(target+1,target*2));
        }
        return finish(false);
    }catch(const std::exception& error){
        std::cerr<<"progressive_error="<<error.what()<<"\n";
        reason="unsupported";return finish(false);
    }
}
}
