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


def handler4USERprefix(permid,query,qid,hitscallback):
    """ Tribler is receiving a user-defined query """
    
    print >>sys.stderr,"test: handler4USERprefix: Got",`permid`,`query`,`qid`,`hitscallback`
    
    assert isinstance(permid,str)
    assert isinstance(query,unicode)
    assert callable(hitscallback)

    hits = 'goodbye'
    
    # Send reply
    hitscallback(permid,qid,None,hits)
    
def handler4METADATASEARCHprefix(permid,query,qid,hitscallback):
    """ Tribler is receiving a user-defined query """
    
    print >>sys.stderr,"test: handler4USERprefix: Got",`permid`,`query`,`qid`,`hitscallback`
    
    assert isinstance(permid,str)
    assert isinstance(query,unicode)
    assert callable(hitscallback)

    hits = 'these are my hits...'
    
    # Send reply
    hitscallback(permid,qid,None,hits)




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
    f = open("emptysuperpeer.txt","wb")
    f.write('#')
    f.close()
    sscfg.set_superpeer_file("emptysuperpeer.txt")


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
       
        dest_path = os.path.join(torrent_coll_dir,os.path.basename(torrent_file))
        if not os.path.exists(dest_path):
            tdef.save(dest_path)
        db.addExternalTorrent(tdef, extra_info={'filename':dest_path},commit=(i==len(torrentFiles)))
    print >>sys.stderr, "Initialized database with",`i`, "torrents."
    # initialize a search path mapper
    id2hits = Query2HitsMap()
    tqueue = TimedTaskQueue(nameprefix="BGTaskQueue")
    SearchPathMapper(s,id2hits,tqueue)
    
    # Define a user-defined query prefix
#    qh = {}
#    qh['USER'] = handler4USERprefix
#    qh['METADATASEARCH'] = handler4METADATASEARCHprefix;
#    s.set_user_query_handlers(qh)
    

    # Hack a peer-to-peer overlay network consisting of 1 peer, this peer.
    b64permid = show_permid(s.get_permid())
    # superpeer1.das2.ewi.tudelft.nl, 7001, MFIwEAYHKoZIzj0CAQYFK4EEABoDPgAEAL2I5yVc1+dWVEx3nbriRKJmOSlQePZ9LU7yYQoGABMvU1uGHvqnT9t+53eaCGziV12MZ1g2p0GLmZP9, SuperPeer1@Tribler
    f = open("mysuperpeer.txt","wb")
    f.write("127.0.0.1, ")
    f.write(str(port)+", ")
    f.write(b64permid+", ")
    f.write("searchserverX")
    f.close()
    

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
