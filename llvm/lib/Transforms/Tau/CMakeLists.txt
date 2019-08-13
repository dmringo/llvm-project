# If we're building the pass alone, get the required LLVM stuff
if(TAU_PROF_STANDALONE)

  project(TauProfiling)

  # Same as most other LLVM projects
  cmake_minimum_required(VERSION 3.4.3)

  set (CMAKE_CXX_STANDARD 11)

  find_package(LLVM REQUIRED CONFIG)

  # required for `add_llvm_loadable_module`
  list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
  include(AddLLVM)

  add_definitions(${LLVM_DEFINITIONS})
  include_directories(${LLVM_INCLUDE_DIRS})

else()
  # This chunk inherited from the example pass (llvm/lib/Transform/Hello) and
  # only seems to be relevant for a full LLVM build.

  # If we don't need RTTI or EH, there's no reason to export anything
  # from the plugin.
  if( NOT LLVM_REQUIRES_RTTI )
    if( NOT LLVM_REQUIRES_EH )
      set(LLVM_EXPORTED_SYMBOL_FILE ${CMAKE_CURRENT_SOURCE_DIR}/Tau.exports)
    endif()
  endif()

endif()


if(WIN32 OR CYGWIN)
  set(LLVM_LINK_COMPONENTS Core Support)
endif()


# For use with C programs
add_llvm_library(TAU_Profiling MODULE
  TAU-Instrument.cpp

  DEPENDS
  intrinsics_gen
  PLUGIN_TOOL
  opt
  )

# For use with C++ programs
add_llvm_library(TAU_Profiling_CXX MODULE
  TAU-Instrument.cpp

  DEPENDS
  intrinsics_gen
  PLUGIN_TOOL
  opt
  )

# define TAU_PROF_CXX to let the CXX version include demangling logic.
target_compile_definitions(TAU_Profiling_CXX PUBLIC TAU_PROF_CXX)
