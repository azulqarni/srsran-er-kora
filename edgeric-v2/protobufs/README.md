This directory stores generated Python protobuf stubs used by `edgeric-v2`.

The canonical `.proto` sources live in:

- `/home/keysight/srsRAN_ER/lib/protobufs`

Do not edit generated `*_pb2.py` files by hand. Regenerate them from the
canonical sources with:

```bash
cd /home/keysight/srsRAN_ER/edgeric-v2/protobufs
protoc -I /home/keysight/srsRAN_ER/lib/protobufs \
  --python_out=. \
  metrics.proto \
  slice_metrics.proto \
  slice_budgets.proto \
  control_mcs.proto \
  control_weights.proto
```

Historical local protobuf variants are archived in:

- `/home/keysight/srsRAN_ER/edgeric-v2/protobufs/original`
