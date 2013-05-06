# Written by Arno Bakker 
# see LICENSE.txt for license information
#

import sys
import os
import shutil
import time
import tempfile
import random
import urllib2
from traceback import print_exc
from threading import Condition
from base64 import encodestring

from Tribler.Core.API import *
import Tribler.Core.BitTornado.parseargs as parseargs
from Tribler.Plugin.AtomFeedParser import RFC3339format2time

argsdef = [('source', '', 'source file or directory'),
           ('tracker', 'http://127.0.0.1:6969/announce', 'tracker URL'),
           ('destdir', '.','dir to save torrent'),
           ('duration', '1:00:00', 'duration of the stream in hh:mm:ss format'),           
           ('piecesize', 32768, 'transport piece size'),
           ('thumb', '', 'filename of image in JPEG format, preferably 171x96'),
           ('url-list', [], 'a URL following BEP19 HTTP Seeding (TODO: support list)'),
            ('url', False, 'Create URL instead of torrent (cannot be used with thumb)'),
            ('cs_keys', '', 
            "Closed swarm torrent keys (semicolon separated if more than one)"),
            ('generate_cs', 'no',
             "Create a closed swarm, generating the keys ('yes' to generate)"),
           ('cs_publish_dir', '.', "Publish public CS key in what dir?"),
           ('merkle', False, 'Create Merkle torrent'),
           ("meta",'',"source of NS Rich Metadata as a file"),
           ("ctime", '', "creation time to store in torrent as RFC3339 time"),
           ("initpeers", '', "a comma-separated list of ip:port addresses of initial peers"),
           ("predefseeds", '', "a comma-separated list of ip:port addresses of predefined seeders")
            ]


def get_usage(defs):
    return parseargs.formatDefinitions(defs,80)

def generate_key(config):
    """
    Generate and a closed swarm key matching the config.  Source is the 
    source of the torrent
    """
    if 'target' in config and config['target']:
        target = os.path.join(params['target'], split(normpath(file))[1])
    else:
        a, b = os.path.split(config['source'])
        if b == '':
            target = a
        else:
            target = os.path.join(a, b)
    target += ".torrent"
    print "Generating key to '%s.tkey' and '%s.pub'"%(target, target)
    
    keypair, pubkey = ClosedSwarm.generate_cs_keypair(target + ".tkey",
                                                      target + ".pub")
    
    return keypair,pubkey

def publish_key(torrent, keypair, target_directory = "."):
 
    t = TorrentDef.load(torrent)
    
    filename = encodestring(t.infohash).replace("\n","")
    filename = filename.replace("/","")
    filename = filename.replace("\\","")
    key_file = os.path.join(target_directory, filename + ".tkey")
    ClosedSwarm.save_cs_keypair(keypair, key_file)
    print "Key saved to:", key_file

def progress(perc):
    print int(100.0*perc),"%",
        
if __name__ == "__main__":

    config, fileargs = parseargs.parseargs(sys.argv, argsdef, presets = {})
    print >>sys.stderr,"config is",config
    
    if config['source'] == '':
        print "Usage:  ",get_usage(argsdef)
        sys.exit(0)
        
    if isinstance(config['source'],unicode):
        usource = config['source']
    else:
        usource = config['source'].decode(sys.getfilesystemencoding())
      
    # Arno, 2011-01-20: Support for storing NS rich metadata in .tstream
    metasource = None
    if config['meta'] != '':
        if isinstance(config['meta'],unicode):
            metasource = config['meta']
        else:
            metasource = config['meta'].decode(sys.getfilesystemencoding())
        
    tdef = TorrentDef()
    if os.path.isdir(usource):
        for filename in os.listdir(usource):
            path = os.path.join(usource,filename)
            tdef.add_content(path,path,playtime=config['duration']) #TODO: only set duration on video file
    else:
        tdef.add_content(usource,playtime=config['duration'])
        
    tdef.set_tracker(config['tracker'])
    tdef.set_piece_length(config['piecesize']) #TODO: auto based on bitrate?

    # CLOSEDSWARM
    cs_keypair = None # Save for publishing later
    if config['generate_cs'].lower() == "yes":
        if config['cs_keys']:
            print "Refusing to generate keys when key is given"
            raise SystemExit(1)
        cs_keypair, cs_pubkey = generate_key(config)
        tdef.set_cs_keys([cs_pubkey])
    elif config['cs_keys']:
        config['cs_keys'] = config['cs_keys'].split(";")
    
    # TODO4DIEGO: DO BE CHANGED TO set_url_list() and support lists of URLs
    if len(config['url-list']) > 0:
        urllist = [config['url-list']]
        tdef.set_urllist(urllist)
    
    if config['url']:
        tdef.set_create_merkle_torrent(1)
        tdef.set_url_compat(1)
    else:
        if len(config['thumb']) > 0:
            tdef.set_thumbnail(config['thumb'])
        # Arno, 2011-01-20: Merkle torrent support
        if config['merkle']:
            print >>sys.stderr,"Creating Merkle torrent"
            tdef.set_create_merkle_torrent(1)

    if metasource is not None:
        metadata = None
        if os.path.isfile(metasource):
            f = file(metasource,"r")
            metadata = f.read()
        else:
            print "Cannot read metadata file: '" + metasource + "'"
        if metadata is not None:
            tdef.set_metadata(metadata)
   
    if config['ctime']:
        tstamp = RFC3339format2time(config['ctime'])
        tdef.set_creation_date(tstamp)

    if config['initpeers']:
        initpeerlist = []
        words = config['initpeers'].split(",")
        for word in words:
            ip,portstr = word.split(':')
            initpeerlist.append([ip,int(portstr)])
        if len(initpeerlist) > 0:
            tdef.set_initial_peers(initpeerlist)

    #PREDEFSEEDS
    if config['predefseeds']:
        predefseeds = []
        words = config['predefseeds'].split(",")
        for word in words:
            ip,portstr = word.split(':')
            predefseeds.append([ip,int(portstr)])
        if len(predefseeds) > 0:
            tdef.set_predef_seeders(predefseeds)

    #
    # Now calculate hashes and encode everything
    #
    tdef.finalize(userprogresscallback=progress)
    
    if config['url']:
        urlbasename = config['source']+'.url'
        urlfilename = os.path.join(config['destdir'],urlbasename)
        f = open(urlfilename,"wb")
        f.write(tdef.get_url())
        f.close()
    else:
        torrentbasename = config['source']+'.tstream'
        torrentfilename = os.path.join(config['destdir'],torrentbasename)
        tdef.save(torrentfilename)
        
    if cs_keypair:
        publish_key(torrentfilename, cs_keypair, config['cs_publish_dir'])
