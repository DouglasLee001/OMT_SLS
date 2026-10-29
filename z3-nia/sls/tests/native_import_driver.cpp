#include "nia_import.h"
#include <z3++.h>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

// Root may link --wrap for these APIs; a conversion must never reach them.
extern "C" Z3_lbool __wrap_Z3_solver_check(Z3_context, Z3_solver) { std::_Exit(91); }
extern "C" Z3_lbool __wrap_Z3_solver_check_assumptions(Z3_context, Z3_solver, unsigned, Z3_ast const*) { std::_Exit(92); }
extern "C" Z3_lbool __wrap_Z3_optimize_check(Z3_context, Z3_optimize, unsigned, Z3_ast const*) { std::_Exit(93); }

int main(int argc, char** argv) {
    try {
        if (argc < 2) throw std::runtime_error("usage: importer_driver INPUT [--keep-definitions] [--no-preprocess]");
        nia::embedded::ImportOptions options;
        for (int i = 2; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--keep-definitions") options.keep_definitions = true;
            else if (arg == "--no-preprocess") options.preprocess = false;
            else throw std::runtime_error("unknown option: " + arg);
        }
        std::ifstream input(argv[1]);
        if (!input) throw std::runtime_error("cannot read input");
        std::string source((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        z3::context proof_context;
        auto x = proof_context.int_const("x"), y = proof_context.int_const("y");
        auto original = (x * y == 0).simplify();
        if (!original.is_or()) throw std::runtime_error("unexpected pinned Z3 default factoring");
        auto converted = nia::embedded::convert_text(source, argv[1], options);
        if (!z3::eq(original, (x * y == 0).simplify())) throw std::runtime_error("import changed proof defaults");
        // A fresh local conversion context also makes repeated conversions stable.
        if (converted != nia::embedded::convert_text(source, argv[1], options)) throw std::runtime_error("unstable repeated conversion");
        std::cout << converted;
        return 0;
    } catch (std::exception const& error) {
        std::cerr << "importer_driver: " << error.what() << '\n';
        return 2;
    }
}
