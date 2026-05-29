#!/usr/bin/env bash

cd $(dirname $(readlink -m $BASH_SOURCE))

#BUILD_TYPE=Debug
BUILD_TYPE=Release
#BUILD_TYPE=RelWithDebInfo

LINKER_FLAGS='-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold -DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold'

cd srsRAN_ER
./docker/scripts/builder.sh -x ../ccache-latest -DBUILD_TESTS=False -DENABLE_ZEROMQ=ON -DENABLE_DPDK=ON $LINKER_FLAGS -DCMAKE_BUILD_TYPE="$BUILD_TYPE" -DCMAKE_CXX_FLAGS="-march=native" .
