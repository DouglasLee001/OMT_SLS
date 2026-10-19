// Automatically generated file
#pragma once
#include "util/params.h"
#include "util/gparams.h"
struct tptp {
  params_ref const & p;
  params_ref g;
  tptp(params_ref const & _p = params_ref::get_empty()):
     p(_p), g(gparams::get_module("tptp")) {}
  static void collect_param_descrs(param_descrs & d) {
    d.insert("root", CPK_SYMBOL, "root directory for resolving TPTP include() axiom paths (replaces the TPTP environment variable)", "","tptp");
    d.insert("dump_smt2", CPK_SYMBOL, "if non-empty, file path to dump the parsed TPTP goal as an SMT-LIB2 benchmark (replaces the Z3_TPTP_DUMP_SMT2 environment variable)", "","tptp");
  }
  /*
     REG_MODULE_PARAMS('tptp', 'tptp::collect_param_descrs')
     REG_MODULE_DESCRIPTION('tptp', 'TPTP frontend parameters')
  */
  symbol root() const { return p.get_sym("root", g, symbol("")); }
  symbol dump_smt2() const { return p.get_sym("dump_smt2", g, symbol("")); }
};
