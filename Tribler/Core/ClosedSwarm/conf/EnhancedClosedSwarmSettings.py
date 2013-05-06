import os
from Tribler.Core.BitTornado.BT1.MessageID import *

DEBUG = True
DEBUG_INFO = False and DEBUG
DEBUG_DEBUG = False and DEBUG
DEBUG_CRITICAL = True
DEBUG_ERROR = True

DEBUG_PEER_DISCOVERY = False and DEBUG
DEBUG_AE = False and DEBUG

# Possible values of the info field
INFO_FIELD = { 0: "An Error has occured",
               1: "Valid request",
               2: "Invalid request because of your requested service properties",
               3: "Invalid request",
               4: "No longer valid request or terminated in favour to other peer",
               5: "Appologies for not having available connection"}

# Possible values of the protocol version
PROTOCOL_VERSION = { 1: "Closed Swarms protocol",
                     2: "Enhanced Closed Swarms protocol"}

ECS_VERSION = 2

# Default maximum number of peers that can be connected for uploading, which support ECS
MAX_ECS_PEERS = 50

# Maximum number of suggested swarm members to a remote peer
MAX_SUGGESTED_MEMBERS = 5

# Size of the AES symmetric key (in bits)
AES_KEY_SIZE = 128

# Mapping from message ids to message numbers
IDS_TO_MESSAGES = {CS_CHALLENGE_A:     '1',
                   CS_CHALLENGE_B:     '2',
                   CS_POA_EXCHANGE_A:  '3',
                   CS_POA_EXCHANGE_B:  '4',
                   CS_INFO_EXCHANGE_B: '5'}

# Possible variable names in the Rules field
GEOLOCATION = "GEOLOCATION"
DAY_HOUR = "DAY_HOUR"
PRIORITY = "PRIORITY"

FORBIDDEN_VARS = [DAY_HOUR, GEOLOCATION]

# GeoIP database path
if "PYTHONPATH" in os.environ:
    GEOIP_DB = os.environ["PYTHONPATH"]+"/Tribler/Core/ClosedSwarm/conf/GeoIP.dat"
else:
    GEOIP_DB = None

