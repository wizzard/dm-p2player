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
from olconn import OLConnection
from Tribler.Core.API import *
from Tribler.Core.BitTornado.bencode import bencode,bdecode
from Tribler.Core.BitTornado.BT1.MessageID import *

DEBUG=True


class MyTracker(ThreadingMixIn,BaseHTTPServer.HTTPServer):
    
    def __init__(self,trackport,myid,myip,myport):
        self.myid = myid
        self.myip = myip
        self.myport = myport
        self.infohashmap = {}
        BaseHTTPServer.HTTPServer.__init__( self, ("",trackport), SimpleServer )
        self.daemon_threads = True
        
    def background_serve( self ):
        thread.start_new_thread( self.serve_forever, () )

    def shutdown(self):
        self.socket.close()

    def set_infohashmap(self,infohashmap):
        self.infohashmap = infohashmap    

    def get_infohashmap(self):
        return self.infohashmap    



class SimpleServer(BaseHTTPServer.BaseHTTPRequestHandler):

    def do_GET(self):
        
        # Scrape:  /scrape?info_hash=%E8%99%BCR%00%0A%BA%D4s%0A%C5%F4Z%17s%CB%60%B6%D6%40
        print >>sys.stderr,"test: tracker: Got GET request",self.path

        infohash = None
        bd = None
        if self.path.startswith("/scrape"):
            (pre,query) = self.path.split('?')
            d = urlparse.parse_qs(query)
            infohash = d['info_hash'][0]

            scraped = {}
            scraped["flags"] = {}
            scraped["min_request_interval"] = 1800

            # throws KeyError if other that good or dead reaches tracker
            
            print >>sys.stderr,"COMPARE",`self.server.infohashmap["good"]["infohash"]`,`infohash`
            
            if self.server.infohashmap["good"]["infohash"] == infohash:
                
                print >>sys.stderr,"test: tracker: Got GET request: GOOD torrent"
                statusd = {}
                statusd["complete"] = 34
                statusd["incomplete"] = 17
                statusd["downloaded"] = 481
                
                scraped["files"] = {}
                scraped["files"][infohash] = statusd 
                
                self.server.infohashmap["good"]["waschecked"] = True
            else:
                print >>sys.stderr,"test: tracker: Got GET request: DEAD torrent"
                
                scraped["files"] = {}
                scraped["failure reason"] = "Torrent has left the building"
                
                self.server.infohashmap["dead"]["waschecked"] = True

            bd = bencode(scraped)

        elif self.path.startswith("/announce"):
            p = []
            p1 = {'peer id':self.server.myid,'ip':self.server.myip,'port':self.server.myport}
            p.append(p1)
            d = {}
            d['interval'] = 1800
            d['peers'] = p
            bd = bencode(d)

        if bd is not None:
            size = len(bd)
    
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", size)
            self.end_headers()
            
            try:
                self.wfile.write(bd)
            except Exception,e:
                print_exc()



class TestTorrentCheck(TestAsServer):
    """ 
    Testing QUERY message of Social Network extension V1
    """
    
    def setUpPreSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPreSession(self)

        self.config.set_overlay(False)
        self.config.set_megacache(True)
        self.config.set_torrent_checking(True)
        self.config.set_torrent_checking_period(5)
        self.config.set_torrent_collecting_dir(os.path.join(self.config_path, "tmp_torrent_collecting"))

        self.mylistenport = 5000 # unused

        self.mytrackerport = 4901
        self.myid = 'R410-----HgUyPu56789'
        self.mytracker = MyTracker(self.mytrackerport,self.myid,'127.0.0.1',self.mylistenport)
        self.mytracker.background_serve()


    def setUpPostSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPostSession(self)

        self.torrent_db = self.session.open_dbhandler(NTFY_TORRENTS)

    
    def tearDown(self):
        TestAsServer.tearDown(self)
        self.session.close_dbhandler(self.torrent_db)



    def test_all(self):
        # Good response from tracker
        url1 = 'http://127.0.0.1:'+str(self.mytrackerport)+'/announce'
        tdef1, bmetainfo1 = self.get_default_torrent('sumfilename1','Hallo Good',announce=url1)
        self.infohash1 = tdef1.get_infohash()
            
        # Unknown tracker
        url2 = 'http://127.0.0.2:10/announce'
        tdef2, bmetainfo2 = self.get_default_torrent('sumfilename2','Hallo Unknown',announce=url2)
        self.infohash2 = tdef2.get_infohash()

        # Tracker ok, torrent gone
        url3 = 'http://127.0.0.1:'+str(self.mytrackerport)+'/announce'
        tdef3, bmetainfo3 = self.get_default_torrent('sumfilename3','Hallo Dead',announce=url3)
        self.infohash3 = tdef3.get_infohash()

        infohashmap = {}
        d1 = {}
        d1['infohash'] = self.infohash1
        d1['waschecked'] = False
        infohashmap['good'] = d1
        
        d2 = {}
        d2['infohash'] = self.infohash2
        d2['waschecked'] = True
        infohashmap['unknown'] = d2
        
        d3 = {}
        d3['infohash'] = self.infohash3
        d3['waschecked'] = False
        infohashmap['dead'] = d3
        self.mytracker.set_infohashmap(infohashmap)

        dbrec= self.torrent_db.addExternalTorrent(tdef1, extra_info={"filename":"sumfilename1",'status':'good'})
        dbrec= self.torrent_db.addExternalTorrent(tdef2, extra_info={"filename":"sumfilename2",'status':'good'})
        dbrec= self.torrent_db.addExternalTorrent(tdef3, extra_info={"filename":"sumfilename3",'status':'good'})
        
        # ALT: download, so it becomes my pref. Then torrent record should remain,
        # but status should be dead and it should not return in query.
        #self.session.start_download(tdef3)


        richmetadata_db = self.session.open_dbhandler(NTFY_NS_RICHMETADATA)
        self.assertTrue("open rich metadata db handler",richmetadata_db is not None)
        localdbhits = richmetadata_db.search("title~Hallo")

        for hit in localdbhits:
            print >>sys.stderr,"test: BEFORE CHECK Found",hit['name']

        # Wait while core checks
        time.sleep(30)

        
        print >>sys.stderr,"\ntest: checking DB state after checks"
        # Check that all torrent checks were made.
        infohashmap = self.mytracker.get_infohashmap()
        for state,value in infohashmap.iteritems():
            print >>sys.stderr,"test: checking state",state
            self.assertEquals(value["waschecked"],True)
            torrent = self.torrent_db.getTorrent(value["infohash"])
            if state == "dead":
                print >>sys.stderr,"test: checking state",state,"torrent must be deleted"
                self.assertEqual(torrent,None)
            else:
                print >>sys.stderr,"test: checking state",state,torrent['name']
                self.assertEquals(torrent['status'],state)


        print >>sys.stderr,"test: Search in RichMetadataDB and find only good torrent"
        # Now query for Hallo and only get infohash1
        richmetadata_db = self.session.open_dbhandler(NTFY_NS_RICHMETADATA)
        self.assertTrue("open rich metadata db handler",richmetadata_db is not None)
        localdbhits = richmetadata_db.search("title~Hallo")
        self.session.close_dbhandler(richmetadata_db)
        
        print >>sys.stderr,"test: Got localdbhits",`localdbhits`
        
        self.assertEquals(len(localdbhits),1)
        hit = localdbhits[0]
        self.assertEquals(hit["infohash"],self.infohash1)
        print >>sys.stderr,"test: Found",hit['name']


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
        return tdef, bencode(metainfo)


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestTorrentCheck))
    
    return suite


if __name__ == "__main__":
    unittest.main()

