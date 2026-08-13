# EdgeRIC Python Protobuf bindings

The canonical, hand-authored schemas live in the repository's
`lib/protobufs/` directory. The setup workflow generates disposable
`*_pb2.py` modules here because the existing EdgeRIC applications import this
directory directly.

From any current directory, create the venv and generate the bindings with:

```bash
/path/to/srsran-er-kora/scripts/setup_edgeric_venv.sh
```

After initial setup, regenerate or smoke-test without reinstalling packages:

```bash
./scripts/setup_edgeric_venv.sh --generate-only
./scripts/setup_edgeric_venv.sh --smoke-only
```

The generation helper first verifies that `protoc`, pkg-config, CMake's
headers/runtime, and the venv's Python Protobuf runtime are compatible. Generated
modules are ignored and must not be committed. Keep `__init__.py`.

`original/` contains historical schema sources. Some differ from the canonical
wire contract; preserve them for reference and never use them as generation
inputs.
