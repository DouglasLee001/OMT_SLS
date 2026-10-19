#include "nia_ls.h"
#include <cmath>
#include <sstream>
#define LS_DEBUG
namespace nia{

//random walk
void ls_solver::update_clause_weight(){
    for(int i=0;i<unsat_clauses->size();i++){
        clause *unsat_cl=&(_clauses[unsat_clauses->element_at(i)]);
        unsat_cl->weight++;
        for(int l_sign_idx:unsat_cl->bool_literals){_vars[_lits[std::abs(l_sign_idx)].delta].score++;}
    }
    total_clause_weight+=unsat_clauses->size();
}

void ls_solver::smooth_clause_weight(){
    for(int i=0;i<_num_clauses;i++){
        if(_clauses[i].weight>1&&!unsat_clauses->is_in_array(i)){
            _clauses[i].weight--;
            total_clause_weight--;
            if(_clauses[i].sat_count==1&&!_lits[std::abs(_clauses[i].watch_lit_idx)].is_nia_lit){
                __int128_t watch_lit_idx=_lits[std::abs(_clauses[i].watch_lit_idx)].delta;
                _vars[watch_lit_idx].score++;
            }
        }
    }
}

//when there is no operation, simply find a lit in a random false clause, pick a random var with coff!=0, set it to 0
void ls_solver::no_operation_random_walk(){
    clause *cp=&(_clauses[unsat_clauses->element_at(mt()%unsat_clauses->size())]);//choose a random unsat clause
    int lit_idx=cp->literals[mt()%cp->literals.size()];
    lit *l=&(_lits[std::abs(lit_idx)]);
    if(!l->is_nia_lit){
        critical_move(l->delta, 0);
        return;
    }//boolean lit
    var_lit_term *vlt;//nia lit
    __int128_t coffs[2]={0,0};//the coff = sum(term_coff*vlt_coff) ( 1 * x1*x2 ) + ( -1 * x1 ) + ( -1 * x3 ) for x1 the coff=(1*x2)+(-1)
    uint64_t var_idx_curr=l->var_lit_terms[0].var_idx;//the current var idx of var_lit_term
    int l_var_lit_term_num=(int)l->var_lit_terms.size();
    std::vector<uint64_t> var_idx_none_zero;//the vars with coff != 0
    //go through the var_lit_term and insert critical move for each var
    for(int vlt_idx=0;vlt_idx<l_var_lit_term_num;vlt_idx++){
        vlt=&(l->var_lit_terms[vlt_idx]);
        if(vlt->var_idx!=var_idx_curr){
            coffs[0]=coffs[1]=0;
            var_idx_curr=vlt->var_idx;
        }//enter a new var
        if(vlt->exponent<3){coffs[vlt->exponent-1]+=vlt->coff*coff_in_term(var_idx_curr, vlt->term_idx,vlt->exponent);}//determine the coff, for vars with only coff>2, the var will not be considered
        if((vlt_idx==l_var_lit_term_num-1)||(var_idx_curr!=l->var_lit_terms[vlt_idx+1].var_idx)){
            if(coffs[0]!=0||coffs[1]!=0){var_idx_none_zero.push_back(var_idx_curr);}// if coff==0, changing the var cannot make any progress
        }//the last vlt of the var or the last vlt of the lit
    }
    if(var_idx_none_zero.size()==0){var_idx_curr=l->var_lit_terms[mt()%l_var_lit_term_num].var_idx;}
    else{var_idx_curr=var_idx_none_zero[mt()%var_idx_none_zero.size()];}
    __int128_t future_solution=0;
    variable *var=&(_vars[var_idx_curr]);
    if(var->low_bound>0){future_solution=var->low_bound;}
    else if(var->upper_bound<0){future_solution=var->upper_bound;}
    critical_move(var_idx_curr, future_solution-_solution[var_idx_curr]);//move a random var with coff!=0 to 0
}

void ls_solver::random_walk(){
    int operation_idx(0),operation_idx_bool(0),clause_idx;
    clause *cp;
    //determine the operations
    for(int i=0;i<3&&operation_idx+operation_idx_bool<5;i++){
        clause_idx=unsat_clauses->element_at(mt()%unsat_clauses->size());
        cp=&(_clauses[clause_idx]);
        for(int l_idx:cp->nia_literals){add_operation_from_false_lit(false, l_idx, operation_idx);}
        for(int l_idx:cp->bool_literals){add_bool_operation(false, l_idx, operation_idx_bool);}
    }
    //recover bool vec
    for(int i=0;i<operation_idx_bool;i++){is_chosen_bool_var[operation_var_idx_bool_vec[i]]=false;}
    //recover the false_lit
    false_lit_occur->clear();
    //if no operation, return
    if(operation_idx+operation_idx_bool==0){
        last_op_var=UINT64_MAX;//in case the random walk make no move, it will not ban the only operation
        no_operation_random_walk();
        return;
    }
    //nia mode make move
    if(operation_idx_bool==0||(operation_idx>0&&operation_idx_bool>0&&!is_in_bool_search)){
        __int128_t best_value_nia;
        int best_var_idx_nia,best_score_nia(INT32_MIN);
        const int STEP=40000,K_MAX=12;
        const int K=std::min(K_MAX,1+random_walk_stall_steps/STEP);
        const int M=std::min(K,operation_idx);
        const bool use_window=K>1&&operation_idx>1;
        uint64_t window_var_idx[K_MAX];
        __int128_t window_change_value[K_MAX];
        if(use_window){
            // Preserve insertion order and pairs before BMS overwrites the work vectors.
            for(int r=0;r<M;r++){
                window_var_idx[r]=operation_var_idx_vec[r];
                window_change_value[r]=operation_change_value_vec[r];
            }
        }
        select_best_operation_from_vec(operation_idx, best_score_nia, best_var_idx_nia, best_value_nia);//choose best nia operation
        if(use_window){
            const int P =
                (random_walk_stall_steps < 120000) ? 2 :
                (random_walk_stall_steps < 400000) ? 3 : 4;
            const bool allow_override=random_walk_window_keeps>=P-1;
            const double alpha=std::max(0.2,4.0-random_walk_stall_steps/50000.0);
            double weights[K_MAX],total_weight=0.0;
            int last_valid=-1;
            for(int r=0;r<M;r++){
                const double weight=std::pow(r+1,-alpha);
                weights[r]=(std::isfinite(weight)&&weight>0.0)?weight:0.0;
                if(weights[r]>0.0){
                    total_weight+=weights[r];
                    last_valid=r;
                }
            }
            if(last_valid>=0&&std::isfinite(total_weight)&&total_weight>0.0){
                const double draw=std::uniform_real_distribution<double>(0.0,1.0)(mt);
                double cumulative=0.0;
                int selected=last_valid;//rounding at the cumulative boundary
                for(int r=0;r<M;r++){
                    cumulative+=weights[r]/total_weight;
                    if(weights[r]>0.0&&draw<cumulative){selected=r;break;}
                }
                if(allow_override){
                    best_var_idx_nia=(int)window_var_idx[selected];
                    best_value_nia=window_change_value[selected];
                }
            }
            // Every eligible call consumes an opportunity, even if sampling falls back.
            // Carry accumulated keeps across stagnation stage boundaries.
            random_walk_window_keeps=allow_override?0:(random_walk_window_keeps+1);
        }
        critical_move(best_var_idx_nia, best_value_nia);
    }
    //bool mode make move
    else{
        //choose best bool operation
        int best_var_idx_bool(0),best_score_bool(INT32_MIN);
        select_best_operation_from_bool_vec(operation_idx_bool, best_score_bool, best_var_idx_bool);
        critical_move(best_var_idx_bool, 0);
    }
}

//basic operations
bool ls_solver::update_best_solution(){
    bool improve=false;
    if(unsat_clauses->size()<best_found_this_restart){
        improve=true;
        best_found_this_restart=unsat_clauses->size();
        if(restart_repair_enabled)
            restart_best_assignment.assign(_solution.begin(),_solution.begin()+_num_vars);
        if(_complete_ls){
            for(uint64_t lit_idx=0;lit_idx<_num_lits;lit_idx++){
                _best_literal_assignment[lit_idx]=(_lits[lit_idx].lits_index==0)?0:_lits[lit_idx].is_true;
            }
        }
    }
    if(unsat_clauses->size()<best_found_cost){
        improve=true;
        best_found_cost=unsat_clauses->size();
        best_cost_time=TimeElapsed();
    }
    return improve;
}

int ls_solver::pick_critical_move_bool(){
    int best_var_idx(-1),best_score(1);
    int operation_idx=0;
    for(int i=0;i<contain_bool_unsat_clauses->size();i++){
        int clause_idx=contain_bool_unsat_clauses->element_at(i);
        clause *cl=&(_clauses[clause_idx]);
        for(int l_sign_idx:cl->bool_literals){add_bool_operation(true, l_sign_idx, operation_idx);}
    }
    for(int i=0;i<operation_idx;i++){is_chosen_bool_var[operation_var_idx_bool_vec[i]]=false;}// recover chosen_bool_var
    select_best_operation_from_bool_vec(operation_idx, best_score, best_var_idx);
    //if there is untabu decreasing move
    if(best_var_idx!=-1){return best_var_idx;}
    //update weight
    if(mt()%10000>smooth_probability){update_clause_weight();}
    else {smooth_clause_weight();}
    random_walk();
    return -1;
}

void ls_solver::add_bool_operation(bool use_tabu, int lit_idx, int &operation_idx_bool){
    __int128_t bool_var_idx=_lits[std::abs(lit_idx)].delta;
    if(is_chosen_bool_var[bool_var_idx])return;//operations will not be inserted repeatedly
    if(!use_tabu||(use_tabu&&_outer_layer_step>tabulist[2*bool_var_idx])){//tabu mechanism
        operation_var_idx_bool_vec[operation_idx_bool++]=(int)bool_var_idx;
        is_chosen_bool_var[bool_var_idx]=true;
    }
}

//calculate the operation to be added for exp==1
void ls_solver::add_coff(uint64_t var_idx_curr, bool use_tabu, int lit_idx, int &operation_idx,int coff_1){
    lit *l=&(_lits[std::abs(lit_idx)]);
    __int128_t lit_delta=l->delta;
    if(l->is_equal){
        if(lit_idx>0){
            if(lit_delta%coff_1==0){insert_operation(var_idx_curr,(-lit_delta/coff_1), operation_idx,use_tabu);}
        }//the delta should be 0, while it is now !=0, so the change value should be -delta/coff
        else{
            insert_operation(var_idx_curr,1, operation_idx,use_tabu);
            insert_operation(var_idx_curr,-1, operation_idx,use_tabu);
        }//the delta should be !=0, while it is now 0, so the change value should be +/- 1
    }
    else{
        if(lit_idx>0){insert_operation(var_idx_curr, devide((-lit_delta), coff_1), operation_idx, use_tabu);}//the delta should be <=0, while it is now >0, so the change value should add -delta/coff
        else{insert_operation(var_idx_curr, devide((1-lit_delta), coff_1), operation_idx, use_tabu);}//the delta should be >0, while it is now <=0, so the change value should add (1-delta)/coff
    }
}
bool IsSqrt(int n,int &sq){
    int i=1;
    while(n>0){n-= i;i+=2;}
    sq=(i-1)/2;
    return 0==n?true:false;
}

//calculate the operation to be added for exp==2: a*x^2+b*x+c
void ls_solver::add_coff(uint64_t var_idx_curr, bool use_tabu, int lit_idx, int &operation_idx,int a,int b,int c){
    lit *l=&(_lits[std::abs(lit_idx)]);
    int D=b*b-4*a*c;//b^2-4*a*c
    int sq=0;//tend to be larger
    bool is_D_sqrt=IsSqrt(D, sq);
    int left_low=floor((double)(-b-sq)/(2*a));
    int left_up=ceil((double)(-b-sq)/(2*a));
    int right_low=floor((double)(-b+sq)/(2*a));
    int right_up=ceil((double)(-b+sq)/(2*a));
    if((D>0&&left_up==right_up)||(D==0&&left_up!=left_low)||(D<0)){return;}//no integer solution on the other side
    if(l->is_equal){
        if(lit_idx>0){
            if(!is_D_sqrt){return;}//no integer solution for 0
            if(left_up==left_low){insert_operation(var_idx_curr,left_up-_solution[var_idx_curr], operation_idx,use_tabu);}
            if(D>0&&right_up==right_low){insert_operation(var_idx_curr,right_up-_solution[var_idx_curr], operation_idx,use_tabu);}
        }//the delta should be 0, while it is now !=0
        else{
            insert_operation(var_idx_curr,1, operation_idx,use_tabu);
            insert_operation(var_idx_curr,-1, operation_idx,use_tabu);
        }//the delta should be !=0, while it is now 0, so the change value should be +/- 1
    }
    else{
        if(lit_idx>0){
            if(D==0&&a>0&&left_up==left_low){insert_operation(var_idx_curr, left_up-_solution[var_idx_curr], operation_idx, use_tabu);}//the only 0 solution is left_up
            else if(D>0){
                if(a>0){
                    if(a*left_up*left_up+b*left_up+c>0){left_up++;}
                    if(a*right_low*right_low+b*right_low+c>0){right_low--;}
                    insert_operation(var_idx_curr, left_up-_solution[var_idx_curr], operation_idx, use_tabu);
                    insert_operation(var_idx_curr, right_low-_solution[var_idx_curr], operation_idx, use_tabu);
                }
                else if(a<0){
                    if(a*left_low*left_low+b*left_low+c>0){left_low--;}
                    if(a*right_up*right_up+b*right_up+c>0){right_up++;}
                    insert_operation(var_idx_curr, left_low-_solution[var_idx_curr], operation_idx, use_tabu);
                    insert_operation(var_idx_curr, right_up-_solution[var_idx_curr], operation_idx, use_tabu);
                }
            }
        }//the delta should be <=0, while it is now >0, so the change value should add -delta/coff
        else{
            if(D==0&&a>0){
                insert_operation(var_idx_curr,1, operation_idx,use_tabu);
                insert_operation(var_idx_curr,-1, operation_idx,use_tabu);
            }
            else if(D>0){
                if(a>0){
                    if(a*left_low*left_low+b*left_low+c<=0){left_low--;}
                    if(a*right_up*right_up+b*right_up+c<=0){right_up++;}
                    insert_operation(var_idx_curr, left_low-_solution[var_idx_curr], operation_idx, use_tabu);
                    insert_operation(var_idx_curr, right_up-_solution[var_idx_curr], operation_idx, use_tabu);
                }
                else if(a<0){
                    if(a*left_up*left_up+b*left_up+c<=0){left_up++;}
                    if(a*right_low*right_low+b*right_low+c<=0){right_low--;}
                    insert_operation(var_idx_curr, left_up-_solution[var_idx_curr], operation_idx, use_tabu);
                    insert_operation(var_idx_curr, right_low-_solution[var_idx_curr], operation_idx, use_tabu);
                }
            }
        }//the delta should be >0, while it is now <=0, so the change value should add (1-delta)/coff
    }
}//a*x^2+b*x+c

//for a falsified NIA lit, choose critical move from it
void ls_solver::add_operation_from_false_lit(bool use_tabu, int lit_idx, int &operation_idx){
    if(false_lit_occur->is_in_array(std::abs(lit_idx))){return;}//if the false lit has been considered, then the lit will not be considered repeatedly
    false_lit_occur->insert_element(std::abs(lit_idx));
#ifdef NIA_CANDIDATE_REFERENCE
    generate_operations_from_false_lit(use_tabu,lit_idx,operation_idx);
#else
    auto& cached=critical_operation_caches[2*size_t(std::abs(lit_idx))+(lit_idx<0)];
    if (!cached.valid) {
        cached.moves.clear();
        capturing_critical_operations=&cached.moves;
        int ignored=0;
        generate_operations_from_false_lit(false,lit_idx,ignored);
        capturing_critical_operations=nullptr;
        cached.valid=true;
        ++candidate_cache_rebuilds;
    } else {
        ++candidate_cache_hits;
    }
#ifdef NIA_CANDIDATE_VERIFY
    // Compare ordered raw moves, including duplicates, with fresh generation.
    std::vector<raw_critical_operation> expected;
    capturing_critical_operations=&expected;
    int ignored=0;
    generate_operations_from_false_lit(false,lit_idx,ignored);
    capturing_critical_operations=nullptr;
    if (expected!=cached.moves) {
        std::cerr << "critical operation cache mismatch at step " << _step << " literal " << lit_idx << '\n';
        std::abort();
    }
#endif
    for (const auto& move:cached.moves)
        insert_operation(move.first,move.second,operation_idx,use_tabu);
#endif
}

void ls_solver::generate_operations_from_false_lit(bool use_tabu,int lit_idx,int& operation_idx){
    int total_term_value=0;//the value of terms containing the var
    lit *l=&(_lits[std::abs(lit_idx)]);
    uint64_t var_idx_curr=l->var_lit_terms[0].var_idx;//the current var idx of var_lit_term
    var_lit_term *vlt;
    int l_var_lit_term_num=(int)l->var_lit_terms.size();
    bool too_high_exp=false;//if the exponent of var is larger than 2, then the var should be modified
    int coffs[2]={0,0};//the coff = sum(term_coff*vlt_coff) ( 1 * x1*x2 ) + ( -1 * x1 ) + ( -1 * x3 ) for x1 the coff=(1*x2)+(-1)
    //go through the var_lit_term and insert critical move for each var
    for(int vlt_idx=0;vlt_idx<l_var_lit_term_num;vlt_idx++){
        vlt=&(l->var_lit_terms[vlt_idx]);
        if(vlt->var_idx!=var_idx_curr){
            total_term_value=0;
            coffs[0]=coffs[1]=0;//reset coffs
            var_idx_curr=vlt->var_idx;
            too_high_exp=false;
        }//enter a new var
        if(vlt->exponent>2){too_high_exp=true;}
        if(too_high_exp){continue;}//if too high exponent, the operations will not be inserted
        coffs[vlt->exponent-1]+=vlt->coff*coff_in_term(var_idx_curr, vlt->term_idx,vlt->exponent);//determine the coff
        total_term_value+=vlt->coff*_terms[vlt->term_idx].value;
        if((vlt_idx==l_var_lit_term_num-1)||(var_idx_curr!=l->var_lit_terms[vlt_idx+1].var_idx)){
            if(coffs[0]==0&&coffs[1]==0){
                insert_operation(var_idx_curr, 1, operation_idx, use_tabu);
                insert_operation(var_idx_curr, -1, operation_idx, use_tabu);
                continue;
            }// if coff==0, change the var by 1
            else if(coffs[1]==0){add_coff(var_idx_curr,use_tabu,lit_idx,operation_idx,coffs[0]);}//high coff is 1
            else{add_coff(var_idx_curr,use_tabu,lit_idx,operation_idx,coffs[1],coffs[0],l->delta-total_term_value);}//high coff is 2
        }//the last vlt of the var or the last vlt of the lit
    }
}
//select best bool operation from
void ls_solver::select_best_operation_from_bool_vec(int operation_idx_bool, int &best_score_bool, int &best_var_idx_bool){
    int cnt,score,var_idx;
    uint64_t best_last_move(UINT64_MAX);
    bool BMS;
    if(operation_idx_bool>45){BMS=true;cnt=45;}
    else{BMS=false;cnt=operation_idx_bool;}
    for(int i=0;i<cnt;i++){
        if(BMS){
            int idx=mt()%(operation_idx_bool-i);
            int tmp=operation_var_idx_bool_vec[operation_idx_bool-i-1];
            var_idx=operation_var_idx_bool_vec[idx];
            operation_var_idx_bool_vec[idx]=tmp;
        }
        else{var_idx=operation_var_idx_bool_vec[i];}
        score=_vars[var_idx].score;
        uint64_t last_move_step=last_move[2*var_idx];
        if(score>best_score_bool||(score==best_score_bool&&last_move_step<best_last_move)){
            best_score_bool=score;
            best_var_idx_bool=var_idx;
            best_last_move=last_move_step;
        }
    }
}
//select the best nia operation from operation_vec depending on score
// Count distinct satisfied positive equality literals broken by a candidate.
// Disequalities (negative occurrences) and clause weights do not contribute.
int ls_solver::equality_break_count(uint64_t var_idx, __int128_t change_value){
    const auto &entries=_vars[var_idx].var_lit_terms;
    int broken=0;
    for(size_t i=0;i<entries.size();){
        const auto lit_idx=entries[i].lit_idx;
        const lit &l=_lits[lit_idx];
        size_t end=i+1;
        while(end<entries.size() && entries[end].lit_idx==lit_idx)++end;
        bool positive=false;
        if(l.is_equal && l.delta==0){
            for(size_t p=_score_literal_offsets[lit_idx];p<_score_literal_offsets[lit_idx+1];++p)
                if(_score_clause_occurrences[p]>0){positive=true;break;}
        }
        if(positive){
            __int128_t delta=l.delta;
            for(size_t j=i;j<end;++j){
                const auto &v=entries[j];
                __int128_t coff=v.coff*coff_in_term(var_idx,v.term_idx,v.exponent);
                if(v.exponent==1)delta+=coff*change_value;
                else delta+=coff*(std::pow((long long)(_solution[var_idx]+change_value),v.exponent)
                                -std::pow((long long)_solution[var_idx],v.exponent));
            }
            if(delta!=0)++broken;
        }
        i=end;
    }
#ifdef NIA_EQUALITY_VERIFY
    // Independent full-formula oracle, recomputing every monomial from assignments.
    int reference=0;
    std::vector<bool> positive_literals(_num_lits,false);
    for(const auto &c:_clauses)for(int id:c.literals)if(id>0)positive_literals[id]=true;
    for(uint64_t id=1;id<_num_lits;++id){
        const lit &l=_lits[id];
        if(l.lits_index==0 || !l.is_nia_lit || !l.is_equal)continue;
        if(!positive_literals[id])continue;
        __int128_t old_delta=l.key,new_delta=l.key;
        for(const auto &ct:l.coff_terms){
            __int128_t old_term=1,new_term=1;
            for(const auto &ve:_terms[ct.term_idx].var_epxs){
                auto old_value=_solution[ve.var_index];
                auto new_value=old_value+(ve.var_index==var_idx?change_value:0);
                if(ve.exponent==1){old_term*=old_value;new_term*=new_value;}
                else{old_term*=std::pow((long long)old_value,ve.exponent);new_term*=std::pow((long long)new_value,ve.exponent);}
            }
            old_delta+=ct.coff*old_term;new_delta+=ct.coff*new_term;
        }
        if(old_delta==0 && new_delta!=0)++reference;
    }
    if(reference!=broken){std::cerr<<"equality score mismatch\n";std::abort();}
#endif
    return broken;
}

void ls_solver::select_best_operation_from_vec(int operation_idx,int &best_score,int &best_var_idx,__int128_t &best_value){
    int best_breaks=-1;
    bool BMS;
    int cnt,score;
    uint64_t operation_var_idx,best_last_move(UINT64_MAX);
    __int128_t operation_change_value,best_future_abs_value(INT64_MAX),future_abs_value;
    if(operation_idx>45){BMS=true;cnt=45;}
    else{BMS=false;cnt=operation_idx;}
    for(int i=0;i<cnt;i++){
        if(BMS){
            int idx=mt()%(operation_idx-i);
            operation_change_value=operation_change_value_vec[idx];
            operation_var_idx=operation_var_idx_vec[idx];
            operation_change_value_vec[idx]=operation_change_value_vec[operation_idx-i-1];
            operation_var_idx_vec[idx]=operation_var_idx_vec[operation_idx-i-1];
        }
        else{
            operation_change_value=operation_change_value_vec[i];
            operation_var_idx=operation_var_idx_vec[i];
        }
        future_abs_value=abs_128(_solution[operation_var_idx]+operation_change_value);
        score=critical_score(operation_var_idx,operation_change_value);
        int opposite_direction=(operation_change_value>0)?1:0;//if the change value is >0, then means it is moving forward, the opposite direction is 1(backward)
        uint64_t last_move_step=last_move[2*operation_var_idx+opposite_direction];
        bool prefer=score>best_score;
        int breaks=-1;
        if(score==best_score){
            bool equal_aux=true;
            if(equality_protection && best_var_idx>=0){
                ++equality_tie_checks;
                if(best_breaks<0)best_breaks=equality_break_count(best_var_idx,best_value);
                breaks=equality_break_count(operation_var_idx,operation_change_value);
                prefer=breaks<best_breaks;
                equal_aux=breaks==best_breaks;
            }
            if(equal_aux)prefer=future_abs_value<best_future_abs_value ||
                (future_abs_value==best_future_abs_value && last_move_step<best_last_move);
        }
        if(prefer){
            best_breaks=breaks;
            best_score=score;
            best_var_idx=(int)operation_var_idx;
            best_value=operation_change_value;
            best_last_move=last_move_step;
            best_future_abs_value=future_abs_value;
        }
    }
}
//pick a NIA critical move
int ls_solver::pick_critical_move(__int128_t &best_value){
    int best_score(1),best_var_idx(-1),operation_idx(0);
    //determine the critical value from unsat sat, using tabu
    for(int i=0;i<unsat_clauses->size();i++){
        clause *cl=&(_clauses[unsat_clauses->element_at(i)]);
        for(int l_idx:cl->nia_literals){add_operation_from_false_lit(true, l_idx, operation_idx);}
    }
    //recover the false_lit
    false_lit_occur->clear();
    //go through the forward and backward move of vars, evaluate their score, pick the untabued best one
    select_best_operation_from_vec(operation_idx,best_score,best_var_idx, best_value);
    //if there is untabu decreasing move
    if(best_var_idx!=-1){return best_var_idx;}
    //choose from swap operations if there is no decreasing unsat critical
    if(!sat_clause_with_false_literal->empty()){
        operation_idx=0;
        add_swap_operation(operation_idx);
        //recover the false_lit
        false_lit_occur->clear();
        select_best_operation_from_vec(operation_idx,best_score,best_var_idx, best_value);
        if(best_var_idx!=-1){return best_var_idx;}
    }
    //update weight and random walk
    if(mt()%10000>smooth_probability){update_clause_weight();}
    else {smooth_clause_weight();}
    random_walk();
    return -1;
}

void ls_solver::modify_frequency_in_unsat_clauses(){
    int l_idx_abs=0;
    int num_lits_in_unsat_clauses=0;
    //record
    for(int i=0;i<unsat_clauses->size();i++){
        for(int l:_clauses[unsat_clauses->element_at(i)].literals){
            l_idx_abs=std::abs(l);
            if(!is_in_unsat_clause[l_idx_abs]){
                is_in_unsat_clause[l_idx_abs]=true;
                lits_in_unsat_clause[num_lits_in_unsat_clauses++]=l_idx_abs;
                _frequency_in_unsat_clauses[l_idx_abs]++;
            }
        }
    }
    //revert
    for(int i=0;i<num_lits_in_unsat_clauses;i++){
        l_idx_abs=lits_in_unsat_clause[i];
        is_in_unsat_clause[l_idx_abs]=false;
    }
}
//make move
void ls_solver::critical_move(uint64_t var_idx, __int128_t change_value){
    int direction=(change_value>0)?0:1;
    if(_vars[var_idx].is_nia){
        last_op_value=change_value;
        last_op_var=var_idx;
        move_update(var_idx, change_value);
        _solution[var_idx]+=change_value;
    }
    else{
        last_op_var=UINT64_MAX;//if last operation is boolean operation, the var should no longer be banned
        int origin_score=_vars[var_idx].score;
        move_update(var_idx);
        _solution[var_idx]*=-1;
        _vars[var_idx].score=-origin_score;
    }
    if(_record_frequency){modify_frequency_in_unsat_clauses();}
    //step
    if(_vars[var_idx].is_nia){
        last_move[2*var_idx+direction]=_step;
        tabulist[var_idx*2+(direction+1)%2]=_step+3+mt()%10;
    }
    else{
        last_move[2*var_idx]=_outer_layer_step;
        tabulist[2*var_idx]=_outer_layer_step+1+mt()%3;
        _outer_layer_step++;
    }
}
//transfer the ">" to "<="
void ls_solver::invert_lit(lit &l){
    l.key=1-l.key;
    for(int i=0;i<l.coff_terms.size();i++){l.coff_terms[i].coff*=-1;}
}
//all coffs are positive, go through all terms of the literal
__int128_t ls_solver::delta_lit(lit &l){
    __int128_t delta=l.key;
    for(int i=0;i<l.coff_terms.size();i++){delta+=(l.coff_terms[i].coff*_terms[l.coff_terms[i].term_idx].value);}
    return delta;
}

__int128_t ls_solver::coff_in_term(uint64_t var_idx, uint64_t term_idx,int exponent){
    if(_terms[term_idx].var_epxs.size()==1){return 1;}//the term only contains the var
    if(_solution[var_idx]!=0){
        if(exponent==1){return _terms[term_idx].value/_solution[var_idx];}
        else{return _terms[term_idx].value/(std::pow((long long)_solution[var_idx], exponent));}
    }//if the var!=0, the coff is value/var_solution
    else{
        __int128_t coff=1;
        for(var_exp &ve:_terms[term_idx].var_epxs){
            if(ve.var_index==var_idx){continue;}//the var itself will not be counted
            if(ve.exponent==1){coff*=_solution[ve.var_index];}
            else{coff*=std::pow((long long)_solution[ve.var_index],ve.exponent);}
            if(coff==0){break;}
        }
        return coff;
    }//the var_solution==0, then the coff should be calculated by going through the ve of the term
}

double ls_solver::TimeElapsed(){
    std::chrono::steady_clock::time_point finish = std::chrono::steady_clock::now();
    std::chrono::duration<double> duration = finish - start;
    return duration.count();
}

//return the upper round of (a/b): (-3.5)->-4; (3.5)->4
__int128_t ls_solver::devide(__int128_t a, __int128_t b){
    __int128_t abs_a=abs_128(a);
    __int128_t abs_b=abs_128(b);
    __int128_t up_round=abs_a/abs_b;
    if(abs_a%abs_b>0){up_round++;}
    return (a^b)>=0?up_round:-up_round;
}
void ls_solver::insert_operation(uint64_t var_idx,__int128_t change_value,int &operation_idx,bool use_tabu){
    // Cache before all dynamic filters; a temporarily forbidden move must survive.
    if (capturing_critical_operations) {
        capturing_critical_operations->emplace_back(var_idx,change_value);
        return;
    }
    if(var_in_long_term->is_in_array((int)var_idx)){
        for(term_exp &te:_vars[var_idx].term_exps){
            term &t=_terms[te.term_idx];
            if(t.var_epxs.size()>2||t.var_epxs[0].exponent>1){
                __int128_t future_term_value;
                if(te.exponent==1){future_term_value=t.value+coff_in_term(var_idx, te.term_idx,te.exponent)*change_value;}
                else{future_term_value=coff_in_term(var_idx, te.term_idx,te.exponent)*std::pow((long long)(_solution[var_idx]+change_value),te.exponent);}
                if(future_term_value>max_int||future_term_value<-max_int){return;}
            }
        }
    }
    if(var_idx==last_op_var&&change_value==-last_op_value){return;}//if op returns to previous assignment, it is banned
    uint64_t direction=(change_value>0)?0:1;
    if(use_tabu&&_step<tabulist[2*var_idx+direction]){return;}// the operation is now tabued
    __int128_t future_solution=_solution[var_idx]+change_value;
    if(future_solution>100000000||future_solution<-100000000){return;}
    bool no_pre_value=(_pre_value_1[var_idx]==INT32_MAX&&_pre_value_2[var_idx]==INT32_MAX&&future_solution>=_vars[var_idx].low_bound&&future_solution<=_vars[var_idx].upper_bound);
    bool has_pre_value_1=(_pre_value_1[var_idx]!=INT32_MAX&&_pre_value_2[var_idx]==INT32_MAX&&future_solution==_pre_value_1[var_idx]);
    bool has_pre_value_2=(_pre_value_1[var_idx]!=INT32_MAX&&_pre_value_2[var_idx]!=INT32_MAX&&(future_solution==_pre_value_1[var_idx]||future_solution==_pre_value_2[var_idx]));
    if(no_pre_value||has_pre_value_1||has_pre_value_2){
//    if(future_solution>=_vars[var_idx].low_bound&&future_solution<=_vars[var_idx].upper_bound){
        // The parser's estimate omits some simple literals. Grow both arrays
        // together rather than dropping candidates or writing past their ends.
        if (size_t(operation_idx)>=operation_var_idx_vec.size()) {
            const size_t size = std::max(size_t(operation_idx)+1, operation_var_idx_vec.size()*2);
            operation_var_idx_vec.resize(size);
            operation_change_value_vec.resize(size);
        }
        operation_var_idx_vec[operation_idx]=var_idx;
        operation_change_value_vec[operation_idx++]=change_value;
    }
}

void ls_solver::add_swap_operation(int &operation_idx){
    int clause_idx;
    clause *cl;
    if(sat_clause_with_false_literal->size()<20){
        for(int i=0;i<sat_clause_with_false_literal->size();i++){
            clause_idx=sat_clause_with_false_literal->element_at(i);
            cl=&(_clauses[clause_idx]);
            for(int l_idx:cl->nia_literals){
                if((_lits[std::abs(l_idx)].is_true^l_idx)<0){add_operation_from_false_lit(true, l_idx, operation_idx);}//determine a false lit, and add operation from it
            }
        }
    }
    else{
        for(int i=0;operation_idx<20&&i<50;i++){
            clause_idx=sat_clause_with_false_literal->element_at(mt()%sat_clause_with_false_literal->size());
            cl=&(_clauses[clause_idx]);
            for(int l_idx:cl->nia_literals){
                if((_lits[std::abs(l_idx)].is_true^l_idx)<0){add_operation_from_false_lit(true, l_idx, operation_idx);}//determine a false lit, and add operation from it
            }
        }
    }
    
}

//choose a clause with small weight, then choose a random lit, select the operation with greatest score in the lit
void ls_solver::swap_from_small_weight_clause(){}


void ls_solver::initialize_scoring_index(){
    _score_literal_offsets.assign(_lits.size()+1, 0);
    for (const clause &c : _clauses)
        for (int l : c.nia_literals)
            ++_score_literal_offsets[std::abs(l)+1];
    for (size_t i=1; i<_score_literal_offsets.size(); ++i)
        _score_literal_offsets[i] += _score_literal_offsets[i-1];
    _score_clause_occurrences.resize(_score_literal_offsets.back());
    std::vector<size_t> next = _score_literal_offsets;
    for (size_t c=0; c<_clauses.size(); ++c)
        for (int l : _clauses[c].nia_literals)
            _score_clause_occurrences[next[std::abs(l)]++] = l>0 ? int(c)+1 : -int(c)-1;
    _score_clause_deltas.assign(_clauses.size(), INT32_MIN);
    _score_touched_clauses.clear();
    _score_touched_clauses.reserve(_clauses.size());
    for (variable &v : _vars) {
        v.scoring_coffs.resize(v.var_lit_terms.size());
        v.scoring_coffs_valid = false;
    }
}

// Evaluate only clauses reached by literals whose truth value changes.
int ls_solver::critical_score(uint64_t var_idx, __int128_t change_value){
#ifdef NIA_SCORING_REFERENCE
    return critical_score_reference(var_idx, change_value);
#else
    variable &var = _vars[var_idx];
    if (change_value==0 || var.var_lit_terms.empty()) return 0;
    if (!var.scoring_coffs_valid) {
        for (const term_exp &te : var.term_exps)
            term_coffs[te.term_idx] = coff_in_term(var_idx, te.term_idx, te.exponent);
        for (size_t i=0; i<var.var_lit_terms.size(); ++i) {
            const var_lit_term &vlt = var.var_lit_terms[i];
            var.scoring_coffs[i] = vlt.coff * term_coffs[vlt.term_idx];
        }
        var.scoring_coffs_valid = true;
    }

    const __int128_t future_value = _solution[var_idx]+change_value;
    size_t i=0;
    while (i<var.var_lit_terms.size()) {
        const uint64_t lit_idx = var.var_lit_terms[i].lit_idx;
        const lit &l = _lits[lit_idx];
        __int128_t delta = l.delta;
        do {
            const var_lit_term &vlt = var.var_lit_terms[i];
            const __int128_t coff = var.scoring_coffs[i];
            // Preserve the original arithmetic order, including higher powers.
            if (vlt.exponent==1) delta += coff*change_value;
            else delta += coff*(std::pow((long long)future_value, vlt.exponent)
                              -std::pow((long long)_solution[var_idx], vlt.exponent));
            ++i;
        } while (i<var.var_lit_terms.size() && var.var_lit_terms[i].lit_idx==lit_idx);
        const bool old_true = l.is_equal ? l.delta==0 : l.delta<=0;
        const bool new_true = l.is_equal ? delta==0 : delta<=0;
        if (old_true==new_true) continue;
        const int change = new_true ? 1 : -1;
        for (size_t p=_score_literal_offsets[lit_idx]; p<_score_literal_offsets[lit_idx+1]; ++p) {
            const int occurrence = _score_clause_occurrences[p];
            const int c = std::abs(occurrence)-1;
            int &count_delta = _score_clause_deltas[c];
            if (count_delta==INT32_MIN) {
                count_delta=0;
                _score_touched_clauses.push_back(c);
            }
            count_delta += occurrence>0 ? change : -change;
        }
    }
    int score=0;
    for (int c_idx : _score_touched_clauses) {
        const clause &c = _clauses[c_idx];
        const int new_count = c.sat_count+_score_clause_deltas[c_idx];
        if (c.sat_count>0 && new_count==0) score-=c.weight;
        else if (c.sat_count==0 && new_count>0) score+=c.weight;
        _score_clause_deltas[c_idx]=INT32_MIN;
    }
    _score_touched_clauses.clear();
#ifdef NIA_SCORING_VERIFY
    const int reference = critical_score_reference(var_idx, change_value);
    if (score!=reference) {
        std::cerr << "incremental score mismatch at step " << _step
                  << ", variable " << var_idx << ": " << score << " != " << reference << '\n';
        std::abort();
    }
#endif
    return score;
#endif
}

#if defined(NIA_SCORING_VERIFY) || defined(NIA_SCORING_REFERENCE)
// Original full clause scan, retained for differential validation.
//calculate score for nia vars
int ls_solver::critical_score_reference(uint64_t var_idx, __int128_t change_value){
    if (change_value==0 || _vars[var_idx].var_lit_terms.empty()) return 0;
    int critical_score=0;
    __int128_t future_value=_solution[var_idx]+change_value;
    variable * var=&(_vars[var_idx]);
    var_lit_term *vlt;
    uint64_t curr_lit_idx=var->var_lit_terms[0].lit_idx;
    __int128_t curr_lit_delta_new=_lits[curr_lit_idx].delta;
    __int128_t coff;//(coff of original term) * (term_value / _solution[var]) ( 2 * x1*x2 ) x1=3, x2=4  -> coff[x2]=2*3
    for(term_exp &te:var->term_exps){term_coffs[te.term_idx]=coff_in_term(var_idx, te.term_idx,te.exponent);}//determine the term coff
    //determine the lit_make_break by going through the vlt of var
    for(int vlt_idx=0;vlt_idx<var->var_lit_terms.size();vlt_idx++){
        vlt=&(var->var_lit_terms[vlt_idx]);
        if(curr_lit_idx!=vlt->lit_idx){
            curr_lit_idx=vlt->lit_idx;
            curr_lit_delta_new=_lits[curr_lit_idx].delta;
        }//enter a new lit
        coff=vlt->coff*term_coffs[vlt->term_idx];
        if(vlt->exponent==1){curr_lit_delta_new+=coff*change_value;}
        else{curr_lit_delta_new+=coff*(std::pow((long long)future_value,vlt->exponent)-std::pow((long long)_solution[var_idx],vlt->exponent));}
        if((vlt_idx==var->var_lit_terms.size()-1)||(curr_lit_idx!=var->var_lit_terms[vlt_idx+1].lit_idx)){
            if(_lits[curr_lit_idx].is_equal){
                if(_lits[curr_lit_idx].delta==0&&curr_lit_delta_new!=0){_lit_make_break[curr_lit_idx]=-1;}
                else if(_lits[curr_lit_idx].delta!=0&&curr_lit_delta_new==0){_lit_make_break[curr_lit_idx]=1;}
            }//equal lit
            else{
                if(_lits[curr_lit_idx].delta<=0&&curr_lit_delta_new>0){_lit_make_break[curr_lit_idx]=-1;}
                else if(_lits[curr_lit_idx].delta>0&&curr_lit_delta_new<=0){_lit_make_break[curr_lit_idx]=1;}
            }//nia <= lit
        }//the last vlt of the var or the last vlt of current lit
    }
    //determine the score by going through the clauses of var
    clause *c;
    for(int cls_idx:var->clause_idxs){
        c=&(_clauses[cls_idx]);
        int clause_sat_count_new=c->sat_count;
        for(int v_lit:c->literals){clause_sat_count_new+=(v_lit>0)?_lit_make_break[v_lit]:(-_lit_make_break[-v_lit]);}
        if(c->sat_count>0&&clause_sat_count_new==0){critical_score-=c->weight;}
        else if(c->sat_count==0&&clause_sat_count_new>0){critical_score+=c->weight;}
    }
    //recover the lit_make_break
    for(uint64_t l_idx:var->literal_idxs){_lit_make_break[l_idx]=0;}
    return critical_score;
}

#endif

//sat or unsat a clause, update the delta, dedicated for nia var
void ls_solver::move_update(uint64_t var_idx, __int128_t change_value){
    variable *var=&(_vars[var_idx]);
    // Invalidate even when the literal truth value or a zero product is unchanged.
    for (uint64_t literal:var->literal_idxs) {
        critical_operation_caches[2*literal].valid=false;
        critical_operation_caches[2*literal+1].valid=false;
    }
    var_lit_term *vlt;
    uint64_t curr_lit_idx=var->var_lit_terms[0].lit_idx;
    __int128_t curr_lit_delta_new=_lits[curr_lit_idx].delta;
    __int128_t term_coff,lit_coff;
    __int128_t future_value=_solution[var_idx]+change_value;
    //update term value
    for(term_exp &te:var->term_exps){
        term_coff=coff_in_term(var_idx, te.term_idx,te.exponent);
        term_coffs[te.term_idx]=term_coff;
        if(te.exponent==1){_terms[te.term_idx].value+=term_coff*change_value;}
        else{_terms[te.term_idx].value=term_coff*std::pow((long long)(future_value),te.exponent);}
        // Even when a product stays zero, another factor's coefficient can change.
        for (const var_exp &ve : _terms[te.term_idx].var_epxs)
            _vars[ve.var_index].scoring_coffs_valid=false;
    }
    //determine the lit_is_true by going through the vlt of var, and update the delta of lits
    for(int vlt_idx=0;vlt_idx<var->var_lit_terms.size();vlt_idx++){
        vlt=&(var->var_lit_terms[vlt_idx]);
        if(curr_lit_idx!=vlt->lit_idx){
            curr_lit_idx=vlt->lit_idx;
            curr_lit_delta_new=_lits[curr_lit_idx].delta;
        }//enter a new lit
        lit_coff=term_coffs[vlt->term_idx]*vlt->coff;
        if(vlt->exponent==1){curr_lit_delta_new+=lit_coff*change_value;}
        else{curr_lit_delta_new+=lit_coff*(std::pow((long long)future_value,vlt->exponent)-std::pow((long long)_solution[var_idx],vlt->exponent));}
        if((vlt_idx==var->var_lit_terms.size()-1)||(curr_lit_idx!=var->var_lit_terms[vlt_idx+1].lit_idx)){
            if(_lits[curr_lit_idx].is_equal){
                if(_lits[curr_lit_idx].delta==0&&curr_lit_delta_new!=0){_lits[curr_lit_idx].is_true=-1;}
                else if(_lits[curr_lit_idx].delta!=0&&curr_lit_delta_new==0){_lits[curr_lit_idx].is_true=1;}
            }//equal lit
            else{
                if(_lits[curr_lit_idx].delta<=0&&curr_lit_delta_new>0){_lits[curr_lit_idx].is_true=-1;}
                else if(_lits[curr_lit_idx].delta>0&&curr_lit_delta_new<=0){_lits[curr_lit_idx].is_true=1;}
            }//nia <= lit
            _lits[curr_lit_idx].delta=curr_lit_delta_new;//update lit delta
        }//the last vlt of the var or the last vlt of current lit
    }
    //unsat/sat a clause by going through the lits in it
    clause *c;
    lit *l;
    for(int cls_idx:var->clause_idxs){
        c=&(_clauses[cls_idx]);
        int clause_sat_count_old=c->sat_count;
        int watch_lit_idx_old=c->watch_lit_idx;
        c->sat_count=0;//reset the sat count
        for(int l_idx:c->literals){
            if((l_idx^_lits[std::abs(l_idx)].is_true)>=0){c->sat_count++;c->watch_lit_idx=l_idx;}
        }
        //sat or unsat a clause, update the lit_in_unsat_clause
        if(c->sat_count>0&&clause_sat_count_old==0){
            sat_a_clause(cls_idx);
            _lit_in_unsat_clause_num-=c->literals.size();
            _bool_lit_in_unsat_clause_num-=c->bool_literals.size();
        }
        else if(c->sat_count==0&&clause_sat_count_old>0){
            unsat_a_clause(cls_idx);
            _lit_in_unsat_clause_num+=c->literals.size();
            _bool_lit_in_unsat_clause_num+=c->bool_literals.size();
        }
        //update the sat_clause_with_false_lit
        if(c->sat_count>0&&c->sat_count<c->literals.size()){sat_clause_with_false_literal->insert_element((int)cls_idx);}
        else{sat_clause_with_false_literal->delete_element((int)cls_idx);}
        //update the score of boolean vars
        if(clause_sat_count_old==0&&c->sat_count>0){
            for(int l_sign_idx:c->bool_literals){
                l=&(_lits[std::abs(l_sign_idx)]);
                _vars[l->delta].score-=c->weight;
            }
        }
        else if(clause_sat_count_old==1&&c->sat_count>1){
            l=&(_lits[std::abs(watch_lit_idx_old)]);
            if(!l->is_nia_lit){_vars[l->delta].score+=c->weight;}
        }
        else if(clause_sat_count_old>0&&c->sat_count==0){
            for(int l_sign_idx:c->bool_literals){
                l=&(_lits[std::abs(l_sign_idx)]);
                _vars[l->delta].score+=c->weight;
            }
        }
        else if(clause_sat_count_old>1&&c->sat_count==1){
            l=&(_lits[std::abs(c->watch_lit_idx)]);
            if(!l->is_nia_lit){_vars[l->delta].score-=c->weight;}
        }
    }
}

//dedicated for boolean var
void ls_solver::move_update(uint64_t var_idx){
    variable *var=&(_vars[var_idx]);
    int v_lit_idx=(int)var->literal_idxs[0];//the lit_idx containing the var
    _lits[v_lit_idx].is_true*=-1;
    int make_break_in_clause=0;
    int cl_sign_idx;
    for(int i=0;i<var->clause_idxs.size();i++){
        cl_sign_idx=var->clause_idxs[i];
        if((_solution[var_idx]>0&&var->bool_var_in_pos_clause[i])||(_solution[var_idx]<0&&!var->bool_var_in_pos_clause[i])){make_break_in_clause=-1;}//true to false
        else{make_break_in_clause=1;}//false to true
        int clause_idx=std::abs(cl_sign_idx);
        clause *cp=&(_clauses[clause_idx]);
        //sat or unsat a clause
        if(cp->sat_count>0&&cp->sat_count+make_break_in_clause==0) {
            unsat_a_clause(clause_idx);
            _lit_in_unsat_clause_num+=cp->literals.size();
            _bool_lit_in_unsat_clause_num+=cp->bool_literals.size();
        }
        else if(cp->sat_count==0&&cp->sat_count+make_break_in_clause>0) {
            sat_a_clause(clause_idx);
            _lit_in_unsat_clause_num-=cp->literals.size();
            _bool_lit_in_unsat_clause_num-=cp->bool_literals.size();
        }
        int origin_watch_lit=cp->watch_lit_idx;
        int origin_sat_count=cp->sat_count;
        cp->sat_count+=make_break_in_clause;
        //update sat_clause_with_false_lit
        if(cp->sat_count>0&&cp->sat_count<cp->literals.size()){sat_clause_with_false_literal->insert_element(clause_idx);}
        else{sat_clause_with_false_literal->delete_element(clause_idx);}
        //update the watch lit
        if(std::abs(origin_watch_lit)==v_lit_idx&&cp->sat_count>0){//if the original watch lit exactly contains the var and the sat_count>0, set the new watch lit
            for(int l_idx:cp->literals){
                if((l_idx^_lits[std::abs(l_idx)].is_true)>=0){cp->watch_lit_idx=l_idx;break;}
            }
        }
        else if(origin_sat_count==0){cp->watch_lit_idx=(cl_sign_idx>0)?(v_lit_idx):(-v_lit_idx);}//if origin sat count==0, then set new watch lit
        //update the score of boolean vars
        if(make_break_in_clause>0){
            if(origin_sat_count==0){
                for(int bl:cp->bool_literals){
                    lit *l=&(_lits[std::abs(bl)]);
                    _vars[l->delta].score-=cp->weight;
                }
            }
            else if(origin_sat_count==1){
                lit *l=&(_lits[std::abs(origin_watch_lit)]);
                if(!l->is_nia_lit){_vars[l->delta].score+=cp->weight;}
            }
        }
        else if(make_break_in_clause<0){
            if(cp->sat_count==0){
                for(int bl:cp->bool_literals){
                    lit *l=&(_lits[std::abs(bl)]);
                    _vars[l->delta].score+=cp->weight;
                }
            }
            else if(cp->sat_count==1){
                lit *l=&(_lits[std::abs(cp->watch_lit_idx)]);
                if(!l->is_nia_lit){_vars[l->delta].score-=cp->weight;}
            }
        }
    }
}

//check
bool ls_solver::check_solution(){
    clause *cp;
    int unsat_num=0;
    for(int term_idx=0;term_idx<_terms.size();term_idx++){
        if(!term_appear[term_idx]){continue;}
        term *t=&(_terms[term_idx]);
        __int128_t new_term_value=1;
        for(var_exp &ve:t->var_epxs){
            if(ve.exponent==1){new_term_value*=_solution[ve.var_index];}
            else{new_term_value*=std::pow((long long)_solution[ve.var_index],ve.exponent);}
            if(new_term_value==0){break;}
        }
        if(new_term_value!=_terms[term_idx].value){std::cout<<"term value wrong: "<<term_idx<<"\n";}
    }//check term value
    for(int lit_idx=0;lit_idx<_lits.size();lit_idx++){
        if(_lits[lit_idx].lits_index==0||!_lits[lit_idx].is_nia_lit){continue;}
        __int128_t delta=delta_lit(_lits[lit_idx]);
        if(delta!=_lits[lit_idx].delta){std::cout<<"lit delta wrong: "<<lit_idx<<"\n";}
    }//check lit delta
    for(uint64_t i=0;i<_num_clauses;i++){
        int sat_count=0;
        cp=&(_clauses[i]);
        for(int lit_idx: cp->literals){
            __int128_t delta=_lits[std::abs(lit_idx)].delta;
            bool is_equal=_lits[std::abs(lit_idx)].is_equal;
            if(!_lits[std::abs(lit_idx)].is_nia_lit){
                __int128_t var_idx=_lits[std::abs(lit_idx)].delta;
                if((_solution[var_idx]>0&&lit_idx>0)||(_solution[var_idx]<0&&lit_idx<0)){sat_count++;}
            }
            else if( (!is_equal&&((delta<=0&&lit_idx>0)||(delta>0&&lit_idx<0))) || (is_equal&&((delta==0&&lit_idx>0)||(delta!=0&&lit_idx<0))) ){sat_count++;}
        }
        if(sat_count!=cp->sat_count){std::cout<<"sat count wrong at clause "<<i<<"\n";}
        if(sat_count==0){unsat_num++;}
    }
    for(int var_idx=0;var_idx<_vars.size();var_idx++){if(_solution[var_idx]>_vars[var_idx].upper_bound||_solution[var_idx]<_vars[var_idx].low_bound){std::cout<<"var "<<var_idx<<" out of range\n";}}
    if(unsat_num==unsat_clauses->size())
        std::cout<<"right solution\n";
    else
        std::cout<<"wrong solution\n";
    return unsat_num==unsat_clauses->size();
}

bool ls_solver::update_inner_best_solution(){
    if(unsat_clauses->size()<_best_found_hard_cost_this_nia){
        _best_found_hard_cost_this_nia=unsat_clauses->size();
        return true;
    }
    return false;
}

bool ls_solver::update_outer_best_solution(){
    if(unsat_clauses->size()<_best_found_hard_cost_this_bool){
        _best_found_hard_cost_this_bool=unsat_clauses->size();
        return true;
    }
    return false;
}

void ls_solver::enter_nia_mode(){
    _best_found_hard_cost_this_nia=unsat_clauses->size();
    if(is_in_bool_search){no_improve_cnt_nia=0;}//only when it is now in boolean mode, the no_improve_cnt_nia will be 0
    is_in_bool_search=false;
}

void ls_solver::enter_bool_mode(){
    _best_found_hard_cost_this_bool=unsat_clauses->size();
    if(!is_in_bool_search){no_improve_cnt_bool=0;}//only when it is now in integer mode, the no_improve_cnt_bool will be 0
    is_in_bool_search=true;
}

//local search
bool ls_solver::local_search(){
    _step=0;
    if(build_unsat){return false;}
    int no_improve_cnt=0;
    int flipv;
    __int128_t change_value=0;
    start = std::chrono::steady_clock::now();
    initialize();
    random_walk_stall_steps=0;
    random_walk_window_keeps=0;
    _outer_layer_step=1;
    for(_step=1;_step<_max_step;_step++){
        if(0==unsat_clauses->size()){
#ifdef NLS_DEBUG
            std::cout<<"step:"<<_step<<"\n";
#endif
            // check_solution();
            up_bool_vars();
            return true;}
        if(_step%1000==0&&(TimeElapsed()>_cutoff)){break;}
        if(uint64_t(no_improve_cnt)>restart_stall_limit){
            if(restart_repair_enabled){
                prepare_restart_repair();
                if(TimeElapsed()>=_cutoff)break;
            }
            initialize();
            no_improve_cnt=0;
            // A diversified initial assignment can itself satisfy the formula.
            if(unsat_clauses->empty())continue;
        }//restart
        random_walk_stall_steps=no_improve_cnt;
        if(no_improve_cnt==0){random_walk_window_keeps=0;}
        bool time_up_bool=(no_improve_cnt_bool*_lit_in_unsat_clause_num>5*_bool_lit_in_unsat_clause_num);
        bool time_up_nia=(no_improve_cnt_nia*_lit_in_unsat_clause_num>20*(_lit_in_unsat_clause_num-_bool_lit_in_unsat_clause_num));
        if((is_in_bool_search&&_bool_lit_in_unsat_clause_num<_lit_in_unsat_clause_num&&time_up_bool)||_bool_lit_in_unsat_clause_num==0){enter_nia_mode();}
        else if((!is_in_bool_search&&_bool_lit_in_unsat_clause_num>0&&time_up_nia)||(_lit_in_unsat_clause_num==_bool_lit_in_unsat_clause_num)){enter_bool_mode();}
        if(is_in_bool_search){
            flipv=pick_critical_move_bool();
            if(flipv!=-1){critical_move(flipv, change_value);}
            if(update_outer_best_solution()) no_improve_cnt_bool=0;
            else                              no_improve_cnt_bool++;
        }
        else{
            flipv=pick_critical_move(change_value);
            if(flipv!=-1){critical_move(flipv, change_value);}
            if(update_inner_best_solution()) no_improve_cnt_nia=0;
            else                               no_improve_cnt_nia++;
        }
        no_improve_cnt=(update_best_solution())?0:(no_improve_cnt+1);
    }
    return false;
}


}
