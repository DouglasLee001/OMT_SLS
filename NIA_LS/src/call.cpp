//
//  call.cpp
//  lia_ls
//
//  Created by DouglasLee on 2021/11/2.
//

#include "call.h"
#include "nia_linear_initialize.h"
#include "nia_lia_sls.h"
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
    const bool help=argc==2 && std::string(argv[1])=="--help";
    if(argc<2 || help){
        (help?std::cout:std::cerr)
            <<"usage: nia_ls <input> [--initialize=on|off] "
              "[--restart-repair=on|off] [--equality-protection=on|off] [--seed=N] [--model-output=path] [--import-only]\n";
        delete m_ls;
        return help?0:2;
    }
    bool initialize_enabled=true;
    bool import_only=false;
    m_ls->restart_repair_enabled=true;
    m_ls->equality_protection=true;
    std::string model_output;
    for(int arg=2;arg<argc;++arg){
        const std::string option=argv[arg];
        if(option=="--import-only")import_only=true;
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
    // Versioned OMT input has a separate, exact reader. Never send it through
    // the feasibility initializer or report the first feasible model as optimum.
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
        if(!import_only){
            std::cerr<<"OMT input loaded, but optimization search is not implemented. "
                       "Use --import-only to check constraints and objective.\n";
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
