cmake_minimum_required(VERSION 3.16)
project(OptBin VERSION 0.2.0 LANGUAGES C CXX)
set(OPTBIN_RELEASE_VERSION "0.2.0rc4")
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(ZLIB REQUIRED)
find_package(Threads REQUIRED)
add_library(optbin src/optbin.c src/validate.c src/section_decode.c src/model_io.c src/bqp.c src/zstd_dyn.c)
set_target_properties(optbin PROPERTIES POSITION_INDEPENDENT_CODE ON)
set_target_properties(optbin PROPERTIES VERSION ${PROJECT_VERSION} SOVERSION 0)
target_include_directories(optbin PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include> PRIVATE src)
target_compile_definitions(optbin PRIVATE _FILE_OFFSET_BITS=64)
target_link_libraries(optbin PUBLIC ZLIB::ZLIB Threads::Threads ${CMAKE_DL_LIBS} m)
foreach(tool mps2optb cbf2optb bqp2optb)
    add_executable(${tool} src/${tool}.cpp)
    target_include_directories(${tool} PRIVATE src)
    target_link_libraries(${tool} PRIVATE optbin)
endforeach()
add_executable(optb_inspect src/inspect.c)
target_link_libraries(optb_inspect PRIVATE optbin)
add_executable(load_optb examples/load_optb.c)
target_link_libraries(load_optb PRIVATE optbin)
include(CTest)
if(BUILD_TESTING)
    find_package(Python3 QUIET COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        add_test(NAME optbin_cli_roundtrip COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/smoke_cli.py
            --bin-dir $<TARGET_FILE_DIR:mps2optb>
            --examples ${CMAKE_CURRENT_SOURCE_DIR}/examples
            --consumer $<TARGET_FILE:load_optb>)
    else()
        message(STATUS "Python not found: CLI smoke test disabled; native tools still build")
    endif()
endif()
option(OPTBIN_WITH_GUROBI_BENCH "Build the optional runtime-loaded Gurobi reference benchmark" OFF)
if(OPTBIN_WITH_GUROBI_BENCH)
    add_executable(optb_gurobi_bench src/gurobi_bench.c)
    target_link_libraries(optb_gurobi_bench PRIVATE optbin ${CMAKE_DL_LIBS})
    if(BUILD_TESTING AND Python3_Interpreter_FOUND)
        add_test(NAME optbin_gurobi_reference_contract COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_gurobi_bench.py
            --build $<TARGET_FILE_DIR:optb_gurobi_bench>)
    endif()
endif()
option(OPTBIN_WITH_HDF5 "Build the optional native HDF5 evaluation frontend" OFF)
if(OPTBIN_WITH_HDF5)
    find_package(HDF5 REQUIRED COMPONENTS C)
    add_library(optbin_hdf5 SHARED src/hdf5_native.c)
    target_include_directories(optbin_hdf5 PUBLIC include PRIVATE src ${HDF5_INCLUDE_DIRS})
    target_link_libraries(optbin_hdf5 PRIVATE optbin ${HDF5_LIBRARIES})
    add_executable(optb_native_bench src/native_bench.c)
    target_link_libraries(optb_native_bench PRIVATE optbin_hdf5 optbin ${CMAKE_DL_LIBS})
    add_executable(optb_format_bench src/native_bench.c src/text_bench_loader.cpp)
    target_compile_definitions(optb_format_bench PRIVATE OPTBIN_TEXT_BENCH)
    target_include_directories(optb_format_bench PRIVATE src)
    target_link_libraries(optb_format_bench PRIVATE optbin_hdf5 optbin ${CMAKE_DL_LIBS})
    if(BUILD_TESTING AND Python3_Interpreter_FOUND)
        add_test(NAME optbin_format_benchmark_contract COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_format_bench.py
            --build $<TARGET_FILE_DIR:optb_format_bench>)
    endif()
    add_executable(optb_h5_export src/hdf5_export.c)
    target_link_libraries(optb_h5_export PRIVATE optbin_hdf5)
endif()
include(GNUInstallDirs)
set_target_properties(mps2optb cbf2optb bqp2optb optb_inspect PROPERTIES
    INSTALL_RPATH "$ORIGIN/../${CMAKE_INSTALL_LIBDIR}")
install(TARGETS optbin mps2optb cbf2optb bqp2optb optb_inspect EXPORT OptBinTargets
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR} LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(FILES include/optbin.h include/bqp.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(FILES LICENSE README.md CHANGELOG.md docs/release.md
    DESTINATION ${CMAKE_INSTALL_DOCDIR})
install(DIRECTORY examples/ DESTINATION ${CMAKE_INSTALL_DOCDIR}/examples
    FILES_MATCHING PATTERN "*.mps" PATTERN "*.cbf" PATTERN "load_optb.c"
    PATTERN "CMakeLists.txt")
install(EXPORT OptBinTargets NAMESPACE OptBin:: DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/OptBin)
include(CMakePackageConfigHelpers)
configure_package_config_file(cmake/OptBinConfig.cmake.in ${CMAKE_CURRENT_BINARY_DIR}/OptBinConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/OptBin)
write_basic_package_version_file(${CMAKE_CURRENT_BINARY_DIR}/OptBinConfigVersion.cmake
    VERSION ${PROJECT_VERSION} COMPATIBILITY SameMinorVersion)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/OptBinConfig.cmake ${CMAKE_CURRENT_BINARY_DIR}/OptBinConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/OptBin)
