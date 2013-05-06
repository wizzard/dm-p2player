# Written by Vladimir Jovanovikj
# see LICENSE.txt for license information

import sys
import os

from threading import Event, Thread, currentThread
from socket import error as socketerror
from time import sleep
import tempfile
from traceback import print_exc
import shutil

from Tribler.Core.BitTornado.RawServer import RawServer
from Tribler.Core.BitTornado.ServerPortHandler import MultiHandler
from Tribler.Core.BitTornado.BT1.MessageID import GET_METADATA

from M2Crypto import EC
from Tribler.Core.Overlay.SecureOverlay import SecureOverlay, OLPROTO_VER_CURRENT
import Tribler.Core.CacheDB.sqlitecachedb as sqlitecachedb  
from Tribler.Core.CacheDB.SqliteCacheDBHandler import PeerDBHandler
from Tribler.Core.Utilities.utilities import show_permid_short


import time
from base64 import encodestring,decodestring


import unittest

import calendar

from Tribler.Core.Overlay import permid
from Tribler.Core.BitTornado.BT1.MessageID import *

from Tribler.Core.ClosedSwarm.ECS_ClosedSwarms import *
from Tribler.Core.ClosedSwarm.ECS_AuthorizationEngine import *

from Tribler.Core.BitTornado.bencode import bencode, bdecode

from Tribler.Core.ClosedSwarm.conf import ecssettings
ecssettings.DEBUG_PEER_DISCOVERY = False

DEBUG = False

protocol_name = "BitTorrent protocol"
cs_infohash = 'P2P-Next\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'

PORT = "PORT"
CS_MESSAGE = "CS"
KEEP_ALIVE = "KA"

class CS_Connection:
    def __init__(self, handler, connection, locally_initiated=None, ecs=None):    # connection: SingleSocket
        self.handler = handler
        self.connection = connection
        self.locally_initiated = locally_initiated

        self.remote_port = None
        self.port = self.handler.peer.listen_port

        # ECS_API per connection
        self.ecs = ecs

        self.closed = False
        #self.buffer = StringIO()
        self.complete = False
        self.handler.sched(self._auto_close, 15)

    def set_ecs(self, ecs):
        self.ecs = ecs

    def _auto_close(self):
        if not self.complete:
            self.close()
        
    def close(self):
        if not self.closed:
            if self.ecs is not None:
                self.ecs.ecs_swarm_manager.unregister_connection(self)
            self.connection.close()
            self.closed = True
            if DEBUG:
                print "Connection closed"

    def data_came_in(self, s, data):
        if DEBUG:
            print "Thread: %s: Data came in: %d"%(currentThread().getName(), len(data))
        data = bdecode(data)
        self.got_message(data)

    def got_message(self, message):
        if message[0] == CS_MESSAGE:
            self.got_cs_message(message[1:])
        if message[0] == PORT:
            self.remote_port = message[1]

    def got_cs_message(self, message):
        response = self.ecs.got_cs_message(message)
        if not isinstance(response, bool):
            self._send_cs_message(response)

    def _send_cs_message(self, message):
        message = [CS_MESSAGE] + message
        self.write(message)

    def write(self, data):
        self.connection.connected = True
        bdata = bencode(data)
        self.connection.write(bdata)

    def connection_flushed(self, ss):
        pass

    def connection_lost(self, ss):
        self.closed = True

    def get_myip(self, real=False):
        return self.connection.get_myip(real)
    
    def get_myport(self, real=False):
        return self.connection.get_myport(real)
        
    def get_ip(self, real=False):
        return self.connection.get_ip(real)

    def get_port(self, real=False):
        #return self.connection.get_port(real)
        return self.remote_port

    def get_extend_listenport(self, real=False):
        #return self.connection.get_port(real)
        return self.remote_port

    def is_locally_initiated(self):
        return self.locally_initiated


class CS_SocketHandler:
    def __init__(self, peer, ecs_cm=None):
        self.peer = peer
        self.ecs_cm = ecs_cm
        # self.rawserver = self.peer.rawserver
        self.sock_hand = self.peer.rawserver.sockethandler
        self.connections = {}
        self.sched = self.peer.rawserver.add_task

    def get_handler(self):
        return self

    def set_ecs_connection_manager(self, ecs_cm):
        self.ecs_cm = ecs_cm

    def start_connection(self, dns):
        ss = self.sock_hand.start_connection(dns)
        cs_con = CS_Connection(self, ss, locally_initiated=True)
        ecs = self.ecs_cm.register_connection(cs_con)
        cs_con.set_ecs(ecs)
        self.connections[ss] = cs_con
        ss.set_handler(cs_con)
        port_message = [PORT, self.peer.listen_port]
        cs_con.write(port_message)
        sleep(1)
        cs_message = ecs.start_ecs()
        cs_con._send_cs_message(cs_message)
        return cs_con

    def external_connection_made(self, ss):
        cs_con = CS_Connection(self, ss, locally_initiated=False)
        ecs = self.ecs_cm.register_connection(cs_con)
        cs_con.set_ecs(ecs)
        self.connections[ss] = cs_con
        ss.set_handler(cs_con)

# Thread must come as first parent class!
class Peer(Thread):
    def __init__(self, testcase, port, torrent_id, ecsmanager, epoa, reqservice, max_ecs_peers=None):
        Thread.__init__(self)
        self.setDaemon(True)

        self.testcase = testcase
        self.doneflag = Event()
        self.ecsmanager = ecsmanager
        self.torrent_id = torrent_id
        self.epoa = epoa
        self.reqservice = reqservice

        config = {}
        config['timeout_check_interval'] = 100000
        config['timeout'] = 100000
        config['ipv6_enabled'] = 0
        config['minport'] = port
        config['maxport'] = port+15
        config['random_port'] = 0
        config['bind'] = ''
        config['ipv6_binds_v4'] = 0
        config['max_message_length'] = 2 ** 23

        self.info = None

        self.rawserver = RawServer(self.doneflag,
                                   config['timeout_check_interval'],
                                   config['timeout'],
                                   ipv6_enable = config['ipv6_enabled'],
                                   failfunc = self.report_failure,
                                   errorfunc = self.report_error)
        while True:
            try:
                self.listen_port = self.rawserver.find_and_bind(0, config['minport'], config['maxport'], config['bind'], reuse = True,
                                                                ipv6_socket_style = config['ipv6_binds_v4'], randomizer = config['random_port'])
                if DEBUG:
                    print >> sys.stderr,"test: Got listen port", self.listen_port
                break
            except socketerror, e:
                self.report_failure(str(e))
                msg = "Couldn't not bind to listen port - " + str(e)
                self.report_failure(msg)
                return

        self.ecs_cm = self.ecsmanager.get_swarm_manager(self.torrent_id)
        self.ecs_cm.set_poa(self.epoa)
        self.ecs_cm.set_reqservice(self.reqservice)
        self.cs_sockethandler = CS_SocketHandler(self)
        self.ecs_cm.set_scheduler(self.cs_sockethandler.sched)
        if max_ecs_peers:
            self.ecs_cm.set_max_ecs_peers(max_ecs_peers)
        self.cs_sockethandler.set_ecs_connection_manager(self.ecs_cm)

        self.rawserver.sockethandler.set_handler(self.cs_sockethandler.get_handler())        
        self.rawserver.add_task(self.dummy_task,0)

    def run(self):
        if DEBUG:
            print >> sys.stderr,"test: MyServer: run called by",currentThread().getName(),'\n'
        #self.multihandler.listen_forever()
        self.rawserver.listen_forever(self.cs_sockethandler)

    def report_failure(self,msg):
        self.testcase.assertRaises(Exception, self.report_failure)
        #print "Failure:", msg

    def report_error(self,msg):
        self.testcase.assertRaises(Exception, self.report_error)
        #print "Error:", msg

    def dummy_task(self):
        self.rawserver.add_task(self.dummy_task,1)

    def get_ext_ip(self):
        return '127.0.0.1'

    def shutdown(self):
        self.doneflag.set()
        self.rawserver.shutdown()

    def eprint(self):
        if DEBUG:
            print "Functin Eprint at your service", currentThread().getName()


class ECS_HighLevelTest(unittest.TestCase):
    
    def setUp(self):

        # Create and set ECS related info/object
        (self.keypair1, self.pub_key1) = generate_cs_keypair()
        (self.keypair2, self.pub_key2) = generate_cs_keypair()
        (self.keypair3, self.pub_key3) = generate_cs_keypair()

        (self.torrent_keypair, self.torrent_pub_key) = generate_cs_keypair()
        # The torrent file actually contains a list of torrent public keys, encoded in base64
        self.torrent_pub_keys = [encodestring(self.torrent_pub_key).replace("\n", "")]
        self.torrent_id = "1234"

        self.expire_time1 = calendar.timegm(time.gmtime()) + 1000
        self.expire_time2 = calendar.timegm(time.gmtime()) + 1000
        self.expire_time3 = calendar.timegm(time.gmtime()) + 1000

        self.ecsmanager1 = ECS_Manager.getInstance(keypair=self.keypair1)
        self.ecsmanager1.resetSingleton()
        self.ecsmanager2 = ECS_Manager.getInstance(keypair=self.keypair2)
        self.ecsmanager2.resetSingleton()
        self.ecsmanager3 = ECS_Manager.getInstance(keypair=self.keypair3)
        self.ecsmanager3.resetSingleton()

        self.ecsmanager1.register_torrent(self.torrent_id, self.torrent_pub_keys)
        self.ecsmanager2.register_torrent(self.torrent_id, self.torrent_pub_keys)
        self.ecsmanager3.register_torrent(self.torrent_id, self.torrent_pub_keys)


    def tearDown(self):
        sleep(2)

    #@skip("skip")
    def test_epoa_verification_scheduling(self):
        # Set rules       
        now = time.gmtime()
        now_day_hour = (now.tm_wday + 1) * 100 + now.tm_hour

        self.rules1 = "GEOLOCATION = 'SI' && PRIORITY <= 30"
        self.rules2 = "GEOLOCATION = 'SI' && PRIORITY <= 10 && DAY_HOUR < %s" % str(now_day_hour + 1)

        # POA set to expire in 10 sec
        self.expire_time1 = calendar.timegm(time.gmtime()) + 10

        # Create the extended PoAs
        self.epoa1 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time1)

        self.epoa2 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key2,
                                 self.rules2,
                                 self.expire_time2)

        # Set ReqService
        self.reqservice1 = [['PRIORITY', '30']]
        self.reqservice2 = [['PRIORITY', '2']]

        # Start the peers (threads)
        self.peer1 = Peer(self, 10000, self.torrent_id, self.ecsmanager1, self.epoa1, self.reqservice1)
        self.peer2 = Peer(self, 20000, self.torrent_id, self.ecsmanager2, self.epoa2, self.reqservice2)
        self.peer1.start()
        self.peer2.start()
        sleep(2) # let threads start

        con = self.peer1.cs_sockethandler.start_connection(("127.0.0.1", 20000))
        sleep(2)
        self.assertEqual(con.ecs.dl_ecs.info, 1)
        sleep(10)
        self.assertEqual(con.ecs.dl_ecs.info, 4)
        
        # Shutdown the peers
        self.peer1.shutdown()
        self.peer2.shutdown()

    #@skip("skip")
    def test_suggest_peers(self):
        # Set rules
        self.rules1 = "GEOLOCATION = 'SI' && PRIORITY <= 10"
        self.rules2 = "GEOLOCATION = 'SI' && PRIORITY <= 10"
        self.rules3 = "GEOLOCATION = 'SI' && PRIORITY <= 10"

        # Create the extended PoAs
        self.epoa1 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time1)

        self.epoa2 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key2,
                                 self.rules2,
                                 self.expire_time2)

        self.epoa3 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key3,
                                 self.rules3,
                                 self.expire_time3)

        # Set ReqService
        self.reqservice1 = [['PRIORITY', '10']]
        self.reqservice2 = [['PRIORITY', '10']]
        self.reqservice3 = [['PRIORITY', '10']]

        # Start the peers (threads)
        self.peer1 = Peer(self, 10000, self.torrent_id, self.ecsmanager1, self.epoa1, self.reqservice1)
        self.peer2 = Peer(self, 20000, self.torrent_id, self.ecsmanager2, self.epoa2, self.reqservice2)
        self.peer3 = Peer(self, 30000, self.torrent_id, self.ecsmanager3, self.epoa3, self.reqservice3)

        self.peer1.start()
        self.peer2.start()
        self.peer3.start()
        sleep(2) # let threads start

        self.assertEqual(len(self.peer1.ecs_cm.connections), 0)

        con2 = self.peer2.cs_sockethandler.start_connection(("127.0.0.1", 10000))
        sleep(2)
        self.assertEqual(con2.ecs.dl_ecs.info, 1)
        self.assertEqual(len(self.peer1.ecs_cm.connections), 1)

        con3 = self.peer3.cs_sockethandler.start_connection(("127.0.0.1", 10000))
        sleep(2)
        self.assertEqual(con3.ecs.dl_ecs.info, 1)
        self.assertEqual(con3.ecs.dl_ecs.peers, [('127.0.0.1', 20000)])
        self.assertEqual(len(self.peer1.ecs_cm.connections), 2)
        
        # Shutdown the peers
        self.peer1.shutdown()
        self.peer2.shutdown()
        self.peer3.shutdown()


    #@skip("skip")
    def test_terminate_according_priority(self):
        # Set rules       
        self.rules1 = "GEOLOCATION = 'SI' && PRIORITY <= 20"
        self.rules2 = "GEOLOCATION = 'SI' && PRIORITY <= 10"
        self.rules3 = "GEOLOCATION = 'SI' && PRIORITY <= 20"

        # Create the extended PoAs
        self.epoa1 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time1)

        self.epoa2 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key2,
                                 self.rules2,
                                 self.expire_time2)

        self.epoa3 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key3,
                                 self.rules3,
                                 self.expire_time3)
        
        # Set ReqService
        self.reqservice1 = [['PRIORITY', '20']]
        self.reqservice2 = [['PRIORITY', '10']]
        self.reqservice3 = [['PRIORITY', '20']]

        # Start the peers (threads)
        self.peer1 = Peer(self, 10000, self.torrent_id, self.ecsmanager1, self.epoa1, self.reqservice1, 1)
        self.peer2 = Peer(self, 20000, self.torrent_id, self.ecsmanager2, self.epoa2, self.reqservice2)
        self.peer3 = Peer(self, 30000, self.torrent_id, self.ecsmanager3, self.epoa3, self.reqservice3)

        self.peer1.start()
        self.peer2.start()
        self.peer3.start()
        sleep(2) # let threads start

        self.assertEqual(len(self.peer1.ecs_cm.connections), 0)

        con2 = self.peer2.cs_sockethandler.start_connection(("127.0.0.1", 10000))
        sleep(2)
        self.assertEqual(con2.ecs.dl_ecs.info, 1)
        self.assertEqual(len(self.peer1.ecs_cm.connections), 1)

        con3 = self.peer3.cs_sockethandler.start_connection(("127.0.0.1", 10000))
        sleep(3)
        self.assertEqual(con3.ecs.dl_ecs.info, 1)
        self.assertEqual(len(self.peer1.ecs_cm.connections), 1)
        self.assertEqual(con2.ecs.dl_ecs.info, 4)
        
        # Shutdown the peers
        self.peer1.shutdown()
        self.peer2.shutdown()
        self.peer3.shutdown()


# # test_cases = (ECS_ExtendedPOATest, ECS_MessageExchangeTest, ECS_AuthorizationEngineTest)
# test_cases = (ECS_HighLevelTest,)
# suite = TestSuite()
# for test_class in test_cases:
#     tests = TestLoader().loadTestsFromTestCase(test_class)
#     suite.addTests(tests)
# # suite = TestLoader().loadTestsFromTestCase(ECS_ExtendedPOATest)
# TextTestRunner(verbosity=2).run(suite)



def suite():
    suite = unittest.TestSuite()
    tests = unittest.TestLoader().loadTestsFromTestCase(ECS_HighLevelTest)
    suite.addTests(tests)
    return suite

if __name__ == "__main__":
    unittest.main()
