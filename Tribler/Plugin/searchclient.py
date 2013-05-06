# Written by Arno Bakker 
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

from Tribler.__init__ import LIBRARYNAME
from Tribler.Core.API import *
from Tribler.Core.Utilities.utilities import show_permid
from Tribler.Core.BitTornado.__init__ import version, report_email
from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue
from Tribler.Plugin.Search import SearchPathMapper, Query2HitsMap
from Tribler.Core.CacheDB.SqliteCacheDBHandler import TorrentDBHandler
from Tribler.Core.TorrentDef import TorrentDef

def usage():
    print "Usage: python searchserver.py [options]"
    print "Options:"
    print "\t--port <port>"
    print "\t-p <port>\t\tuse <port> to listen for connections"
    print "\t\t\t\t(default is random value)"
    print "\t--version"
    print "\t-v\t\t\tprint version and exit"
    print "\t--help"
    print "\t-h\t\t\tprint this help screen"
    print
    print "Report bugs to <" + report_email + ">"

def print_version():
    print version, "<" + report_email + ">"


def sesscb_got_remote_hits(permid,query,hits):
    # Called by SessionCallback thread 

    print >>sys.stderr,"sesscb_got_remote_hits",`hits`
    
def sesscb_got_search_hits(permid,query,hits):
    # Called by SessionCallback thread 

    print >>sys.stderr,"sesscb_got_search_hits",`hits`


def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "hvp:", ["help", "version", "port"])
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
        elif o in ("-p", "--port"):
            port = int(a)
        elif o in ("-v", "--version"):
            print_version()
            sys.exit(0)
        else:
            assert False, "unhandled option"

    print "Press Ctrl-C to stop the server"

    # setup session
    sscfg = SessionStartupConfig()
    statedir = tempfile.mkdtemp()
    sscfg.set_state_dir(statedir)
    sscfg.set_listen_port(port)
    sscfg.set_megacache(True)
    sscfg.set_overlay(True)
    sscfg.set_dialback(False)
    sscfg.set_internal_tracker(False)

    # Hack 2 peer overlay network, pretend searchserver.py is superpeer
    sscfg.set_superpeer_file("mysuperpeer.txt")


    # initialize session and databases
    s = Session(sscfg)
    
    # add example torrents to database
    db = TorrentDBHandler.getInstance()
    torrent_dir = os.path.join(LIBRARYNAME,"Plugin","torrents")
    torrent_coll_dir = s.get_torrent_collecting_dir()
    if os.path.exists(torrent_dir):
        torrentFiles = os.listdir(torrent_dir)
    else: 
        print >>sys.stderr, "Cannot find torrent directory, db will be empty."
    i=0
    for torrent_file in torrentFiles:
        i += 1
        if ".torrent" not in torrent_file:
            continue
        torrent_file = os.path.join(torrent_dir,torrent_file)
        tdef = TorrentDef.load(torrent_file)
        i += 1
        dest_path = os.path.join(torrent_coll_dir,os.path.basename(torrent_file))
        if not os.path.exists(dest_path):
            tdef.save(dest_path)
        db.addExternalTorrent(tdef, extra_info={'filename':dest_path},commit=(i==len(torrentFiles)))
    print >>sys.stderr, "Initialized database with",`i`, "torrents."
    print >>sys.stderr,"Give peer some time to connect to other peers"
    time.sleep(2)
    # search mapper is used to test the rich metadata query
    id2hits = Query2HitsMap()
    tqueue = TimedTaskQueue(nameprefix="BGTaskQueue")
    searchmapper = SearchPathMapper(s,id2hits,tqueue)
    searchmapper.process_search_p2p('title=Memento', True)
    
    #
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
