CC ?= cc
CXX ?= c++
AR ?= ar
PYTHON ?= python3
BUILD ?= build
CPPFLAGS += -Iinclude -Isrc -D_FILE_OFFSET_BITS=64
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wpedantic
CXXFLAGS ?= -O3 -std=c++17 -Wall -Wextra -Wpedantic
LDLIBS = -lz -ldl -lpthread -lm
CORE = $(BUILD)/bqp.o $(BUILD)/zstd_dyn.o
OPTB = $(CORE) $(BUILD)/optbin.o $(BUILD)/validate.o $(BUILD)/section_decode.o $(BUILD)/model_io.o
all: $(BUILD)/liboptbin.a $(BUILD)/liboptbin.so $(BUILD)/mps2optb $(BUILD)/cbf2optb $(BUILD)/bqp2optb $(BUILD)/optb_inspect legacy
$(BUILD):
	mkdir -p $(BUILD)
$(BUILD)/%.o: src/%.c include/optbin.h include/bqp.h src/internal.h | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -c $< -o $@
$(BUILD)/liboptbin.a: $(OPTB)
	$(AR) rcs $@ $^
$(BUILD)/liboptbin.so: $(OPTB)
	$(CC) $(LDFLAGS) -shared $^ $(LDLIBS) -o $@
$(BUILD)/mps2optb: src/mps2optb.cpp src/mps_parser.hpp src/writer.hpp src/converter_common.hpp $(OPTB)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $< $(OPTB) $(LDLIBS) -o $@
$(BUILD)/cbf2optb: src/cbf2optb.cpp src/cbf_parser.hpp src/writer.hpp src/converter_common.hpp $(OPTB)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $< $(OPTB) $(LDLIBS) -o $@
$(BUILD)/bqp2optb: src/bqp2optb.cpp src/converter_common.hpp $(OPTB)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $< $(OPTB) $(LDLIBS) -o $@
$(BUILD)/optb_inspect: $(BUILD)/inspect.o $(OPTB)
	$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@
legacy: $(BUILD)/mps2bqp $(BUILD)/bqp_inspect $(BUILD)/libbqp.so $(BUILD)/libbqp.a $(BUILD)/load_auto
$(BUILD)/mps2bqp: src/mps2bqp.cpp $(CORE)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $< $(CORE) $(LDLIBS) -o $@
$(BUILD)/bqp_inspect: $(BUILD)/bqp_inspect.o $(CORE) $(BUILD)/bqp_cuda.o
	$(CC) $(LDFLAGS) $^ $(LDLIBS) -o $@
$(BUILD)/libbqp.a: $(CORE) $(BUILD)/bqp_cuda.o
	$(AR) rcs $@ $^
$(BUILD)/libbqp.so: $(CORE) $(BUILD)/bqp_cuda.o
	$(CC) $(LDFLAGS) -shared $^ $(LDLIBS) -o $@
$(BUILD)/load_auto: examples/load_auto.c $(BUILD)/libbqp.a
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) $< $(BUILD)/libbqp.a $(LDLIBS) -o $@
test: all
	BQP_BUILD="$(abspath $(BUILD))" $(PYTHON) tests/test_bqp.py
	OPTBIN_BUILD="$(abspath $(BUILD))" OPTBIN_LIBRARY="$(abspath $(BUILD))/liboptbin.so" OPTBIN_TEST_LDFLAGS="$(LDFLAGS)" $(PYTHON) -m unittest discover -s tests -p 'test_optbin.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_jump_compare.py' -v
test-solvers: all
	OPTBIN_BUILD="$(abspath $(BUILD))" OPTBIN_LIBRARY="$(abspath $(BUILD))/liboptbin.so" $(PYTHON) -m unittest discover -s tests -p 'test_solvers.py' -v
clean:
	rm -rf $(BUILD)
.PHONY: all legacy test test-solvers clean
