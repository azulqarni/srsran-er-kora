# EdgeRIC Python Protobuf bindings

The canonical, hand-authored schemas live in the repository's
`lib/protobufs/` directory. The setup workflow generates disposable `*_pb2.py`
modules in this directory because the existing EdgeRIC applications import it
directly. Generated modules are ignored and must not be committed; keep
`__init__.py`.

The commands below are shown from the repository root. Create the default
`.venv`, generate the bindings, and run the smoke checks with:

```bash
./scripts/setup_edgeric_venv.sh
```

Use `--venv` consistently when selecting another repository-local environment:

```bash
./scripts/setup_edgeric_venv.sh --venv .venv-custom
source .venv-custom/bin/activate
```

The script locates the repository from its own path, so it can also be invoked
from another current directory with an absolute path. A relative `--venv` value
is still resolved from the repository root.

For an environment that already exists, the maintenance modes are:

```bash
./scripts/setup_edgeric_venv.sh --venv .venv-custom --generate-only
./scripts/setup_edgeric_venv.sh --venv .venv-custom --smoke-only
```

`--generate-only` regenerates the canonical bindings and then runs the smoke
checks without reinstalling packages. `--smoke-only` checks the existing
environment and bindings without regenerating them or reinstalling packages.
Both modes require the selected environment to exist.

Before generation, the helper verifies that `protoc`, pkg-config, CMake's
headers/runtime, and the selected environment's Python Protobuf runtime are
compatible.

`original/` contains historical schema sources. Some differ from the canonical
wire contract; preserve them for reference and never use them as generation
inputs.
