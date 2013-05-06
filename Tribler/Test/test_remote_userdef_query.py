# Written by Arno Bakker
# see LICENSE.txt for license information

# TODO: let one hit to SIMPLE+METADATA be P2PURL
import unittest
import os
import sys
import time
from Tribler.Core.Utilities.Crypto import sha
from types import StringType, DictType, IntType
from M2Crypto import EC

from Tribler.Test.test_as_server import TestAsServer
from olconn import OLConnection
from Tribler.Core.API import *
from Tribler.Core.BitTornado.bencode import bencode,bdecode
from Tribler.Core.BitTornado.BT1.MessageID import *


DEBUG=True


class TestRemoteUserDefQuery(TestAsServer):
    """ 
    Testing QUERY message with user-defined query prefixes.
    """
    
    def setUpPreSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPreSession(self)
        # Enable remote query
        self.config.set_remote_query(True)
        self.config.set_torrent_collecting_dir(os.path.join(self.config_path, "tmp_torrent_collecting"))

    def setUpPostSession(self):
        """ override TestAsServer """
        TestAsServer.setUpPostSession(self)

        # Define a user-defined query prefix
        qh = {}
        qh['USER'] = self.handler4USERprefix
        self.session.set_user_query_handlers(qh)

    def tearDown(self):
        TestAsServer.tearDown(self)
      

    def test_all(self):
        """ 
            I want to start a Tribler client once and then connect to
            it many times. So there must be only one test method
            to prevent setUp() from creating a new client every time.

            The code is constructed so unittest will show the name of the
            (sub)test where the error occured in the traceback it prints.
        """
        
        # 1. test good QUERY
        self.subtest_good_simple_query("USER hallo")

    #
    # Good QUERY
    #
    def subtest_good_simple_query(self,query):
        """ 
            test good QUERY messages: SIMPLE
        """
        print >>sys.stderr,"test: good user-defined query",query
        s = OLConnection(self.my_keypair,'localhost',self.hisport)
        msg = self.create_good_simple_query(query)
        s.send(msg)
        resp = s.recv()
        if len(resp) > 0:
            print >>sys.stderr,"test: good QUERY: got",getMessageName(resp[0])
        self.assert_(resp[0] == QUERY_REPLY)
        self.check_rquery_reply("USER",resp[1:])
        time.sleep(10)
        # the other side should not have closed the connection, as
        # this is all valid, so this should not throw an exception:
        s.send('bla')
        s.close()


    def create_good_simple_query(self,query):
        d = {}
        d['q'] = query
        d['id'] = 'a' * 20
        return self.create_payload(d)


    def create_payload(self,r):
        return QUERY+bencode(r)

    def handler4USERprefix(self,permid,query,qid,hitscallback):
        """ Tribler is receiving a user-defined query """
        
        print >>sys.stderr,"test: handler4USERprefix: Got",`permid`,`query`,`qid`,`hitscallback`
        
        self.assert_(isinstance(permid,str))
        self.assert_(isinstance(query,unicode))
        self.assert_(callable(hitscallback))

        self.assertEquals(query,"USER hallo")

        hits = 'goodbye'
        
        # Send reply
        hitscallback(permid,qid,None,hits)
        

    def check_rquery_reply(self,querytype,data):
        d = bdecode(data)
        
        print >>sys.stderr,"test: Got reply",`d`
        
        self.assert_(type(d) == DictType)
        self.assert_(d.has_key('a'))
        self.assert_(d.has_key('id'))
        id = d['id']
        self.assert_(type(id) == StringType)

        self.assertEquals(d['a'],"goodbye")


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestRemoteUserDefQuery))
    
    return suite

if __name__ == "__main__":
    unittest.main()

