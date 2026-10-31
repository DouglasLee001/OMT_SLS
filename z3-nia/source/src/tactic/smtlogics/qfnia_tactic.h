/*++
Copyright (c) 2012 Microsoft Corporation

Module Name:

    qfnia_tactic.h

Abstract:

    Tactic for QF_NIA

Author:

    Leonardo (leonardo) 2012-02-28

Notes:

--*/
#pragma once

#include "util/params.h"
class ast_manager;
class tactic;

tactic * mk_qfnia_tactic(ast_manager & m, params_ref const & p = params_ref());
tactic * mk_qfnia_omt_bv_tactic(ast_manager & m, params_ref const & p = params_ref());
tactic * mk_qfnia_omt_tactic(ast_manager & m, params_ref const & p = params_ref());
/*
  ADD_TACTIC("qfnia", "builtin strategy for solving QF_NIA problems.", "mk_qfnia_tactic(m, p)")
  ADD_TACTIC("qfnia-omt-bv", "extended-budget BV branch for QF_NIA, preserving under-approximation guards.", "mk_qfnia_omt_bv_tactic(m, p)")
  ADD_TACTIC("qfnia-omt", "extended-budget BV first, followed by the complete QF_NIA fallback strategy.", "mk_qfnia_omt_tactic(m, p)")
*/

