# Written by Arno Bakker, George Milescu
# see LICENSE.txt for license information

import sys

from Tribler.Core.simpledefs import *
from Tribler.Core.DownloadConfig import DownloadConfigInterface
from Tribler.Core.APIImplementation.DownloadRuntimeConfigBaseImpl import DownloadRuntimeConfigBaseImpl
from Tribler.Core.exceptions import OperationNotPossibleAtRuntimeException

DEBUG = False

# 10/02/10 Boudewijn: pylint points out that member variables used in
# DownloadRuntimeConfig do not exist.  This is because they are set in
# Tribler.Core.Download which is a subclass of DownloadRuntimeConfig.
#
# We disable this error
# pylint: disable-msg=E1101

class DownloadRuntimeConfig(DownloadRuntimeConfigBaseImpl):
    """
    Implements the Tribler.Core.DownloadConfig.DownloadConfigInterface
    
    Only implement the setter for parameters that are actually runtime
    configurable here. Default behaviour implemented by BaseImpl.
    
    DownloadConfigInterface: All methods called by any thread
    """
    def set_max_speed(self,direct,speed):
        if DEBUG:
            print >>sys.stderr,"Download: set_max_speed",`self.get_def().get_metainfo()['info']['name']`,direct,speed
        #print_stack()
        
        self.dllock.acquire()
        try:
            # Don't need to throw an exception when stopped, we then just save the new value and
            # use it at (re)startup.
            if self.sd is not None:
                set_max_speed_lambda = lambda:self.sd is not None and self.sd.set_max_speed(direct,speed,None)
                self.session.lm.rawserver.add_task(set_max_speed_lambda,0)
                
            # At the moment we can't catch any errors in the engine that this 
            # causes, so just assume it always works.
            DownloadConfigInterface.set_max_speed(self,direct,speed)
        finally:
            self.dllock.release()

    def set_video_event_callback(self,usercallback,dlmode=DLMODE_VOD):
        """ Note: this currently works only when the download is stopped. """
        self.dllock.acquire()
        try:
            DownloadConfigInterface.set_video_event_callback(self,usercallback,dlmode=dlmode)
        finally:
            self.dllock.release()

    def set_video_events(self,events):
        """ Note: this currently works only when the download is stopped. """
        self.dllock.acquire()
        try:
            DownloadConfigInterface.set_video_events(self,events)
        finally:
            self.dllock.release()

    def set_mode(self,mode):
        """ Note: this currently works only when the download is stopped. """
        self.dllock.acquire()
        try:
            DownloadConfigInterface.set_mode(self,mode)
        finally:
            self.dllock.release()

    def set_selected_files(self,files):
        """ Note: this currently works only when the download is stopped. """
        self.dllock.acquire()
        try:
            DownloadConfigInterface.set_selected_files(self,files)
            self.set_filepieceranges(self.tdef.get_metainfo())
        finally:
            self.dllock.release()

    def set_max_conns_to_initiate(self,nconns):
        self.dllock.acquire()
        try:
            if self.sd is not None:
                set_max_conns2init_lambda = lambda:self.sd is not None and self.sd.set_max_conns_to_initiate(nconns,None)
                self.session.lm.rawserver.add_task(set_max_conns2init_lambda,0.0)
            DownloadConfigInterface.set_max_conns_to_initiate(self,nconns)
        finally:
            self.dllock.release()

    def set_max_conns(self,nconns):
        """ Arno, 2011-06-28: This now actively limits the number of connections
        to the new limit, by closing the connections with the lowest combined 
        ul+dl speeds.
        """
        self.dllock.acquire()
        try:
            if self.sd is not None:
                set_max_conns_lambda = lambda:self.sd is not None and self.sd.set_max_conns(nconns,None)
                self.session.lm.rawserver.add_task(set_max_conns_lambda,0.0)
            DownloadConfigInterface.set_max_conns(self,nconns)
        finally:
            self.dllock.release()
    
    
    #
    # ProxyService_
    #
    def set_proxy_mode(self,value):
        """ Set the proxymode for current download
        .
        @param value: the proxyservice mode: PROXY_MODE_OFF, PROXY_MODE_PRIVATE or PROXY_MODE_SPEED
        """
        self.dllock.acquire()
        try:
            DownloadConfigInterface.set_proxy_mode(self, value)
        finally:
            self.dllock.release()

    
    def set_no_helpers(self,value):
        """ Set the maximum number of helpers used for a download.
        @param value: a positive integer number
        """
        self.dllock.acquire()
        try:
            return DownloadConfigInterface.set_no_helpers(self, value)
        finally:
            self.dllock.release()

    #
    # _ProxyService
    #
       
