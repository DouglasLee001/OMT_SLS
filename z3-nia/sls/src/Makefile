
CXX = g++
CXXFLAGS = -Wall -g -w -O3
# Keep the C++ headers and the loaded library on the same pinned release.
Z3_ROOT = $(abspath ../../competitor/z3-5.1.0)
Z3_API_DIR = $(Z3_ROOT)/source/src/api
Z3_BUILD_DIR = $(Z3_ROOT)/build
Z3_LIBRARY = $(Z3_BUILD_DIR)/libz3.so.5.1.0.0
Z3_HEADERS = $(wildcard $(Z3_API_DIR)/z3*.h) $(Z3_API_DIR)/c++/z3++.h
CPPFLAGS = -I$(Z3_API_DIR)/c++ -I$(Z3_API_DIR)
LDLIBS = -l:libz3.so.5.1.0.0 -lgmpxx -lgmp
# Frozen Slurm runs copy the SONAME into source/lib beside the executable.
LDFLAGS = -L$(Z3_BUILD_DIR) -Wl,-rpath,'$$ORIGIN/lib:$(Z3_BUILD_DIR)'

TARGET = nia_ls
SRCS =   nia_omt_cutset.cpp nia_omt_model_bridge.cpp nia_omt_core_neighborhood.cpp nia_omt_convex_exchange.cpp nia_omt_optimality.cpp nia_omt_unbounded.cpp nia_omt_bilinear.cpp nia_omt_feasibility.cpp nia_omt_repair.cpp nia_omt_optimize.cpp nia_omt_first.cpp nia_omt_input.cpp nia_build_instance.cpp  nia_initialize.cpp nia_linear_initialize.cpp nia_restart_repair.cpp nia_ls.cpp    nia_print.cpp call.cpp
SRCS += nia_omt_objective_slice.cpp
OBJS = $(SRCS:.cpp=.o)
HEADERS = nia_omt_bilinear.h nia_omt_legacy_arithmetic.h nia_omt_disjunctive.h nia_omt_difference.h nia_omt_feasibility.h nia_omt_neighborhood.h nia_omt_repair.h nia_omt_optimize.h nia_omt_first.h nia_omt_input.h nia_Array.h nia_ls.h call.h nia_linear_initialize.h nia_linearize.h nia_lia_sls.h nia_lia_preprocess.h nia_repair_selection.h

all: $(TARGET)

nia_omt_optimize.o: nia_omt_large_target.h nia_omt_unbounded.h nia_omt_optimality.h nia_omt_optimality_schedule.h
nia_omt_optimality.o: nia_omt_optimality.h nia_omt_model_bridge.h nia_omt_cutset.h
nia_omt_cutset.o: nia_omt_cutset.h
nia_omt_model_bridge.o: nia_omt_model_bridge.h
nia_omt_core_neighborhood.o: nia_omt_core_neighborhood.h
nia_omt_optimize.o: nia_omt_core_neighborhood.h
nia_omt_convex_exchange.o: nia_omt_convex_exchange.h
nia_omt_optimality.o: nia_omt_convex_exchange.h
nia_omt_objective_slice.o: nia_omt_objective_slice.h
nia_omt_optimality.o: nia_omt_objective_slice.h
nia_omt_unbounded.o: nia_omt_unbounded.h

$(TARGET): $(OBJS) $(Z3_LIBRARY)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)

%.o: %.cpp $(HEADERS) Makefile $(Z3_HEADERS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $<

clean:
	rm -f $(TARGET) $(OBJS)

# Verification builds use separate outputs so they cannot contaminate release objects.
SCORING_TEST_DIR = ../tests/.build
SCORING_SRCS = $(filter-out call.cpp,$(SRCS))

.PHONY: test-scoring scoring-drivers

test-scoring: scoring-drivers
	python3 ../tests/run_incremental_scoring.py

scoring-drivers:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-scoring-new ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_SCORING_REFERENCE -DNIA_CANDIDATE_REFERENCE -I. -o $(SCORING_TEST_DIR)/nia-scoring-reference ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_SCORING_VERIFY -DNIA_CANDIDATE_VERIFY -I. -o $(SCORING_TEST_DIR)/nia-scoring-verify ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)

.PHONY: test-linear-initialization
test-linear-initialization:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-linear-initialization ../tests/linear_initialization_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_linear_initialization.py

.PHONY: linear-model-validator
linear-model-validator: $(SCORING_TEST_DIR)/validate-initial-model

$(SCORING_TEST_DIR)/validate-initial-model: ../tests/validate_initial_model.cpp
	mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

.PHONY: test-restart-repair
test-restart-repair: $(TARGET) test-repair-selection
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-restart-repair ../tests/restart_repair_driver.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-restart-repair

.PHONY: test-lia-sls
test-lia-sls:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-lia-sls ../tests/lia_sls_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-lia-sls

.PHONY: test-lia-scoring lia-scoring-drivers

test-lia-scoring: lia-scoring-drivers
	python3 ../tests/run_lia_incremental_scoring.py

lia-scoring-drivers:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-scoring-reference ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -I. -o $(SCORING_TEST_DIR)/lia-scoring-incremental ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-scoring-verify ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)

.PHONY: test-lia-variable

test-lia-variable:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_VARIABLE_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-variable-reference ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-variable-heuristic ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_lia_variable_heuristic.py

.PHONY: test-repair-selection
test-repair-selection:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/repair-selection ../tests/repair_selection_driver.cpp
	$(SCORING_TEST_DIR)/repair-selection

.PHONY: test-equality-merge
test-equality-merge: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/equality-merge ../tests/equality_merge_driver.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/equality-merge

.PHONY: test-omt-import
test-omt-import: $(TARGET) $(SCORING_TEST_DIR)/omt-native-input
	$(SCORING_TEST_DIR)/omt-native-input
	python3 ../tests/test_omt_import.py

$(SCORING_TEST_DIR)/omt-native-input: ../tests/omt_native_driver.cpp $(filter-out call.o,$(OBJS)) $(HEADERS)
	mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $@ $< $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)

.PHONY: test-first-solution
test-first-solution: $(TARGET)
	python3 ../tests/test_first_solution.py

.PHONY: test-optimization
test-optimization: $(TARGET)
	python3 ../tests/test_optimization.py

.PHONY: test-objective-repair
test-objective-repair: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-repair ../tests/test_objective_repair.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	cd ../.. && NIA_LS/tests/.build/test-omt-repair

.PHONY: test-conditional-projection
test-conditional-projection: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-conditional-projection ../tests/test_conditional_projection.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-conditional-projection

.PHONY: test-optimization-decisions
test-optimization-decisions: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-decisions ../tests/test_optimization_decisions.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-decisions

.PHONY: test-optimization-neighborhood
test-optimization-neighborhood: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-neighborhood ../tests/test_optimization_neighborhood.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-neighborhood

.PHONY: test-optimization-neutral
test-optimization-neutral: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-neutral ../tests/test_optimization_neutral.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-neutral

.PHONY: test-optimization-difference
test-optimization-difference: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-difference ../tests/test_optimization_difference.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-difference

.PHONY: test-omt-feasibility
test-omt-feasibility: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-feasibility ../tests/test_omt_feasibility.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-feasibility

.PHONY: test-optimization-frontier
test-optimization-frontier: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-frontier ../tests/test_optimization_frontier.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-frontier

.PHONY: test-optimization-disjunctive
test-optimization-disjunctive: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-disjunctive ../tests/test_optimization_disjunctive.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-disjunctive

.PHONY: test-omt-legacy-arithmetic
test-omt-legacy-arithmetic: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-legacy-arithmetic ../tests/test_omt_legacy_arithmetic.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-legacy-arithmetic

.PHONY: test-omt-bilinear
test-omt-bilinear: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-bilinear ../tests/test_omt_bilinear.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-bilinear

.PHONY: test-omt-large-target
test-omt-large-target:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-large-target ../tests/test_omt_large_target.cpp -lgmpxx -lgmp
	$(SCORING_TEST_DIR)/test-omt-large-target

.PHONY: test-omt-unbounded
test-omt-unbounded: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-unbounded ../tests/test_omt_unbounded.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-unbounded

.PHONY: test-omt-cutset test-omt-optimality test-optimality-optimization
test-omt-cutset: $(SCORING_TEST_DIR)/test-omt-cutset
	$(SCORING_TEST_DIR)/test-omt-cutset

$(SCORING_TEST_DIR)/test-omt-cutset: ../tests/test_omt_cutset.cpp nia_omt_cutset.cpp nia_omt_cutset.h Makefile
	mkdir -p $(@D)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $@ ../tests/test_omt_cutset.cpp nia_omt_cutset.cpp $(LDFLAGS) $(LDLIBS)

test-omt-optimality: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-optimality ../tests/test_omt_optimality.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-optimality

test-optimality-optimization: $(TARGET)
	PYTHONDONTWRITEBYTECODE=1 python3 ../tests/test_optimality_optimization.py

.PHONY: test-omt-optimality-schedule test-omt-model-bridge
test-omt-optimality-schedule:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-optimality-schedule ../tests/test_omt_optimality_schedule.cpp
	$(SCORING_TEST_DIR)/test-omt-optimality-schedule

test-omt-model-bridge: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-model-bridge ../tests/test_omt_model_bridge.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-model-bridge

# These cached test executables also depend on their SDK and build recipes.
$(SCORING_TEST_DIR)/validate-initial-model $(SCORING_TEST_DIR)/omt-native-input: Makefile $(Z3_HEADERS) $(Z3_LIBRARY)

.PHONY: test-native-z3-version
test-native-z3-version:
	PYTHONDONTWRITEBYTECODE=1 python3 ../tests/test_native_z3_version.py
