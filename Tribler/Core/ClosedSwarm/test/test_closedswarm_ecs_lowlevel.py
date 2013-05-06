# Written by Vladimir Jovanovikj
# see LICENSE.txt for license information
#
import time
from base64 import encodestring,decodestring

import unittest

import os.path
import calendar

from M2Crypto import EC

from Tribler.Core.Overlay import permid
from Tribler.Core.BitTornado.BT1.MessageID import *

from Tribler.Core.ClosedSwarm.ECS_ClosedSwarms import *
from Tribler.Core.ClosedSwarm.ECS_AuthorizationEngine import *
from Tribler.Core.ClosedSwarm.conf import ecssettings

ecssettings.DEBUG_PEER_DISCOVERY = False

class Tst_Con:
    def __init__(self):
        pass

    def get_ip(self):
        return "127.0.0.1"

class Tst_Connection:
    def __init__(self):
        self.connection = Tst_Con()
        pass

    def set_ecs(self, ecs):
        pass

    def _auto_close(self):
        pass
        
    def close(self):
        pass

    def data_came_in(self, s, data):
        pass

    def got_message(self, message):
        pass

    def got_cs_message(self, message):
        pass

    def _send_cs_message(self, message):
        pass

    def write(self, data):
        pass

    def connection_flushed(self, ss):
        pass

    def connection_lost(self, ss):
        pass

    def get_ip(self):
        return "127.0.0.1"
        pass

    def get_port(self):
        #return 10000
        pass

    def get_extend_listenport(self):
        pass

    def is_locally_initiated(self):
        pass

def scheduler(param1, param2):
    pass


#@skip("Skip")
class ECS_ExtendedPOATest(unittest.TestCase):

    def setUp(self):
        (self.keypair, self.pub_key) = generate_cs_keypair()
        (self.torrent_keypair, self.torrent_pub_key) = generate_cs_keypair()
        # The torrent file actually contains a list of torrent public keys, encoded in base64
        self.torrent_pub_keys = [encodestring(self.torrent_pub_key).replace("\n", "")]

        self.torrent_id = "1234"
        self.expire_time = calendar.timegm(time.gmtime()) + 60
        self.rules = "GEOLOCATION = 'SI' && PRIORITY = 1"
        
        # Create the certificate for this torrent ("proof of access")
        self.epoa = create_epoa(self.torrent_id,
                                self.torrent_keypair,
                                self.pub_key,
                                self.rules,
                                self.expire_time)
        assert isinstance(self.epoa, EPOA)


    def _verify_epoas(self, epoa_a, epoa_b):
        self.assertEquals(epoa_a.torrent_id, epoa_b.torrent_id)
        self.assertEquals(epoa_a.torrent_pub_key, epoa_b.torrent_pub_key)
        self.assertEquals(epoa_a.node_pub_key, epoa_b.node_pub_key)
        self.assertEquals(epoa_a.rules, epoa_b.rules)
        self.assertEquals(epoa_a.expire_time, epoa_b.expire_time)
        self.assertEquals(epoa_a.signature, epoa_b.signature)

        
    def test_epoa_serialization(self):

        serialized = self.epoa.serialize()
        deserialized = EPOA.deserialize(serialized)
        self._verify_epoas(self.epoa, deserialized)
        deserialized.verify()
      
        # Also serialize/deserialize using lists
        serialized = self.epoa.serialize_to_list()
        deserialized = self.epoa.deserialize_from_list(serialized)
        self._verify_epoas(self.epoa, deserialized)
        deserialized.verify()

    def test_epoa_expire_time(self):

        # Test expired EPOA
        self.expire_time = calendar.timegm(time.gmtime()) - 60
        self.epoa = create_epoa(self.torrent_id,
                                self.torrent_keypair,
                                self.pub_key,
                                self.rules,
                                self.expire_time)
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has expired", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)

        # Test expired EPOA, with intentional manually changed expry time
        self.epoa.expire_time += 120
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has invalid signature", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)

        # Test valid EPOA, which has not expired
        self.expire_time = calendar.timegm(time.gmtime()) + 60
        self.epoa = create_epoa(self.torrent_id,
                                self.torrent_keypair,
                                self.pub_key,
                                self.rules,
                                self.expire_time)
        self.epoa.verify()
    
    #@skip("Skip")
    def test_epoa_fields_signature(self):
        # Test intentional manual changes of EPOA fields.

        temp = self.epoa.torrent_id
        self.epoa.torrent_id = 0
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has invalid signature", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)
        self.epoa.torrent_id = temp

        temp = self.epoa.torrent_pub_key
        self.epoa.torrent_pub_key = 0
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has invalid signature", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)
        self.epoa.torrent_pub_key = temp

        temp = self.epoa.node_pub_key
        self.epoa.node_pub_key = 0
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has invalid signature", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)
        self.epoa.node_pub_key = temp

        temp = self.epoa.rules
        self.epoa.rules = ""
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has invalid signature", self.epoa.verify,)
        self.assertRaises(InvalidEPOAException, self.epoa.verify,)
        self.epoa.rules = temp

    #@skip("Skip")
    def test_dh_key(self):
        pk1 = EC.pub_key_from_der(self.pub_key)
        pk2 = EC.pub_key_from_der(self.torrent_pub_key)
        key1 = self.keypair.compute_dh_key(pk2)
        key2 = self.torrent_keypair.compute_dh_key(pk1)
        self.assertEquals(key1, key2)


#@skip("Skip")
class ECS_MessageExchangeTest(unittest.TestCase):

    def setUp(self):
        (self.keypair1, self.pub_key1) = generate_cs_keypair()
        (self.keypair2, self.pub_key2) = generate_cs_keypair()

        (self.torrent_keypair, self.torrent_pub_key) = generate_cs_keypair()
        # The torrent file actually contains a list of torrent public keys, encoded in base64
        self.torrent_pub_keys = [encodestring(self.torrent_pub_key).replace("\n", "")]
        self.torrent_id = "1234"
       
        self.rules1 = "GEOLOCATION = 'SI' && PRIORITY <= 30"
        self.rules2 = "GEOLOCATION = 'SI' && PRIORITY <= 10"

        self.expire_time = calendar.timegm(time.gmtime()) + 1000
        
        # Create two correct and valid extended PoAs
        self.epoa1 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time)

        self.epoa2 = create_epoa(self.torrent_id,
                                 self.torrent_keypair,
                                 self.pub_key2,
                                 self.rules2,
                                 self.expire_time)
        
        self.reqservice1 = [['PRIORITY', '30']]
        self.reqservice2 = [['PRIORITY', '2']]
        self.priority1 = 30
        self.priority2 = 2

        self.ecsmanager1 = ECS_Manager.getInstance(keypair=self.keypair1)
        self.ecsmanager1.resetSingleton()
        self.ecsmanager2 = ECS_Manager.getInstance(keypair=self.keypair2)
        self.ecsmanager2.resetSingleton()

        self.ecsmanager1.register_torrent(self.torrent_id, self.torrent_pub_keys)
        self.ecsmanager2.register_torrent(self.torrent_id, self.torrent_pub_keys)

        self.ecs_cm1 = self.ecsmanager1.get_swarm_manager(self.torrent_id)
        self.ecs_cm2 = self.ecsmanager2.get_swarm_manager(self.torrent_id)

        self.ecs_cm1.set_scheduler(scheduler)
        self.ecs_cm2.set_scheduler(scheduler)
        self.ecs_cm1.set_poa(self.epoa1)
        self.ecs_cm1.set_reqservice(self.reqservice1)
        self.ecs_cm2.set_poa(self.epoa2)
        self.ecs_cm2.set_reqservice(self.reqservice2)

        self.con1 = Tst_Connection()
        self.con2 = Tst_Connection()

        self.ecs1 = self.ecs_cm1.register_connection(self.con1)
        self.ecs2 = self.ecs_cm2.register_connection(self.con2)


    '''
    Message 1: [VersionA, SwarmID, Na]
    '''
    #@skip("Skip")
    def test_ecs_incorrect_message1_t1(self):
        # Incorrect Version
        m1 = self.ecs1.start_ecs()
        m1[1] = 3
        #self.assertRaisesRegexp(InvalidMessageException, "Unexisting protocol version", self.ecs2._got_cs_message, m1)
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m1)

    #@skip("Skip")
    def test_ecs_incorrect_message1_t2(self):
        # Incorrect SwarmID
        m1 = self.ecs1.start_ecs()
        m1[2] = "2345"
        #self.assertRaisesRegexp(InvalidMessageException, "Different SwarmID", self.ecs2._got_cs_message, m1)
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m1)

    #@skip("Skip")
    def test_ecs_incorrect_message1_t3(self):
        # Incorrect number of fields
        m1 = self.ecs1.start_ecs()
        del m1[2]
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid number of elements", self.ecs2._got_cs_message, m1)
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m1)

    #@skip("Skip")
    def test_ecs_incorrect_message1_t4(self):
        # Missing Nonce
        m1 = self.ecs1.start_ecs()
        m1[3] = None
        #self.assertRaisesRegexp(InvalidMessageException, "Missing Nonce", self.ecs2._got_cs_message, m1)
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m1)


    '''
    Message 2: [VersionB, SwarmID, Nb]
    '''
    #@skip("Skip")
    def test_ecs_incorrect_message2_t1(self):
        # Incorrect Version
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m2[1] = 3
        #self.assertRaisesRegexp(InvalidMessageException, "Unexisting protocol version", self.ecs1._got_cs_message, m2)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m2)

    #@skip("Skip")
    def test_ecs_incorrect_message2_t2(self):
        # Incorrect SwarmID
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m2[2] = "2345"
        #self.assertRaisesRegexp(InvalidMessageException, "Different SwarmID", self.ecs1._got_cs_message, m2)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m2)

    #@skip("Skip")
    def test_ecs_incorrect_message2_t2(self):
        # Incorrect number of fields
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        del m2[2]
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid number of elements", self.ecs1._got_cs_message, m2)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m2)

    #@skip("Skip")
    def test_ecs_incorrect_message2_t3(self):
        # Missing Nonce
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m2[3] = None
        #self.assertRaisesRegexp(InvalidMessageException, "Missing Nonce", self.ecs1._got_cs_message, m2)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m2)


    '''
    Message 3: [PoaA, ReqServiceA, {Na, Nb, PoaA, ReqServiceA}privA]
    '''
    #@skip("Skip")
    def test_ecs_incorrect_message3_t1(self):
        # Create correct POA, but invalid for this swarm since signed with other key.
        # Start the exchange process with this POA.
        (self.torrent_keypair1, self.torrent_pub_key1) = generate_cs_keypair()
        self.torrent_pub_keys1 = [encodestring(self.torrent_pub_key1).replace("\n", "")]

        self.epoa3 = create_epoa(self.torrent_id,
                                 self.torrent_keypair1,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time)

        self.ecsmanager3 = ECS_Manager.getInstance(keypair=self.keypair1)
        self.ecsmanager3.resetSingleton()
        self.ecsmanager3.register_torrent(self.torrent_id, self.torrent_pub_keys)
        self.ecs_cm3 = self.ecsmanager3.get_swarm_manager(self.torrent_id)

        self.ecs_cm3.set_scheduler(scheduler)
        self.ecs_cm3.set_poa(self.epoa3)
        self.ecs_cm3.set_reqservice(self.reqservice1)

        self.con3 = Tst_Connection()
        self.ecs3 = self.ecs_cm3.register_connection(self.con3)

        # Invalid POA
        m1 = self.ecs3.start_ecs()
        # We need to manually add the POA since it is invalid
        self.ecs3.dl_ecs.poa = self.epoa3
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs3._got_cs_message(m2)
        #self.assertRaisesRegexp(InvalidEPOAException, "EPOA has different issuer's public key", self.ecs2._got_cs_message, m3)        
        self.assertRaises(InvalidEPOAException, self.ecs2._got_cs_message, m3)        

    #@skip("Skip")
    def test_ecs_incorrect_message3_t2(self):
        # Message 3: [PoaA, ReqServiceA, {Na, Nb, PoaA, ReqServiceA}privA]

        # Incorrect ReqService
        incorrect_reqservices = ["a", ["a"], [['PRIORITY',1,1]]]
        for reqservice in incorrect_reqservices:
            self.ecs_cm1.set_reqservice(reqservice)

            self.con4 = Tst_Connection()
            self.ecs4 = self.ecs_cm1.register_connection(self.con4)

            m1 = self.ecs4.start_ecs()
            m2 = self.ecs2._got_cs_message(m1)
            m3 = self.ecs4._got_cs_message(m2)
            #self.assertRaisesRegexp(InvalidRulesSintaxException, "Invalid ReqService field", self.ecs2._got_cs_message, m3)        
            self.assertRaises(InvalidRulesSintaxException, self.ecs2._got_cs_message, m3)        

        # ReqService not according authorizations
        reqservice = [['PRIORITY', '50']]
        self.ecs_cm1.set_reqservice(reqservice)

        self.con4 = Tst_Connection()
        self.ecs4 = self.ecs_cm1.register_connection(self.con4)

        m1 = self.ecs4.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs4._got_cs_message(m2)
        m4 = self.ecs2._got_cs_message(m3)
        info_field = m4[2]
        self.assertEqual(info_field, 2) # 2 - Invalid request because of your requested service properties
        
    #@skip("Skip")
    def test_ecs_incorrect_message3_t3(self):
        # Incorrect number of fields
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m3.append(0)
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid number of elements", self.ecs2._got_cs_message, m3)        
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m3)        

    #@skip("Skip")
    def test_ecs_incorrect_message3_t4(self):
        # Create one correct, but invalid extended PoA
        (self.torrent_keypair1, self.torrent_pub_key1) = generate_cs_keypair()
        self.torrent_pub_keys1 = [encodestring(self.torrent_pub_key1).replace("\n", "")]

        self.epoa3 = create_epoa(self.torrent_id,
                                 self.torrent_keypair1,
                                 self.pub_key1,
                                 self.rules1,
                                 self.expire_time)

        # Incorrect fields/signature
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m3[1] = self.epoa3.serialize_to_list()
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs2._got_cs_message, m3)        
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m3)        

    #@skip("Skip")        
    def test_ecs_incorrect_message3_t5(self):
        # Incorrect nonces/message freshness
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)

        m21 = self.ecs1.start_ecs()
        m22 = self.ecs2._got_cs_message(m11)
        m23 = self.ecs1._got_cs_message(m22)
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs2._got_cs_message, m13)        
        self.assertRaises(InvalidMessageException, self.ecs2._got_cs_message, m13)        


    '''
    Message 4: [PoaB, InfoB, PeersB, {Kab}pubA, {Na, Nb, PoaB, InfoB, PeersB, {Kab}pubA}privB]
    '''
    #@skip("Skip")
    def test_ecs_incorrect_message4_t1(self):
        # Incorrect number of fields
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m4 = self.ecs2._got_cs_message(m3)
        m4.append(0)
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid number of elements", self.ecs1._got_cs_message, m4)        
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m4)        

    #@skip("Skip")
    def test_ecs_incorrect_message4_t2(self):
        # Incorrect fields/signature
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m4 = self.ecs2._got_cs_message(m3)
        m4[1] = self.epoa1.serialize_to_list()
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m4)        
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs1._got_cs_message, m4)        
        
    #@skip("Skip")
    def test_ecs_incorrect_message4_t3(self):
        # Correct nonces/message freshness
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)
        m14 = self.ecs2._got_cs_message(m13)
        m14v = self.ecs1._got_cs_message(m14)
        self.assertTrue(m14v)

        # Incorrect nonces/message freshness
        m21 = self.ecs1.start_ecs()
        m22 = self.ecs2._got_cs_message(m21)
        m23 = self.ecs1._got_cs_message(m22)
        m24 = self.ecs2._got_cs_message(m23)
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs1._got_cs_message, m14)        
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m14)        

    def _sign_message(self, msg, keypair):
        from Tribler.Core.Overlay import permid        
        blst = bencode(msg)
        digest = permid.sha(blst).digest()
        sig = keypair.sign_dsa_asn1(digest)
        msg.append(sig)
        return msg
        
    #@skip("Skip")
    def test_ecs_incorrect_message4_t4(self):
        # Incorrect Peers
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)
        m14 = self.ecs2._got_cs_message(m13)
        mid = m14[0]
        temp = m14[1:-1]
        temp[2] = 'wrongDNS'
        temp = self._sign_message(temp, self.keypair2)
        m14i = [mid] + temp        
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid peers received", self.ecs1._got_cs_message, m14i)        
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m14i)        

    #@skip("Skip")
    def test_ecs_incorrect_message4_t5(self):
        # Incorrect Info
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)
        m14 = self.ecs2._got_cs_message(m13)
        mid = m14[0]
        temp = m14[1:-1]
        temp[1] = 9
        temp = self._sign_message(temp, self.keypair2)
        m14i = [mid] + temp        
        #self.assertRaisesRegexp(InvalidMessageException, "Undefined info field", self.ecs1._got_cs_message, m14i)        
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m14i)

    #@skip("Skip")
    def test_ecs_incorrect_message4_t6(self):
        # Incorrect Initialization Vector length
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)
        m14 = self.ecs2._got_cs_message(m13)
        mid = m14[0]
        temp = m14[1:-1]
        temp[3] = os.urandom(32)
        temp = self._sign_message(temp, self.keypair2)
        m14i = [mid] + temp
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid initialization vector length", self.ecs1._got_cs_message, m14i)                
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m14i)                


    '''
    Message 5: [InfoB, {Na, Nb, InfoB}privB]
    '''
    #@skip("Skip")
    def test_ecs_incorrect_message5_t1(self):
        # Incorrect number of fields
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m4 = self.ecs2._got_cs_message(m3)
        m4v = self.ecs1._got_cs_message(m4)
        m5 = self.ecs2.terminate_ecs()
        m5.append(0)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m5)
        #self.assertRaisesRegexp(InvalidMessageException, "Invalid number of elements", self.ecs1._got_cs_message, m5)

    #@skip("Skip")
    def test_ecs_incorrect_message5_t2(self):
        # Message 5: [InfoB, {Na, Nb, InfoB}privB]

        # Incorrect field/signature
        m1 = self.ecs1.start_ecs()
        m2 = self.ecs2._got_cs_message(m1)
        m3 = self.ecs1._got_cs_message(m2)
        m4 = self.ecs2._got_cs_message(m3)
        m4v = self.ecs1._got_cs_message(m4)
        m5 = self.ecs2.terminate_ecs()
        m5[1] = 3
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs1._got_cs_message, m5)
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m5)

    #@skip("Skip")
    def test_ecs_incorrect_message5_t3(self):
        # Message 5: [InfoB, {Na, Nb, InfoB}privB]

        # Incorrect nonces/message freshness
        # First, correct exchange
        m11 = self.ecs1.start_ecs()
        m12 = self.ecs2._got_cs_message(m11)
        m13 = self.ecs1._got_cs_message(m12)
        m14 = self.ecs2._got_cs_message(m13)
        m14v = self.ecs1._got_cs_message(m14)
        m15 = self.ecs2.terminate_ecs()
        m15v = self.ecs1._got_cs_message(m15)
        self.assertTrue(m15v)
        
        # Reregister connection, since it is closed
        self.ecs2 = self.ecs_cm2.register_connection(self.con2)

        # Then, incorrect exchange
        m21 = self.ecs1.start_ecs()
        m22 = self.ecs2._got_cs_message(m21)
        m23 = self.ecs1._got_cs_message(m22)
        m24 = self.ecs2._got_cs_message(m23)
        m24v = self.ecs1._got_cs_message(m24)
        m25 = self.ecs2.terminate_ecs()
        self.assertRaises(InvalidMessageException, self.ecs1._got_cs_message, m15)
        #self.assertRaisesRegexp(InvalidMessageException, "Signaure and nonces verification failed", self.ecs1._got_cs_message, m15)


#@skip("Skip")
class ECS_AuthorizationEngineTest(unittest.TestCase):

    def setUp(self):
        self.ae = Authorization_Engine()
        self.ecs_connection = Tst_Connection()

    
    #@skip("Skip")
    def test_ae_reqservice_invalid_variable_name(self):
        # Actually this is done by comparing the lengths of the matched var name and the first field in a reqservice

        # Invalid length (>21)
        self.reqservice = [["PRIORITY11111111111111", "1"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable name", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Invalie first character (not alphabet)
        self.reqservice = [["1PRIORITY", "1"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable name", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Invalid character (not alphabet, number or _)
        self.reqservice = [["P@RIORITY", "1"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable name", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

    #@skip("Skip")        
    def test_ae_reqservice_invalid_variable_value(self):
        # Actually this is done by comparing the lengths of the matched var value and the second field in a reqservice

        # Number
        # Invalid length (>10)
        self.reqservice = [["PRIORITY", "12345678901"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Invalid character
        self.reqservice = [["PRIORITY", "123j2"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Float
        # Invalid length (>10.1)
        self.reqservice = [["PRIORITY", "1234567890.11"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Invalid character
        self.reqservice = [["PRIORITY", "12.a"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # String
        # Invalid length (>10)
        self.reqservice = [["PRIORITY", "a1234567890"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        # Invlaid character(not alphabet, number or _)
        self.reqservice = [["PRIORITY", "a1_*"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "invalid variable value", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

    #@skip("skip")
    def test_ae_reqservice_fobiden_variables(self):

        self.reqservice = [["DAY_HOUR", "100"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "cannot be requested", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

        self.reqservice = [["GEOLOCATION", "SI"]]
        #self.assertRaisesRegexp(InvalidRulesSintaxException, "cannot be requested", self.ae.set_environment, self.ecs_connection, self.reqservice)
        self.assertRaises(InvalidRulesSintaxException, self.ae.set_environment, self.ecs_connection, self.reqservice)

    #@skip("skip")
    def test_ae_rules_invalid_variable_name(self):
        # Actually, when rules are parsed, it is syntax error on basis on the whole grammar
        # It's not an invalid variable name

        # Invalid length (>21)
        self.reqservice = [["PRIORITY", "1"]]
        self.ae.set_environment(self.ecs_connection, self.reqservice)

        self.rules = "PRIORITY11111111111111 <= 10"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Invalie first character (not alphabet)
        self.rules = "1PRIORITY <= 10"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Invalid character (not alphabet, number or _)
        self.rules = "P@RIORITY <= 10"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

    #@skip("skip")
    def test_ae_rules_invalid_variable_value(self):
        # Actually this is done by comparing the lengths of the matched var value and the second field in a reqservice

        self.reqservice = [["PRIORITY", "1"]]
        self.ae.set_environment(self.ecs_connection, self.reqservice)

        # Number
        # Invalid length (>10)
        self.rules = "PRIORITY <= 12345678901"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Invalid character
        self.rules = "PRIORITY <= 123j2"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Float
        # Invalid length (>10.1)
        self.rules = "PRIORITY <= 1234567890.11"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Invalid character
        self.rules = "PRIORITY <= 12.a"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Invlaid character(not alphabet, number or _)
        self.reqservice = [["PRIORITY", "1"]]
        self.rules = "PRIORITY <= a1_*"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)

        # Var name
        # Invalid first character
        self.rules = "PRIORITY <= 1PRIORITY1"
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assert_(result is None)


    #@skip("skip")
    def test_ae_rules_outcomes(self):

        self.reqservice = [["PRIORITY", "20"]]
        self.ae.set_environment(self.ecs_connection, self.reqservice)

        '''
        A higher priority is requested, not according to the authorizations in Rules. Expected outcome:
             result: False - the evaluated rules result in False
             authorized_service_properties_requested: False - requested priority is not according to authorizations
             minDH: None
        '''
        now = time.gmtime()
        now_day_hour = (now.tm_wday + 1) * 100 + now.tm_hour

        day_hour = now_day_hour + 100
        self.rules = "PRIORITY <= 10 && GEOLOCATION = 'SI' && DAY_HOUR <= " + str(day_hour)
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assertFalse(result)
        self.assertFalse(authorized_service_properties_requested)
        self.assert_(minDH is None)

        '''
        Expected outcome:
             result: True - the evaluated rules result in True
             authorized_service_properties_requested: True - requested priority is according to authorizations
             minDH: 2 - because of the <= operator, which means that peer is authorized for the next 2 hours
                    hint: Monday 12:00 and Monday 12:59 have same DAY_HOUR = 112
        '''
        day_hour1 = now_day_hour - 1
        day_hour2 = now_day_hour + 1
        self.rules = "PRIORITY <= 20 && GEOLOCATION = 'SI' && (DAY_HOUR > %s && DAY_HOUR <= %s)" % (day_hour1, day_hour2)
        result, authorized_service_properties_requested, minDH = self.ae.evaluate_rules(self.ecs_connection, self.rules, self.reqservice)
        self.assertTrue(result)
        self.assertTrue(authorized_service_properties_requested)
        self.assertEqual(minDH, 2)


def suite():
    test_cases = (ECS_ExtendedPOATest, ECS_MessageExchangeTest, ECS_AuthorizationEngineTest)
    suite = unittest.TestSuite()
    for test_class in test_cases:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    return suite

if __name__ == "__main__":
    unittest.main()
