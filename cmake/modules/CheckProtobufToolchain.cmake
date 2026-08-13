# Verify that CMake will use one coherent native Protobuf installation.
function(srsran_check_protobuf_toolchain)
  if(NOT TARGET protobuf::libprotobuf)
    message(FATAL_ERROR "Protobuf was found without the protobuf::libprotobuf imported target")
  endif()
  if(NOT TARGET protobuf::protoc)
    message(FATAL_ERROR "Protobuf was found without the protobuf::protoc imported target")
  endif()

  set(_protobuf_protoc "${Protobuf_PROTOC_EXECUTABLE}")
  if(NOT _protobuf_protoc)
    get_target_property(_protobuf_protoc protobuf::protoc IMPORTED_LOCATION)
  endif()
  if(NOT _protobuf_protoc)
    message(FATAL_ERROR "CMake did not report the Protobuf compiler location")
  endif()

  execute_process(
    COMMAND "${_protobuf_protoc}" --version
    RESULT_VARIABLE _protobuf_protoc_result
    OUTPUT_VARIABLE _protobuf_protoc_output
    ERROR_VARIABLE _protobuf_protoc_error
    OUTPUT_STRIP_TRAILING_WHITESPACE
  )
  if(NOT _protobuf_protoc_result EQUAL 0)
    message(FATAL_ERROR
      "Unable to run ${_protobuf_protoc} --version: ${_protobuf_protoc_error}")
  endif()
  if(NOT _protobuf_protoc_output MATCHES "^libprotoc ([0-9]+\\.[0-9]+(\\.[0-9]+)?)$")
    message(FATAL_ERROR
      "Unexpected protoc version output from ${_protobuf_protoc}: ${_protobuf_protoc_output}")
  endif()
  set(_protobuf_protoc_version "${CMAKE_MATCH_1}")

  if(NOT _protobuf_protoc_version VERSION_EQUAL Protobuf_VERSION)
    message(FATAL_ERROR
      "Mixed native Protobuf toolchain: protoc ${_protobuf_protoc_version} at "
      "${_protobuf_protoc}, but CMake found Protobuf ${Protobuf_VERSION}. "
      "Remove conflicting installations or set Protobuf_ROOT consistently.")
  endif()

  get_target_property(_protobuf_target_protoc protobuf::protoc IMPORTED_LOCATION)
  if(_protobuf_target_protoc)
    get_filename_component(_protobuf_protoc_real "${_protobuf_protoc}" REALPATH)
    get_filename_component(_protobuf_target_protoc_real "${_protobuf_target_protoc}" REALPATH)
    if(NOT _protobuf_protoc_real STREQUAL _protobuf_target_protoc_real)
      message(FATAL_ERROR
        "Mixed native Protobuf toolchain: Protobuf_PROTOC_EXECUTABLE resolves to "
        "${_protobuf_protoc_real}, while protobuf::protoc resolves to "
        "${_protobuf_target_protoc_real}")
    endif()
  endif()

  set(_protobuf_prefix "${EXPECTED_PROTOBUF_PREFIX}")
  if(NOT _protobuf_prefix)
    list(GET Protobuf_INCLUDE_DIRS 0 _protobuf_primary_include)
    get_filename_component(
      _protobuf_primary_include "${_protobuf_primary_include}" REALPATH)
    get_filename_component(_protobuf_include_leaf "${_protobuf_primary_include}" NAME)
    if(NOT _protobuf_include_leaf STREQUAL "include")
      message(FATAL_ERROR
        "Cannot derive the Protobuf installation prefix from "
        "${_protobuf_primary_include}. Set EXPECTED_PROTOBUF_PREFIX explicitly.")
    endif()
    get_filename_component(
      _protobuf_prefix "${_protobuf_primary_include}" DIRECTORY)
  endif()
  get_filename_component(_protobuf_prefix "${_protobuf_prefix}" REALPATH)

  set(_protobuf_prefix_protoc "${_protobuf_prefix}/bin/protoc")
  if(NOT EXISTS "${_protobuf_prefix_protoc}")
    message(FATAL_ERROR
      "Protobuf installation prefix ${_protobuf_prefix} does not contain "
      "bin/protoc")
  endif()
  get_filename_component(
    _protobuf_prefix_protoc "${_protobuf_prefix_protoc}" REALPATH)
  get_filename_component(_protobuf_protoc_real "${_protobuf_protoc}" REALPATH)
  if(NOT _protobuf_protoc_real STREQUAL _protobuf_prefix_protoc)
    message(FATAL_ERROR
      "Mixed native Protobuf installation prefixes: protoc resolves to "
      "${_protobuf_protoc_real}, but headers/runtime use prefix "
      "${_protobuf_prefix} (expected ${_protobuf_prefix_protoc})")
  endif()

  set(_protobuf_library "")
  foreach(_protobuf_location_property
      IMPORTED_LOCATION
      IMPORTED_LOCATION_RELEASE
      IMPORTED_LOCATION_RELWITHDEBINFO
      IMPORTED_LOCATION_DEBUG)
    if(NOT _protobuf_library)
      get_target_property(
        _protobuf_library protobuf::libprotobuf ${_protobuf_location_property})
      if(_protobuf_library MATCHES "-NOTFOUND$")
        set(_protobuf_library "")
      endif()
    endif()
  endforeach()
  if(NOT _protobuf_library)
    set(_protobuf_library "${Protobuf_LIBRARY}")
  endif()
  if(NOT _protobuf_library)
    message(FATAL_ERROR "CMake did not report the Protobuf runtime library location")
  endif()

  get_filename_component(_protobuf_library_real "${_protobuf_library}" REALPATH)
  file(RELATIVE_PATH
    _protobuf_library_relative "${_protobuf_prefix}" "${_protobuf_library_real}")
  if(_protobuf_library_relative STREQUAL ".." OR
     _protobuf_library_relative MATCHES "^\\.\\./")
    message(FATAL_ERROR
      "Mixed native Protobuf installation prefixes: runtime library "
      "${_protobuf_library_real} is outside ${_protobuf_prefix}")
  endif()

  if(DEFINED EXPECTED_PROTOBUF_VERSION AND
     NOT EXPECTED_PROTOBUF_VERSION STREQUAL "" AND
     NOT Protobuf_VERSION VERSION_EQUAL EXPECTED_PROTOBUF_VERSION)
    message(FATAL_ERROR
      "pkg-config reports Protobuf ${EXPECTED_PROTOBUF_VERSION}, while CMake found "
      "${Protobuf_VERSION}")
  endif()

  if(DEFINED EXPECTED_PROTOBUF_INCLUDE_DIR AND
     NOT EXPECTED_PROTOBUF_INCLUDE_DIR STREQUAL "")
    get_filename_component(
      _protobuf_expected_include "${EXPECTED_PROTOBUF_INCLUDE_DIR}" REALPATH)
    set(_protobuf_include_matches FALSE)
    foreach(_protobuf_include IN LISTS Protobuf_INCLUDE_DIRS)
      get_filename_component(_protobuf_include_real "${_protobuf_include}" REALPATH)
      if(_protobuf_include_real STREQUAL _protobuf_expected_include)
        set(_protobuf_include_matches TRUE)
      endif()
    endforeach()
    if(NOT _protobuf_include_matches)
      message(FATAL_ERROR
        "pkg-config reports Protobuf headers in ${_protobuf_expected_include}, while "
        "CMake found ${Protobuf_INCLUDE_DIRS}")
    endif()
  endif()

  if(DEFINED EXPECTED_PROTOBUF_LIBRARY_DIR AND
     NOT EXPECTED_PROTOBUF_LIBRARY_DIR STREQUAL "")
    get_filename_component(_protobuf_library_dir "${_protobuf_library_real}" DIRECTORY)
    get_filename_component(
      _protobuf_expected_library_dir "${EXPECTED_PROTOBUF_LIBRARY_DIR}" REALPATH)
    if(NOT _protobuf_library_dir STREQUAL _protobuf_expected_library_dir)
      message(FATAL_ERROR
        "pkg-config reports Protobuf libraries in ${_protobuf_expected_library_dir}, "
        "while CMake found ${_protobuf_library_real}")
    endif()
  endif()

  message(STATUS "Protobuf protoc: ${_protobuf_protoc} (${_protobuf_protoc_version})")
  message(STATUS "CMake-discovered Protobuf version: ${Protobuf_VERSION}")
  message(STATUS "Protobuf installation prefix: ${_protobuf_prefix}")
  message(STATUS "Protobuf header/include location: ${Protobuf_INCLUDE_DIRS}")
  message(STATUS "Protobuf library location: ${_protobuf_library}")
endfunction()
