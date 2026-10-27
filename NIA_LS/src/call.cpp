//
//  call.cpp
//  lia_ls
//
//  Created by DouglasLee on 2021/11/2.
//

#include "call.h"
#include "nia_linear_initialize.h"
#include "nia_lia_sls.h"
#include "nia_omt_first.h"
#include "nia_omt_optimize.h"
#include "nia_omt_input.h"
#include <cmath>
#include<map>
#include<limits>
#include <set>
#include <iomanip>
#include <sstream>
using namespace std;
std::map<std::string, int64_t> restored_md;
namespace call{
Call_fun::Call_fun(){
    m_ls=new nia::ls_solver(0,INT64_MAX,true);
}

int Call_fun::func(int argc,char *argv[]){
    const auto invocation_began=std::chrono::steady_clock::now();
    const bool help=argc==2 && std::string(argv[1])=="--help";
    if(argc<2 || help){
        (help?std::cout:std::cerr)
            <<"usage: nia_ls <input> [--initialize=on|off] "
              "[--restart-repair=on|off] [--equality-protection=on|off] [--seed=N] [--model-output=path] [--import-only|--first-solution|--optimize] [--target=integer] [--large-target=positive-integer] [--prove-unbounded] [--unbounded-threshold=positive-integer] [--prove-optimal] [--optimality-resume] [--optimality-stall=seconds] [--optimality-timeout=seconds] [--optimality-search-window=seconds] [--initial-z3-after=seconds] [--initial-z3-timeout=seconds] [--optimality-query-output=path] [--timeout=seconds]\n";
        delete m_ls;
        return help?0:2;
    }
    bool initialize_enabled=true;
    bool import_only=false;
    bool first_solution=false;
    bool optimize=false;
    std::string optimization_target;
    std::string large_target;
    bool prove_unbounded=false,unbounded_threshold_given=false;
    std::string unbounded_threshold="1000000";
    nia::OmtOptimalityOptions optimality;
    bool optimality_options_given=false;
    double timeout_seconds=1200.0;
    m_ls->restart_repair_enabled=true;
    m_ls->equality_protection=true;
    std::string model_output;
    for(int arg=2;arg<argc;++arg){
        const std::string option=argv[arg];
        if(option=="--import-only")import_only=true;
        else if(option=="--first-solution")first_solution=true;
        else if(option=="--optimize")optimize=true;
        else if(option=="--prove-unbounded")prove_unbounded=true;
        else if(option=="--prove-optimal")optimality.enabled=true;
        else if(option=="--optimality-resume"){optimality.resume=true;optimality_options_given=true;}
        else if(option.rfind("--optimality-stall=",0)==0 || option.rfind("--optimality-timeout=",0)==0){
            optimality_options_given=true;
            try{
                const std::string value=option.substr(option.find('=')+1);
                size_t used=0;const double seconds=std::stod(value,&used);
                if(used!=value.size()||!std::isfinite(seconds)||seconds<=0)throw std::invalid_argument("seconds");
                if(option.rfind("--optimality-stall=",0)==0)optimality.stall_seconds=seconds;
                else optimality.timeout_seconds=seconds;
            }catch(...){std::cerr<<"invalid optimality seconds: expected a finite positive number\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--optimality-search-window=",0)==0){
            try{
                const std::string value=option.substr(option.find('=')+1);
                size_t used=0;const double seconds=std::stod(value,&used);
                if(used!=value.size()||!std::isfinite(seconds)||seconds<0)throw std::invalid_argument("seconds");
                optimality.search_window_seconds=seconds;
                if(seconds>0)optimality_options_given=true;
            }catch(...){std::cerr<<"invalid optimality search window: expected a finite nonnegative number\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--initial-z3-after=",0)==0 || option.rfind("--initial-z3-timeout=",0)==0){
            try{
                const bool after=option.rfind("--initial-z3-after=",0)==0;
                const std::string value=option.substr(option.find('=')+1);
                size_t used=0;const double seconds=std::stod(value,&used);
                if(used!=value.size()||!std::isfinite(seconds)||(after?seconds<0:seconds<=0))
                    throw std::invalid_argument("seconds");
                if(after)optimality.initial_z3_after_seconds=seconds;
                else optimality.initial_z3_timeout_seconds=seconds;
            }catch(...){std::cerr<<"invalid initial Z3 timing: after must be finite and nonnegative; timeout must be finite and positive\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--optimality-query-output=",0)==0){
            optimality_options_given=true;
            optimality.query_output=option.substr(std::string("--optimality-query-output=").size());
            if(optimality.query_output.empty()){std::cerr<<"empty optimality query output path\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--unbounded-threshold=",0)==0){
            unbounded_threshold=option.substr(std::string("--unbounded-threshold=").size());
            unbounded_threshold_given=true;
            __int128_t magnitude;
            if(unbounded_threshold.empty()||unbounded_threshold.find_first_not_of("0123456789")!=std::string::npos
               ||!nia::parse_omt_integer(unbounded_threshold,magnitude)||magnitude<=0){
                std::cerr<<"invalid unbounded threshold: expected a positive integer at most 2^127-1\n";
                delete m_ls;return 2;
            }
        }
        else if(option.rfind("--target=",0)==0){
            optimization_target=option.substr(9);
            size_t begin=(!optimization_target.empty() && optimization_target[0]=='-')?1:0;
            if(begin==optimization_target.size() || !std::all_of(optimization_target.begin()+begin,optimization_target.end(),[](char c){return c>='0'&&c<='9';})){
                std::cerr<<"invalid optimization target\n";delete m_ls;return 2;
            }
        }
        else if(option.rfind("--large-target=",0)==0){
            large_target=option.substr(15);
            __int128_t magnitude;
            if(large_target.empty()||large_target.find_first_not_of("0123456789")!=std::string::npos
               ||!nia::parse_omt_integer(large_target,magnitude)||magnitude<=0){
                std::cerr<<"invalid finite large target: expected a positive integer at most 2^127-1\n";
                delete m_ls;return 2;
            }
        }
        else if(option.rfind("--timeout=",0)==0){
            try{
                const std::string value=option.substr(10);
                size_t used=0;timeout_seconds=std::stod(value,&used);
                if(used!=value.size() || !std::isfinite(timeout_seconds) || timeout_seconds<=0)
                    throw std::invalid_argument("timeout");
            }catch(...){std::cerr<<"invalid timeout\n";delete m_ls;return 2;}
        }
        else if(option=="--initialize=on")initialize_enabled=true;
        else if(option=="--initialize=off")initialize_enabled=false;
        else if(option=="--restart-repair=on")m_ls->restart_repair_enabled=true;
        else if(option=="--restart-repair=off")m_ls->restart_repair_enabled=false;
        else if(option=="--equality-protection=on")m_ls->equality_protection=true;
        else if(option=="--equality-protection=off")m_ls->equality_protection=false;
        else if(option.rfind("--seed=",0)==0){
            try{
                const std::string value=option.substr(7);
                if(value.empty() || !std::all_of(value.begin(),value.end(),[](char ch){return ch>='0' && ch<='9';}))
                    throw std::invalid_argument("seed");
                unsigned long seed=std::stoul(value);
                if(seed>std::numeric_limits<unsigned>::max())throw std::out_of_range("seed");
                nia::lia_seed=static_cast<unsigned>(seed);m_ls->mt.seed(nia::lia_seed);
            }catch(...){std::cerr<<"invalid seed\n";delete m_ls;return 2;}
        }
        else if(option.rfind("--model-output=",0)==0 && option.size()>15)model_output=option.substr(15);
        else{
            std::cerr<<"unknown option: "<<option<<"\n";
            delete m_ls;
            return 2;
        }
    }
    if(int(import_only)+int(first_solution)+int(optimize)>1 || ((!optimization_target.empty()||!large_target.empty())&&!optimize)){
        std::cerr<<"execution modes are mutually exclusive; --target and --large-target require --optimize\n";
        delete m_ls;return 2;
    }
    if((prove_unbounded&&!optimize)||(unbounded_threshold_given&&!prove_unbounded)){
        std::cerr<<"--prove-unbounded requires --optimize; --unbounded-threshold requires --prove-unbounded\n";
        delete m_ls;return 2;
    }
    if((optimality.enabled&&!optimize)||((optimality_options_given
        ||optimality.initial_z3_after_seconds!=0||optimality.initial_z3_timeout_seconds!=120)&&!optimality.enabled)){
        std::cerr<<"--prove-optimal requires --optimize; --optimality-* and nondefault --initial-z3-* require --prove-optimal\n";
        delete m_ls;return 2;
    }
    // Versioned OMT input has an exact reader and explicit execution modes.
    // Feasibility search never reports its first model as an optimum.
    std::ifstream input_probe(argv[1]);
    std::string input_kind;
    if(!input_probe || !(input_probe>>input_kind)){
        std::cerr<<"cannot read input: "<<argv[1]<<"\n";
        delete m_ls;return 2;
    }
    if(input_kind=="NIA_LS_OMT"){
        input_probe.clear();input_probe.seekg(0);
        std::string error;
        if(!m_ls->load_omt_instance(input_probe,error)){
            std::cerr<<"OMT import error: "<<error<<"\n";
            delete m_ls;return 2;
        }
        if(optimize){
            const double import_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-invocation_began).count();
            const int result=nia::run_omt_optimization(*m_ls,argv[1],initialize_enabled,timeout_seconds,import_seconds,model_output,optimization_target,large_target,prove_unbounded?unbounded_threshold:"",optimality);
            delete m_ls;return result;
        }
        if(first_solution){
            const double import_seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-invocation_began).count();
            const int result=nia::run_omt_first_solution(*m_ls,argv[1],initialize_enabled,timeout_seconds,import_seconds,model_output);
            delete m_ls;return result;
        }
        if(!import_only){
            std::cerr<<"OMT input loaded. Select --optimize to improve the objective, "
                       "--first-solution to find one feasible model, or --import-only to inspect the instance.\n";
            delete m_ls;return 2;
        }
        auto json_string=[](const std::string& value){
            std::ostringstream out;out<<'"';
            for(unsigned char ch:value){
                if(ch=='"' || ch=='\\')out<<'\\'<<char(ch);
                else if(ch<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(ch)<<std::dec;
                else out<<char(ch);
            }
            out<<'"';return out.str();
        };
        // print_128 predates this reader; handle INT128_MIN without signed negation.
        auto decimal=[](__int128 value){
            bool negative=value<0;
            unsigned __int128 magnitude=negative?0-static_cast<unsigned __int128>(value):static_cast<unsigned __int128>(value);
            std::string result;
            do{result.push_back(char('0'+magnitude%10));magnitude/=10;}while(magnitude);
            if(negative)result.push_back('-');
            std::reverse(result.begin(),result.end());return result;
        };
        std::set<int> objective_vars;
        unsigned long long max_degree=0;
        for(const auto& term:m_ls->_terms){
            unsigned long long degree=0;
            for(const auto& factor:term.var_epxs)degree+=factor.exponent;
            max_degree=std::max(max_degree,degree);
        }
        for(const auto& ct:m_ls->omt_objective_terms)
            for(const auto& factor:m_ls->_terms.at(ct.term_idx).var_epxs)objective_vars.insert(factor.var_index);
        std::cout<<"{\"status\":"<<json_string(m_ls->build_unsat?"preprocessing_unsat":"imported")
                 <<",\"source\":"<<json_string(m_ls->omt_source)
                 <<",\"direction\":"<<json_string(m_ls->omt_direction)
                 <<",\"original_objective\":"<<json_string(m_ls->omt_original_objective)
                 <<",\"variables\":"<<m_ls->_num_vars
                 <<",\"integer_variables\":"<<m_ls->_num_nia_vars
                 <<",\"boolean_variables\":"<<m_ls->bool_var_vec.size()
                 <<",\"literals\":"<<m_ls->_num_lits
                 <<",\"clauses\":"<<m_ls->_num_clauses
                 <<",\"monomials\":"<<m_ls->_terms.size()
                 <<",\"max_degree\":"<<max_degree
                 <<",\"objective_constant\":"<<json_string(decimal(m_ls->omt_objective_constant))
                 <<",\"objective_terms\":"<<m_ls->omt_objective_terms.size()
                 <<",\"objective_variables\":[";
        bool first=true;
        for(int id:objective_vars){
            if(!first)std::cout<<',';
            first=false;std::cout<<json_string(m_ls->_vars.at(id).var_name);
        }
        std::cout<<"],\"native_solver_state\":true,\"search_started\":false}\n";
        delete m_ls;return 0;
    }
    if(first_solution || optimize){
        std::cerr<<"--first-solution/--optimize requires NIA_LS_OMT input; convert SMT2 first\n";
        delete m_ls;return 2;
    }
    if(import_only){
        std::cerr<<"--import-only requires NIA_LS_OMT input; convert SMT2 with converter/omt_to_ls.py first\n";
        delete m_ls;return 2;
    }
    if(input_kind.empty() || input_kind[0]<'0' || input_kind[0]>'9'){
        std::cerr<<"expected legacy NIA_LS input or NIA_LS_OMT; convert SMT2 with converter/omt_to_ls.py first\n";
        delete m_ls;return 2;
    }
    auto write_model=[&](const std::map<std::string,std::string>& values){
        if(model_output.empty())return true;
        std::ofstream out(model_output);
        for(const auto& entry:values)out<<entry.first<<'\t'<<entry.second<<'\n';
        if(!out){std::cerr<<"cannot write model\n";return false;}
        return true;
    };
    m_ls->_cutoff=timeout_seconds;
    std::cerr<<"lia_backend=sls\n";
    if(initialize_enabled){
        nia::initial_model initial;
        if(nia::make_progressive_initial_model(argv[1],10.0,initial)){
            std::cerr<<"pipeline_stage=full\n";
            std::cerr<<"linear_initialization=validated seconds="<<initial.seconds
                     <<" bounded_variables="<<initial.bounded_variables<<"\n";
            if(!write_model(initial.values)){delete m_ls;return 2;}
            std::cout<<"sat"<<std::endl;
            delete m_ls;
            return 0;
        }
        m_ls->progressive_values=std::move(initial.values);
        std::cerr<<"pipeline_stage=local saved_candidate="<<(!m_ls->progressive_values.empty())<<"\n";
    }
    string in_string;
    uint64_t num_lits;
    freopen(argv[1],"r",stdin);
    cin>>num_lits;
    m_ls->make_lits_space(num_lits);//此处需要设定lits数量
    getline(cin,in_string);
    getline(cin, in_string);
    while(in_string!="0"){
        m_ls->build_lits(in_string);//传入string到lits
        getline(cin,in_string);
    }
    int size;
    cin>>size;
    std::vector<std::vector<int> > vec;
    vec.resize(size);
    int size_now=0;
    while(size_now<size){
        cin>>in_string;
        if(in_string=="(")continue;
        else if(in_string==")"){size_now++;}
        else{vec[size_now].push_back(atoi(in_string.c_str()));}
    }
    size=0;
    cin>>size;
    for(;size>0;size--){
        std::string v_name;
        int val;
        cin>>v_name;
        cin>>val;
        restored_md[v_name]=val;
    }
    m_ls->build_instance(vec);//传入一个vector<vector<int> >
    // m_ls->construct_solution();
    // m_ls->print_formula();
//    m_ls->print_formula_pbs();
    bool is_sat = m_ls->local_search();
    std::cerr<<"search_result search_sat="<<is_sat<<" steps="<<(m_ls->build_unsat?0:m_ls->_step)<<"\n";
    if(is_sat && !model_output.empty()){
        std::map<std::string,std::string> values;
        for(const auto& var:m_ls->_vars){
            auto index=m_ls->name2var.at(var.var_name);
            values[var.var_name]=var.is_nia?m_ls->print_128(m_ls->_solution[index]):(m_ls->_solution[index]>0?"true":"false");
        }
        for(const auto& var:m_ls->_resolution_vars)
            if(!var.is_nia && !values.count(var.var_name))values[var.var_name]=var.up_bool>0?"true":"false";
        if(!write_model(values)){delete m_ls;return 2;}
    }
    std::cout << (is_sat ? "sat" : "unknown") << std::endl;
    // string var_value;
    // string var_name="tmp189__global__428__instrumented__";
    // m_ls->print_var_solution(var_name, var_value);
    delete m_ls;
    // std::cout<<var_name<<" : "<<var_value<<endl;
    return 0;
}
}
using namespace call;
int main(int argc,char *argv[]){
    Call_fun c;
    return c.func(argc,argv);
}
