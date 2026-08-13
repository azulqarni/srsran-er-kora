set(EDGERIC_PROTO_DIR "${PROJECT_SOURCE_DIR}/lib/protobufs")
set(EDGERIC_PROTO_FILES
    "${EDGERIC_PROTO_DIR}/control_mcs.proto"
    "${EDGERIC_PROTO_DIR}/control_weights.proto"
    "${EDGERIC_PROTO_DIR}/metrics.proto"
    "${EDGERIC_PROTO_DIR}/slice_metrics.proto"
    "${EDGERIC_PROTO_DIR}/slice_budgets.proto"
)

protobuf_generate(
    LANGUAGE cpp
    OUT_VAR EDGERIC_PROTO_GENERATED
    PROTOC_OUT_DIR "${CMAKE_CURRENT_BINARY_DIR}"
    APPEND_PATH
    PROTOS ${EDGERIC_PROTO_FILES}
)

add_library(edgeric STATIC edgeric.cpp ${EDGERIC_PROTO_GENERATED})

target_include_directories(edgeric
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>"
)
target_include_directories(edgeric SYSTEM
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}>"
        ${Protobuf_INCLUDE_DIRS}
        ${ZEROMQ_INCLUDE_DIRS}
        ${CPPZMQ_INCLUDE_DIR}
)

target_link_libraries(edgeric PUBLIC ${ZEROMQ_LIBRARIES} protobuf::libprotobuf)

if (CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    foreach(EDGERIC_PROTO_OUTPUT IN LISTS EDGERIC_PROTO_GENERATED)
        if (EDGERIC_PROTO_OUTPUT MATCHES "\\.pb\\.cc$")
            set_source_files_properties(
                "${EDGERIC_PROTO_OUTPUT}"
                PROPERTIES COMPILE_OPTIONS "-Wno-error;-Wno-array-bounds;-Wno-extra-semi"
            )
        endif ()
    endforeach()
endif ()

# Add to exported libraries if ENABLE_EXPORT is on
ADD_TO_EXPORTED_LIBS(edgeric)
