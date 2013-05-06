import sys
import binascii
import urlparse
from cStringIO import StringIO
from traceback import print_exc,print_stack
from threading import RLock

from Tribler.Core.API import *
from Tribler.Core.Overlay.permid import verify_torrent_signature
from Tribler.Video.VideoServer import AbstractPathMapper
from Tribler.Plugin.defs import *
from JSI.RichMetadata.RichMetadata import RichMetadataGenerator
from JSI.RichMetadata.conf import metadata


DEBUG = False

METADATA_PREFIX = ""

def streaminfo404():
    return {'statuscode':404, 'statusmsg':'404 Not Found'}


class MetadataMapper(AbstractPathMapper):
    
    def __init__(self):
        """
        list of torrents reteived for metadata but not for download:
        format:
               { infohash1 : {metainfo=..., tminfo=..., ric_meta=..., metadata=...}, infohash2 : {...} }
        """
        self.torrents = {}
        self.rmg = RichMetadataGenerator.getInstance()
        self.lock = RLock()
                       
    def addTorrent(self, tdef):
    
        if DEBUG:
            print >>sys.stderr,"\nbg: metadata: Adding torrent to the mapper"
            
        metainfo = tdef.get_metainfo()
        infohash = tdef.get_infohash()
        tminfo = {'infohash': binascii.hexlify(infohash),
                  'piece_length': str(metainfo['info']['piece length']),
                  'file_name': None,
                  'file_size': None,
                  'piece_number': None,
                  'last_piece_length': None,
                  'root_hash': None,
                  'live': None,
                  'announce': metainfo['announce'],
                  'announce_list': None,
                  'http_seeds': None,
                  'url_list': None,
                  'signature': None,
                  'signer': None,
                  'signature_verification': None,
                  'comment': None,
                  'playtime': None}
        rich_meta = None
        if tdef.get_metadata() != None:
            rich_meta = self.get_rich_meta(tdef.get_metadata())
        attr = self.get_attr(rich_meta, metainfo, tminfo)
        metadata = tdef.get_metadata()
                       
        self.torrents[infohash] = {"metainfo":metainfo, "tminfo":tminfo, "rich_meta":rich_meta, "metadata": metadata, "attr":attr}
        
    def get(self,urlpath):
        """ Access control """
        try:
            self.lock.acquire()
            return self.doget(urlpath)
        finally:
            self.lock.release()
        
    def doget(self,urlpath):
        """
        Possible paths:
        /content/<infohash_of_torrent>/<random number>/metadata?[field|torrent_meta_info|attr|did-base]
        """
        parsedurl = urlparse.urlparse(urlpath)
        if not parsedurl[2].startswith('/content'):
            return streaminfo404()

        segments = parsedurl[2].split('/')
        infohash = urlpath2infohash(segments[2])
        
        field = parsedurl[4]
        if DEBUG:
            print >>sys.stderr,"\nbg: metadata: Got metadata request for",`field`
        
        if field != '':
            return self.metadata_request(field, infohash)        
        else:
            return streaminfo404()


    def metadata_request(self, field, infohash):    
        """ Returns a request for metadata field """

        torrent_info = self.torrents[infohash]
        
        # TODO return torrent not found error... should not happen
        if torrent_info is None:
            return streaminfo404()
            
        metainfo = torrent_info["metainfo"]
        tminfo = torrent_info["tminfo"]
        rich_meta = torrent_info["rich_meta"]
        metadata = torrent_info["metadata"]
        attr = torrent_info["attr"]
        
        field_value = None
        mime_type = None
        if field in tminfo:
            field_value = self.torrent_info_request(field, tminfo)
        elif field == 'attr':
            field_value = attr
        elif rich_meta is not None:
            # Arno, 2011-01-13: Make core elements accessible:
            try:
                if rich_meta.has_key('core'):
                    for k, m in rich_meta['core'].__dict__.items():
                        if k == "get"+field:
                            field_value = m()
                            break
            except:
                print_exc()
                
            if field_value is None:
                if not rich_meta.has_key('did-base'):
                    return streaminfo404()
                if field == 'did-base':
                    field_value = metadata
                    mime_type = "application/xml"
                else:
                    for k, m in rich_meta.items():
                        if getattr(m, "get" + field):
                            f = getattr(m, "get" + field)
                            field_value = f()
                            break
                if field == "LimoReference":
                    if field_value != None:
                        url = urlparse.urlparse(field_value)
                        if url[0] == "":
                            mime_type = "text/html"

        if field_value != None:
            stream_value = StringIO(field_value)
            if mime_type == None:
                mime_type = 'text/html'
            streaminfo = {'statuscode':200,'mimetype': mime_type, 'stream': stream_value, 'length': len(field_value)}
            return streaminfo
        return streaminfo404()


    def torrent_info_request(self, field, tminfo, metainfo=None):

        if tminfo[field] != None:
            return tminfo[field]
        elif field == 'file_name' or field == 'file_name':
            self.get_torrent_file_info(tminfo, metainfo)
        elif field == 'piece_number' or field == 'last_piece_length':
            self.get_torrent_file_info(tminfo, metainfo)
            pn, lpl = divmod(int(tminfo['file_size']), int(tminfo['piece_length']))
            tminfo['piece_number'] = str(pn)
            tminfo['last_piece_length'] = str(lpl)
        elif field == 'root_hash':
            if metainfo['info'].has_key('root hash'):
                tminfo['root_hash'] = metainfo['info']['root hash']
        elif field == 'live':
            if metainfo['info'].has_key('live'):
                tminfo['live'] = "1"
        elif field == 'announce_list':
            self.get_torrent_announce_list(tminfo, metainfo)
        elif field == 'http_seeds':
            if metainfo.has_key('httpseeds'):
                l = []
                for seed in metainfo['httpseeds']:
                    l += [seed]
                tminfo['http_seeds'] = "\n".join(s for s in l)
        elif field == 'url_list':
            if metainfo.has_key('url-list'):
                l = []
                for seed in metainfo['url-list']:
                    l += [seed]
                tminfo['url_list'] = "\n".join(s for s in l)
        elif field == 'signature':
            if metainfo.has_key('signature'):
                tminfo['signature'] = metainfo['signature']
        elif field == 'signer':
            if metainfo.has_key('signer'):
                tminfo['signer'] = metainfo['signer']
        elif field == 'signature_verification':
            if metainfo.has_key('signature') and metainfo.has_key('signer'):
                if verify_torrent_signature(metainfo):
                    tminfo['signature_verification'] = "1"
                else:
                    tminfo['signature_verification'] = "0"
        elif field == 'comment':
            if metainfo.has_key('comment'):
                tminfo['comment'] = metainfo['comment']
        elif field == 'playtime':
            if metainfo['info'].has_key('playtime'):
                tminfo['playtime'] = str(metainfo['info']['playtime'])

        return tminfo[field]            

      
    def get_torrent_file_info(self, tminfo, metainfo):

        if metainfo['info'].has_key('length'):
            tminfo['file_name'] = metainfo['info']['name']
            tminfo['file_size'] = str(metainfo['info']['length'])
        else:
            name = metainfo['info']['name'] + "\n"
            file_length = 0;
            for f in metainfo['info']['files']:
                name += f + "\n"
                path = ''
                for item in f['path']:
                    if (path != ''):
                        path = path + "/"
                        path = path + item
                        file_length += f['length']
            tminfo['file_name'] = name
            tminfo['file_size'] = str(file_length)

    def get_torrent_announce_list(self, tminfo, metainfo):

        if metainfo.has_key('announce-list'):
            list = []
            for tier in metainfo['announce-list']:
                for tracker in tier:
                    list+=[tracker,',']
                    del list[-1]
            liststring = ''
            for i in list:
                liststring += i + "\n"
            tminfo['announce_list'] = liststring


    def get_rich_meta(self, metadata):

        rich_meta = {}
        if metadata != None:
            meta = self.rmg.getRichMetadata(metadata)
            if meta != None:
                #print >>sys.stderr,"MetadataMapper: get_rich_meta: META DICT",meta.__dict__
                
                rich_meta['did-base'] = meta
                if meta.getMetaCore() != None:
                    rich_meta['core'] = self.rmg.getRichMetadata(meta.getMetaCore())
                    #print >>sys.stderr,"MetadataMapper: get_rich_meta: core set to",rich_meta['core'].__dict__
        return rich_meta

    def get_attr(self, rich_meta, metainfo, tminfo):

        """
        Returns available rich metadata and torrent info attributes
        for the current torrent definition as a string of attributes
        separated by new line.
        """
        retval = []
        if rich_meta != None:
            for k, m in rich_meta.items():
                api = m.getAPIMethods()
                for method in api:
                    if method.startswith("get"):
                        if getattr(m, method):
                            f = getattr(m, method)
                            if f() != None:
                                retval.append(method.lstrip("get"))
        for k in tminfo:
            self.torrent_info_request(k, tminfo, metainfo)
        tif = []
        for k, v in tminfo.items():
            if v != None:
                tif.append(k)
        retval = retval + tif
        return "\n".join(a for a in retval)


def urlpath2infohash(hex):

    if len(hex) != 40:
        raise ValueError("hex len 40 !=" + str(len(hex)) + " " + hex)

    infohash = binascii.unhexlify(hex)
    if len(infohash) != 20:
        raise ValueError("infohash len 20 !=" + str(len(infohash)))
    
    return infohash


# Main! used for running isolated tests:
# TODO see it should be removed
if __name__ == '__main__':

    import os
    import unittest
#    from JSI.RichMetadata.RichMetadata import RichMetadataGenerator, __revision__
#    from JSI.RichMetadata.conf import metadata
#    from Tribler.Plugin.MetadataMapper import MetadataMapper, streaminfo404

    from JSI.RichMetadata.RichMetadata import *
    from JSI.RichMetadata.conf import *
    from Tribler.Plugin.MetadataMapper import *

    def suite():
        return unittest.TestLoader().loadTestsFromTestCase(MetadataMapperTest)

    def getMetaCore(xml=False, formatType=None):
        rmm = RichMetadataGenerator.getInstance()
        meta = rmm.getRichMetadata()
        meta.setProductionLocation("SI").setLanguage("Slovenian")
        meta.setOriginator("JSI")
        meta.setCaptionLanguage("SI").setGenre("Code").setPublisher("p2p-next")
        meta.setProductionDate("2010-8-16").setCaptionLanguage("EN")
        meta.setTitleSeriesTitle("P2P-Next code")
        meta.setTitleMain("Rich Metadata implementation")
        meta.setTitleEpisodeTitle("Rich Metadata v" + __revision__)
        meta.setDuration("1M").setMinimumAge("3")
        meta.setHorizontalSize("640").setVerticalSize("480")
        meta.setFrameRate("27").setAspectRatio("4:3")
        meta.setVideoCoding("Generated").setAudioCoding("Manual")
        meta.setNumOfChannels("2").setFileSize("120k").setBitRate("75")
        meta.setSynopsis("Initial study of RichMetadata API according to the P2P-Next project design")
        meta.setProgramId("crid://p2p-next/example123")
        meta.setAudioCoding("MPEG-1 Audio Layer III")
        meta.setVideoCoding("MPEG-2 Video Main Profile @ Main Level")
        meta.setFileFormat("mp4")
        if xml:
            return rmm.build(meta, formatType)
        return meta

    def getDIDBase(xml = False):
        rmg = RichMetadataGenerator.getInstance()
        meta = rmg.getRichMetadata(None, metadata.MPEG_21_BASE)
        meta.setIdentifier("urn:p2p-next:item:rtv-slo-slo1-xyz") 
        meta.setRelatedIdentifier("urn:rtv-slo:slo1-xyz") 
        # Will build core metadata of type TVA
        meta.setMetaCore(rmg.build(getMetaCore())) 
        meta.setPaymentReference("URI to additional MPEG_21 data (payment)") 
        meta.setAdvertisementReference("URI to additional MPEG_21 data (advertising)") 
        meta.setScalabilityReference("URI to additional MPEG_21 data (scalability)")
        meta.setContentReference("URI to video included in the torrent")
        meta.setContentType("video/ts") 
        meta.setLimoReference("URI to additional MPEG_21 data (limo)")
        if xml:
            return rmg.build(meta)
        return meta

    def getTorrentDef(rich=True):
        tdef = TorrentDef()
        tdef.add_content(os.path.abspath("Tribler/readme.txt"), playtime="1:00:00")
        tdef.set_tracker("http://127.0.0.1:8081")
        tdef.set_piece_length(32768)
        if rich: 
            did_xml = getDIDBase(True)
            tdef.set_metadata(did_xml)
        tdef.finalize()
        return tdef

    class MetadataMapperTest(unittest.TestCase):
        """ 
        Metadata Mapper tests
        """

        def testReturns(self):
            metamapper = MetadataMapper(getTorrentDef())
            did = getDIDBase()
            core = getMetaCore()
            nldvars = metamapper.get("/metadata?attr")['stream']
            attribs = [l.strip() for l in nldvars.readlines() if l.strip()]
            for a in attribs:
                value = metamapper.get("/metadata?" + a)['stream'].getvalue()
                self.assertTrue(value != None)
                if getattr(did, "get" + a):
                    self.assertTrue(value == getattr(did, "get" + a)())
                elif getattr(core, "get" + a):
                    self.assertTrue(value == getattr(core, "get" + a)()) 
                else:
                    self.assertTrue(value == metamapper.torrent_info_request(a)) 
            self.assertTrue(metamapper.get("/metadata?did-base")['stream'].getvalue() == getDIDBase(True))
            self.assertTrue(metamapper.get("/metadata?core")['stream'].getvalue() == getMetaCore(True))
            metamapper2 = MetadataMapper(getTorrentDef(False))
            nldvars = metamapper2.get("/metadata?attr")['stream']
            attribs = [l.strip() for l in nldvars.readlines() if l.strip()]
            for a in attribs:
                self.assertTrue(a in metamapper2.tminfo)
                self.assertTrue(metamapper2.tminfo[a] != None)

    unittest.main()

