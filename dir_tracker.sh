#!/bin/bash

set -e

if [ $# -ne 1 ]
then
  echo "Usage: `basename $0` directory"
  exit 1
fi

export PYTHONPATH=.

TRACKER_PORT=6969

echo "Starting Tracker server .."
python Tribler/Tools/dirtrackerseeder.py --port $TRACKER_PORT $1
echo "Started!"

