# Written by Arno Bakker, Diego Rabaioli
# see LICENSE.txt for license information
#
# Implements the BackgroundProcess, i.e. SwarmEngine for SwarmPlugin and 
# SwarmTransport=SwarmPlayer v2. See Plugin/SwarmEngine.py and Transport/SwarmEngine.py
# for main startup.
#
# The BackgroundProcess shares a base class BaseApp with the SwarmPlayer v1,
# which is a standalone P2P-based video player.
#
#
# Notes: 
# - Implement play while hashcheck?
#        Not needed when proper shutdown & restart was done.
# - load_checkpoint with DLSTATUS_DOWNLOADING for Plugin? 
#        Nah, if we start BG when plugin started we have a video to play soon,
#        so start others in STOPPED state (rather than switching them all
#        to off and restart one in VOD mode just after)
#

# History:
#
# NSSA API 1.0.2
#
#  1.0.2    Added STOP message to tell plugin to stop playing the current item
#           (needed to support new behaviour where control conn is not always
#           shutdown anymore to support input.set_p2ptarget.
#
#           Added ERROR message to tell plugin NSSA won't be able to serve the
#           content requested via START (for <video> support).    
#
#  1.0.1    Added INFO message to convey NSSA info to plugin for providing 
#           feedback to the user.
#
# NSPlugin JavaScript API 1.0.2
#
#  1.0.2    Added input.set_p2ptarget() method to switch the TorrentDef currently
#           playing. Released in M24.1
#
#  1.0.1    Added input.p2pstatus read-only property giving the latest status as
#           reported by the NSSA. Released in M24.
#
#  1.0.0    Copy of VLC's Javascript interface
#
# 
# modify the sys.stderr and sys.stdout for safe output
import Tribler.Debug.console

import os
import sys
import time
import random
import binascii
import tempfile
import urllib
from cStringIO import StringIO
from base64 import b64encode, encodestring, decodestring
from traceback import print_exc,print_stack
from threading import Thread,currentThread,Lock

if sys.platform == "win32":
    try:
        import win32event
        import win32api
    except:
        pass

try:
    import wxversion
    wxversion.select('2.8')
except:
    pass
import wx

from Tribler.Core.API import *
from Tribler.Core.osutils import *
from Tribler.Core.Utilities.utilities import get_collected_torrent_filename
from Tribler.Utilities.LinuxSingleInstanceChecker import *
from Tribler.Utilities.Instance2Instance import InstanceConnectionHandler,InstanceConnection, Instance2InstanceClient
from Tribler.Utilities.TimedTaskQueue import TimedTaskQueue
from Tribler.Player.BaseApp import BaseApp
from Tribler.Player.swarmplayer import get_status_msgs
from Tribler.Plugin.defs import *
from Tribler.Plugin.Search import *
from Tribler.Plugin.AtomFeedParser import *
from Tribler.Plugin.MetadataMapper import *

from Tribler.Video.defs import *
from Tribler.Video.utils import videoextdefaults
from Tribler.Video.VideoServer import VideoHTTPServer,MultiHTTPServer
from Tribler.Video.Ogg import is_ogg,OggMagicLiveStream

from Tribler.Core.Statistics.Status import Status, LivingLabReporter
from Tribler.Core.Statistics.Status.Status import TRIALLOG, NSPCLOG
from Tribler.WebUI.WebUI import WebIFPathMapper
from Tribler.Core.ClosedSwarm.ClosedSwarm import InvalidPOAException
from Tribler.Core.Video.VideoOnDemand import MovieOnDemandTransporter # LAYERVIOLATION


DEBUG = True
PHONEHOME = True

ALLOW_MULTIPLE = False

KILLONIDLE = False
IDLE_BEFORE_SELFKILL = 60.0 # Number of seconds 


class BackgroundApp(BaseApp):

    def __init__(self, redirectstderrout, appname, appversion, params, single_instance_checker, installdir, i2iport, sport, httpport):

        # Almost generic HTTP server
        self.videoHTTPServer = VideoHTTPServer(httpport)
        self.videoHTTPServer.register(self.videoservthread_error_callback,self.videoservthread_set_status_callback)

        self.ulanc_stats_reporting_freq = None
        self.ulanc_stats_gather_time = None
        self.getpeerlist = False


        BaseApp.__init__(self, redirectstderrout, appname, appversion, params, single_instance_checker, installdir, i2iport, sport)
        self.httpport = httpport
        
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
        self.searchmapper = SearchPathMapper(self.s,self.id2hits,self.tqueue,schemeauth)
        self.hits2anypathmapper = Hits2AnyPathMapper(self.s,self.id2hits,schemeauth)
        
        self.videoHTTPServer.add_path_mapper(self.searchmapper)
        self.videoHTTPServer.add_path_mapper(self.hits2anypathmapper)

        # WEB Interface        
        # Maps a URL path received by HTTP server to the requested resource,
        # reading or generating it dynamically.
        self.webIFmapper = WebIFPathMapper(self, self.s)
        self.videoHTTPServer.add_path_mapper(self.webIFmapper)
        
        # metadata mapper 
        self.mdMapper = MetadataMapper()
        self.videoHTTPServer.add_path_mapper(self.mdMapper)

        # Generic HTTP server start. Don't add mappers dynamically afterwards!
        self.videoHTTPServer.background_serve()

        # Maps Downloads to a using InstanceConnection and streaminfo when it 
        # plays. So it contains the Downloads in VOD mode for which there is
        # active interest from a plugin.
        #
        # At the moment each Download is used/owned by a single IC and a new
        # request for the same torrent will stop playback to the original IC
        # and resume it to the new user.
        #
        self.dusers = {}   
        self.approxplayerstate = MEDIASTATE_STOPPED
        self.iseedeadpeople = False
        
        if sys.platform == "win32":
            # If the BG Process is started by the plug-in notify it with an event
            try:
                startupEvent = win32event.CreateEvent( None, 0, 0, 'startupEvent' )
                win32event.SetEvent( startupEvent )
                win32api.CloseHandle( startupEvent ) # TODO : is it possible to avoid importing win32api just to close an handler?
            except:
                pass

    def OnInit(self):
        try:
            # Do common initialization
            BaseApp.OnInitBase(self)
            
            # Arno, 2010-07-15: We try to detect browser presence by looking
            # at get_speed_info JSON request from Firefox statusbar. However.
            # these calls are unreliable, i.e., somethings the XmlHTTPRequest
            # at the client doesn't reach the server, although the server is
            # capable of replying to the request. Hence, we disable self-destruct
            # for now.
            if KILLONIDLE:
                print >>sys.stderr,"bg: Kill-on-idle test enabled"
                self.i2is.add_task(self.i2i_kill_on_browser_gone,IDLE_BEFORE_SELFKILL/2)
            else:
                print >>sys.stderr,"bg: Kill-on-idle test disabled"
            
            print >>sys.stderr,"bg: Awaiting commands"
            return True

        except Exception,e:
            print_exc()
            self.show_error(str(e))
            self.OnExit()
            return False


    # Arno: SEARCH: disable overlay for now
    # Also need to ensure that *stats*db SQL scripts are copied along during
    # build and crap.
    
    """
    def configure_session(self):
        # Arno, 2011-03-02: Upgrade to overlay enabled.
        self.sconfig.set_superpeer_file(None) # autoreset when changing installdir
        self.sconfig.set_crawler_file(None) # autoreset when changing installdir
    
        self.sconfig.set_megacache(True)
        self.sconfig.set_overlay(True)
        # Leave buddycast, etc. enabled for SEARCH
        self.sconfig.set_social_networking(False)
        self.sconfig.set_bartercast(False)
        #self.sconfig.set_dialback(False)
        self.sconfig.set_crawler(False) # Arno: Cleanup million stats dbs first
        
        # Arno, 2011-01-19: Make less aggressive first
        self.sconfig.set_channelcast(False)

        # Arno, 2011-01-28: Facilitates BuddyCast debugging for now.
        self.sconfig.set_multicast_local_peer_discovery(False)
        
        # Arno, 2011-03-18: Disable download help (and creation of <def>/download_help dir
        self.sconfig.set_download_help(False)
    """

    #
    # InstanceConnectionHandler interface. Called by Instance2InstanceThread
    #
    def external_connection_made(self,s):
        ic = BGInstanceConnection(s,self,self.i2ithread_readlinecallback,self.videoHTTPServer)
        self.singsock2ic[s] = ic
        if DEBUG:
            print >>sys.stderr,"bg: Plugin connection_made",len(self.singsock2ic),"++++++++++++++++++++++++++++++++++++++++++++++++"
          

    def connection_lost(self,s):
        if DEBUG:
            print >>sys.stderr,"bg: Plugin: connection_lost ------------------------------------------------" 

        ic = self.singsock2ic[s]
        InstanceConnectionHandler.connection_lost(self,s)
        wx.CallAfter(self.gui_connection_lost,ic)
        
    def gui_connection_lost(self,ic,switchp2ptarget=False):
        # Find which download ic was interested in
        d2remove = None
        for d,duser in self.dusers.iteritems():
            if duser['uic'] == ic:
                duser['uic'] = None
                d2remove = d
                break
        
        # IC may or may not have been shutdown:
        # Not: sudden browser crashes
        # Yes: controlled stop via ic.shutdown()
        try:
            if switchp2ptarget:
                ic.cleanup_playback() # idempotent
            else:
                ic.shutdown() # idempotent
        except:
            print_exc()
        
        if d2remove is not None:
            # For VOD, apply cleanup policy to the Download, but only 
            # after X seconds so if the plugin comes back with a new 
            # request for the same stuff we can give it to him pronto. 
            # This is expected to happen a lot due to page reloads / 
            # history navigation.
            #
            # Arno, 2010-08-01: Restored old behaviour for live. Zapping
            # more important than extra robustness.
            #
            d_delayed_remove_if_lambda = lambda:self.i2ithread_delayed_remove_if_not_complete(d2remove)
            # h4x0r, abuse Istance2Instance server task queue for the delay
            self.i2is.add_task(d_delayed_remove_if_lambda,10.0)
        
    def i2ithread_delayed_remove_if_not_complete(self,d2remove):
        if DEBUG:
            print >>sys.stderr,"bg: i2ithread_delayed_remove_if_not_complete"
        d2remove.set_state_callback(self.sesscb_remove_playing_callback)
        
    def remove_playing_download(self,d2remove):
        """ Called when sesscb_remove_playing_callback has determined that
        we should remove this Download, because it would take too much
        bandwidth to download it. However, we must check in another user has not
        become interested. 
        """
        if DEBUG:
            print >>sys.stderr,"bg: remove_playing_download @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@"
        if d2remove in self.dusers:
            duser = self.dusers[d2remove]
            if duser['uic'] is None:
                # No interest
                if DEBUG:
                    print >>sys.stderr,"bg: remove_playing_download: Yes, no interest"
                BaseApp.remove_playing_download(self,d2remove)
                if 'streaminfo' in duser:
                    stream = duser['streaminfo']['stream']
                    stream.close() # Close original stream. 
                del self.dusers[d2remove]
            elif DEBUG:
                print >>sys.stderr,"bg: remove_playing_download: No, someone interested",`duser['uic']`

        
    def i2ithread_readlinecallback(self,ic,cmd):
        """ Called by Instance2Instance thread """
        wx.CallAfter(self.gui_readlinecallback,ic,cmd)

    def split_params(self, url):
        """
        Returns a tuple (path, {name:value}) where the map can be empty.
        Example: "/path?p1=v1&p2=v2" -> ('/path', {"p1":"v1", "p2":"v2"})
        """
        params = {}
        idx = url.find("?")
        if idx > -1:
            _params = url[idx+1:].split("&")
            url = url[:idx]
            for param in _params:
                if param.find("=") == -1:
                    continue # Not a parameter
                (name, value) = param.split("=", 1)
                params[name] = value
        return (url, params)
        
    def gui_readlinecallback(self,ic,cmd):
        """ Receive command from Plugin """
        
        if DEBUG:
            print >>sys.stderr,"bg: Got command:",cmd
        try:
            # START command
            if cmd.startswith( 'START' ):
                torrenturl = cmd.partition( ' ' )[2]
                if torrenturl is None:
                    raise ValueError('bg: Unformatted START command')
                else:
                    self.handle_start(torrenturl,ic)

            # SHUTDOWN command
            elif cmd.startswith('SHUTDOWN'):
                print >>sys.stderr,"bg: Got SHUTDOWN, sending SHUTDOWN"
                ic.shutdown()
                
                # Arno, 2011-02-03: Use NSPCLOG SPEC
                if NSPCLOG:
                    try:
                        event_reporter = Status.get_status_holder("LivingLab")
                        if event_reporter is not None:
                            if ic.get_infohash() is not None:
                                w = {}
                                w['infohash'] = b64encode(ic.get_infohash())
                                values = [w]
                                event_reporter.create_and_add_event("media_play_stop",values)
                    except:
                        print_exc() 

                
            elif cmd.startswith('SUPPORTS'):
                # Arno, 2010-06-15: only used by SwarmTransport at the moment
                # to convey it cannot pause.
                ic.set_supported_vod_events([VODEVENT_START])
            else:
                raise ValueError('bg: Unknown command: '+cmd)
        except Exception,e:
            print_exc()
            # Arno, 2010-05-27: Don't kill Control connection, for set_p2ptarget
            ic.error(str(e))
            ic.cleanup_playback()
            
            # Logging
            ic.report_error(str(e))


    def handle_start(self,url,ic):
        
        # SWITCHP2PTARGET: See if already downloading/playing something
        items = self.dusers.items() 
        for d,duser in items:
            if duser['uic'] == ic:
                # Stop current
                self.gui_connection_lost(ic,switchp2ptarget=True)
        
        if url.endswith(".html") or url.endswith("/"):
            # Search mode
            # in which case URL is apparently the base URL of the search page.
            # Just to keep exception trace away.
            pass
        elif url.split('/')[-1].startswith("metadata"):
            # A metadata request:
            # remove the metadata part and get the torrent url
            tdef  = TorrentDef.load_from_url( url[:url.rfind("/meta")] )
            # just add the torrent to the mapper
            self.mdMapper.addTorrent(tdef)
            
            infohash = tdef.get_infohash()
            ic.set_infohash(infohash)    
            ic.start_playback(metadata=url[url.rfind(URLPATH_METADATA_PREFIX):] )

        elif url.startswith("/search"):
            # Arno, 2011-01-17:
            # A search request via the tribe: scheme (normal searches go
            # via SearchPathMapper). Forward to HTTP interface as normal HTTP
            # search query.
            #
            print >>sys.stderr,"bg: handle_start: GOT SEARCH",url
            fullurl = 'http://127.0.0.1:'+str(self.videoHTTPServer.get_port())+url
            ic.play(fullurl)
        else:
            # A tstream to download and play!
            # 
            # Here we need to drag the POA off the torrent URL,
            # if one is given
            (strippedurl, params) = self.split_params(url)
            if "poa" in params:
                poa_serialized = decodestring(params["poa"])
                try:
                    poa = ClosedSwarm.POA.deserialize(poa_serialized)
                    poa.verify()
                except:
                    print >>sys.stderr,"Bad POA, ignoring"
                    poa = None
            else:
                poa = None
                strippedurl = url
            
            self.get_torrent_start_download(ic,strippedurl,poa=poa)
        
    
    def get_torrent_start_download(self,ic,url,poa=None):
        """ Retrieve torrent file from url and start it in VOD mode, if not already """

        # Arno, 2010-10-27: Logging. torrent URL must be saved before retrieval
        # to ic.report_error() has it in case of bad URL / server down.
        ic.got_start(url)
            
        tdef  = TorrentDef.load_from_url(url)

        ic.set_infohash(tdef.get_infohash())
        # Arno, 2011-02-03: Use NSPCLOG SPEC
        if NSPCLOG:
            try:
                event_reporter = Status.get_status_holder("LivingLab")
                if event_reporter is not None:
                    w = {}
                    w['status'] = 'torrent'
                    w['value'] = url
                    w['infohash'] = b64encode(tdef.get_infohash()) 
                    values = [w]
                    event_reporter.create_and_add_event("media_play_start", values )
                    
                # SEARCH LOGGING
                self.searchmapper.nspclog_register_playback(url,tdef.get_infohash())
            except:
                print_exc() 
        
        # Test unreachable tracker           
        #tdef.input['announce'] = "http://dead.globe.cs.vu.nl:6969/announce"
        #tdef.metainfo['announce'] = "http://dead.globe.cs.vu.nl:6969/announce"
        
        # Select which video to play (if multiple)
        if tdef.get_live():
            videofiles = tdef.get_files()
        else:
            videofiles = tdef.get_files(exts=videoextdefaults)
        if len(videofiles) == 1:
            dlfile = videofiles[0]
        elif len(videofiles) == 0:
            raise ValueError("bg: get_torrent_start_download: No video files found! Giving up")
        elif len(videofiles) > 1:
            raise ValueError("bg: get_torrent_start_download: Too many files found! Giving up")

        if DEBUG:
            print >>sys.stderr,"bg: get_torrent_start_download: Found video file",dlfile

        # Closed swarms?
        if not poa:
            if tdef.get_cs_keys():
                # This is a closed swarm, try to get a POA
                poa = self._get_poa(tdef)

        infohash = tdef.get_infohash()
        oldd = None
        for d in self.s.get_downloads():
            if d.get_def().get_infohash() == infohash:
                oldd = d
                break
        
        #
        # Start a new Download, or if it already exists, start playback from
        # beginning. This means that we don't currently support two ICs
        # playing the same video. That is, two browser windows cannot play the
        # same video.
        #
        if oldd is None or (oldd not in self.downloads_in_vodmode):
            # New Download, or Download exists, but not in VOD mode, restart
          
            if DEBUG:
                if oldd is None:
                    print >>sys.stderr,"bg: get_torrent_start_download: Starting new Download"
                else:
                    print >>sys.stderr,"bg: get_torrent_start_download: Restarting old Download in VOD mode"
            
            d = self.start_download(tdef,dlfile,poa,ic.get_supported_vod_events())
            duser = {'uic':ic}
            self.dusers[d] = duser
        else:
            # oldd is already running in VOD mode. If it's a VOD torrent we
            # don't need to restart, we can just seek(0) on the stream.
            # If it's a live torrent, we should tell EOF to any old IC and
            # continue playback to the new IC where it left off.
            #
            duser = self.dusers[d]
            olduic = duser['uic']
            if olduic is not None:
                # Cleanup like a shutdown, but send STOP
                print >>sys.stderr,"bg: get_torrent_start_download: Telling old player to stop"
                olduic.cleanup_playback()
                olduic.stop()
            duser['uic'] = ic
            if 'streaminfo' not in duser:
                # Hasn't started playing yet.
                # Arno, 2011-02-04: Recontact tracker, to have some refreshing
                # behaviour on a page reload.
                print >>sys.stderr,"bg: get_torrent_start_download: Recontacting tracker on page refresh"
                d.recontact_tracker()
            else:
                # Already playing. Tell previous owner IC to quit, let new IC 
                # start either from start (VOD) or where previous left off 
                # (live).
                if not tdef.get_live():
                    duser['streaminfo']['stream'].seek(0)
                ic.set_streaminfo(duser['streaminfo'])
                
                ic.start_playback()
                
        duser['said_start_playback'] = False
        duser['decodeprogress'] = 0
        
    #
    # DownloadStates
    #
    def sesscb_states_callback(self,dslist):
        """ Override BaseApp """
        # Called by Session thread

        # Display some stats
        if (int(time.time()) % 5) == 0:
            for ds in dslist:
                d = ds.get_download()
                print >>sys.stderr, '%s %s %5.2f%% %s up %8.2fKB/s down %8.2fKB/s' % \
                    (d.get_def().get_name(), \
                     dlstatus_strings[ds.get_status()], \
                     ds.get_progress() * 100, \
                     ds.get_error(), \
                     ds.get_current_speed(UPLOAD), \
                     ds.get_current_speed(DOWNLOAD))
        
        # Arno, 2011-02-04:
        # Before the LL report is sent we need to insert the events.
        # Before we can insert the events we need to gather the full 
        # DownloadStates including peerlists. The peerlists we need to ask for 
        # in advance. Asking them every second by default may be costly.
        #
        haspeerlist = False
        testnow = True 
        if self.ulanc_stats_gather_time is not None:
            if time.time() > self.ulanc_stats_gather_time:
                # We want peerlists in next callback
                print >>sys.stderr,"bg: PHONEHOME: Want peerlists now"
                self.getpeerlist = True
                testnow = False
                # Set next gathering moment
                self.schedule_ulanc_stats_gathering()
                
        if testnow and self.getpeerlist:
            # We have peerlists in this callback
            print >>sys.stderr,"bg: PHONEHOME: Got peerlists"
            haspeerlist = True
            self.getpeerlist = False
                
        # Arno: delegate to GUI thread. This makes some things (especially
        #access control to self.videoFrame easier
        #self.gui_states_callback(dslist)
        #print >>sys.stderr,"bg: sesscb_states_callback: calling GUI",currentThread().getName()
        wx.CallAfter(self.gui_states_callback_wrapper,dslist,haspeerlist)
        
        #print >>sys.stderr,"main: SessStats:",self.getpeerlistcount,getpeerlist,haspeerlist
        return (1.0,self.getpeerlist) 
    
    
    
    def gui_states_callback(self,dslist,haspeerlist):
        """ Override BaseApp """
        #print >>sys.stderr,"bg: gui_states_callback",currentThread().getName()

        (playing_dslist,totalhelping,totalspeed) = BaseApp.gui_states_callback(self,dslist,haspeerlist)
        try:
            if haspeerlist:
                self.report_periodic_vod_stats(playing_dslist)
        except:
            print_exc()
       
        for ds in playing_dslist:
            try:
                d = ds.get_download()
                duser = self.dusers[d]
                uic = duser['uic']
                if uic is not None:
                    # Generate info string for all
                    [topmsg,msg,duser['said_start_playback'],duser['decodeprogress']] = get_status_msgs(ds,self.approxplayerstate,self.appname,duser['said_start_playback'],duser['decodeprogress'],totalhelping,totalspeed)
                    info = msg
                    #if DEBUG:
                    #    print >>sys.stderr, 'bg: 4INFO: Sending',info
                    uic.info(info)

                    uic.check_report_play_timeout(ds)
            except:
                print_exc()
            
    def sesscb_vod_event_callback( self, d, event, params ):
        """ Registered by BaseApp. Called by SessionCallbackThread """
        wx.CallAfter(self.gui_vod_event_callback,d,event,params)
        
    def gui_vod_event_callback( self, d, event, params ):
        if DEBUG:
            print >>sys.stderr,"bg: gui_vod_event_callback: Event: ", event
            print >>sys.stderr,"bg: gui_vod_event_callback: Params: ", params

        if event == VODEVENT_START:
            
            # Arno, 2011-02-03: Use NSPCLOG SPEC
            if NSPCLOG:
                try:
                    event_reporter = Status.get_status_holder("LivingLab")
                    if event_reporter is not None:
                        w = {}
                        w['infohash'] = b64encode(d.get_def().get_infohash())
                        values = [w]
                        event_reporter.create_and_add_event("media_play_started",values)
                        
                except:
                    print_exc() 
            
            if params['filename']:
                stream = open( params['filename'], "rb" )
            else:
                stream = params['stream']
    
            # Ric: small hack for the ogg mimetype (just for windows, 
            # linux thinks it's an audio/ogg file)
            if params['mimetype'] == 'video/x-ogg':
                params['mimetype'] = 'application/ogg'
                
            # Arno: My Win7 thinks this is 'video/mpeg', so patch for that.  
            selectedfiles = d.get_selected_files()
            if selectedfiles is not None and len(selectedfiles) > 0:
                for fn in selectedfiles:
                    if is_ogg(fn):
                        params['mimetype'] = 'application/ogg'
            else:
                name = d.get_def().get_name_as_unicode()
                if is_ogg(name):
                    params['mimetype'] = 'application/ogg'
                    
                    if d.get_def().get_live():
                        # Live Ogg stream. To support this we need to do
                        # two things:
                        # 1. Write Ogg headers (stored in .tstream)
                        # 2. Find first Ogg page in stream.
                        stream = OggMagicLiveStream(d.get_def(),stream)

            # Arno, 2011-01-24: Only rate limit when HTTP seeds avail.
            urllist = d.get_def().get_urllist()
            httpseeds = d.get_def().get_httpseeds()     
            if not d.get_def().get_live() and not params['filename'] and (urllist is not None or httpseeds is not None):
                # Arno, < 2010-08-10: Firefox reads aggressively, we just
                # give it data at bitrate pace such that we know when we
                # have to fallback to HTTP servers.
                #
                # 2010-08-10: not when file complete on disk ;-)
                stream = AtBitrateStream( stream, params['bitrate'] )
            
            blocksize = d.get_def().get_piece_length()
            #Ric: add svc on streaminfo, added bitrate
            streaminfo = { 'mimetype': params['mimetype'], 'stream': stream, 'length': params['length'], 'blocksize':blocksize, 'svc': d.get_mode() == DLMODE_SVC, 'bitrate': params['bitrate'] }

            duser = self.dusers[d]
            duser['streaminfo'] = streaminfo
            if duser['uic'] is not None:
                # Only if playback wasn't canceled since starting
                duser['uic'].set_streaminfo(duser['streaminfo'])
                duser['uic'].start_playback()
            
                self.approxplayerstate = MEDIASTATE_PLAYING
            else:
                self.approxplayerstate = MEDIASTATE_STOPPED
            
        elif event == VODEVENT_PAUSE:
            duser = self.dusers[d]
            if duser['uic'] is not None:
                duser['uic'].pause()
            self.approxplayerstate = MEDIASTATE_PAUSED
            
        elif event == VODEVENT_RESUME:
            duser = self.dusers[d]
            if duser['uic'] is not None:
                duser['uic'].resume()
            self.approxplayerstate = MEDIASTATE_PLAYING


    def get_supported_vod_events(self):
        # See BGInstanceConnection.set_supported_vod_events() too.
        return [ VODEVENT_START, VODEVENT_PAUSE, VODEVENT_RESUME ]

    #
    # VideoServer status/error reporting
    #
    def videoservthread_error_callback(self,e,url):
        """ Called by HTTP serving thread """
        wx.CallAfter(self.videoserver_error_guicallback,e,url)
        
    def videoserver_error_guicallback(self,e,url):
        print >>sys.stderr,"bg: Video server reported error",str(e)
        #    self.show_error(str(e))
        pass
        # ARNOTODO: schedule current Download for removal?

    def videoservthread_set_status_callback(self,status):
        """ Called by HTTP serving thread """
        wx.CallAfter(self.videoserver_set_status_guicallback,status)

    def videoserver_set_status_guicallback(self,status):
        #print >>sys.stderr,"bg: Video server sets status callback",status
        # ARNOTODO: Report status to plugin
        pass

    #
    # reports vod stats collected periodically
    #
    def report_periodic_vod_stats(self,playing_dslist):
        event_reporter = Status.get_status_holder("LivingLab")
        if event_reporter is None:
            return
        
        for ds in playing_dslist:
            dw = ds.get_download()
            tdef = dw.get_def()
            b64_infohash = b64encode(tdef.get_infohash())
            
            vod_stats = ds.get_vod_stats()
            if TRIALLOG:
                print >>sys.stderr,"ULANCLOG: adding TRIAL VOD stats"
                if len(vod_stats) > 0:
                    #event_reporter.add_event(b64_infohash, "prebufp:%d" % vod_stats['prebuf']) # prebuffering time that was needed
                    event_reporter.create_and_add_event("stall", [b64_infohash, vod_stats['stall']]) # time the player stalled
                    event_reporter.create_and_add_event("late", [b64_infohash, vod_stats['late']]) # number of pieces arrived after they were due
                    event_reporter.create_and_add_event("dropped", [b64_infohash, vod_stats['dropped']]) # number of pieces lost
                    event_reporter.create_and_add_event("pos", [b64_infohash, vod_stats['pos']]) # playback position
            if NSPCLOG:
                print >>sys.stderr,"ULANCLOG: adding NSPC VOD stats"
                d = {}
                d['listening_port'] = self.s.get_listen_port()
                d['infohash'] = b64_infohash
                peerid = dw.get_peer_id()
                if peerid is None:
                    d['peerid'] = "None"
                else:
                    d['peerid'] = b64encode(peerid)
                if tdef.get_live():
                    d['auth_method'] = tdef.get_live_authmethod()
                else:
                    d['auth_method'] = "None"
                if len(vod_stats) > 0:
                    d['pieces_played'] = vod_stats['pos']
                    d['pieces_late'] = vod_stats['late']
                    d['pieces_dropped'] = vod_stats['dropped']
                    d['stalled_time'] = vod_stats['stall']
                else:
                    d['pieces_played'] = -1
                    d['pieces_late'] = -1
                    d['pieces_dropped'] = -1
                    d['stalled_time'] = 0
                if tdef.get_live():    
                    d['prebuff_used'] = MovieOnDemandTransporter.PREBUF_SEC_LIVE
                else:
                     d['prebuff_used'] = MovieOnDemandTransporter.PREBUF_SEC_VOD
                
                d['total_up'] = ds.get_total_transferred(UPLOAD)/1024.0
                d['total_down'] = ds.get_total_transferred(DOWNLOAD)/1024.0
                d['up_rate'] = ds.get_current_speed(UPLOAD)
                d['down_rate'] = ds.get_current_speed(DOWNLOAD)
                peerlist = ds.get_peerlist()
                d['num_peers'] = len(peerlist)
                splist = []
                for peer in peerlist:
                    sp = {}
                    sp['ip'] = peer['ip']
                    sp['port'] = peer['port']
                    sp['peerid'] = b64encode(peer['id'])
                    sp['direction'] = peer['direction']
                    if peer['g2g']:
                        sp['protocol'] = 'GiveToGet'
                    else:
                        sp['protocol'] = 'BitTorrent'
                    sp['g2gscore'] = str(peer['g2g_score'][0])+','+str(peer['g2g_score'][1])
                    sp['ip'] = peer['ip']
                    down = {}
                    down['total'] = peer['dtotal']/1024.0
                    down['rate'] = peer['downrate']/1024.0
                    down['behaviour_choke'] = 'Choked' if peer['dchoked'] else 'Unchoked'
                    down['behaviour_interest'] = 'Interested' if peer['dinterested'] else 'Uninterested'
                    sp['down'] = down
                    up = {}
                    up['total'] = peer['utotal']/1024.0
                    up['rate'] = peer['uprate']/1024.0
                    up['behaviour_choke'] = 'Choked' if peer['uchoked'] else 'Unchoked'
                    up['behaviour_interest'] = 'Interested' if peer['uinterested'] else 'Uninterested'
                    up['behaviour_type'] = 'Optimistic' if peer['optimistic'] else 'Normal'
                    sp['up'] = up
                    metapeer = {'peer':sp}
                    splist.append(metapeer)
                d['peer-list'] = splist
                
                statsd = {'statistics':d}
                event_reporter.create_and_add_event("statistics", [statsd]) 



    def gui_webui_remove_download(self,d2remove):
        """ Called when user has decided to remove a specific DL via webUI """
        if DEBUG:
            print >>sys.stderr,"bg: gui_webui_remove_download"
        self.gui_webui_halt_download(d2remove,stop=False)


    def gui_webui_stop_download(self,d2stop):
        """ Called when user has decided to stop a specific DL via webUI """
        if DEBUG:
            print >>sys.stderr,"bg: gui_webui_stop_download"
        self.gui_webui_halt_download(d2stop,stop=True)
        
        
    def gui_webui_restart_download(self,d2restart):
        """ Called when user has decided to restart a specific DL via webUI for sharing """
        duser = {'uic':None}
        self.dusers[d2restart] = duser
        d2restart.restart()


    def gui_webui_halt_download(self,d2halt,stop=False):
        """ Called when user has decided to stop or remove a specific DL via webUI.
        For stop the Download is not removed. """
        if d2halt in self.dusers:
            try:
                duser = self.dusers[d2halt]
                olduic = duser['uic'] 
                if olduic is not None:
                    print >>sys.stderr,"bg: gui_webui_halt_download: Oops, someone interested, removing anyway"
                    olduic.shutdown()
                if 'streaminfo' in duser:
                    # Download was already playing, clean up.
                    stream = duser['streaminfo']['stream']
                    stream.close() # Close original stream.
            finally: 
                del self.dusers[d2halt]
        if stop:
            BaseApp.stop_playing_download(self,d2halt)
        else:
            BaseApp.remove_playing_download(self,d2halt)


    def gui_webui_remove_all_downloads(self,ds2remove):
        """ Called when user has decided to remove all DLs via webUI """
        if DEBUG:
            print >>sys.stderr,"bg: gui_webui_remove_all_downloads"
            
        for d2remove in ds2remove:
            self.gui_webui_halt_download(d2remove,stop=False)
            
            
    def gui_webui_stop_all_downloads(self,ds2stop):
        """ Called when user has decided to stop all DLs via webUI """
        if DEBUG:
            print >>sys.stderr,"bg: gui_webui_stop_all_downloads"
            
        for d2stop in ds2stop:
            self.gui_webui_halt_download(d2stop,stop=True)


    def gui_webui_restart_all_downloads(self,ds2restart):
        """ Called when user has decided to restart all DLs via webUI """
        if DEBUG:
            print >>sys.stderr,"bg: gui_webui_restart_all_downloads"
            
        for d2restart in ds2restart:
            self.gui_webui_restart_download(d2restart)

    def i2i_kill_on_browser_gone(self):
        resched = True
        try:
            lastt = self.webIFmapper.lastreqtime
            
            #print >>sys.stderr,"bg: Test for self destruct: idle",time.time()-lastt,currentThread().getName()
            
            if time.time() - IDLE_BEFORE_SELFKILL > lastt:
                if self.iseedeadpeople:
                    #print >>sys.stderr,"bg: SHOULD HAVE self destructed, hardcore stylie"
                    resched = False
                    #os._exit(0)
                else:
                    #print >>sys.stderr,"bg: SHOULD HAVE self destructed"
                    self.iseedeadpeople = True
                    # No sign of life from statusbar, self destruct 
                    #wx.CallAfter(self.ExitMainLoop)            
        finally:
            if resched:
                self.i2is.add_task(self.i2i_kill_on_browser_gone,IDLE_BEFORE_SELFKILL/2)


    def ulanc_stats_am_i_playing_callback(self):
        return len(self.downloads_in_vodmode) > 0
        
        
    def schedule_ulanc_stats_gathering(self):
        if self.ulanc_stats_reporting_freq is None:
            self.ulanc_stats_gather_time = None
        else:
            # Gather and write stats to Reporter N secs before report is sent
            if self.ulanc_stats_gather_time is None:
                # First call, schedule N seconds before report is sent
                self.ulanc_stats_gather_time = time.time() + self.ulanc_stats_reporting_freq - (self.ulanc_stats_reporting_freq/2.0)
            else:
                # Next calls: schedule at frequency
                self.ulanc_stats_gather_time = time.time() + self.ulanc_stats_reporting_freq
            
            print >>sys.stderr,"bg: PHONEHOME: Next gathering in ",self.ulanc_stats_gather_time - time.time(),"seconds"
        

class BGInstanceConnection(InstanceConnection):
    
    def __init__(self,singsock,connhandler,readlinecallback,videoHTTPServer):
        InstanceConnection.__init__(self, singsock, connhandler, readlinecallback)
        
        self.bgapp = connhandler
        self.videoHTTPServer = videoHTTPServer
        self.urlpath = None
        self.cstreaminfo = {}
        self.shutteddown = False
        self.supportedvodevents = [VODEVENT_START,VODEVENT_PAUSE,VODEVENT_RESUME]
        self.startt = None
        self.torrenturl = None
        self.infohash = None
        self.reported_no_play = False

    def got_start(self,torrenturl):
        self.torrenturl = torrenturl
        self.startt = time.time()
        self.reported_no_play = False

    def set_streaminfo(self,streaminfo):
        """ Copy streaminfo contents and replace stream with a ControlledStream """
        """
        For each IC we create separate stream object and a unique path in the 
        HTTP server. This avoids nasty thread synchronization with the server
        when a new IC wants to play the same content. The Tribler Core stream
        does not allow multiple readers. This means we would have to stop
        the HTTP server from writing the stream to the old IC, before we
        can allow the new IC to read.
        
        We solved this as follows. The original Tribler Core stream is
        wrapped in a ControlledStream, one for each IC. When a new IC 
        wants to play we tell the old IC's ControlledStream to generate
        an EOF to the HTTP server, and tell the old IC to SHUTDOWN. We
        then either rewind the Tribler Core stream (VOD) or leave it (live)
        and tell the new IC to PLAY. The new ControlledStream will then
        be read by the HTTP server again.
        """
        self.cstreaminfo.update(streaminfo)
        stream = streaminfo['stream']
        cstream = ControlledStream( stream )
        self.cstreaminfo['stream'] = cstream

    # TODO nicer :-)
    # for the moment we just add a parameter for metadata requests
    def start_playback(self,metadata=None):
        """ Register cstream with HTTP server and tell IC to start reading """
        
        self.urlpath = URLPATH_CONTENT_PREFIX+'/'+infohash2urlpath(self.infohash)+'/'+str(random.random())
        
        # add metadata request if available
        if metadata is not None:
            self.urlpath += metadata
        else:
            self.videoHTTPServer.set_inputstream(self.cstreaminfo,self.urlpath)
        
        if DEBUG:
            print >> sys.stderr, "bg: Telling plugin to start playback of",self.urlpath

        self.play(self.get_video_url())
        
    def cleanup_playback(self):
        if DEBUG:
            print >>sys.stderr,'bg: ic: cleanup'
        # Cause HTTP server thread to receive EOF on inputstream
        if len(self.cstreaminfo) != 0:
            self.cstreaminfo['stream'].close()
            try:
                # TODO: get rid of del_inputstream lock
                # Arno, 2009-12-11: Take this out of critical path on MainThread
                http_del_inputstream_lambda = lambda:self.videoHTTPServer.del_inputstream(self.urlpath)
                self.bgapp.tqueue.add_task(http_del_inputstream_lambda,0) 
            except:
                print_exc()
        

    def get_video_url(self):
        return 'http://127.0.0.1:'+str(self.videoHTTPServer.get_port())+self.urlpath

    def play(self,url):
        self.write( 'PLAY '+url+'\r\n' )

    def pause(self):
        self.write( 'PAUSE\r\n' )
        
    def resume(self):
        self.write( 'RESUME\r\n' )

    def info(self,infostr):
        self.write( 'INFO '+infostr+'\r\n' )        

    # Arno, 2010-05-28: Convey the BGprocess won't be able to serve the content
    def error(self,infostr):
        self.write( 'ERROR '+infostr+'\r\n' )        

    # Arno, 2010-05-27: Stop playback
    def stop(self):
        # Stop playback
        self.write( 'STOP\r\n' )

    def shutdown(self):
        # SHUTDOWN Service
        if DEBUG:
            print >>sys.stderr,'bg: ic: shutdown'
        if not self.shutteddown:
            self.shutteddown = True
            self.cleanup_playback()
            
            self.write( 'SHUTDOWN\r\n' )
            # Will cause BaseApp.connection_lost() to be called, where we'll
            # handle what to do about the Download that was started for this
            # IC.
            try:
                self.close()
            except:
                print_exc()

    def get_supported_vod_events(self):
        return self.supportedvodevents
    
    def set_supported_vod_events(self,eventlist):
        self.supportedvodevents = eventlist

    def report_error(self,estr):
        event_reporter = Status.get_status_holder("LivingLab")
        if event_reporter is not None:
            values = [self.torrenturl,estr]
            print >>sys.stderr,"bg: ic: Reporting immediate error",`values`
            event_reporter.create_and_add_event("playback-error", values ) 
        

    def check_report_play_timeout(self,ds):
        """ A long time has passed between starting download, see if we got
            VODEVENT_START, and if not, report to logging reporter.
        """
        if self.startt is None or self.reported_no_play:
            return
        
        if self.startt+60.0 < time.time():
            # Long time after START
            self.reported_no_play = True
            if self.urlpath is None: 
                # No playback
                event_reporter = Status.get_status_holder("LivingLab")
                if event_reporter is not None:
                    d = ds.get_download()
                    b64_infohash = b64encode(d.get_def().get_infohash())
                    dlspeedstr = '%.2f' % (ds.get_current_speed(DOWNLOAD))
                    values = [b64_infohash, dlspeedstr]+ds.get_log_messages()
                    event_reporter.create_and_add_event("playback-error", values )
                    print >>sys.stderr,"bg: ic: Reporting playback didn't start after N s: ",`values` 

    def set_infohash(self,infohash):
        self.infohash = infohash
                
    def get_infohash(self):
        return self.infohash


class ControlledStream:
    """ A file-like object that throws EOF when closed, without actually closing
    the underlying inputstream. See BGInstanceConnection.set_streaminfo() for
    an explanation on how this is used. 
    """
    def __init__(self,stream):
        self.stream = stream
        self.done = False # Event()
        
    def read(self,nbytes=None):
        if not self.done:
            return self.stream.read(nbytes)
        else:
            return '' # EOF

    def seek(self,pos,whence=os.SEEK_SET):
        self.stream.seek(pos,whence)
        
    def close(self):
        self.done = True
        # DO NOT close original stream


class AtBitrateStream:
    """ Give from playback position plus a safe margin at video bitrate speed.
        On seeking resync the playback position and the safe margin.
    """

    # Give at bitrate speed policy: give from playback position + SAFE_MARGIN_TIME
    # at bitrate speed during STREAM_STATE_PLAYING, give at full speed during
    # STREAM_STATE_PREBUFFER. STREAM_STATE_TRANSITION indicates that the playback has
    # to start or that the user just seeked.

    # Safe buffer size in seconds
    SAFE_MARGIN_TIME = 10.0  # same as VideoOnDemand.py

    # Increment the bitrate by percentage (give more bandwidth to the player).
    BITRATE_SPEED_INCREMENT = 1.05 # +5%

    # Streaming status
    STREAM_STATE_TRANSITION = 0
    STREAM_STATE_PREBUFFER  = 1
    STREAM_STATE_PLAYING    = 2

    def __init__( self, stream, bitrate ):
        self.stream = stream
        self.done = False # Event()
        self.bitrate = bitrate
        self.safe_bytes = self.SAFE_MARGIN_TIME * bitrate
        self.stream_state = self.STREAM_STATE_TRANSITION
        self.last_time = 0.0
        self.playback = 0.0
        self.given_bytes_till = 0

    def has_to_sleep( self, nbytes ):
        curr_time = time.time()
        if self.stream_state is self.STREAM_STATE_TRANSITION:
            self.last_time = curr_time
            elapsed_time = 0.0
            self.stream_state = self.STREAM_STATE_PREBUFFER
        else:
            elapsed_time = curr_time - self.last_time
            self.last_time = curr_time

        self.playback += elapsed_time * self.BITRATE_SPEED_INCREMENT
        if self.stream_state is self.STREAM_STATE_PREBUFFER:
            played_bytes = self.playback * self.bitrate
            if played_bytes + self.safe_bytes <= self.given_bytes_till:
                self.stream_state = self.STREAM_STATE_PLAYING
            self.given_bytes_till += nbytes
            return 0.0
        else:
            delta_time = ( self.given_bytes_till / float( self.bitrate ) ) - ( self.playback + self.SAFE_MARGIN_TIME )
            if delta_time <= 0.0:
                self.stream_state = self.STREAM_STATE_PREBUFFER
            self.given_bytes_till += nbytes
            return max( 0.0, delta_time )

    def read(self,nbytes=None):
        if not self.done:
            to_give = self.stream.read( nbytes )
            sleep_time = self.has_to_sleep( nbytes )
            #print >>sys.stderr,"DIEGO DEBUG : SLEEP_time", sleep_time
            if sleep_time > 0.0:
                time.sleep( sleep_time )
            return to_give
        else:
            return '' # EOF

    def seek(self,pos,whence=os.SEEK_SET):
        self.stream.seek(pos,whence)
        self.stream_state = self.STREAM_STATE_TRANSITION
        self.given_bytes_till = pos
        self.playback = pos / float( self.bitrate )
        
    def close(self):
        self.done = True
        # DO NOT close original stream


##############################################################
#
# Main Program Start Here
#
##############################################################
def run_bgapp(appname,appversion,i2iport,sessport,httpport, params = None,killonidle=False):
    """ Set sys.argv[1] to "--nopause" to inform the Core that the player
    doesn't support VODEVENT_PAUSE, e.g. the SwarmTransport.
    """ 
    if params is None:
        params = [""]
    
    if len(sys.argv) > 1:
        params = sys.argv[1:]

    global KILLONIDLE
    KILLONIDLE = killonidle

    """
    # Create single instance semaphore
    # Arno: On Linux and wxPython-2.8.1.1 the SingleInstanceChecker appears
    # to mess up stderr, i.e., I get IOErrors when writing to it via print_exc()
    #
    if sys.platform != 'linux2':
        single_instance_checker = wx.SingleInstanceChecker(appname+"-"+ wx.GetUserId())
    else:
        single_instance_checker = LinuxSingleInstanceChecker(appname)
    """
    # Arno, 2010-03-05: This is a vital print that must not be removed, otherwise
    # the program will just say "15:29:02: Deleted stale lock file '/home/arno/SwarmPlugin-arno'"
    # and exit after a restart of the instance :-(
    #
    print >>sys.stderr,"bg: Test if already running"
    single_instance_checker = wx.SingleInstanceChecker(appname+"-"+ wx.GetUserId())
    if single_instance_checker.IsAnotherRunning():
        print >>sys.stderr,"bg: Already running, exit"
        os._exit(0)

    arg0 = sys.argv[0].lower()
    if arg0.endswith('.exe'):
        installdir = os.path.abspath(os.path.dirname(sys.argv[0]))
    else:
        installdir = os.getcwd()  

    # Launch first single instance
    app = BackgroundApp(0, appname, appversion, params, single_instance_checker, installdir, i2iport, sessport, httpport)
    s = app.s

    # Enable P2P-Next ULANC logging.
    if PHONEHOME: 
        status = Status.get_status_holder("LivingLab")
        id = encodestring(s.get_permid()).replace("\n","")
        
        # Arno, 2011-02-03: Use NSPCLOG SPEC
        app.ulanc_stats_reporting_freq = 15 * 60 # Arno, 2011-12-1: removed test 30! 
        reporter = LivingLabReporter.LivingLabPeriodicReporter("Living lab CS reporter", app.ulanc_stats_reporting_freq, id, ext_ip_callback=s.get_external_ip,activity_callback=app.ulanc_stats_am_i_playing_callback, report_if_no_events=True)  
        status.add_reporter(reporter)
    else:
        app.ulanc_stats_reporting_freq = None
    app.schedule_ulanc_stats_gathering()

    app.MainLoop()

    if PHONEHOME:
        reporter.stop()

    print >>sys.stderr,"Sleeping seconds to let other threads finish"
    time.sleep(2)

    if not ALLOW_MULTIPLE:
        del single_instance_checker
        
    # Ultimate catchall for hanging popen2's and what not
    os._exit(0)

