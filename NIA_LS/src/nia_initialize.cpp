#include "nia_ls.h"
#include "nia_omt_legacy_arithmetic.h"
namespace nia{

//initialize
ls_solver::ls_solver()
:_swt_p(0.3),
_swt_threshold(50),
smooth_probability(3),
_cutoff(1200),
_additional_len(10),
_max_step(UINT64_MAX),
_complete_ls(false),
_record_frequency(false)
{mt.seed(1);}

ls_solver::ls_solver(int random_seed,uint64_t max_step,bool complete_ls,bool record_frequency)
:_swt_p(0.3),
_swt_threshold(50),
smooth_probability(3),
_cutoff(1200),
_additional_len(10),
_max_step(max_step),
_complete_ls(complete_ls),
_record_frequency(record_frequency)
{mt.seed(random_seed);}



void ls_solver::initialize(){
    critical_operation_caches.clear();
    critical_operation_caches.resize(2*_lits.size());
    capturing_critical_operations=nullptr;
    clear_prev_data();
    construct_slution_score();
    initialize_term_datas();
    initialize_lit_datas();
    initialize_clause_datas();
    initialize_variable_datas();
    best_found_this_restart=unsat_clauses->size();
    if(restart_repair_enabled)
        restart_best_assignment.assign(_solution.begin(),_solution.begin()+_num_vars);
    update_best_solution();
}
void ls_solver::clear_prev_data(){
    if(restart_repair_enabled && initialization_round>0){
        std::fill(tabulist.begin(),tabulist.end(),0);
        std::fill(last_move.begin(),last_move.end(),0);
        last_op_var=UINT64_MAX;
        last_op_value=0;
        is_in_bool_search=false;
        sat_clause_with_false_literal->clear();
    }
    for(int v:bool_var_vec){_vars[v].score=0;}
    _best_found_hard_cost_this_bool=INT32_MAX;
    _best_found_hard_cost_this_nia=INT32_MAX;
    no_improve_cnt_bool=0;
    no_improve_cnt_nia=0;
}

//construction
void ls_solver::construct_slution_score(){
    const uint64_t round=initialization_round++;
    for(int i=0;i<_num_vars;++i){
        const variable& v=_vars[i];
        if(!v.is_nia){_solution[i]=-1;continue;}
        _solution[i]=std::max(v.low_bound,std::min(v.upper_bound,__int128_t(0)));
    }
    if(round==0 && !progressive_values.empty()){
        std::vector<__int128_t> candidate(_solution.begin(),_solution.begin()+_num_vars);
        unsigned mapped=0;
        for(size_t i=0;i<_num_vars;++i){
            auto found=progressive_values.find(_vars[i].var_name);
            if(found==progressive_values.end())continue;
            const std::string& text=found->second;
            if(!_vars[i].is_nia){
                if(text!="true" && text!="false")continue;
                candidate[i]=text=="true"?1:-1;++mapped;
            }else{
                try{
                    std::string number=text;
                    if(number.rfind("(- ",0)==0 && number.back()==')')number="-"+number.substr(3,number.size()-4);
                    size_t used=0;__int128_t value=std::stoll(number,&used);
                    if(used!=number.size())continue;
                    candidate[i]=std::max(_vars[i].low_bound,std::min(_vars[i].upper_bound,value));++mapped;
                }catch(const std::exception&){} // Keep the baseline value if not representable.
            }
        }
        auto cost=[&](const std::vector<__int128_t>& assignment)->uint64_t{
            std::vector<__int128_t> terms(_terms.size(),0);
            for(size_t i=0;i<_terms.size();++i){
                if(!term_appear[i])continue;
                __int128_t value=1;
                for(const auto& ve:_terms[i].var_epxs)
                    for(int k=0;k<ve.exponent;++k)
                        if(__builtin_mul_overflow(value,assignment[ve.var_index],&value))return UINT64_MAX;
                terms[i]=value;
            }
            uint64_t unsat=0;
            for(const auto& clause:_clauses){
                bool satisfied=false;
                for(int signed_index:clause.literals){
                    const auto& l=_lits[std::abs(signed_index)];bool truth;
                    if(l.is_nia_lit){
                        __int128_t delta=l.key;
                        for(const auto& ct:l.coff_terms){
                            __int128_t product;
                            if(__builtin_mul_overflow(ct.coff,terms[ct.term_idx],&product) ||
                               __builtin_add_overflow(delta,product,&delta))return UINT64_MAX;
                        }
                        truth=l.is_equal?delta==0:delta<=0;
                    }else truth=assignment[l.delta]>0;
                    if(truth==(signed_index>0)){satisfied=true;break;}
                }
                if(!satisfied)++unsat;
            }
            return unsat;
        };
        std::vector<__int128_t> original(_solution.begin(),_solution.begin()+_num_vars);
        uint64_t before=cost(original),after=cost(candidate);
        const bool installed=mapped>0 && after<before;
        if(installed)std::copy(candidate.begin(),candidate.end(),_solution.begin());
        std::cerr<<"progressive_handoff mapped="<<mapped<<" installed="<<installed
                 <<" before="<<before<<" after="<<after<<"\n";

    }
    if(round>0 && restart_seed.size()==_num_vars){
        std::copy(restart_seed.begin(),restart_seed.end(),_solution.begin());
        restart_seed.clear();
    }
//    read_model();
}

void ls_solver::read_model(){
    int size;
    std::cin>>size;
    std::string in_string,in_string_2;
    for(int i=0;i<size;i++){
        std::cin>>in_string;
        std::cin>>in_string_2;
        if(name2var.find(in_string)!=name2var.end()){
            if(in_string_2=="false"){_solution[name2var[in_string]]=-1;}
            else if(in_string_2=="true"){_solution[name2var[in_string]]=1;}
            else{_solution[name2var[in_string]]=atoi(in_string_2.c_str());}
        }
    }
}

void ls_solver::initialize_variable_datas(){
    
}

//initialize the value of each term
void ls_solver::initialize_term_datas(){
    for (variable &v : _vars) v.scoring_coffs_valid = false;
    for(term &t:_terms){
        t.value=1;
        for(var_exp &ve:t.var_epxs){
            if(omt_legacy_arithmetic::enabled){t.value=omt_legacy_arithmetic::multiply(t.value,omt_legacy_arithmetic::power(_solution[ve.var_index],ve.exponent));}
            else if(ve.exponent==1){t.value*=_solution[ve.var_index];}
            else{t.value*=std::pow((long long)_solution[ve.var_index],ve.exponent);}
            if(t.value==0)break;
        }
    }
}

//initialize the delta of each literal by delta_lit operation
void ls_solver::initialize_lit_datas(){
    for(lit &l:_lits){
        if(l.lits_index!=0){
            if(l.is_nia_lit){
                l.delta=delta_lit(l);
                if(l.is_equal){l.is_true=(l.delta==0)?1:-1;}
                else{l.is_true=(l.delta<=0)?1:-1;}
            }//nia lit
            else{l.is_true=(_solution[l.delta]>0)?1:-1;}//boolean lit
        }
    }
}
//set the sat num of each clause, and sat/unsat a clause
void ls_solver::initialize_clause_datas(){
    _lit_in_unsat_clause_num=0;
    _bool_lit_in_unsat_clause_num=0;
    for(uint64_t c=0;c<_num_clauses;c++){
        clause *cl=&(_clauses[c]);
        cl->sat_count=0;
        cl->weight=1;
        for(int l_idx:cl->literals){
            if(l_idx*_lits[std::abs(l_idx)].is_true>0){cl->sat_count++;cl->watch_lit_idx=l_idx;}//determine the sat count and watch lit
        }
        if(cl->sat_count==0){
            unsat_a_clause(c);
            _lit_in_unsat_clause_num+=_clauses[c].literals.size();
            _bool_lit_in_unsat_clause_num+=_clauses[c].bool_literals.size();
            for(int l_sign_idx:cl->bool_literals){_vars[_lits[std::abs(l_sign_idx)].delta].score++;}
        }
        else{sat_a_clause(c);}
        if(cl->sat_count>0&&cl->sat_count<cl->literals.size()){sat_clause_with_false_literal->insert_element((int)c);}
        //TODO::else{sat_clause_with_false_literal->delete_element((int)c);}
        if(cl->sat_count==1){
            lit *l=&(_lits[std::abs(cl->watch_lit_idx)]);
            if(!l->is_nia_lit){_vars[l->delta].score--;}
        }
    }
    total_clause_weight=_num_clauses;
}


}
