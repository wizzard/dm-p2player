# Written by Arno Bakker
# see LICENSE.txt for license information
#
# Utility methods for DB tests.
#

import base64


def testbin2str(bin):
    # Full BASE64-encoded 
    return base64.encodestring(bin).replace("\n","")
    
def teststr2bin(str):
    return base64.decodestring(str)
