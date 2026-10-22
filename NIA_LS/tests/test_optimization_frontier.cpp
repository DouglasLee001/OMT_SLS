// Exercise objective-neighborhood generation on the actual exact State.
#include "../src/nia_omt_optimize.cpp"
#include <cstdlib>

namespace {
void check(bool ok,const char* message){if(!ok){std::cerr<<"FAIL: "<<message<<'\n';std::exit(1);}}
std::string fixture(bool maximize,int lower_w=1,int lower_h=1,int upper=8){
    return std::string("NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 77\nDIRECTION ")+(maximize?"max":"min")+
        "\nVARIABLES 2\n0 I 77\n1 I 68\nLITERALS 4\n1 L "+std::to_string(lower_w)+" 1\n-1 1 0 1\n2 L -"+std::to_string(upper)+" 1\n1 1 0 1\n3 L "+std::to_string(lower_h)+" 1\n-1 1 1 1\n4 L -"+std::to_string(upper)+" 1\n1 1 1 1\nCLAUSES 4\n1 1\n1 2\n1 3\n1 4\nOBJECTIVE 0 1\n"+(maximize?"-1":"1")+" 2 0 1 1 1\nEND\n";
}
void run(bool maximize){
    nia::ls_solver solver(0,UINT64_MAX,true);std::string error;
    std::istringstream input(fixture(maximize));check(solver.load_omt_instance(input,error),"frontier native fixture loads");
    nia::State state(solver);state.reset({3,7});
    const auto assignment=state.x;const auto terms=state.terms;const auto deltas=state.delta;
    const auto truth=state.truth;const auto sat=state.sat;const auto objective=state.objective;
    const std::function<bool()> available=[](){return true;};
    auto probes=state.frontier_probes(1,available,24);
    bool joint=false;
    for(const auto& move:probes){
        auto e=state.evaluate(move);
        check(e.cost!=UINT64_MAX&&state.better(e.objective,objective),"every frontier probe strictly improves the actual own objective");
        auto values=assignment;for(auto p:e.move)values[p.first]=p.second;
        check(values[0]>=1&&values[0]<=8&&values[1]>=1&&values[1]<=8,"frontier respects true bounds");
        joint|=values[0]==5&&values[1]==4&&e.objective==(maximize?-20:20);
    }
    check(joint,"frontier must include the joint unequal-delta (3,7)->(5,4) improvement");
    check(probes.size()<=24,"frontier respects requested candidate cap");
    check(state.x==assignment&&state.terms==terms&&state.delta==deltas&&state.truth==truth&&state.sat==sat&&state.objective==objective,"frontier generation leaves assignment and caches unchanged");
    check(state.frontier_probes(1,available,0).empty(),"zero candidate cap does no work");
    const std::function<bool()> expired=[](){return false;};
    check(state.frontier_probes(1,expired,24).empty(),"expired budget returns no candidate");
}
void bound_frontier(){
    nia::ls_solver solver(0,UINT64_MAX,true);std::string error;
    std::istringstream input(fixture(false,8,7,20));
    check(solver.load_omt_instance(input,error),"endpoint frontier fixture loads");
    nia::State state(solver);state.reset({8,12});
    bool found=false;
    for(const auto& move:state.frontier_probes(1,[](){return true;},24)){
        auto e=state.evaluate(move,false);auto values=state.x;
        for(auto p:e.move)values[p.first]=p.second;
        found|=values[0]==13&&values[1]==7&&e.objective==91;
    }
    // Two rectangles 8x7 and 5x5 require a joint five-unit
    // change from a vertical8x12 to a horizontal13x7 arrangement.
    check(found,"own-objective frontier includes true-bound endpoint beyond the local radius");
}
std::string packing_fixture(){
    std::vector<std::string> literals;std::vector<std::vector<int>> clauses;
    auto linear=[&](int constant,std::initializer_list<std::pair<size_t,int>> terms){
        std::ostringstream text;text<<"L "<<constant<<' '<<terms.size()<<'\n';
        for(auto t:terms)text<<t.second<<" 1 "<<t.first<<" 1\n";
        literals.push_back(text.str());return int(literals.size());
    };
    auto unit=[&](int id){clauses.push_back({id});};
    unit(linear(8,{{0,-1}}));unit(linear(-14,{{0,1}}));
    unit(linear(7,{{1,-1}}));unit(linear(-13,{{1,1}}));
    const int width[]={8,5,1},height[]={7,5,1};
    for(size_t object=0;object<3;++object){
        const size_t x=2+2*object,y=x+1;
        unit(linear(0,{{x,-1}}));unit(linear(0,{{y,-1}}));
        unit(linear(width[object],{{x,1},{0,-1}}));
        unit(linear(height[object],{{y,1},{1,-1}}));
    }
    for(size_t a=0;a<3;++a)for(size_t b=a+1;b<3;++b){
        const size_t xa=2+2*a,ya=xa+1,xb=2+2*b,yb=xb+1;
        clauses.push_back({linear(width[a],{{xa,1},{xb,-1}}),linear(width[b],{{xb,1},{xa,-1}}),
                           linear(height[a],{{ya,1},{yb,-1}}),linear(height[b],{{yb,1},{ya,-1}})});
    }
    literals.push_back("L -98 1\n1 2 0 1 1 1\n");unit(int(literals.size()));
    std::ostringstream text;
    text<<"NIA_LS_OMT 1\nSTATUS ready\nSOURCE -\nORIGINAL_OBJECTIVE 30\nDIRECTION min\nVARIABLES 8\n";
    for(size_t v=0;v<8;++v)text<<v<<" I "<<std::hex<<(0x61+v)<<std::dec<<'\n';
    text<<"LITERALS "<<literals.size()<<'\n';
    for(size_t l=0;l<literals.size();++l)text<<l+1<<' '<<literals[l];
    text<<"CLAUSES "<<clauses.size()<<'\n';
    for(const auto& clause:clauses){text<<clause.size();for(int id:clause)text<<' '<<id;text<<'\n';}
    text<<"OBJECTIVE 0 1\n1 2 0 1 1 1\nEND\n";return text.str();
}
void repack_frontier(){
    nia::ls_solver solver(0,UINT64_MAX,true);std::string error;
    std::istringstream input(packing_fixture());
    check(solver.load_omt_instance(input,error),"packing frontier fixture loads");
    nia::State state(solver);state.reset({8,12,0,0,0,7,5,7});
    check(state.unsat.empty()&&nia::validate(solver,state.x)==96,"old packing is fully feasible");
    const auto original=state.x;bool found=false;
    const std::function<bool()> budget=[](){return true;};
    for(const auto& probe:state.frontier_probes(1,budget,24)){
        auto values=state.x;for(auto p:probe)values[p.first]=p.second;
        if(values[0]!=13||values[1]!=7)continue;
        const auto old=nia::difference_branch_move(solver,state.x,state.lo,state.hi,probe,{},budget,0);
        check(old.empty()||state.evaluate(old,false).violations,"old true branches cannot perform this repacking");
        auto move=nia::disjunctive_difference_move(solver,state.x,state.lo,state.hi,probe,{},budget,256);
        if(move.empty())continue;
        auto candidate=state.evaluate(move,false);
        if(candidate.violations)continue;
        values=state.x;for(auto p:candidate.move)values[p.first]=p.second;
        check(candidate.objective==91&&nia::validate(solver,values)==91,"new branch topology improves actual feasible area");
        found=true;break;
    }
    check(found,"frontier and alternative branches jointly repack three objects across a five-unit shape change");
    check(state.x==original&&nia::validate(solver,state.x)==96,"repacking preview preserves original assignment and caches");
}
}
int main(){run(false);run(true);bound_frontier();repack_frontier();std::cout<<"objective frontier checks passed\n";}
