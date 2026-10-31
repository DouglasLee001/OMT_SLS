#include "nia_frontend.h"
#include "nia_import.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

namespace nia { namespace embedded {
namespace {
namespace fs = std::filesystem;
struct Options {
    std::string source,native,export_path,mode;
    std::vector<std::string> legacy;
    ImportOptions import;
    bool import_only=false,first_solution=false,optimize=false;
    bool import_options_given=false,help=false,version=false;
    double timeout=1200;
};
bool prefix(const std::string& text,const std::string& start){return text.rfind(start,0)==0;}
std::string read_file(const fs::path& path) {
    std::ifstream input(path,std::ios::binary);
    if(!input)throw std::runtime_error("cannot read input: "+path.string());
    std::string text{std::istreambuf_iterator<char>(input),{}};
    if(input.bad())throw std::runtime_error("cannot finish reading input: "+path.string());
    return text;
}
fs::path input_path(const std::string& path) {
    const auto absolute=fs::canonical(fs::path(path));
    if(!fs::is_regular_file(absolute))throw std::runtime_error("input is not a regular file: "+absolute.string());
    return absolute;
}
// Read only the fixed native header: preflight must protect its original
// SMT2 source before export or the legacy model/query writers can run. Relative
// SOURCE paths retain the legacy reader's current-working-directory meaning.
std::string native_source(const fs::path& path) {
    std::ifstream input(path,std::ios::binary);
    std::string magic,version,status_key,status,source_key,encoded;
    if(!(input>>magic))throw std::runtime_error("cannot read native input header");
    if(magic!="NIA_LS_OMT")return {}; // Preserve legacy non-OMT native inputs.
    if(!(input>>version>>status_key>>status>>source_key>>encoded)||version!="1"||
       status_key!="STATUS"||(status!="ready"&&status!="unsat")||source_key!="SOURCE")
        throw std::runtime_error("invalid native input header");
    if(encoded=="-")return {};
    if(encoded.size()%2)throw std::runtime_error("invalid native input SOURCE encoding");
    auto digit=[](char value)->int {
        if(value>='0'&&value<='9')return value-'0';
        if(value>='a'&&value<='f')return value-'a'+10;
        if(value>='A'&&value<='F')return value-'A'+10;
        return -1;
    };
    std::string result;result.reserve(encoded.size()/2);
    for(size_t i=0;i<encoded.size();i+=2) {
        const int high=digit(encoded[i]),low=digit(encoded[i+1]);
        if(high<0||low<0||!(high||low))throw std::runtime_error("invalid native input SOURCE encoding");
        result.push_back(char(high*16+low));
    }
    return result;
}
void preflight_outputs(const Options& options,const std::vector<fs::path>& protected_inputs,
                       const std::string& runtime_input={}) {
    std::vector<std::pair<std::string,fs::path>> outputs;
    auto add=[&](const char* flag,const std::string& path){if(!path.empty())outputs.emplace_back(flag,path);};
    add("--export-native",options.export_path);
    std::string model,query;
    bool prove_optimal=false,initial_probe=false,convex_exchange=false;
    for(const auto& option:options.legacy) {
        if(prefix(option,"--model-output="))model=option.substr(std::string("--model-output=").size());
        else if(prefix(option,"--optimality-query-output="))query=option.substr(std::string("--optimality-query-output=").size());
        else if(option=="--prove-optimal")prove_optimal=true;
        else if(option=="--convex-exchange")convex_exchange=true;
        else if(prefix(option,"--initial-z3-after="))initial_probe=std::stod(option.substr(std::string("--initial-z3-after=").size()))>0;
    }
    if(!model.empty())for(const char* suffix:{"",".tmp",".prev",".prev.tmp"})add("--model-output",model+suffix);
    if(query.empty()&&prove_optimal&&!runtime_input.empty())query=runtime_input+".optimality.smt2";
    add("--optimality-query-output",query);
    if(convex_exchange&&!query.empty())add("--convex-exchange",query+".convex.json");
    if(initial_probe&&!query.empty())add("--initial-z3-after",query+".initial.smt2");
    for(const auto& output:outputs) {
        const auto normalized_output=fs::weakly_canonical(fs::absolute(output.second));
        for(const auto& input:protected_inputs) {
            if(input.empty())continue;
            const auto normalized_input=fs::weakly_canonical(fs::absolute(input));
            std::error_code error;
            if(normalized_output==normalized_input||
               (fs::equivalent(normalized_output,normalized_input,error)&&!error))
                throw std::runtime_error(output.first+" conflicts with protected input: "+input.string());
        }
    }
}
void export_native(const std::string& target,const fs::path& input,const std::string& native) {
    if(target.empty())return;
    const auto output=fs::absolute(target).lexically_normal();
    if(output==input || (fs::exists(output)&&fs::equivalent(output,input)))
        throw std::runtime_error("--export-native would overwrite the input");
    std::ofstream file(output,std::ios::binary|std::ios::trunc);
    if(!file)throw std::runtime_error("cannot write native export: "+output.string());
    file<<native;file.close();
    if(!file)throw std::runtime_error("cannot finish native export: "+output.string());
}
class TemporaryNative {
    std::string path_;
public:
    explicit TemporaryNative(const std::string& text) {
        auto pattern=(fs::temp_directory_path()/"z3-nia-XXXXXX").string();
        std::vector<char> name(pattern.begin(),pattern.end());name.push_back(0);
        const int fd=mkstemp(name.data());
        if(fd<0)throw std::runtime_error("cannot create private native input: "+std::string(std::strerror(errno)));
        path_=name.data();
        size_t offset=0;
        while(offset<text.size()) {
            auto count=write(fd,text.data()+offset,text.size()-offset);
            if(count<0&&errno==EINTR)continue;
            if(count<=0){close(fd);unlink(path_.c_str());throw std::runtime_error("cannot write private native input");}
            offset+=size_t(count);
        }
        if(close(fd)){unlink(path_.c_str());throw std::runtime_error("cannot close private native input");}
    }
    ~TemporaryNative(){if(!path_.empty())unlink(path_.c_str());}
    TemporaryNative(const TemporaryNative&)=delete;
    TemporaryNative& operator=(const TemporaryNative&)=delete;
    const std::string& path()const{return path_;}
};
void help() {
    std::cout<<"usage: z3 --nia-ls INPUT.smt2 [--mode=sls|unbounded|optimal] [NIA options]\n"
        "       z3 --nia-ls --native-input INPUT.nia [original NIA execution/options]\n"
        "       z3 --nia-ls INPUT.smt2 --import-only --export-native=PATH\n"
        "SMT2 runs default to optimal when no original execution mode is supplied.\n"
        "sls disables both proof modes; unbounded enables only unbounded proof; optimal enables both.\n"
        "--initialize=on retains HEAD's linearized local-search initialization; --initialize=off disables it.\n"
        "Original --timeout, --model-output, --seed, --optimality-* and other NIA flags are preserved.\n"
        "--optimality-tail caps a balanced wait near the total deadline; requires --optimality-balanced.\n"
        "--advisory-share=FRACTION shares a finite auxiliary prefix of the first proof window; requires 0<FRACTION<1.\n"
        "--optimality-bv-seconds=SECONDS tries native BV for a soft wall budget before default (0 OFF, or [0.001,1200]); requires --prove-optimal.\n"
        "--guarded-product enables a source-bound QF_LRA proof phase with guarded cubic consequences (OFF by default, at most 3 wall seconds); requires --prove-optimal.\n"
        "--core-neighborhood enables experimental conflict-guided search exchange (not a proof).\n"
        "--core-neighborhood-stall=SECONDS and --core-neighborhood-budget=SECONDS set its finite allowance.\n"
        "--convex-exchange enables certified convex-quadratic source cuts in the optimality worker.\n"
        "--objective-slice checks and refines original-source assertion subsets in an isolated proof context.\n"
        "--objective-slice-budget=SECONDS sets its finite allowance (default 2); requires --objective-slice.\n"
        "--integer-convex-exchange adds certified integer-square cuts; requires --convex-exchange.\n"
        "--integer-affine-convex-exchange adds guarded residual integer-affine square cuts; requires --convex-exchange.\n"
        "--isolate-exchange-context protects the default proof context from advisory searches.\n"
        "--slack-repair enables bounded integer witness compensation proposals in local search.\n"
        "--smt-exchange=backend|hints selects explicit SMT arith2, optionally seeded with our own integer assignment.\n"
        "Use --keep-definitions or --no-preprocess to control AST import.\n"
        "SLS is compiled into this shell and uses its Z3 core; proof workers may fork and resume.\n";
}
Options parse(int argc,char* argv[]) {
    Options out;
    const std::set<std::string> valued={"--timeout","--model-output","--seed","--target","--large-target",
        "--unbounded-threshold","--optimality-stall","--optimality-timeout","--optimality-search-window","--optimality-bv-seconds",
        "--initial-z3-after","--initial-z3-timeout","--optimality-query-output","--initialize",
        "--restart-repair","--equality-protection","--core-neighborhood-stall","--core-neighborhood-budget","--smt-exchange"};
    auto assign_mode=[&](const std::string& value){
        if(!out.mode.empty())throw std::runtime_error("--mode may be specified only once");
        if(value!="sls"&&value!="unbounded"&&value!="optimal")throw std::runtime_error("invalid --mode: expected sls, unbounded, or optimal");
        out.mode=value;
    };
    for(int i=1;i<argc;++i) {
        std::string option=argv[i];
        auto next=[&](){if(++i>=argc)throw std::runtime_error("missing value for "+option);return std::string(argv[i]);};
        if(option=="--nia-ls")continue;
        if(option=="--help"||option=="-h"){out.help=true;continue;}
        if(option=="--z3-version"){out.version=true;continue;}
        if(option=="--mode"){assign_mode(next());continue;}
        if(prefix(option,"--mode=")){assign_mode(option.substr(7));continue;}
        if(option=="--native-input"||prefix(option,"--native-input=")){
            if(!out.native.empty())throw std::runtime_error("--native-input may be specified only once");
            out.native=option=="--native-input"?next():option.substr(15);continue;
        }
        if(option=="--export-native"||prefix(option,"--export-native=")){
            if(!out.export_path.empty())throw std::runtime_error("--export-native may be specified only once");
            out.export_path=option=="--export-native"?next():option.substr(16);
            if(out.export_path.empty())throw std::runtime_error("empty --export-native path");
            continue;
        }
        if(option=="--keep-definitions"){out.import.keep_definitions=true;out.import_options_given=true;continue;}
        if(option=="--no-preprocess"){out.import.preprocess=false;out.import_options_given=true;continue;}
        if(valued.count(option))option+='='+next();
        if(option=="--import-only")out.import_only=true;
        if(option=="--first-solution")out.first_solution=true;
        if(option=="--optimize")out.optimize=true;
        if(prefix(option,"--timeout=")) {
            // This value is for import telemetry only. Keep malformed flags
            // verbatim so HEAD remains the sole validator, in its old order.
            try {
                size_t used=0;const auto text=option.substr(10);
                const double seconds=std::stod(text,&used);
                if(used==text.size()&&std::isfinite(seconds)&&seconds>0)out.timeout=seconds;
            } catch(const std::invalid_argument&) {
            } catch(const std::out_of_range&) {
            }
        }
        if(!option.empty()&&option[0]=='-'){out.legacy.push_back(option);continue;}
        if(!out.source.empty())throw std::runtime_error("multiple input files");
        out.source=option;
    }
    if(out.help||out.version)return out;
    if(out.source.empty()==out.native.empty())throw std::runtime_error("provide one SMT2 input or --native-input PATH");
    if(!out.native.empty()&&out.import_options_given)throw std::runtime_error("AST import options conflict with --native-input");
    if(!out.mode.empty()&&(out.import_only||out.first_solution))throw std::runtime_error("--mode conflicts with --import-only/--first-solution");
    if(out.mode.empty()&&out.native.empty()&&!out.import_only&&!out.first_solution&&!out.optimize)out.mode="optimal";
    if(!out.mode.empty()) {
        for(const auto& option:out.legacy) {
            const bool optimal=option=="--prove-optimal"||option=="--guarded-product"||prefix(option,"--optimality-")||prefix(option,"--initial-z3-");
            const bool unbounded=option=="--prove-unbounded"||prefix(option,"--unbounded-threshold=");
            if((out.mode=="sls"&&(optimal||unbounded))||(out.mode=="unbounded"&&optimal))
                throw std::runtime_error("--mode="+out.mode+" conflicts with "+option);
        }
        auto add=[&](const std::string& option){if(std::find(out.legacy.begin(),out.legacy.end(),option)==out.legacy.end())out.legacy.push_back(option);};
        add("--optimize");
        if(out.mode=="unbounded"||out.mode=="optimal")add("--prove-unbounded");
        if(out.mode=="optimal")add("--prove-optimal");
    }
    return out;
}
int invoke(const std::string& input,const std::vector<std::string>& options,double imported,
           const std::function<void()>& before_input={}) {
    std::vector<std::string> arguments{"nia_ls",input};arguments.insert(arguments.end(),options.begin(),options.end());
    std::vector<char*> argv;for(auto& argument:arguments)argv.push_back(argument.data());argv.push_back(nullptr);
    return run_native_cli(int(arguments.size()),argv.data(),imported,before_input);
}
}
bool requested(int argc,char* const argv[])noexcept {
    for(int i=1;i<argc;++i)if(std::strcmp(argv[i],"--nia-ls")==0)return true;
    return false;
}
int run_frontend(int argc,char* argv[]) {
    const auto began=std::chrono::steady_clock::now();
    try {
        const auto options=parse(argc,argv);
        if(options.help){help();return 0;}
        if(options.version)return invoke("--z3-version",{},0);
        if(!options.native.empty()) {
            return invoke(options.native,options.legacy,0,[&](){
                const auto input=input_path(options.native);
                const auto source=native_source(input);
                preflight_outputs(options,{input,fs::path(source)},options.native);
                if(!options.export_path.empty())export_native(options.export_path,input,read_file(input));
            });
        }
        const auto input=input_path(options.source);const auto source=read_file(input);
        if(source.find('\0')!=std::string::npos)throw std::runtime_error("SMT2 input contains a NUL byte");
        const auto native=convert_text(source,input.string(),options.import);
        TemporaryNative temporary(native);
        const auto imported=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
        std::cerr<<std::setprecision(12)<<"nia_embedded_import_seconds="<<imported
            <<" remaining_seconds="<<std::max(0.0,options.timeout-imported)<<" source="<<std::quoted(input.string())<<'\n';
        return invoke(temporary.path(),options.legacy,imported,[&](){
            preflight_outputs(options,{input,fs::path(temporary.path())},temporary.path());
            export_native(options.export_path,input,native);
        });
    } catch(const std::exception& error) {
        std::cerr<<"NIA_LS: "<<error.what()<<'\n';return 2;
    }
}
}}
