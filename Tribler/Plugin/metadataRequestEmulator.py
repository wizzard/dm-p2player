# BAD CLIENT: SENDS 
# GET /path\r\n
# HTTP/1.1\r\n
# Host: localhost:6878\r\n
# \r\n
#
# Then Python HTTP server doesn't correctly send headers.

import os
import sys
import socket
import urlparse
import time
import httplib
from optparse import OptionParser


class PluginEmulator:
    
    def __init__(self,port,cmd,param):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1',port))
        msg = cmd+' '+param+'\r\n'
        s.send(msg)
        #s.close()
        st = time.time()
        data = s.recv(1024)
        while not data.startswith("PLAY"):
            data = s.recv(1024)
        et = time.time()
        diff = et - st
        print >>sys.stderr,"START + REPLY TOOK",diff
        st = et

        url = data[len("PLAY "):]
        print >>sys.stderr,"metadataEm.: local URL: ", url
        p = urlparse.urlparse(url)
        
        conn = httplib.HTTPConnection("127.0.0.1", 6877)        

        conn.request("GET", url, "HTTP/1.0")
        r = conn.getresponse()

        if r.status == 200:
            print>>sys.stderr, "got headers", r.getheaders()
            data = r.read()

            if len(data) > 0:
                
                et = time.time()
                diff = et - st
                print >>sys.stderr,"metadataEm.: Got HTTP restponse ", data
                print >>sys.stderr,"PLAY TOOK",diff

            else:
                print >>sys.stderr,"----------------------------------"
                print >>sys.stderr,"|  metadataEm.: No response!!!    |"
                print >>sys.stderr,"----------------------------------"
                    
        else:
            print>>sys.stderr, "GOT status:", r.status
            
        conn.close()

def exitOnInputError(parser, message=None):
    if message:
        print "\n" + "Reason for failure: " + message + "\n"
    parser.print_help()
    sys.exit(0)
        
if __name__ == "__main__":

    usage = "usage: %prog [options]\n\n  Issues request to SwarmPlugin and returns a request."
    version = '0.1'

    # Command line options
    parser = OptionParser(usage, version="%prog v" + version)
    parser.add_option("-f", "--file", help = "Torrent file to query", action="store", dest="file", default = None)
    parser.add_option("-q", "--query", help = "Parameter to query", action="store", dest="query", default = None)
    (options, args) = parser.parse_args()
    
    if options.query == None:
        exitOnInputError(parser, "Query parameter '-q' needs to be defined!")

    if options.file == None:
        #options.file = "file:///home/dusan/delo/src/metadata-m32/today_20101118-0645a.tstream"
        options.file = "file:///d:/build/metadata-m32/tol-w-metadata.tstream"
        print>>sys.stderr, "Will query default torrent file:", options.file
    else:
        purl = urlparse.urlparse(options.file)
        if purl[0] == '':
            if os.path.exists(options.file):
                options.file = 'file://' + options.file
            else:
                exitOnInputError(parser, "File should be specified as url (file://) and besides the specified file '" + options.file + "'does not exists!")

    pe = PluginEmulator(62063,"START", options.file + "/metadata?" + options.query)
     
        
