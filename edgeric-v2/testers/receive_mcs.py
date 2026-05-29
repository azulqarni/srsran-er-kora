import time

import os
import sys

import zmq

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from protobufs import control_mcs_pb2

context = zmq.Context()
subscriber_mcs_socket = context.socket(zmq.SUB)
subscriber_mcs_socket.setsockopt_string(zmq.SUBSCRIBE, "")
subscriber_mcs_socket.connect("ipc:///tmp/control_mcs_actions")

def process_mcs_message(message):
    mcs_msg = control_mcs_pb2.mcs_control()
    mcs_msg.ParseFromString(message)
    print(f"Received MCS Control - Ran Index: {mcs_msg.ran_index}")
    for i in range(0, len(mcs_msg.mcs), 2):
        rnti = mcs_msg.mcs[i]
        mcs_value = mcs_msg.mcs[i + 1]
        print(f"  RNTI: {rnti}, MCS: {mcs_value}")

while True:
    time.sleep(0.001)
    try:
        message = subscriber_mcs_socket.recv()
        process_mcs_message(message)
    except zmq.Again:
        pass
    except Exception as e:
        print(f"Error receiving MCS Control: {e}")
