
CXX = g++
CXXFLAGS = -Wall -g -w -O3
LDLIBS = -lz3 -lgmpxx -lgmp
LDFLAGS = -Wl,-rpath,$(abspath .lib)

TARGET = nia_ls
SRCS =   nia_omt_bilinear.cpp nia_omt_feasibility.cpp nia_omt_repair.cpp nia_omt_optimize.cpp nia_omt_first.cpp nia_omt_input.cpp nia_build_instance.cpp  nia_initialize.cpp nia_linear_initialize.cpp nia_restart_repair.cpp nia_ls.cpp    nia_print.cpp call.cpp
OBJS = $(SRCS:.cpp=.o)
HEADERS = nia_omt_bilinear.h nia_omt_legacy_arithmetic.h nia_omt_disjunctive.h nia_omt_difference.h nia_omt_feasibility.h nia_omt_neighborhood.h nia_omt_repair.h nia_omt_optimize.h nia_omt_first.h nia_omt_input.h nia_Array.h nia_ls.h call.h nia_linear_initialize.h nia_linearize.h nia_lia_sls.h nia_lia_preprocess.h nia_repair_selection.h

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.cpp $(HEADERS)
	$(CXX) $(CXXFLAGS) -c $<

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
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-scoring-new ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_SCORING_REFERENCE -DNIA_CANDIDATE_REFERENCE -I. -o $(SCORING_TEST_DIR)/nia-scoring-reference ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_SCORING_VERIFY -DNIA_CANDIDATE_VERIFY -I. -o $(SCORING_TEST_DIR)/nia-scoring-verify ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)

.PHONY: test-linear-initialization
test-linear-initialization:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-linear-initialization ../tests/linear_initialization_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_linear_initialization.py

.PHONY: linear-model-validator
linear-model-validator: $(SCORING_TEST_DIR)/validate-initial-model

$(SCORING_TEST_DIR)/validate-initial-model: ../tests/validate_initial_model.cpp
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

.PHONY: test-restart-repair
test-restart-repair: $(TARGET) test-repair-selection
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-restart-repair ../tests/restart_repair_driver.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-restart-repair

.PHONY: test-lia-sls
test-lia-sls:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-lia-sls ../tests/lia_sls_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-lia-sls

.PHONY: test-lia-scoring lia-scoring-drivers

test-lia-scoring: lia-scoring-drivers
	python3 ../tests/run_lia_incremental_scoring.py

lia-scoring-drivers:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-scoring-reference ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -I. -o $(SCORING_TEST_DIR)/lia-scoring-incremental ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-scoring-verify ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)

.PHONY: test-lia-variable

test-lia-variable:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_VARIABLE_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-variable-reference ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-variable-heuristic ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_lia_variable_heuristic.py

.PHONY: test-repair-selection
test-repair-selection:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/repair-selection ../tests/repair_selection_driver.cpp
	$(SCORING_TEST_DIR)/repair-selection

.PHONY: test-equality-merge
test-equality-merge: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/equality-merge ../tests/equality_merge_driver.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/equality-merge

.PHONY: test-omt-import
test-omt-import: $(TARGET) $(SCORING_TEST_DIR)/omt-native-input
	$(SCORING_TEST_DIR)/omt-native-input
	python3 ../tests/test_omt_import.py

$(SCORING_TEST_DIR)/omt-native-input: ../tests/omt_native_driver.cpp $(filter-out call.o,$(OBJS)) $(HEADERS)
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) -I. -o $@ $< $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)

.PHONY: test-first-solution
test-first-solution: $(TARGET)
	python3 ../tests/test_first_solution.py

.PHONY: test-optimization
test-optimization: $(TARGET)
	python3 ../tests/test_optimization.py

.PHONY: test-objective-repair
test-objective-repair: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-repair ../tests/test_objective_repair.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	cd ../.. && NIA_LS/tests/.build/test-omt-repair

.PHONY: test-conditional-projection
test-conditional-projection: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-conditional-projection ../tests/test_conditional_projection.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-conditional-projection

.PHONY: test-optimization-decisions
test-optimization-decisions: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-decisions ../tests/test_optimization_decisions.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-decisions

.PHONY: test-optimization-neighborhood
test-optimization-neighborhood: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-neighborhood ../tests/test_optimization_neighborhood.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-neighborhood

.PHONY: test-optimization-neutral
test-optimization-neutral: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-neutral ../tests/test_optimization_neutral.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-neutral

.PHONY: test-optimization-difference
test-optimization-difference: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-difference ../tests/test_optimization_difference.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-difference

.PHONY: test-omt-feasibility
test-omt-feasibility: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-feasibility ../tests/test_omt_feasibility.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-feasibility

.PHONY: test-optimization-frontier
test-optimization-frontier: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-frontier ../tests/test_optimization_frontier.cpp $(filter-out call.o nia_omt_optimize.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-frontier

.PHONY: test-optimization-disjunctive
test-optimization-disjunctive: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-optimization-disjunctive ../tests/test_optimization_disjunctive.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-optimization-disjunctive

.PHONY: test-omt-legacy-arithmetic
test-omt-legacy-arithmetic: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-legacy-arithmetic ../tests/test_omt_legacy_arithmetic.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-legacy-arithmetic

.PHONY: test-omt-bilinear
test-omt-bilinear: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/test-omt-bilinear ../tests/test_omt_bilinear.cpp $(filter-out call.o,$(OBJS)) $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/test-omt-bilinear
