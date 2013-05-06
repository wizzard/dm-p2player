
from Tribler.Core.APIImplementation.miscutils import parse_playtime_to_secs

# Arno: 2011-05-27: bencode doesn't support floats.
assert parse_playtime_to_secs("0") == 0
assert parse_playtime_to_secs("1:00") == 60
assert parse_playtime_to_secs("10:00") == 600
assert parse_playtime_to_secs("10:56:11") == 39371
