#include "nia_omt_objective_slice.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <limits>
#include <linux/fs.h>
#include <set>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <vector>

namespace nia {
namespace {
using Clock=std::chrono::steady_clock;
constexpr uint64_t max_components=65536,max_ast_work=262144,max_traversal=1048576;
constexpr uint64_t max_seed_ast=8192,max_refinements=256,batch_size=8,max_decimal_chars=65536;
struct Expired {};
struct Unsupported:std::runtime_error {using std::runtime_error::runtime_error;};
void require(bool condition,const char* reason){if(!condition)throw Unsupported(reason);}
std::string errno_detail(int error){return "errno="+std::to_string(error)+" ("+std::strerror(error)+")";}
struct Budget {
    Clock::time_point began=Clock::now();double seconds;
    explicit Budget(double allowance):seconds(allowance){}
    double elapsed()const{return std::chrono::duration<double>(Clock::now()-began).count();}
    double remaining()const{return seconds-elapsed();}
    void check()const{if(!(remaining()>0))throw Expired{};}
};
void visit(const Budget& budget,OmtObjectiveSliceResult& result){
    budget.check();require(++result.traversal_nodes<=max_traversal,"source traversal guard exceeded");
}
using Constants=std::set<unsigned>;
struct AstSummary {uint64_t nodes=0;Constants constants;};
AstSummary summarize(const z3::expr& expression,const Budget& budget,OmtObjectiveSliceResult& result){
    AstSummary summary;std::set<unsigned> visited;std::vector<z3::expr> pending{expression};
    while(!pending.empty()){
        visit(budget,result);auto current=pending.back();pending.pop_back();
        if(!visited.insert(current.id()).second)continue;
        require(++summary.nodes<=max_ast_work,"source component AST guard exceeded");
        require(!current.is_quantifier(),"quantified source is unsupported");
        if(current.is_app()){
            if(current.num_args()==0&&current.decl().decl_kind()==Z3_OP_UNINTERPRETED)summary.constants.insert(current.id());
            for(unsigned i=current.num_args();i>0;--i){
                budget.check();
                require(pending.size()<max_traversal,"source traversal stack guard exceeded");pending.push_back(current.arg(i-1));
            }
        }
    }
    return summary;
}
bool intersects(const Constants& left,const Constants& right,const Budget& budget){
    auto a=left.begin(),b=right.begin();
    while(a!=left.end()&&b!=right.end()){
        budget.check();if(*a==*b)return true;if(*a<*b)++a;else ++b;
    }return false;
}
struct Component {
    z3::expr expression;AstSummary ast;bool active=false;
    Component(z3::expr value,AstSummary summary):expression(std::move(value)),ast(std::move(summary)){}
};
std::vector<z3::expr> flatten(const z3::expr_vector& hard,const Budget& budget,OmtObjectiveSliceResult& result){
    std::vector<z3::expr> components;
    for(const auto& constraint:hard){
        budget.check();require(constraint.is_bool(),"original hard assertion must be Boolean");
        std::vector<z3::expr> pending{constraint};
        while(!pending.empty()){
            visit(budget,result);auto current=pending.back();pending.pop_back();
            if(current.is_and()){
                for(unsigned i=current.num_args();i>0;--i){
                    budget.check();
                    require(pending.size()<max_traversal,"source flattening stack guard exceeded");pending.push_back(current.arg(i-1));
                }
            }else {
                require(components.size()<max_components,"flattened source component guard exceeded");components.push_back(current);
            }
        }
    }
    result.total_assertions=components.size();return components;
}
std::string decimal(const std::string& text,const Budget& budget){
    require(!text.empty()&&text.size()<=max_decimal_chars,"expected a bounded exact decimal incumbent");
    size_t at=text[0]=='-'||text[0]=='+'?1:0;
    require(at<text.size(),"expected an exact decimal incumbent");
    for(;at<text.size();++at){budget.check();require(text[at]>='0'&&text[at]<='9',"expected an exact decimal incumbent");}
    return text[0]=='+'?text.substr(1):text;
}
std::string quoted(const std::string& value,const Budget& budget){
    std::string out;out.reserve(value.size()+2);out+='"';
    constexpr char hex[]="0123456789abcdef";
    for(unsigned char ch:value){
        budget.check();
        if(ch=='"'||ch=='\\'){out+='\\';out+=char(ch);}
        else if(ch<32){out+="\\u00";out+=hex[ch>>4];out+=hex[ch&15];}
        else out+=char(ch);
    }out+='"';return out;
}
std::string certificate(const z3::expr& objective,bool minimize,const std::string& best,
    const std::vector<Component>& components,const std::vector<size_t>& selected,z3::solver& query,const Budget& budget){
    budget.check();std::ostringstream out;
    out<<"{\"version\":\"omt-objective-slice-v1\",\"original_objective\":"<<quoted(objective.to_string(),budget)
       <<",\"direction\":\""<<(minimize?"min":"max")<<"\",\"original_best\":"<<quoted(best,budget)
       <<",\"flattened_source_count\":"<<components.size()<<",\"selected_indices\":[";
    for(size_t i=0;i<selected.size();++i){budget.check();if(i)out<<',';out<<selected[i];}
    out<<"],\"selected_assertions\":[";
    for(size_t i=0;i<selected.size();++i){budget.check();if(i)out<<',';out<<quoted(components[selected[i]].expression.to_string(),budget);}
    out<<"],\"query_smt2\":"<<quoted(query.to_smt2(),budget)<<"}\n";
    budget.check();return out.str();
}
// Own only the temporary file and the inode published by this invocation.
// In particular, never truncate, replace or clean up a pre-existing caller file.
struct AtomicCertificate {
    std::string temporary,output;int fd=-1;bool published=false,keep=false;
    struct stat owned{};
    ~AtomicCertificate(){
        if(fd>=0)::close(fd);
        if(!temporary.empty())::unlink(temporary.c_str());
        if(published&&!keep){struct stat current{};
            if(::lstat(output.c_str(),&current)==0&&current.st_dev==owned.st_dev&&current.st_ino==owned.st_ino)::unlink(output.c_str());}
    }
    void save(const std::string& path,const std::string& contents,const Budget& budget){
        budget.check();if(path.empty())return;
        require(path.find('\0')==std::string::npos,"certificate output contains a null byte");
        struct stat existing{};
        if(::lstat(path.c_str(),&existing)==0)throw Unsupported("certificate destination already exists: "+errno_detail(EEXIST));
        const int inspection_error=errno;
        if(inspection_error!=ENOENT)throw Unsupported("cannot inspect certificate destination: "+errno_detail(inspection_error));
        output=path;std::string pattern=path+".tmp.XXXXXX";std::vector<char> buffer(pattern.begin(),pattern.end());buffer.push_back('\0');
        fd=::mkstemp(buffer.data());
        if(fd<0)throw Unsupported("cannot create temporary certificate output: "+errno_detail(errno));
        temporary=buffer.data();
        if(::fstat(fd,&owned)!=0)throw Unsupported("cannot inspect temporary certificate output: "+errno_detail(errno));
        size_t done=0;
        while(done<contents.size()){
            budget.check();const auto written=::write(fd,contents.data()+done,std::min<size_t>(contents.size()-done,65536));
            if(written<0&&errno==EINTR)continue;
            if(written<=0)throw Unsupported("cannot write temporary certificate output: "+errno_detail(written<0?errno:EIO));
            done+=size_t(written);
        }
        const int close_result=::close(fd);const int close_error=errno;fd=-1;
        if(close_result!=0)throw Unsupported("cannot close temporary certificate output: "+errno_detail(close_error));
        budget.check();
        int rename_error=ENOSYS;
#ifdef SYS_renameat2
        if(::syscall(SYS_renameat2,AT_FDCWD,temporary.c_str(),AT_FDCWD,output.c_str(),RENAME_NOREPLACE)==0){
            published=true;temporary.clear();budget.check();return;
        }
        rename_error=errno;
#endif
        if(rename_error!=EINVAL&&rename_error!=ENOSYS&&rename_error!=EOPNOTSUPP)
            throw Unsupported("cannot atomically publish certificate by no-clobber rename: "+errno_detail(rename_error));
        std::fprintf(stderr,"objective-slice phase=publication method=link rename_%s\n",errno_detail(rename_error).c_str());
        // Both names are in the destination directory. link atomically creates
        // the output only if absent, including when a competing symlink exists.
        budget.check();
        if(::link(temporary.c_str(),output.c_str())!=0){
            const int link_error=errno;
            throw Unsupported("cannot atomically publish certificate by no-clobber link: "+errno_detail(link_error)+"; rename_"+errno_detail(rename_error));
        }
        published=true;budget.check();
        if(::unlink(temporary.c_str())!=0){
            const int unlink_error=errno;
            throw Unsupported("cannot remove linked temporary certificate: "+errno_detail(unlink_error)+"; rename_"+errno_detail(rename_error));
        }
        temporary.clear();budget.check();
    }
};
void trace(const char* phase,const OmtObjectiveSliceResult& result,const char* detail=""){
    std::fprintf(stderr,"objective-slice phase=%s queries=%llu refinements=%llu initial=%llu selected=%llu total=%llu %s\n",phase,
        (unsigned long long)result.queries,(unsigned long long)result.refinements,(unsigned long long)result.initial_assertions,
        (unsigned long long)result.selected_assertions,(unsigned long long)result.total_assertions,detail);
}
}
OmtObjectiveSliceResult solve_omt_objective_slice(z3::context& context,const z3::expr_vector& original_hard,
    const z3::expr& original_objective,bool minimize,const std::string& original_best,double seconds,const std::string& certificate_output){
    OmtObjectiveSliceResult result;Budget budget(seconds);AtomicCertificate publication;trace("entry",result);
    try {
        require(std::isfinite(seconds)&&seconds>0,"objective slice requires a finite positive time allowance");budget.check();
        require(&original_hard.ctx()==&context&&&original_objective.ctx()==&context,"source inputs belong to a different context");
        require(original_objective.is_int(),"original objective must have integer sort");
        const auto exact_best=decimal(original_best,budget);
        const auto objective=summarize(original_objective,budget,result);result.objective_ast_nodes=objective.nodes;
        auto flattened=flatten(original_hard,budget,result);std::vector<Component> components;components.reserve(flattened.size());
        std::vector<size_t> selected;Constants active_variables;
        for(const auto& expression:flattened){
            budget.check();auto ast=summarize(expression,budget,result);
            require(ast.nodes<=max_ast_work-result.total_ast_nodes,"total source AST work guard exceeded");result.total_ast_nodes+=ast.nodes;
            const bool seed=intersects(ast.constants,objective.constants,budget);
            components.emplace_back(expression,std::move(ast));
            if(seed){auto& current=components.back();current.active=true;selected.push_back(components.size()-1);
                result.initial_ast_nodes+=current.ast.nodes;active_variables.insert(current.ast.constants.begin(),current.ast.constants.end());}
        }
        result.initial_assertions=selected.size();result.selected_assertions=selected.size();result.selected_ast_nodes=result.initial_ast_nodes;
        require(result.initial_ast_nodes<=max_seed_ast,"initial source AST work exceeds 8192-node gate");
        require(result.initial_ast_nodes<=result.total_ast_nodes/2,"initial source AST work exceeds one-half full work gate");
        result.applicable=true;budget.check();
        const auto best=context.int_val(exact_best.c_str());
        const auto strict=minimize?original_objective<best:original_objective>best;
        z3::solver query(context);query.add(strict);
        for(auto index:selected){budget.check();query.add(components[index].expression);}
        while(true){
            budget.check();const double milliseconds=std::floor(budget.remaining()*1000);
            if(milliseconds<1)throw Expired{};
            query.set("timeout",unsigned(std::min(milliseconds,double(std::numeric_limits<unsigned>::max()))));
            trace("check",result,"begin");budget.check();++result.queries;const auto answer=query.check();budget.check();
            trace("check",result,answer==z3::sat?"sat":(answer==z3::unsat?"unsat":"unknown"));
            if(answer==z3::unknown){result.reason="subset solver returned unknown: "+query.reason_unknown();break;}
            if(answer==z3::unsat){
                auto proof=certificate(original_objective,minimize,original_best,components,selected,query,budget);
                publication.save(certificate_output,proof,budget);budget.check();
                result.certificate_json=std::move(proof);result.answer="unsat";result.reason="original source subset and strict bound are UNSAT";break;
            }
            auto model=std::make_unique<z3::model>(query.get_model());bool all_true=true;
            for(const auto& assertion:original_hard){
                budget.check();const auto value=model->eval(assertion,true);
                require(value.is_true()||value.is_false(),"original hard assertion is unevaluable in subset model");
                if(value.is_false())all_true=false;
            }
            budget.check();require(model->eval(strict,true).is_true(),"subset model does not satisfy the original strict bound");
            if(all_true){
                budget.check();const auto value=model->eval(original_objective,true);
                require(value.is_int()&&value.is_numeral(),"original objective is not an exact integer in the source model");
                budget.check();result.source_model=std::move(model);result.answer="sat";result.reason="complete original source and strict bound are true";break;
            }
            std::vector<size_t> overlapping,other;
            for(size_t i=0;i<components.size();++i){
                budget.check();auto& component=components[i];const auto value=model->eval(component.expression,true);
                require(value.is_true()||value.is_false(),"flattened original assertion is unevaluable in subset model");
                if(!value.is_false())continue;
                require(!component.active,"already active original assertion is false in subset model");
                auto& candidates=intersects(component.ast.constants,active_variables,budget)?overlapping:other;
                if(candidates.size()<batch_size)candidates.push_back(i);
            }
            require(!overlapping.empty()||!other.empty(),"false source model has no violated inactive component");
            require(result.refinements<max_refinements,"objective slice reached its 256-refinement guard");
            std::vector<size_t> additions=overlapping;
            for(auto index:other){if(additions.size()>=batch_size)break;additions.push_back(index);}
            for(auto index:additions){
                budget.check();auto& component=components[index];query.add(component.expression);component.active=true;selected.push_back(index);
                active_variables.insert(component.ast.constants.begin(),component.ast.constants.end());result.selected_ast_nodes+=component.ast.nodes;
            }
            ++result.refinements;result.selected_assertions=selected.size();trace("refinement",result,"added actually false source components");
        }
    }catch(const Expired&){result.answer="unknown";result.reason="objective slice exhausted its time allowance";}
    catch(const Unsupported& error){result.answer="unknown";result.reason=error.what();}
    catch(const z3::exception& error){result.answer="unknown";result.reason=error.msg();}
    catch(const std::exception& error){result.answer="unknown";result.reason=error.what();}
    result.seconds=budget.elapsed();
    if(std::isfinite(seconds)&&result.seconds>=seconds){result.answer="unknown";result.reason="objective slice completed after its time allowance";}
    if(result.answer=="unknown"){result.source_model.reset();result.certificate_json.clear();}
    else if(result.answer=="unsat")publication.keep=true;
    trace("return",result,(result.answer+": "+result.reason).c_str());return result;
}
}
