# Written by Arno Bakker, George Milescu 
# see LICENSE.txt for license information
#
# Razvan Deaconescu, 2008:
#       * corrected problem when running in background
#       * added usage and print_version functions
#       * uses getopt for command line argument parsing

import sys
import shutil
import time
import tempfile
import random
import os
import getopt
from traceback import print_exc

from Tribler.Core.API import *
from Tribler.Core.BitTornado.__init__ import version, report_email

# Print usage message
def usage():
    print "Usage: python overlaymon.py [options]"
    print "Options:"
    print "\t--version"
    print "\t-v\t\t\tprint version and exit"
    print "\t--help"
    print "\t-h\t\t\tprint this help screen"
    print
    print "Report bugs to <" + report_email + ">"

# Print version information
def print_version():
    print version, "<" + report_email + ">"


def sesscb_ntfy_activities(subject,changeType,objectID,*args):
    if subject == NTFY_ACTIVITIES:
        if objectID == NTFY_ACT_MEET:
            print >>sys.stderr,"Met peer",args
        elif objectID == NTFY_ACT_RECOMMEND:
            print >>sys.stderr,"Got BuddyCast",args
        elif objectID == NTFY_ACT_GOT_METADATA:
            print >>sys.stderr,"Collected torrent",args
        else:
            print >>sys.stderr,"Activity",objectID,"args",args

def sesscb_ntfy_torrentinserts(subject, changeType, objectID, *args):
    if subject == NTFY_TORRENTS:
        print >>sys.stderr,"Torrent: objectid",objectID,"args",args
    



def main():
    try:
        # opts = a list of (option, value) pairs
        # args = the list of program arguments left after the option list was stripped
        opts, args = getopt.getopt(sys.argv[1:], "hv", ["help", "version"])
    except getopt.GetoptError, err:
        print str(err)
        usage()
        sys.exit(2)

    # init to default values
    port = random.randint(10000, 65535)
    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit(0)
        elif o in ("-v", "--version"):
            print_version()
            sys.exit(0)
        else:
            assert False, "unhandled option"

    print "Press Ctrl-C to stop the monitor"

    # setup session
    sscfg = SessionStartupConfig()
    statedir = tempfile.mkdtemp()
    sscfg.set_state_dir(statedir)
    sscfg.set_listen_port(port)
    sscfg.set_internal_tracker(False)
    
    sscfg.set_social_networking(False)
    sscfg.set_bartercast(False)
    sscfg.set_torrent_collecting(True) 
    # Arno, 2011-01-19: Make less aggressive first
    sscfg.set_channelcast(False) 
    # Arno, 2011-01-27: Finding out if we're connectable is slow without
    # this. If the superpeer doesn't known we're connectable it won't
    # advertise us to other peers.
    #
    sscfg.set_dialback(True)
    sscfg.set_multicast_local_peer_discovery(False)

    s = Session(sscfg)
    s.add_observer(sesscb_ntfy_activities,NTFY_ACTIVITIES,[NTFY_INSERT])
    #s.add_observer(sesscb_ntfy_torrentinserts,NTFY_TORRENTS,[NTFY_INSERT])
    

    # loop while waiting for CTRL-C (or any other signal/interrupt)
    #
    # - cannot use sys.stdin.read() - it means busy waiting when running
    #   the process in background
    # - cannot use condition variable - that don't listen to KeyboardInterrupt
    #
    # time.sleep(sys.maxint) has "issues" on 64bit architectures; divide it
    # by some value (2048) to solve problem
    #
    try:
        while True:
            time.sleep(sys.maxint/2048)
    except:
        print_exc()

    s.shutdown()
    time.sleep(3)
    shutil.rmtree(statedir)


if __name__ == "__main__":
    main()
