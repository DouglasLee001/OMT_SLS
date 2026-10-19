// Automatically generated file
#pragma once
#include "util/params.h"
#include "util/gparams.h"
struct lp_params_helper {
  params_ref const & p;
  params_ref g;
  lp_params_helper(params_ref const & _p = params_ref::get_empty()):
     p(_p), g(gparams::get_module("lp")) {}
  static void collect_param_descrs(param_descrs & d) {
    d.insert("dio", CPK_BOOL, "use Diophantine equalities", "true","lp");
    d.insert("dio_branching_period", CPK_UINT, "Period of calling branching on undef in Diophantine handler", "100","lp");
    d.insert("dio_cuts_enable_gomory", CPK_BOOL, "enable Gomory cuts together with Diophantine cuts, only relevant when dioph_eq is true", "false","lp");
    d.insert("dio_gomory_enable_period", CPK_UINT, "number of consecutive unproductive (undef) Diophantine-handler calls after which the controller starts running Gomory cuts and the gcd test alongside dio; a dio conflict resets the count and stops them; set very large to never start them this way so Gomory follows dio_cuts_enable_gomory only", "16","lp");
    d.insert("dio_cuts_enable_hnf", CPK_BOOL, "enable hnf cuts together with Diophantine cuts, only relevant when dioph_eq is true", "true","lp");
    d.insert("dio_ignore_big_nums", CPK_BOOL, "Ignore the terms with big numbers in the Diophantine handler, only relevant when dioph_eq is true", "true","lp");
    d.insert("dio_calls_period", CPK_UINT, "Period of calling the Diophantine handler in the final_check()", "1","lp");
    d.insert("dio_calls_period_decrease", CPK_UINT, "Amount by which dio_calls_period is decreased on each final_check() call where the Diophantine handler is not triggered, until it returns to its initial value", "2","lp");
    d.insert("dio_run_gcd", CPK_BOOL, "Run the GCD heuristic if dio is on, if dio is disabled the option is not used", "false","lp");
    d.insert("dio_undo_max_work", CPK_UINT, "work budget, in machine words, for eliminating the columns of the retired terms from the certificate matrix of the Diophantine handler; when a scope pop exceeds it the Diophantine state is discarded and rebuilt lazily on the next check instead of being updated incrementally; 0 means no budget", "1000000","lp");
    d.insert("lcube", CPK_BOOL, "use the largest cube test for integer feasibility", "true","lp");
    d.insert("lcube_flips", CPK_UINT, "maximal number of coordinate flips when repairing the rounded largest cube center, only relevant when lcube is true", "16","lp");
    d.insert("int_hammer_period", CPK_UINT, "period (in final_check calls) for the integer cut/cube heuristics (find_cube, hnf, gomory); a smaller value calls them more often", "4","lp");
    d.insert("random_hammers", CPK_BOOL, "draw the periodic integer heuristic gates (find_cube, lcube, hnf, gomory, dio) at random with the same 1/period rate instead of a deterministic every-k-th-call modulus", "true","lp");
  }
  /*
     REG_MODULE_PARAMS('lp', 'lp_params_helper::collect_param_descrs')
     REG_MODULE_DESCRIPTION('lp', 'linear programming parameters')
  */
  bool dio() const { return p.get_bool("dio", g, true); }
  unsigned dio_branching_period() const { return p.get_uint("dio_branching_period", g, 100u); }
  bool dio_cuts_enable_gomory() const { return p.get_bool("dio_cuts_enable_gomory", g, false); }
  unsigned dio_gomory_enable_period() const { return p.get_uint("dio_gomory_enable_period", g, 16u); }
  bool dio_cuts_enable_hnf() const { return p.get_bool("dio_cuts_enable_hnf", g, true); }
  bool dio_ignore_big_nums() const { return p.get_bool("dio_ignore_big_nums", g, true); }
  unsigned dio_calls_period() const { return p.get_uint("dio_calls_period", g, 1u); }
  unsigned dio_calls_period_decrease() const { return p.get_uint("dio_calls_period_decrease", g, 2u); }
  bool dio_run_gcd() const { return p.get_bool("dio_run_gcd", g, false); }
  unsigned dio_undo_max_work() const { return p.get_uint("dio_undo_max_work", g, 1000000u); }
  bool lcube() const { return p.get_bool("lcube", g, true); }
  unsigned lcube_flips() const { return p.get_uint("lcube_flips", g, 16u); }
  unsigned int_hammer_period() const { return p.get_uint("int_hammer_period", g, 4u); }
  bool random_hammers() const { return p.get_bool("random_hammers", g, true); }
};
