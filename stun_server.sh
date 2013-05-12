#!/bin/bash

set -e

export PYTHONPATH=.

STUN_PORT=3478
PING_PORT=3479
STUN_IP="0.0.0.0"

echo "Starting STUN server .."
python Tribler/Tools/stunserver.py $STUN_PORT $STUN_IP $STUN_PORT  
echo "Started!"

echo "Starting PingBack server .."
python Tribler/Tools/pingbackserver.py $PING_PORT 
echo "Started!"
