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
              "[--restart-repair=on|off] [--equality-protection=on|off] [--seed=N] [--model-output=path]\n";
        delete m_ls;
        return help?0:2;
    }
    bool initialize_enabled=true;
    m_ls->restart_repair_enabled=true;
    m_ls->equality_protection=true;
    std::string model_output;
    for(int arg=2;arg<argc;++arg){
        const std::string option=argv[arg];
        if(option=="--initialize=on")initialize_enabled=true;
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
