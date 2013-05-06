# Written by Arno Bakker
# see LICENSE.txt for license information

# TODO: let one hit to SIMPLE+METADATA be P2PURL
import unittest
import os
import sys
import time
from Tribler.Core.Utilities.Crypto import sha
from types import StringType, DictType, IntType
import thread
import BaseHTTPServer
from SocketServer import ThreadingMixIn
import urlparse


from Tribler.Test.test_as_server import TestAsServer
from Tribler.Core.API import *
import Tribler.Core.Utilities.timeouturlopen as timeouturlopen

from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue
from Tribler.Plugin.Search import *

from Tribler.Video.defs import *
from Tribler.Video.VideoServer import VideoHTTPServer,MultiHTTPServer
from Tribler.Core.Statistics.Status import Status, LivingLabReporter


DEBUG=True



class TestNSPCSearchLogging(TestAsServer):
    """ 
    Testing a decentralized search and see if it is logged to ULANC.
    """
    
    def setUpPreSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPreSession(self)

        self.config.set_torrent_checking(False)
        self.config.set_torrent_collecting_dir(os.path.join(self.config_path, "tmp_torrent_collecting"))

    def setUpPostSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPostSession(self)

        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)

        # Create LL reporter
        status = Status.get_status_holder("LivingLab")
        id = encodestring(self.session.get_permid()).replace("\n","")
        
        # Arno, 2011-02-03: Use NSPCLOG SPEC
        self.ulanc_stats_reporting_freq = 30  
        reporter = LivingLabReporter.LivingLabPeriodicReporter("Living lab CS reporter", self.ulanc_stats_reporting_freq, id, report_if_no_events=True)  
        status.add_reporter(reporter)

        self.httpport = 8087

        # Almost generic HTTP server
        self.videoHTTPServer = VideoHTTPServer(self.httpport)
        self.videoHTTPServer.register(self.videoservthread_error_callback,self.videoservthread_set_status_callback)

        # SEARCH:P2P
        # Maps a query ID to the original searchstr, timestamp and all hits (local + remote)
        self.id2hits = Query2HitsMap()
        
        # Maps a URL path received by HTTP server to the requested resource,
        # reading or generating it dynamically.
        #
        # For saving .torrents received in hits to P2P searches using
        # SIMPLE+METADATA queries
        schemeauth = 'http://127.0.0.1:'+str(self.videoHTTPServer.get_port())
        self.tqueue = TimedTaskQueue(nameprefix="BGTaskQueue")
        self.searchmapper = SearchPathMapper(self.session,self.id2hits,self.tqueue,schemeauth)
        self.hits2anypathmapper = Hits2AnyPathMapper(self.session,self.id2hits,schemeauth)
        
        self.videoHTTPServer.add_path_mapper(self.searchmapper)
        self.videoHTTPServer.add_path_mapper(self.hits2anypathmapper)

        # Generic HTTP server start. Don't add mappers dynamically afterwards!
        self.videoHTTPServer.background_serve()


    
    def tearDown(self):
        TestAsServer.tearDown(self)
        self.session.close_dbhandler(self.torrent_db)



    def test_all(self):
        
        print >>sys.stderr,"test: !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        print >>sys.stderr,"test: First disable actual posting in LivingLab report"
        print >>sys.stderr,"test: and just print XML report to stderr"
        print >>sys.stderr,"test: !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        
        # 1. Add torrent to local DB
        url1 = 'http://127.0.0.1:6972/announce'
        tdef1, bmetainfo1, path1 = self.get_default_torrent('sumfilename1','Hallo Good',announce=url1)
        self.infohash1 = tdef1.get_infohash()
        dbrec= self.torrent_db.addExternalTorrent(tdef1, extra_info={"filename":path1,'status':'good'})
        
        # 2. Search for this torrent via HTTP interface
        q = 'Hallo'
        searchurl = 'http://127.0.0.1:'+str(self.httpport)+'/search?q=%28title~'+q+'%29&advq=on&collection=buddycast'
        
        feedp = FeedParser(searchurl)
        feedp.parse()
        hitentries = feedp.search('Hallo Good')
        
        mpeg7url = None
        for hitentry in hitentries:
            titleelement = hitentry.find('{http://www.w3.org/2005/Atom}title')
            linkelement = hitentry.find('{http://www.w3.org/2005/Atom}link')
            mpeg7url = linkelement.attrib['href']
            print >>sys.stderr,"test: Got hit",titleelement.text,mpeg7url
        
        # 3. Retrieve MPEG7 to extract torrent URL
        stream = timeouturlopen.urlOpenTimeout(mpeg7url,10)
        tree = etree.parse(stream)
        #entries = tree.findall('{http://www.w3.org/2005/Atom}entry')
        element = tree.find('{urn:mpeg:mpeg7:schema:2001}MediaLocator')
        
        print >>sys.stderr,"\ntest: Found MediaUri",`element`
        #print >>sys.stderr,"test: Found MediaUri",entries[0].text

        torrenturl = None
        entries = tree.iter('{urn:mpeg:mpeg7:schema:2001}MediaLocator')
        for e in entries:
            urientries = e.iter('{urn:mpeg:mpeg7:schema:2001}MediaUri')
            for e2 in urientries:
                print >>sys.stderr,"test: Found MediaUri",e2.text
                torrenturl = e2.text
                break
            if torrenturl is not None:
                break
        
        # 3. Pretend we're playing it
        tdef = TorrentDef.load_from_url(torrenturl)
        self.searchmapper.nspclog_register_playback(torrenturl,tdef.get_infohash())
        
        # 4. Now check LivingLabReporter output
        print >>sys.stderr,"test: Now watch LivingLab reporter output to see if logged. Sleeping..."
        time.sleep(60)
        
        

    def get_default_torrent(self,filename,title,announce,paths=None):
        metainfo = {}
        metainfo['announce'] = announce
        #metainfo['announce-list'] = []
        metainfo['creation date'] = int(time.time())
        metainfo['encoding'] = 'UTF-8'
        info = {}
        info['name'] = title.encode("UTF-8")
        info['piece length'] = 2 ** 16
        info['pieces'] = '*' * 20
        if paths is None:
            info['length'] = 481
        else:
            d1 = {}
            d1['path'] = [paths[0].encode("UTF-8")]
            d1['length'] = 201
            d2 = {}
            d2['path'] = [paths[1].encode("UTF-8")]
            d2['length'] = 280
            info['files'] = [d1,d2]

        info['ns-metadata'] = '<DIDL xmlns:didl="urn:mpeg:mpeg21:2002:02-DIDL-NS" xmlns:dii="urn:mpeg:mpeg21:2002:01-DII-NS" xmlns:xi="http://www.w3.org/2001/XInclude" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="urn:mpeg:mpeg21:2002:02-DIDL-NS didl.xsd urn:mpeg:mpeg21:2002:01-DII-NS dii.xsd"><Item><Descriptor><Statement mimeType="text/xml"><dii:Identifier>urn:p2p-next:item:rtv-slo-slo1-xyz</dii:Identifier></Statement></Descriptor><Descriptor><Statement mimeType="text/xml"><dii:RelatedIdentifier relationshipType="urn:mpeg:mpeg21:2002:01-RDD-NS:IsAbstractionOf">urn:rtv-slo:slo1-xyz</dii:RelatedIdentifier></Statement></Descriptor><Descriptor><Statement mimeType="text/xml"><dii:Type>urn:p2p-next:type:item:2009</dii:Type></Statement></Descriptor><Descriptor><Descriptor><Statement mimeType="text/xml"><dii:Type>urn:p2p-next:type:rm:core:2009</dii:Type></Statement></Descriptor><xi:include href="URI to additional MPEG_21 data (payment)" xpointer="rm.payment" /><xi:include href="URI to additional MPEG_21 data (advertising)" xpointer="rm.advertisement" /><xi:include href="URI to additional MPEG_21 data (scalability)" xpointer="rm.scalability" /><Statement mimeType="text/xml">&lt;TVAMain publisher="p2p-next" xmlns="urn:tva:metadata:2007" xmlns:mpeg7="urn:mpeg:mpeg7:schema:2001" xmlns:mpeg7_tva="urn:tva:mpeg7:2005" xmlns:p2pnext="urn:p2pnext:metadata:2008" xmlns:tva="urn:tva:metadata:2007" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="urn:tva:metadata:2007 tva_metadata_3-1_v141_p2p.xsd"&gt;&lt;ProgramDescription&gt;&lt;ProgramInformationTable&gt;&lt;ProgramInformation&gt;&lt;BasicDescription type="p2pnext:BasicP2PDataDescriptionType"&gt;&lt;Title type="main"&gt;'+info['name']+' The Series&lt;/Title&gt;&lt;Title type="seriesTitle"&gt;Met with Bett&lt;/Title&gt;&lt;Title type="episodeTitle"&gt;Weather Forecast July 13th, 2009&lt;/Title&gt;&lt;Synopsis&gt;Darren on the Weather&lt;/Synopsis&gt;&lt;Genre href="urn:mpeg:mpeg7:cs:GenreCS:2001"&gt;&lt;Name&gt;Codatainment&lt;/Name&gt;&lt;/Genre&gt;&lt;ParentalGuidance&gt;&lt;mpeg7_tva:MinimumAge&gt;3&lt;/mpeg7_tva:MinimumAge&gt;&lt;/ParentalGuidance&gt;&lt;Language&gt;si&lt;/Language&gt;&lt;CaptionLanguage&gt;si&lt;/CaptionLanguage&gt;&lt;SignLanguage&gt;si&lt;/SignLanguage&gt;&lt;ProductionDate&gt;&lt;TimePoint&gt;2010-08-16&lt;/TimePoint&gt;&lt;/ProductionDate&gt;&lt;ProductionLocation&gt;SI&lt;/ProductionLocation&gt;&lt;p2pnext:Originator&gt;BBC&lt;/p2pnext:Originator&gt;&lt;/BasicDescription&gt;&lt;AVAttributes&gt;&lt;FileFormat href="urn:mpeg:mpeg7:cs:FileFormatCS:2001"&gt;&lt;Name&gt;ogg&lt;/Name&gt;&lt;/FileFormat&gt;&lt;FileSize&gt;12345432&lt;/FileSize&gt;&lt;BitRate&gt;80000&lt;/BitRate&gt;&lt;AudioAttributes&gt;&lt;Coding href="urn:mpeg:mpeg7:cs:AudioCodingFormatCS:2001"&gt;&lt;Name&gt;Vorbis&lt;/Name&gt;&lt;/Coding&gt;&lt;NumOfChannels&gt;2&lt;/NumOfChannels&gt;&lt;/AudioAttributes&gt;&lt;VideoAttributes&gt;&lt;Coding href="urn:mpeg:mpeg7:cs:VisualCodingFormatCS:2001"&gt;&lt;Name&gt;Theora @ MainLevel&lt;/Name&gt;&lt;/Coding&gt;&lt;HorizontalSize&gt;720&lt;/HorizontalSize&gt;&lt;VerticalSize&gt;405&lt;/VerticalSize&gt;&lt;AspectRatio&gt;4:3&lt;/AspectRatio&gt;&lt;FrameRate&gt;30&lt;/FrameRate&gt;&lt;/VideoAttributes&gt;&lt;/AVAttributes&gt;&lt;/ProgramInformation&gt;&lt;/ProgramInformationTable&gt;&lt;/ProgramDescription&gt;&lt;/TVAMain&gt;</Statement></Descriptor><xi:include href="http://news.bbc.co.uk/weather/" xpointer="limo" /><Component><Resource mimeType="video/ts" ref="URI to video included in the torrent" /></Component></Item></DIDL>'            
        metainfo['info'] = info
        
        
        path = os.path.join(self.config.get_torrent_collecting_dir(),filename)
        tdef = TorrentDef.load_from_dict(metainfo)
        tdef.save(path)
        return tdef, bencode(metainfo), path


    def videoservthread_error_callback(self,e,url):
        print >>sys.stderr,"test: Video server reported error",str(e)

    def videoservthread_set_status_callback(self,status):
        print >>sys.stderr,"test: Video server sets status callback",status



def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestNSPCSearchLogging))
    
    return suite


if __name__ == "__main__":
    unittest.main()

