# Written by Arno Bakker, Diego Rabioli
# see LICENSE.txt for license information

#
# TODO:
#   - Switch to SIMPLE+METADATA query
#
#   - adjust SIMPLE+METADATA such that it returns P2PURLs if possible.

#   - DO NOT SAVE P2PURLs as .torrent, put in 'torrent_file_name' field in DB.
#
#   - Implement continuous dump of results to JS. I.e. push sorting and 
#     rendering to browser.
#       * One option is RFC5023: Atom Pub Proto, $10.1 "Collecting Partial 
#       Lists" I.e. return a partial list and add a 
#            <link ref="next" href="/.../next10> tag pointing
#       to the next set. See http://www.iana.org/assignments/link-relations/link-relations.xhtml
#       for def of next/first/last, etc. link relations.
#
#        Arno, 2009-10-10: we current add such a <link ref="next" link,
#        which contains a URL that will give all hits found so far. So
#        people should poll this URL.
#
#  - Background thread to save torrentfiles to localdb.
#        Arno, 2009-12-03: Now offloaded to a new TimedTaskQueue.
# 
#
#  - garbage collect hits at connection close. 
#     Not vital, current mechanism will GC. 
#        
#  - Support for multifile torrents
#
#  - BuddyCast hits: Create LIVE MPEG7 fields for live (i.e., livetimepoint) 
#    and VOD MPEG7 fields for VOD. 
#
#  - Use separate HTTP server, Content-serving one needs to be single-threaded
#    at the moment to prevent concurrent RANGE queries on same stream from VLC.
#    Alternative is to put a Condition variable on a content stream.
#
#       Arno, 2009-12-4: I've added locks per content URL and made 
#       VideoHTTPServer multithreaded and it now also serves the search traffic.
#
#  - Debug hanging searches on Windows. May be due to "incomplete outbound TCP 
#    connection" limit, see Encrypter.py :-( I get timeouts opening the feeds
#    listed in the metafeed, whilst the feed server is responding fast.
#    Lowering Encrypter's MAX_INCOMPLETE doesn't help. Alt is to periodically
#    parse the feeds and store the results. 
#
#       Arno, 2009-12-4: Problem still exists. Note that TCP limit has been
#       lifted on Windows > Vista SP2.
#
#  - Update VLC plugin-1.0.1 such that it doesn't show a video window when
#    target is empty.
#
#       Arno, 2009-12-4: At the moment, setting the window size to (0,0) and
#       not providing a URL of a torrent works.
# 
# - query_connected_peers() now returns Unicode names, make sure these are
#   properly handled when generating HTML output.


import sys
import time
import random
import urllib
import urlparse
import cgi
import binascii
import copy
import re
import os
from cStringIO import StringIO
from traceback import print_exc,print_stack
from threading import RLock
from base64 import b64encode, encodestring

from xml.etree.ElementTree import Element, SubElement, Comment, tostring
#from ElementTree_pretty import prettify

from Tribler.__init__ import LIBRARYNAME
from Tribler.Core.API import *
from Tribler.Core.BitTornado.bencode import *
from Tribler.Core.Utilities.utilities import get_collected_torrent_filename
from Tribler.Core.simpledefs import *
from Tribler.Core.CacheDB.SqliteCacheDBHandler import RichMetadataDBHandler
from Tribler.Video.VideoServer import AbstractPathMapper
from Tribler.Plugin.defs import *
from Tribler.Plugin.AtomFeedParser import *
from Tribler.Core.Statistics.Status import Status
from Tribler.Core.Statistics.Status.Status import NSPCLOG


DEBUG = False
DEBUGLOCK = False

# Arno, 2011-01-28: Fetch .tstream too. Alt is to fetch this on-demand via
# Session.download_torrent_from_peer() when user selects this item for
# playback.
#
P2PQUERYTYPE = "SIMPLE+METADATA"
# user defined query type for the P2P Rich metadata search
P2PRICHTMETAQUERYTYPE = "RICHMETA+METADATA"#


def streaminfo404():
    return {'statuscode':404, 'statusmsg':'404 Not Found'}

def streaminfo501():
    return {'statuscode':501, 'statusmsg':'501 Not Implemented: No overlay network'}



class SearchPathMapper(AbstractPathMapper):
    
    def __init__(self,session,id2hits,tqueue,schemeauth):
        self.session = session
        self.id2hits = id2hits
        self.tqueue = tqueue
        self.schemeauth = schemeauth
        
        self.metafp = None
        self.metafeedurl = None
      
    def get(self,urlpath):
        """
        Possible paths:
        /search<application/x-www-form-urlencoded query> -> search request
        /search/opensearch.xml -> OpenSearch description request (when engine is added)
        /search/favicon.ico -> Favicon for OpenSearch engine (when engine is added)
        """
        if not urlpath.startswith(URLPATH_SEARCH_PREFIX):
            return streaminfo404()
        if DEBUG:
            print >>sys.stderr, "bg: search: searchmap got request: "+urlpath
            
        fakeurl = 'http://127.0.0.1'+urlpath
        o = urlparse.urlparse(fakeurl)
        # Serve OpenSearch description file
        if urlpath.startswith(URLPATH_SEARCH_PREFIX+'/opensearch.xml'):
            path = os.path.join(self.session.get_install_dir(),LIBRARYNAME,"Plugin","opensearch.xml")
            response = open(path,'r')
            length = os.path.getsize(path)
            #response = '<?xml version="1.0"?><OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/"><ShortName>P2P-next search</ShortName><Description>Search P2P-next buddies for content.</Description><Image height="16" width="16" type="image/x-icon">http://de.wikipedia.org/favicon.ico</Image> <Url type="application/atom+xml" method="get" template="http://127.0.0.1:6878/search?q={searchTerms}&amp;collection=buddycast&amp;advq=on"/></OpenSearchDescription>'
            #length = len(response)
            #response = StringIO(response)
            return { 'statuscode':200,'mimetype': 'text/xml', 'length': length, 'stream': response};
        # Serve favicon for OpenSearch engine
        if urlpath.startswith(URLPATH_SEARCH_PREFIX+"/favicon.ico"):
            path = os.path.join(self.session.get_install_dir(),LIBRARYNAME,"Plugin","favicon.ico")
            response = open(path,'rb')
            length = os.path.getsize(path)
            return { 'statuscode':200,'mimetype': 'text/xml', 'length': length, 'stream': response};
        
        #search request
        qdict = cgi.parse_qs(o[4])
        if DEBUG:
            print >>sys.stderr,"bg: search: qdict",qdict
        
        searchstr = qdict['q'][0]
        searchstr = searchstr.strip()
        collection = qdict['collection'][0]
        if collection == "metafeed":
            metafeedurl = qdict['metafeed'][0]
        advQuery = qdict.get('advq',False);

        print >>sys.stderr,"\nbg: search: Got search for",`searchstr`,"in",collection
        
        # Garbage collect:
        self.id2hits.garbage_collect_timestamp_smaller(time.time() - HITS_TIMEOUT)

        
        if collection == "metafeed":
            if not self.check_reload_metafeed(metafeedurl):
                return {'statuscode':504, 'statusmsg':'504 MetaFeed server did not respond'}
            return self.process_search_metafeed(searchstr)
        else:
            return self.process_search_p2p(searchstr, advQuery)
            


    def process_search_metafeed(self,searchstr):
        """ Search for hits in the ATOM feeds we got from the meta feed """

        allhits = []
        for feedurl in self.metafp.get_feedurls():
            feedp = FeedParser(feedurl)
            try:
                feedp.parse()
            except:
                # TODO: return 504 gateway error if none of the feeds return anything
                print_exc()
            hits = feedp.search(searchstr)
            allhits.extend(hits)
        
        for hitentry in allhits:
            titleelement = hitentry.find('{http://www.w3.org/2005/Atom}title')
            print >>sys.stderr,"bg: search: meta: Got hit",titleelement.text

        
        id = str(random.random())[2:]
        atomurlpathprefix = self.schemeauth+URLPATH_HITS_PREFIX+'/'+str(id)
        atomxml = feedhits2atomxml(allhits,searchstr,atomurlpathprefix)
        
        atomstream = StringIO(atomxml)
        atomstreaminfo = { 'statuscode':200,'mimetype': 'application/atom+xml', 'stream': atomstream, 'length': len(atomxml)}

        return atomstreaminfo


    def process_search_p2p(self,searchstr,richmetaquery=False):
        """ Search for hits in local database and perform remote query. 
        EXPERIMENTAL: needs peers with SIMPLE+METADATA query support.
        """
        
        if richmetaquery == False:
            # Simple case, searchstr = keywords
            q = P2PQUERYTYPE+' '+searchstr
        else:
            # User query based on Rich Metadata
            q = P2PRICHTMETAQUERYTYPE +' ' +searchstr
               
        id = str(random.random())[2:]
        self.id2hits.add_query(id,searchstr,time.time(),richmetaquery=richmetaquery)

        self.st = time.time()
        if self.session.get_remote_query(): # Arno: actually means we reply to remote, but anyway
            # Parallel:  initiate remote query
            if DEBUG:
                print >>sys.stderr,"bg: search: Send remote query for",q
            if richmetaquery == False: # hits callback for SIMPLE {+METADATA} search
                got_remote_hits_lambda = lambda permid,query,remotehits:self.sesscb_got_remote_hits(id,permid,query,remotehits)
            else: # hits callback for Rich Metadata search
                got_remote_hits_lambda = lambda permid,query,remotehits:self.sesscb_got_richmeta_remote_hits(id,permid,query,remotehits)
            self.session.query_connected_peers(q,got_remote_hits_lambda,max_peers_to_query=20)

        # Query local DB while waiting
        localhits = query_localdb(self.session,searchstr,richmetaquery)
        print >>sys.stderr,"bg: search: Local hits",len(localhits)
        self.id2hits.add_hits(id,localhits)

        atomurlpathprefix = self.schemeauth+URLPATH_HITS_PREFIX+'/'+str(id)
        nextlinkpath = atomurlpathprefix  

        ret = None
        if True: 
            # Return ATOM feed directly
            atomhits = hits2atomhits(localhits,atomurlpathprefix)
            atomxml = atomhits2atomxml(atomhits,searchstr,atomurlpathprefix,nextlinkpath=nextlinkpath)
            
            atomstream = StringIO(atomxml)
            atomstreaminfo = { 'statuscode':200,'mimetype': 'application/atom+xml', 'stream': atomstream, 'length': len(atomxml)}
            
            ret = atomstreaminfo
        else:
            # Return redirect to ATOM feed URL, this allows us to do a page 
            # page reload to show remote queries that have come in (DEMO)
            streaminfo = { 'statuscode':301,'statusmsg':nextlinkpath }
            ret = streaminfo
            
        # Arno, 2011-06-20: Check after N seconds if the search resulted in 
        # playback, and log that.
        self.nspclog_start_timer(id)
        return ret
        

    def sesscb_got_remote_hits(self,id,permid,query,remotehits):
        # Called by SessionCallback thread 
        try:
            
            et = time.time()
            diff = et - self.st
            print >>sys.stderr,"bg: search: Got",len(remotehits),"remote hits" # ,"after",diff

            hits = remotehits2hits(remotehits)
            self.id2hits.add_hits(id,hits,permid=permid,Tsearch=diff)
        
            if P2PQUERYTYPE=="SIMPLE+METADATA": 
                bgsearch_save_remotehits_lambda = lambda:self.tqueue_save_remote_hits(remotehits) 
                self.tqueue.add_task(bgsearch_save_remotehits_lambda,0)
            
        except:
            print_exc()
    
    # handler for P2P Rich metadata search hits
    def sesscb_got_richmeta_remote_hits(self,id,permid,query,remotehits):
        # Called by SessionCallback thread 
        try:
            et = time.time()
            diff = et - self.st
            hits = remotehits2hits(remotehits)
            
            print >>sys.stderr,"bg: search: Got",len(remotehits),"remote hits" ,"after",diff,"kept",len(hits)

            self.id2hits.add_hits(id,hits,permid=permid,Tsearch=diff)
        
            bgsearch_save_remotehits_lambda = lambda:self.tqueue_save_remote_hits(remotehits) 
            self.tqueue.add_task(bgsearch_save_remotehits_lambda,0)
            
        except:
            print_exc()


    def check_reload_metafeed(self,metafeedurl):
        if self.metafeedurl is None or self.metafeedurl != metafeedurl:
            self.metafp = MetaFeedParser(metafeedurl)
            try:
                self.metafp.parse() # TODO: offload to separate thread?
                print >>sys.stderr,"bg: search: meta: Found feeds",self.metafp.get_feedurls()
                self.metafeedurl = metafeedurl
            except:
                print_exc()
                return False
            
        return True
                
    def tqueue_save_remote_hits(self,remotehits):
        """ Save .torrents received from SIMPLE+METADATA query on a separate
        thread.
        Run by TimedTaskQueueThread
        """
        torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)        
        extra_info = {'status':'good'}
        
        n = len(remotehits)
        count = 0
        commit = False
        for infohash,remotehit in remotehits.iteritems():
            if count == n-1:
                commit = True
            try:
                torrentpath = self.tqueue_save_collected_torrent(remotehit['metatype'],remotehit['metadata'])
                # craffels, BUGFIX: addExternalTorrent now needs a TorrentDef
                torrentdef = TorrentDef.load(torrentpath)
                # Arno, 2011-05-23: Errr... set filename always
                extra_info['filename'] = torrentpath
                torrent_db.addExternalTorrent(torrentdef, source='BC', extra_info=extra_info, commit=commit)
            except:
                print_exc()
            count += 1
            
        self.session.close_dbhandler(torrent_db)

    def tqueue_save_collected_torrent(self,metatype,metadata):
        """ Run by TimedTaskQueueThread """
        if metatype == URL_MIME_TYPE:
            tdef = TorrentDef.load_from_url(metadata)
        else:
            metainfo = bdecode(metadata)
            tdef = TorrentDef.load_from_dict(metainfo)

        infohash = tdef.get_infohash()
        colldir = self.session.get_torrent_collecting_dir()
        
        filename = get_collected_torrent_filename(infohash)
        torrentpath = os.path.join(colldir, filename)
        
        if DEBUG:
            print >>sys.stderr,"bg: search: saving remotehit",torrentpath
        tdef.save(torrentpath)
        return torrentpath

    #
    # ULANC logging
    #
    def nspclog_start_timer(self,id):
        """ Schedule a check to see if the user started playback on one of the hits """
        nspclog_check_playback_lambda = lambda:self.tqueue_nspclog_check_playback(id) 
        self.tqueue.add_task(nspclog_check_playback_lambda,20)
        
    def nspclog_register_playback(self,torrenturl,infohash):
        """ Called when a download is started to register whether the torrent
        played came from a search
        """
        atomurlpathprefix = self.schemeauth+URLPATH_HITS_PREFIX
        if torrenturl.startswith(atomurlpathprefix):
            p = urlparse.urlparse(torrenturl)
            sidx = len(URLPATH_HITS_PREFIX)+1
            idstuff = p.path[sidx:] 
            id = idstuff[0:idstuff.find('/')]
            try:
                self.id2hits.nspclog_add_playback(id,infohash)
                # Send event immediately
                self.tqueue_nspclog_check_playback(id)
                self.id2hits.nspclog_set_logged(id)
                
            except:
                print_exc()
    
    
    def tqueue_nspclog_check_playback(self,id):
        """ Check if a hit from query with id has resulted in playback. """
        
        # Arno, 2011-02-03: Use NSPCLOG SPEC
        if NSPCLOG:
            if self.id2hits.nspclog_get_logged(id):
                return # already logged on playback
            try:
                searchstr = self.id2hits.get_searchstr(id)
                hits = self.id2hits.get_hits(id)
                peers = self.id2hits.nspclog_get_peers(id)
                infohash = self.id2hits.nspclog_get_playback(id)

                thits = sorted(hits.values(),cmp=sort_hit_time_desc)
                if len(thits) > 0:
                    lasthittime = thits[0]['time']
                    timesincelasthit = int(time.time() - lasthittime)
                else:
                    timesincelasthit = 2 ** 32 # never
                
                print >>sys.stderr,"bg: search: Logging search",`searchstr`,"nhits",len(hits),"npeers",len(peers),"played",`infohash`
                
                d = {}
                d['context'] = 'DECENTRALIZED'
                d['scope'] = ''
                d['words'] = searchstr
                if infohash is not None:
                    d['resulted_in_playback'] = b64encode(infohash)
                else:
                    d['resulted_in_playback'] = ''
                d['results_complete'] = timesincelasthit 
                d['num_results'] = len(hits)
                peerlist = []
                for permid,prec in peers.iteritems():
                    p = {}
                    p['permid'] = encodestring(permid).replace("\n","")
                    p['time'] = prec['Tsearch']
                    p['results'] = prec['nhits']
                    w = {}
                    w['peer'] = p
                    peerlist.append(w)
                d['peerlist'] = peerlist
                
                event_reporter = Status.get_status_holder("LivingLab")
                if event_reporter is not None:
                    w = {}
                    w['search'] = d 
                    values = [w]
                    event_reporter.create_and_add_event("search", values )
            except:
                print_exc() 


def sort_hit_time_desc(a,b):
    """ Sort hits in common hit format by time it came in, descending """
    if a['time'] < b['time']:
        return 1
    elif a['time'] > b['time']:
        return -1
    else:
        return 0


def query_localdb(session,searchstr,richmetaquery):
    if richmetaquery == False:
        torrent_db = session.open_dbhandler(NTFY_TORRENTS)
        if torrent_db is None:
            return streaminfo501()
        keywords = searchstr.split()
        localdbhits = torrent_db.searchNames(keywords)
        session.close_dbhandler(torrent_db)
    else:
        richmetadata_db = session.open_dbhandler(NTFY_NS_RICHMETADATA)
        if richmetadata_db is None:
            return streaminfo501()
        localdbhits = richmetadata_db.search(searchstr)
        session.close_dbhandler(richmetadata_db)
    
    # Convert list to dict keyed by infohash
    localhits = localdbhits2hits(localdbhits)
    return localhits


def localdbhits2hits(localdbhits):
    hits = {}
    for dbhit in localdbhits:
        localhit = {}
        localhit['hittype'] = "localdb"
        localhit['time'] = time.time()
        localhit.update(dbhit)
        infohash = dbhit['infohash'] # convenient to also have in record
        hits[infohash] = localhit
    return hits


def remotehits2hits(remotehits):
    hits = {}
    for infohash,hit in remotehits.iteritems():
        
        #print >>sys.stderr,"remotehit2hits: keys",hit.keys()

        remotehit = {}
        remotehit['hittype'] = "remote"
        remotehit['time'] = time.time()
        #remotehit['query_permid'] = permid # Bit of duplication, ignore
        remotehit['infohash'] = infohash  # convenient to also have in record
        remotehit.update(hit)

        # HACK until we use SIMPLE+METADATA: Create fake torrent file
        if not 'metadata' in hit:
            metatype = TSTREAM_MIME_TYPE
            metadata = hack_make_default_merkletorrent(hit['content_name'])
            remotehit['metatype'] = metatype
            remotehit['metadata'] = metadata
        else:
            # Arno, 2011-05-23: Check sanity of reply
            metainfo = bdecode(hit['metadata'])
            tdef = TorrentDef.load_from_dict(metainfo)
            ih2 = tdef.get_infohash()
            if infohash != ih2:
                # Hit bad, infohash in record doesn't match attached .tstream's
                # due to UNIKLU bug in SearchPathMapper.tqueue_save_remote_hits 
                continue
        
        hits[infohash] = remotehit
    return hits


class Query2HitsMap:
    """ Stores localdb and remotehits in common hits format, i.e., each
    hit has a 'hittype' attribute that tells which type it is (localdb or remote).
    This Query2HitsMap is passed to the Hits2AnyPathMapper, which is connected
    to the internal HTTP server. 
    
    The HTTP server will then forward all "/hits" GET requests to this mapper.
    The mapper then dynamically generates the required contents from the stored
    hits, e.g. an ATOM feed, MPEG7 description, .torrent file and thumbnail
    images from the torrent.
    """

    def __init__(self):
        self.lock = RLock()
        self.d = {}

        
    def add_query(self,id,searchstr,timestamp,richmetaquery=False):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock1",id
        self.lock.acquire()
        try:
            qrec = self.d.get(id,{})
            qrec['searchstr'] = searchstr
            qrec['timestamp'] = timestamp
            qrec['richmetaquery'] = richmetaquery
            qrec['playback_infohash'] = None # NSPCLOG
            qrec['hitlist'] = {}  # maps infohash to common hit record
            qrec['peers'] = {}    # maps permid to prec = {'nhits':, 'Tsearch']
            qrec['nspclogged'] = False
            self.d[id] = qrec
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock1"
            self.lock.release()

        
    def add_hits(self,id,hits,permid=None,Tsearch=None):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock2",id,len(hits)
        self.lock.acquire()
        try:
            qrec = self.d[id]
            # Arno, 2011-05-23: Only overwrite remote hit with localdb hit,
            # remote hits could theoretically arrive sooner.
            for infohash,hit in hits.iteritems():
                if infohash not in qrec['hitlist'] or qrec['hitlist'][infohash]['hittype'] != 'localdb':
                    qrec['hitlist'][infohash] = hit
                    
            if permid is not None:
                prec = {}
                prec['Tsearch'] = Tsearch
                prec['nhits'] = len(hits)
                qrec['peers'][permid] = prec
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock2"
            self.lock.release()
            
    def get_hits(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock3",id
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return copy.copy(qrec['hitlist']) # return shallow copy
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock3"
            self.lock.release()

    def get_searchstr(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock4"
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return qrec['searchstr']
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock4"
            self.lock.release()

    def get_richmetaquery(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock5"
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return qrec['richmetaquery']
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock5"
            self.lock.release()


    def garbage_collect_timestamp_smaller(self,timethres):
        self.lock.acquire()
        try:
            idlist = []
            for id,qrec in self.d.iteritems():
                if qrec['timestamp'] < timethres:
                    idlist.append(id)
            for id in idlist:
                del self.d[id]
        finally:
            self.lock.release()
            

    def nspclog_add_playback(self,id,infohash):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock6"
        self.lock.acquire()
        try:
            qrec = self.d[id]
            qrec['playback_infohash'] = infohash
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock6"
            self.lock.release()
        

    def nspclog_get_playback(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock7"
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return qrec['playback_infohash']
        except KeyError,e:
            return None
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock7"
            self.lock.release()

    def nspclog_get_peers(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock8",id
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return copy.copy(qrec['peers']) # return shallow copy
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock8"
            self.lock.release()


    def nspclog_set_logged(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock9",id
        self.lock.acquire()
        try:
            qrec = self.d[id]
            qrec['nspclogged'] = True
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock9"
            self.lock.release()

    def nspclog_get_logged(self,id):
        if DEBUGLOCK:
            print >>sys.stderr,"q2h: lock10",id
        self.lock.acquire()
        try:
            qrec = self.d[id]
            return qrec['nspclogged']
        finally:
            if DEBUGLOCK:
                print >>sys.stderr,"q2h: unlock10"
            self.lock.release()



class Hits2AnyPathMapper(AbstractPathMapper):
    """ See Query2Hits description """
    
    def __init__(self,session,id2hits,schemeauth):
        self.session = session
        self.id2hits = id2hits
        self.schemeauth = schemeauth
        
    def get(self,urlpath):
        """ 
        Possible paths:
        /hits/id -> ATOM feed
        /hits/id/infohash.xml  -> MPEG 7
        /hits/id/infohash.tstream -> Torrent file
        /hits/id/infohash.tstream/thumbnail -> Thumbnail
        """
        if not urlpath.startswith(URLPATH_HITS_PREFIX):
            return streaminfo404()

        if DEBUG:
            print >>sys.stderr,"hitsmap: Got",urlpath


        paths = urlpath.split('/')
        if len(paths) < 3:
            return streaminfo404()
        
        id = paths[2]
        if len(paths) == 3:
            # ATOM feed
            searchstr = self.id2hits.get_searchstr(id)
            
            # Arno, 2011-05-23: Reread saved remote hits now in DB into hitslist
            richmetaquery = self.id2hits.get_richmetaquery(id)
            if richmetaquery:
                localhits = query_localdb(self.session,searchstr,richmetaquery)
                print >>sys.stderr,"hitsmap: Current local hits",len(localhits)
                self.id2hits.add_hits(id,localhits)
            
            hits = self.id2hits.get_hits(id)

            if DEBUG:
                print >>sys.stderr,"hitsmap: Found total",len(hits),"hits"

            
            atomhits = hits2atomhits(hits,urlpath)

            if DEBUG:
                print >>sys.stderr,"hitsmap: Found",len(atomhits),"atomhits"
            
            
            atomxml = atomhits2atomxml(atomhits,searchstr,urlpath)
            
            #if DEBUG:
            #    print >>sys.stderr,"hitsmap: atomstring is",`atomxml`
                
            atomstream = StringIO(atomxml)
            atomstreaminfo = { 'statuscode':200,'mimetype': 'application/atom+xml', 'stream': atomstream, 'length': len(atomxml)}
            return atomstreaminfo
        
        elif len(paths) >= 4:
            # Either NS Metadata, Torrent file, or thumbnail
            urlinfohash = paths[3]
            
            if DEBUG:
                print >>sys.stderr,"hitsmap: path3 is",urlinfohash
            
            if urlinfohash.endswith(URLPATH_TORRENT_POSTFIX):
                # Torrent file, or thumbnail
                coded = urlinfohash[:-len(URLPATH_TORRENT_POSTFIX)]
                infohash = urlpath2infohash(coded)
            else:
                # NS Metadata / MPEG7
                coded = urlinfohash[:-len(URLPATH_NSMETA_POSTFIX)]
                infohash = urlpath2infohash(coded)
            
            # Check if hit:
            hits = self.id2hits.get_hits(id)
            
            if DEBUG:
                print >>sys.stderr,"hitsmap: meta: Found",len(hits),"hits"
            
            hit = hits.get(infohash,None)
            if hit is not None:
                if len(paths) == 5:
                    # Thumbnail
                    return self.get_thumbstreaminfo(infohash,hit)
                
                elif urlinfohash.endswith(URLPATH_TORRENT_POSTFIX):
                    # Torrent file
                    return self.get_torrentstreaminfo(infohash,hit)
                else:
                    # NS Metadata / MPEG7
                    hiturlpathprefix = self.schemeauth+URLPATH_HITS_PREFIX+'/'+id
                    return self.get_nsmetastreaminfo(infohash,hit,hiturlpathprefix,urlpath)
        return streaminfo404()

    def get_torrentstreaminfo(self,infohash,hit):
        
        if DEBUG:
            print >>sys.stderr,"hitmap: get_torrentstreaminfo",infohash2urlpath(infohash)
        
        torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)
        try:
            if hit['hittype'] == "localdb":
                
                dbhit = torrent_db.getTorrent(infohash,include_mypref=False)
                
                colltorrdir = self.session.get_torrent_collecting_dir()
                # craffels, BUGFIX: db stores absolute path
                filepath = dbhit['torrent_file_name']
                #filepath = os.path.join(colltorrdir,dbhit['torrent_file_name'])
                
                # Return stream that contains torrent file
                stream = open(filepath,"rb")
                length = os.path.getsize(filepath)
                torrentstreaminfo = {'statuscode':200,'mimetype':TSTREAM_MIME_TYPE,'stream':stream,'length':length}
                return torrentstreaminfo
            else:
                if hit['metatype'] == URL_MIME_TYPE:
                    # Shouldn't happen, P2PURL should be embedded in atom
                    return streaminfo404()
                else:
                    stream = StringIO(hit['metadata'])
                    length = len(hit['metadata'])
                    torrentstreaminfo = {'statuscode':200,'mimetype':TSTREAM_MIME_TYPE,'stream':stream,'length':length}
                    return torrentstreaminfo
        finally:
            self.session.close_dbhandler(torrent_db)

    def get_thumbstreaminfo(self,infohash,hit):
        
        if DEBUG:
            print >>sys.stderr,"hitmap: get_thumbstreaminfo",infohash2urlpath(infohash)
        
        torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)
        try:
            if hit['hittype'] == "localdb":
                dbhit = torrent_db.getTorrent(infohash,include_mypref=False)
                
                colltorrdir = self.session.get_torrent_collecting_dir()
                filepath = os.path.join(colltorrdir,dbhit['torrent_file_name'])
                tdef = TorrentDef.load(filepath)
                (thumbtype,thumbdata) = tdef.get_thumbnail()
                return self.create_thumbstreaminfo(thumbtype,thumbdata)
                    
            else:
                if hit['metatype'] == URL_MIME_TYPE:
                    # Shouldn't happen, not thumb in P2PURL
                    return streaminfo404()
                else:
                    if DEBUG:
                        print >>sys.stderr,"hitmap: get_thumbstreaminfo: looking for thumb in remote hit"
                    
                    metainfo = bdecode(hit['metadata'])
                    tdef = TorrentDef.load_from_dict(metainfo)
                    (thumbtype,thumbdata) = tdef.get_thumbnail()
                    return self.create_thumbstreaminfo(thumbtype,thumbdata)
        finally:
            self.session.close_dbhandler(torrent_db)


    def create_thumbstreaminfo(self,thumbtype,thumbdata):
        if thumbtype is None:
            return streaminfo404()
        else:
            # Return stream that contains thumb
            stream = StringIO(thumbdata)
            length = len(thumbdata)
            thumbstreaminfo = {'statuscode':200,'mimetype':thumbtype,'stream':stream,'length':length}
            return thumbstreaminfo

    def get_nsmetastreaminfo(self,infohash,hit,hiturlpathprefix,hitpath):
        colltorrdir = self.session.get_torrent_collecting_dir()
        nsmetahit = hit2nsmetahit(hit,hiturlpathprefix,colltorrdir)
        
        if DEBUG:
            print >>sys.stderr,"hitmap: get_nsmetastreaminfo: nsmetahit is",`nsmetahit`
        
        nsmetarepr = nsmetahit2nsmetarepr(nsmetahit,hitpath)
        nsmetastream = StringIO(nsmetarepr)
        nsmetastreaminfo = { 'statuscode':200,'mimetype': 'text/xml', 'stream': nsmetastream, 'length': len(nsmetarepr)}
        return nsmetastreaminfo


#
# Functions
#

def infohash2urlpath(infohash):
    
    if len(infohash) != 20:
        raise ValueError("infohash len 20 !=" + str(len(infohash)))
    
    hex = binascii.hexlify(infohash)
    if len(hex) != 40:
        raise ValueError("hex len 40 !=" + str(len(hex)))
    
    return hex
    
def urlpath2infohash(hex):

    if len(hex) != 40:
        raise ValueError("hex len 40 !=" + str(len(hex)) + " " + hex)

    infohash = binascii.unhexlify(hex)
    if len(infohash) != 20:
        raise ValueError("infohash len 20 !=" + str(len(infohash)))
    
    return infohash


def hits2atomhits(hits,urlpathprefix):
    atomhits = {}
    for infohash,hit in hits.iteritems():
        if hit['hittype'] == "localdb":
            atomhit = localdbhit2atomhit(hit,urlpathprefix)
            atomhits[infohash] = atomhit
        else:
            atomhit = remotehit2atomhit(hit,urlpathprefix)
            atomhits[infohash] = atomhit
            
    return atomhits
            

def localdbhit2atomhit(dbhit,urlpathprefix):
    atomhit = {}
    atomhit['title'] = htmlfilter(dbhit['name'].encode("UTF-8"))
    atomhit['summary'] = htmlfilter(dbhit['comment'].encode("UTF-8"))
    if dbhit['thumbnail']:
        urlpath = urlpathprefix+'/'+infohash2urlpath(dbhit['infohash'])+URLPATH_TORRENT_POSTFIX+URLPATH_THUMBNAIL_POSTFIX
        atomhit['p2pnext:image'] = urlpath
    
    return atomhit

def remotehit2atomhit(remotehit,urlpathprefix):
    # TODO: make RemoteQuery return full DB schema of TorrentDB
    
    #print >>sys.stderr,"remotehit2atomhit: keys",remotehit.keys()
    
    atomhit = {}
    atomhit['title'] = htmlfilter(remotehit['content_name'].encode("UTF-8"))
    atomhit['summary'] = "Seeders: "+str(remotehit['seeder'])+" Leechers: "+str(remotehit['leecher'])
    if remotehit['metatype'] != URL_MIME_TYPE:
        # TODO: thumbnail, see if we can detect presence (see DB schema remark). 
        # Now we assume it's always there if not P2PURL
        urlpath = urlpathprefix+'/'+infohash2urlpath(remotehit['infohash'])+URLPATH_TORRENT_POSTFIX+URLPATH_THUMBNAIL_POSTFIX
        atomhit['p2pnext:image'] = urlpath

    return atomhit

def htmlfilter(s):
    """ Escape characters to which HTML parser is sensitive """
    if s is None:
        return ""
    news = s
    news = news.replace('&','&amp;')
    news = news.replace('<','&lt;')
    news = news.replace('>','&gt;')
    return news

def xmlfilter(s):
    """ Escape characters to which XML parser is sensitive """
    if s is None:
        return ""
    news = s
    news = news.replace('&','&amp;')
    news = news.replace('<','&lt;')
    news = news.replace('>','&gt;')
    news = news.replace('"','&quot;')
    news = news.replace("'",'&apos;')
    return news

def atomhits2atomxml(atomhits,searchstr,urlpathprefix,nextlinkpath=None):
    
    # TODO: use ElementTree parser here too, see AtomFeedParser:feedhits2atomxml
    atom = ''
    atom += '<?xml version="1.0" encoding="UTF-8"?>\n'
    atom += '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:sy="http://purl.org/rss/1.0/modules/syndication/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:p2pnext="urn:p2pnext:contentfeed:2009" xmlns:taxo="http://purl.org/rss/1.0/modules/taxonomy/">\n'
    # craffels: Escape special chars (i.e., &,<,>) found in Rich Metadata search
    atom += '  <title>Hits for '+htmlfilter(searchstr)+'</title>\n'
    atom += '  <link rel="self" href="'+urlpathprefix+'" />\n'
    if nextlinkpath:
        atom += '  <link rel="next" href="'+nextlinkpath+'" />\n'
    atom += '  <author>\n'
    atom += '  <name>NSSA</name>\n'
    atom += '  </author>\n'
    atom += '  <id>urn:nssa</id>\n'
    atom += '  <updated>'+now2formatRFC3339()+'</updated>\n'
    #atom += '<p2pnext:image src="http://p2pnextfeed1.rad0.net/images/bbc.png" />\n' # TODO

    for infohash,hit in atomhits.iteritems():
        urlinfohash = infohash2urlpath(infohash)
        hitpath = urlpathprefix+'/'+urlinfohash+URLPATH_NSMETA_POSTFIX
        atom += '  <entry>\n'
        atom += '    <title>'+hit['title']+'</title>\n'
        atom += '    <link type="application/xml" href="'+hitpath+'" />\n'
        atom += '    <id>urn:nssa-'+urlinfohash+'</id>\n'
        atom += '    <updated>'+now2formatRFC3339()+'</updated>\n'
        if hit['summary'] is not None:
            atom += '    <summary>'+hit['summary']+'</summary>\n'
        if 'p2pnext:image' in hit:
            atom += '    <p2pnext:image src="'+hit['p2pnext:image']+'" />\n'
        atom += '  </entry>\n'
    
    atom += '</feed>\n'

    return atom


def hit2nsmetahit(hit,hiturlprefix,colltorrdir):
    """ Convert common hit to the fields required for the MPEG7 NS metadata """

    if DEBUG:
        print >>sys.stderr,"his2nsmetahit:"
    
    # Read info from torrent files / P2PURLs
    if hit['hittype'] == "localdb":  
        name = hit['name']
        
        if hit['torrent_file_name'].startswith(P2PURL_SCHEME): 
            # Local DB hit that is P2PURL 
            torrenturl = hit['torrent_file_name']
            titleimgurl = None
            tdef = TorrentDef.load_from_url(torrenturl)
        else: 
            # Local DB hit that is torrent file
            torrenturlpath = '/'+infohash2urlpath(hit['infohash'])+URLPATH_TORRENT_POSTFIX
            torrenturl = hiturlprefix + torrenturlpath
            filepath = os.path.join(colltorrdir,hit['torrent_file_name'])
            tdef = TorrentDef.load(filepath)
            (thumbtype,thumbdata) = tdef.get_thumbnail()
            if thumbtype is None:
                titleimgurl = None
            else:
                titleimgurl = torrenturl+URLPATH_THUMBNAIL_POSTFIX
           
    else:
        # Remote hit
        name = hit['content_name']
        if hit['metatype'] == URL_MIME_TYPE:
            torrenturl = hit['torrent_file_name']
            titleimgurl = None
            tdef = TorrentDef.load_from_url(torrenturl)
        else:
            torrenturlpath = '/'+infohash2urlpath(hit['infohash'])+URLPATH_TORRENT_POSTFIX
            torrenturl = hiturlprefix + torrenturlpath
            metainfo = bdecode(hit['metadata'])
            tdef = TorrentDef.load_from_dict(metainfo)
            (thumbtype,thumbdata) = tdef.get_thumbnail()
            if thumbtype is None:
                titleimgurl = None
            else:
                titleimgurl = torrenturl+URLPATH_THUMBNAIL_POSTFIX

    
    # Extract info required for NS metadata MPEG7 representation.
    # Notes: 
    #  - the dictionary keys for rich metadata results are set in RichMetadataDBHandler
    #  - sanity checks are performed in RichMetadataDBHandler, no need to check here again
    #  - rich metadata results provide unicode strings but MPEG-7 XML is UTF-8, use unicode2utf8() 
    nsmetahit = {}
    nsmetahit['title'] = unicode2iri(name)
    nsmetahit['torrent_url'] = torrenturl
    
    # dict containing all rich metadata fields (set in RichMetadataDBHandler)
    rmfields = ['aspectratio',
                'audiochannels',
                'audiocoding',      
                'bitrate',
                'captionlanguage', 
                'comment',
                'contenttype',
                'copyrightstr',
                'disseminator',
                'duration',
                'episode',
                'fileformat',
                'filesize',
                'framerate',
                'genre',
                'height', 
                'language',
                'minimumage', 
                'name',
                'producer',
                'productiondate', 
                'productionlocation',
                'releasedate', 
                'series',
                'signlanguage',
                'comment',
                'titleimgurl',
                'videocoding',
                'width']
    
    # Store all rich metadata field in nsmetahit dictionary
    # note: conversion of strings from unicode (hit dict) to utf-8 is needed (mpeg-7)
    for field in rmfields:
        if field in hit and hit[field] is not None:
            try:
                nsmetahit[field] = unicode2utf8(hit[field])
            # thrown in case the field value is an integer
            except AttributeError:
                nsmetahit[field] = str(hit[field])
             
    
    # Some fields need special care, handle them now 
    
    # Try to get data from torrent file if result did not contain rich metadata
    if 'titleimgurl' not in nsmetahit and titleimgurl is not None:
        nsmetahit['titleimgurl'] = titleimgurl
    
    if 'comment' not in nsmetahit:
        comment = tdef.get_comment()
        if comment:
            nsmetahit['comment'] = unicode2utf8(tdef.get_comment())
        
    if 'disseminator' not in nsmetahit:
        creator = tdef.get_created_by()
        if creator:
            nsmetahit['disseminator'] = unicode2utf8(creator) 
   
    if 'copyrightstr' not in nsmetahit:
        if 'disseminator' in nsmetahit:
            nsmetahit['copyrightstr'] = "Copyright "+nsmetahit['disseminator']
    
    if 'duration' not in nsmetahit:
        # TODO: multifile torrents, LIVE
        nsmetahit['duration']  = bitratelength2nsmeta_duration(tdef.get_bitrate(),tdef.get_length())
    # rich metadata stores duration in secs, convert to iso8601  
    else:
        nsmetahit['duration'] = seconds2nsmeta_duration(nsmetahit['duration'])    

    # aspect ratio checking:
    # only a string like "4:3" or a floating point number is valid
    if 'aspectratio' in nsmetahit:
        match = re.search('(\d*\.?\d+):(\d*\.?\d+)', nsmetahit['aspectratio'])
        if match:
            width = float(match.group(1))
            height = float(match.group(2))
            if height > 0 and width > 0:
                nsmetahit['aspectratio'] = str(height/width)
            else:
                del nsmetahit['aspectratio']
        # string is numerical if string can be converted to float
        else: 
            try:
                float(hit['aspectratio'])
            except ValueError:
                del nsmetahit['aspectratio']
            
    # no aspectratio, try to fall back by calculating height/width        
    if 'aspectratio' not in nsmetahit and 'width' in hit and 'height' in hit:
        nsmetahit['aspectratio'] = `float(hit['height'])/float(hit['width'])`
   
    # TODO: try to derive the correct content type from other metadata 
    # (e.g., audio and video codec informaton available -> audiovisual)
    if 'contenttype' not in hit:
        nsmetahit['contenttype'] = 'audiovisual'
    # check if content type contains an allowed value
    else:
        ctype = nsmetahit['contenttype']
        if ctype.lower() not in ['audio', 'video', 'image', 'audiovisual', 'scene definition', 'unspecified']:
            nsmetahit['contenttype'] = 'audiovisual'  
   
    return nsmetahit


def unicode2iri(uni):
    # Roughly after http://www.ietf.org/rfc/rfc3987.txt Sec 3.1 procedure.
    # TODO: do precisely after.
    s = uni.encode('UTF-8')
    return urllib.quote(s)    

def unicode2utf8(uni):
    return uni.encode('utf-8')
    
def bitratelength2nsmeta_duration(bitrate,length):    
    # Format example: PT0H15M0S
    if bitrate is None or bitrate == 0:
        return 'PT01H00M0S' # 1 hour
    secs = float(length)/float(bitrate)
    hours = float(int(secs / 3600.0))
    secs = secs - hours*3600.0
    mins = float(int(secs / 60.0))
    secs = secs - mins*60.0
    
    return 'PT%02.0fH%02.0fM%02.0fS' % (hours,mins,secs)

def seconds2nsmeta_duration(seconds):    
    try:
        seconds = int(seconds)
    # None or not a numerical string
    except ValueError, TypeError:
        return 'PT01H00M0S' # 1 hour
    
    hours = seconds / 3600
    mins = (seconds / 60) % 60
    secs = seconds % 60
    return 'PT%02.0fH%02.0fM%02.0fS' % (hours,mins,secs)

def nsmetahit2nsmetarepr(hit,hitpath):
    abstract = hit.get('comment')
    aspectratio = hit.get('aspectratio')
    audiochannels = hit.get('audiochannels')
    audiocoding = hit.get('audiocoding')
    bitrate = hit.get('bitrate')
    captionlanguage = hit.get('captionlanguage')
    contenttype = hit.get('contenttype')
    copyrightstr = hit.get('copyrightstr', 'Copyright unknown')
    disseminator = hit.get('disseminator','Unknown')
    duration = hit.get('duration')
    episode = hit.get('episode')
    fileformat = hit.get('fileformat')
    filesize = hit.get('filesize')
    framerate = hit.get('framerate')
    genre = hit.get('genre')
    height = hit.get('height')
    language = hit.get('language')
    minimumage = hit.get('minimumage')
    producer = hit.get('producer','Unknown')
    productiondate = hit.get('productiondate')
    productionlocation = hit.get('productionlocation')
    releasedate = hit.get('releasedate')
    series = hit.get('series')
    signlanguage = hit.get('signlanguage')
    title = hit.get('title')
    titleimgurl = hit.get('titleimgurl')
    torrenturl = hit['torrent_url']
    videocoding = hit.get('videocoding')
    width = hit.get('width')
    #livetimepoint = now2formatRFC3339() # Format example: '2009-10-05T00:40:00+01:00' # TODO VOD
    
    mpeg7Node = Element('Mpeg7')
    mpeg7Node.set('xmlns:p2pnext', 'urn:p2pnext:metadata:2008')
    mpeg7Node.set('xmlns', 'urn:mpeg:mpeg7:schema:2001')
    mpeg7Node.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    mpeg7Node.set('xmlns:mpeg7', 'urn:mpeg:mpeg7:schema:2001')
    mpeg7Node.set('xsi:schemaLocation', 'urn:mpeg:mpeg7:schema:2001 mpeg7-v1_p2p.xsd')

    creationDescriptionNode = SubElement(mpeg7Node, 'Description')
    creationDescriptionNode.set('xsi:type', 'CreationDescriptionType')
    creationInformationNode = SubElement(creationDescriptionNode, 'CreationInformation')
    creationNode = SubElement(creationInformationNode, 'Creation')
    titleNode = SubElement(creationNode, 'Title')
    titleNode.set('type', 'main')
    titleNode.set('xml:lang', 'en')
    titleNode.text = title
    if series:
        seriesTitleNode = SubElement(creationNode, 'Title')
        seriesTitleNode.set('type', 'seriesTitle')
        seriesTitleNode.text = series
    if episode:
        episodeTitleNode = SubElement(creationNode, 'Title')
        episodeTitleNode.set('type', 'episodeTitle')
        seriesTitleNode.text = episode
    if titleimgurl:
        titleMediaNode = SubElement(creationNode, 'TitleMedia')
        titleMediaNode.set('xsi:type', 'TitleMediaType')
        titleImageNode = SubElement(titleMediaNode, 'TitleImage')
        mediaUriNode = SubElement(titleImageNode, 'MediaUri')
        mediaUriNode.text = titleimgurl
    if abstract:
        abstractNode = SubElement(creationNode, 'Abstract')
        freeTextAnnotationNode = SubElement(abstractNode, 'FreeTextAnnotation')
        freeTextAnnotationNode.text = abstract
    prodCreatorNode = SubElement(creationNode, 'Creator')
    prodRoleNode = SubElement(prodCreatorNode, 'Role')
    prodRoleNode.set('href', 'urn:mpeg:mpeg7:cs:RoleCS:2001:PRODUCER')
    prodAgentNode = SubElement(prodCreatorNode, 'Agent')
    prodAgentNode.set('xsi:type', 'OrganizationType')
    prodNameNode = SubElement(prodAgentNode, 'Name')
    prodNameNode.text = producer
    dissCreatorNode = SubElement(creationNode, 'Creator')
    dissRoleNode = SubElement(dissCreatorNode, 'Role')
    dissRoleNode.set('href', 'urn:mpeg:mpeg7:cs:RoleCS:2001:DISSEMINATOR')
    dissAgentNode = SubElement(dissCreatorNode, 'Agent')
    dissAgentNode.set('xsi:type', 'OrganizationType')
    dissNameNode = SubElement(dissAgentNode, 'Name')
    dissNameNode.text = disseminator
    if productionlocation or productiondate:
        creationCoordinatesNode = SubElement(creationNode, 'CreationCoordinates')
    if productionlocation:
        locationNode = SubElement(creationCoordinatesNode, 'Location')
        locNameNode = SubElement(locationNode, 'Name')
        locNameNode.text = productionlocation
    if productiondate:
        prodDateNode = SubElement(creationCoordinatesNode, 'Date')
        prodTimeNode = SubElement(prodDateNode, 'TimePoint')
        prodTimeNode.text = productiondate+'T12:00+00:00'
    copyrightStringNode = SubElement(creationNode, 'CopyrightString')
    copyrightStringNode.text = copyrightstr
    if genre or language or captionlanguage or signlanguage or releasedate or minimumage:
        classificationNode = SubElement(creationInformationNode, 'Classification')
    if genre:
        genreNode = SubElement(classificationNode, 'Genre')
        genreNode.set("href", "urn:mpeg:mpeg7:cs:GenreCS:2001")
        genreNameNode = SubElement(genreNode, 'Name')
        genreNameNode.text = genre
    if language:
        languageNode = SubElement(classificationNode, 'Language')
        languageNode.text = language
    if captionlanguage:
        captionLanguageNode = SubElement(classificationNode, 'CaptionLanguage')
        captionLanguageNode.text = captionlanguage
    if signlanguage:
        signLanguageNode = SubElement(classificationNode, 'SignLanguage')
        signLanguageNode.text = signlanguage
    if releasedate:
        releaseNode = SubElement(classificationNode, 'Release')
        releaseNode.set('date', releasedate+'T12:00+00:00')
    if minimumage:
        parentalGuidanceNode = SubElement(classificationNode, 'ParentalGuidance')
        minimumAgeNode = SubElement(parentalGuidanceNode, 'MinimumAge')
        minimumAgeNode.text = minimumage
    relatedMaterialNode = SubElement(creationInformationNode, 'RelatedMaterial')
    materialTypeNode = SubElement(relatedMaterialNode, 'MaterialType')
    matNameNode = SubElement(materialTypeNode, 'Name')
    matNameNode.text = 'p2p-vod'
    mediaLocatorNode = SubElement(relatedMaterialNode, 'MediaLocator')
    mediaUriNode = SubElement(mediaLocatorNode, 'MediaUri')
    mediaUriNode.text = torrenturl

    mediaDescriptionNode = SubElement(mpeg7Node, 'Description')
    mediaDescriptionNode.set('xsi:type', 'MediaDescriptionType')
    mediaInformationNode = SubElement(mediaDescriptionNode, 'MediaInformation')
    mediaIdentificationNode = SubElement(mediaInformationNode, 'MediaIdentification')
    entityIdentifierNode = SubElement(mediaIdentificationNode, 'EntityIdentifier')
    entityIdentifierNode.set('organization', 'p2p-next')
    entityIdentifierNode.set('type', 'MPEG7ContentSetId')
    entityIdentifierNode.text = hitpath
    mediaProfileNode = SubElement(mediaInformationNode, 'MediaProfile')
    mediaFormatNode = SubElement(mediaProfileNode, 'MediaFormat')
    if contenttype:
        contentNode = SubElement(mediaFormatNode, 'Content')
        contentNode.set('href', 'MPEG7ContentCS')
        contentNameNode = SubElement(contentNode, 'Name')
        contentNameNode.text = contenttype
    if fileformat:
        fileFormatNode = SubElement(mediaFormatNode, 'FileFormat')
        fileFormatNode.set('href', 'urn:mpeg:mpeg7:cs:FileFormatCS:2001')
        fileFormatName = SubElement(fileFormatNode, 'Name')
        fileFormatName.text = fileformat
    if filesize:
        fileSizeNode = SubElement(mediaFormatNode, 'FileSize')
        fileSizeNode.text = filesize
    if bitrate:
        bitRateNode = SubElement(mediaFormatNode, 'BitRate')
        bitRateNode.text = bitrate
    if videocoding or width or height or aspectratio or framerate:
        visualCodingNode = SubElement(mediaFormatNode, 'VisualCoding')
    if videocoding:
        visualFormatNode = SubElement(visualCodingNode, 'Format')
        visualFormatNode.set('href', 'urn:mpeg:mpeg7:cs:VisualCodingFormatCS:2001')
        visualNameNode = SubElement(visualFormatNode, 'Name')
        visualNameNode.text = videocoding
    if width or height or aspectratio or framerate:
        frameNode = SubElement(visualCodingNode, 'Frame')
    if width:
        frameNode.set('width', width)
    if height:
        frameNode.set('height', height)
    if aspectratio:
        frameNode.set('aspectRatio', aspectratio)
    if framerate:
        frameNode.set('rate', framerate)
    if audiocoding or audiochannels:
        audioCodingNode = SubElement(mediaFormatNode, 'AudioCoding')
    if audiocoding:
        audioFormatNode = SubElement(audioCodingNode, 'Format')
        audioFormatNode.set('href', 'urn:mpeg:mpeg7:cs:AudioCodingFormatCS:2001')
        audioFormatName = SubElement(audioFormatNode, 'Format')
        audioFormatName.text = audiocoding
    if audiochannels:
        audioChannelsNode = SubElement(audioCodingNode, 'AudioChannels')
        audioChannelsNode.text = audiochannels

    contentEntityNode = SubElement(mpeg7Node, 'Description')
    contentEntityNode.set('xsi:type', 'ContentEntityType')
    multimediaContentNode = SubElement(contentEntityNode, 'MultimediaContent')
    multimediaContentNode.set('xsi:type', 'VideoType')
    videoNode = SubElement(multimediaContentNode, 'Video')
    mediaTimeNode = SubElement(videoNode, 'MediaTime')
    mediaTimePointNode = SubElement(mediaTimeNode, 'MediaTimePoint')
    mediaTimePointNode.text = 'T00:00:00'
    mediaDurationNode = SubElement(mediaTimeNode, 'MediaDuration')
    mediaDurationNode.text = duration

    return tostring(mpeg7Node)

#    s = ''
#    s += '<Mpeg7 xmlns:p2pnext="urn:p2pnext:metadata:2008" xmlns="urn:mpeg:mpeg7:schema:2001" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:mpeg7="urn:mpeg:mpeg7:schema:2001" xsi:schemaLocation="urn:mpeg:mpeg7:schema:2001 mpeg7-v1_p2p.xsd">\n'
#    s += '  <Description xsi:type="CreationDescriptionType">\n'
#    s += '    <CreationInformation>\n'
#    s += '      <Creation>\n'
#    s += '        <Title type="main" xml:lang="en">'+xmlfilter(title)+'</Title>\n'
#    if series:
#        s += '        <Title type="seriesTitle">'+xmlfilter(series)+'</Title>\n'
#    if episode:
#        s += '        <Title type="episodeTitle">'+xmlfilter(episode)+'</Title>\n'
#    if titleimgurl:
#        s += '        <TitleMedia xsi:type="TitleMediaType">\n'
#        s += '          <TitleImage>\n'
#        s += '            <MediaUri>'+xmlfilter(titleimgurl)+'</MediaUri>\n'
#        s += '          </TitleImage>\n'
#        s += '        </TitleMedia>\n'
#    if abstract:
#        s += '        <Abstract>\n'
#        s += '          <FreeTextAnnotation>'+xmlfilter(abstract)+'</FreeTextAnnotation>\n'
#        s += '        </Abstract>\n'
#    s += '        <Creator>\n'
#    s += '          <Role href="urn:mpeg:mpeg7:cs:RoleCS:2001:PRODUCER" />\n'
#    s += '          <Agent xsi:type="OrganizationType">\n'
#    s += '            <Name>'+xmlfilter(producer)+'</Name>\n'
#    s += '          </Agent>\n'
#    s += '        </Creator>\n'
#    s += '        <Creator>\n'
#    s += '          <Role href="urn:mpeg:mpeg7:cs:RoleCS:2001:DISSEMINATOR" />\n'
#    s += '          <Agent xsi:type="OrganizationType">\n'
#    s += '            <Name>'+xmlfilter(disseminator)+'</Name>\n'
#    s += '          </Agent>\n'
#    s += '        </Creator>\n'
#    if productionlocation or productiondate:  
#        s += '        <CreationCoordinates>\n'
#    if productionlocation:
#        s += '          <Location>\n'
#        s += '            <Name>'+xmlfilter(productionlocation)+'</Name>\n'
#        s += '          </Location>\n'
#    if productiondate:
#        s += '          <Date>\n'
#        s += '            <TimePoint>'+productiondate+'T12:00+00:00</TimePoint>\n'
#        s += '          </Date>\n'
#    if productionlocation or productiondate:
#        s += '        </CreationCoordinates>\n'
#    s += '        <CopyrightString>'+xmlfilter(copyrightstr)+'</CopyrightString>\n'
#    s += '      </Creation>\n'
#    if genre or language or captionlanguage or signlanguage or releasedate or minimumage:
#        s += '      <Classification>\n'
#    if genre:
#        s += '        <Genre href="urn:mpeg:mpeg7:cs:GenreCS:2001">\n'
#        s += '          <Name>'+xmlfilter(genre)+'</Name>\n'
#        s += '        </Genre>\n'
#    if language:
#        s += '        <Language>'+language+'</Language>\n'
#    if captionlanguage:
#        s += '        <CaptionLanguage>'+captionlanguage+'</CaptionLanguage>\n'
#    if signlanguage:
#        s += '        <SignLanguage>'+signlanguage+'</SignLanguage>\n'
#    if releasedate:         
#        s += '        <Release date="'+releasedate+'T12:00+00:00">\n'
        #TODO: add release region information 
        #s += '          <Region>UK</Region>
#        s += '        </Release>\n'
#    if minimumage:
#        s += '        <ParentalGuidance>\n'
#        s += '          <MinimumAge>'+minimumage+'</MinimumAge>\n'
#        s += '        </ParentalGuidance>\n'
#    if genre or language or captionlanguage or signlanguage or releasedate or minimumage:
#        s += '      </Classification>\n'
#    s += '      <RelatedMaterial>\n'
#    s += '        <MaterialType>\n'
#    s += '          <Name>p2p-vod</Name>\n'
#    s += '        </MaterialType>\n'
#    s += '        <MediaLocator>\n'
#    s += '          <MediaUri>'+xmlfilter(torrenturl)+'</MediaUri>\n'
#    s += '        </MediaLocator>\n'
#    s += '      </RelatedMaterial>\n'
#    s += '    </CreationInformation>\n'
#    s += '  </Description>\n'
#    s += '  <Description xsi:type="MediaDescriptionType">\n'
#    s += '    <MediaInformation>\n'
#    s += '      <MediaIdentification>\n'
#    s += '            <EntityIdentifier organization="p2p-next" type="MPEG7ContentSetId">'
#    s += hitpath
#    s += '</EntityIdentifier>\n'
#    s += '      </MediaIdentification>\n'
#    s += '        <MediaProfile>\n'
#    s += '          <MediaFormat>\n'
#    if contenttype:
#        s += '            <Content href="MPEG7ContentCS">\n'
#        s += '              <Name>'+contenttype+'</Name>\n'
#        s += '            </Content>\n'
#    if fileformat:
#        s += '            <FileFormat href="urn:mpeg:mpeg7:cs:FileFormatCS:2001">\n'
#        s += '              <Name>'+fileformat+'</Name>\n'
#        s += '            </FileFormat>\n'
#    if filesize:
#        s += '            <FileSize>'+filesize+'</FileSize>\n'
#    if bitrate:
#        s += '            <BitRate>'+bitrate+'</BitRate>\n'
#    if videocoding or width or height or aspectratio or framerate:
#        s += '            <VisualCoding>\n'
#    if videocoding:
#        s += '              <Format href="urn:mpeg:mpeg7:cs:VisualCodingFormatCS:2001">\n'
#        s += '                <Name>'+xmlfilter(videocoding)+'</Name>\n'
#        s += '              </Format>\n'
#    if width or height or aspectratio or framerate:
#        s += '              <Frame '
#    if width:
#        s += 'width="'+width+'" '
#    if height:
#        s += 'height="'+height+'" '
#    if aspectratio:
#        s += 'aspectRatio="'+aspectratio+'" '
#    if framerate:
#        s += 'rate="'+framerate+'"'
#    if width or height or aspectratio or framerate:
#        s +='/>\n'
#    if videocoding or width or height or aspectratio or framerate:
#        s += '            </VisualCoding>\n'
#    if audiocoding or audiochannels:
#        s += '            <AudioCoding>\n'
#    if audiocoding:
#        s += '              <Format href="urn:mpeg:mpeg7:cs:AudioCodingFormatCS:2001">\n'
#        s += '                <Name>'+xmlfilter(audiocoding)+'</Name>\n'
#        s += '              </Format>\n'
#    if audiochannels:
#        s += '              <AudioChannels>'+audiochannels+'</AudioChannels>\n'
#    if audiocoding or audiochannels:
#        s += '            </AudioCoding>\n'
#    s += '          </MediaFormat>\n'
#    s += '        </MediaProfile>\n'
#    s += '    </MediaInformation>\n'
#    s += '  </Description>\n'
#    s += '  <Description xsi:type="ContentEntityType">\n'
#    s += '    <MultimediaContent xsi:type="VideoType">\n'
#    s += '      <Video>\n'
#    s += '        <MediaTime>\n'
#    s += '          <MediaTimePoint>T00:00:00</MediaTimePoint>\n'
#    s += '          <MediaDuration>'+duration+'</MediaDuration>\n'
#    s += '        </MediaTime>\n'
#    s += '      </Video>\n'
#    s += '    </MultimediaContent>\n'
#    s += '  </Description>\n'
# usage information is needed for live streams only
#    s += '  <Description xsi:type="UsageDescriptionType">\n'
#    s += '    <UsageInformation>\n'
#    s += '      <Availability>\n' 
#    s += '        <InstanceRef href="'+hitpath+'"/>\n'
#    s += '        <AvailabilityPeriod type="live">\n'
#    s += '          <TimePoint>2010-10-08T12:00:00+00:00</TimePoint>\n' 
#    s += '          <Duration>PT30M</Duration>\n'
#    s += '        </AvailabilityPeriod>\n' 
#    s += '      </Availability>\n'
#    s += '    </UsageInformation>\n' 
#    s += '  </Description>\n' 
#    s += '</Mpeg7>\n'
#    return s

def hack_make_default_merkletorrent(title):
    metainfo = {}
    metainfo['announce'] = 'http://localhost:0/announce'
    metainfo['creation date'] = int(time.time())
    info = {}
    info['name'] = title
    info['length'] = 2 ** 30
    info['piece length'] = 2 ** 16
    info['root hash'] = '*' * 20
    metainfo['info'] = info
    
    mdict = {}
    mdict['Publisher'] = 'Tribler'
    mdict['Description'] = ''
    mdict['Progressive'] = 1
    mdict['Speed Bps'] = str(2 ** 16)
    mdict['Title'] = metainfo['info']['name']
    mdict['Creation Date'] = long(time.time())
    # Azureus client source code doesn't tell what this is, so just put in random value from real torrent
    mdict['Content Hash'] = 'PT3GQCPW4NPT6WRKKT25IQD4MU5HM4UY'
    mdict['Revision Date'] = long(time.time())
    cdict = {}
    cdict['Content'] = mdict
    metainfo['azureus_properties'] = cdict
    
    return bencode(metainfo)



    

"""
class Infohash2TorrentPathMapper(AbstractPathMapper):
    Mapper to map in the collection of known torrents files (=collected + started
    + own) into the HTTP address space of the local HTTP server. In particular,
    it maps a "/infohash/aabbccdd...zz.tstream" path to a streaminfo dict.
    
    Also supported are "/infohash/aabbccdd...zz.tstream/thumbnail" queries, which
    try to read the thumbnail from the torrent.
        
    def __init__(self,urlpathprefix,session):
        self.urlpathprefix = urlpathprefix
        self.session = session
        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)
        
    def get(self,urlpath):
        if not urlpath.startswith(self.urlpathprefix):
            return None
        try:
            wantthumb = False
            if urlpath.endswith(URLPATH_THUMBNAIL_POSTFIX):
                wantthumb = True
                infohashquote = urlpath[len(self.urlpathprefix):-len(URLPATH_TORRENT_POSTFIX+URLPATH_THUMBNAIL_POSTFIX)]
            else:
                infohashquote = urlpath[len(self.urlpathprefix):-len(URLPATH_TORRENT_POSTFIX)]
            infohash = urlpath2infohash(infohash)
            dbhit = self.torrent_db.getTorrent(infohash,include_mypref=False)
            
            colltorrdir = self.session.get_torrent_collecting_dir()
            filepath = os.path.join(colltorrdir,dbhit['torrent_file_name'])
                                                      
            if not wantthumb:
                # Return stream that contains torrent file
                stream = open(filepath,"rb")
                length = os.path.getsize(filepath)
                streaminfo = {'statuscode':200,'mimetype':TSTREAM_MIME_TYPE,'stream':stream,'length':length}
            else:
                # Return stream that contains thumbnail
                tdef = TorrentDef.load(filepath)
                (thumbtype,thumbdata) = tdef.get_thumbnail()
                if thumbtype is None:
                    return None
                else:
                    stream = StringIO(thumbdata)
                    streaminfo = {'statuscode':200,'mimetype':thumbtype,'stream':stream,'length':len(thumbdata)}
                
            return streaminfo
        except:
            print_exc()
            return None

"""
