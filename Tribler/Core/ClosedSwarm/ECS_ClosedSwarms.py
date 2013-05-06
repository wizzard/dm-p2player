# Written by Vladimir Jovanovikj
# see LICENSE.txt for license information

import time
import calendar
import os
import sys
from random import shuffle
from types import ListType, StringType
from operator import itemgetter
from base64 import encodestring, decodestring
import cStringIO
from M2Crypto.EC import pub_key_from_der
from M2Crypto import EVP

from Tribler.Core.BitTornado.BT1.track import compact_peer_info
from Tribler.Core.BitTornado.BT1.MessageID import *
from Tribler.Core.BitTornado.bencode import bencode, bdecode
from Tribler.Core.Overlay import permid

from Tribler.Core.ClosedSwarm.ClosedSwarm import *
from Tribler.Core.ClosedSwarm.ECS_AuthorizationEngine import Authorization_Engine, get_max_priority
from Tribler.Core.ClosedSwarm.ECS_Exceptions import *
from Tribler.Core.ClosedSwarm.conf import ecssettings


'''
Functions for encryption and decryption of data.
They use 
'''
def _cipher_filter(cipher, inf, outf):
    while 1:
        buf=inf.read()
        if not buf:
            break
        outf.write(cipher.update(buf))
    outf.write(cipher.final())
    return outf.getvalue()

def encrypt_AES(plaintext, key, iv):
    '''
    Encryption function. Uses AES in CBC mode, with 128 bit key length.

    @param plaintext Plaintext to encrypt
    @param key Encryption key
    @param iv Initialization vector
    @return Ciphertext
    '''
    enc = 1
    k = EVP.Cipher(alg='aes_128_cbc', key=key, iv=iv, op=enc)
    pbuf = cStringIO.StringIO(plaintext)
    cbuf = cStringIO.StringIO()
    ciphertext = _cipher_filter(k, pbuf, cbuf)
    pbuf.close()
    cbuf.close()
    return ciphertext

def decrypt_AES(ciphertext, key, iv):
    '''
    Decryption function. Uses AES in CBC mode, with 128 bit key length.

    @param plaintext Ciphertext to decrypt
    @param key Decryption key
    @param iv Initialization vector
    @return Plaintext
    '''
    dec = 0
    k = EVP.Cipher(alg='aes_128_cbc', key=key, iv=iv, op=dec)
    pbuf = cStringIO.StringIO()
    cbuf = cStringIO.StringIO(ciphertext)
    plaintext = _cipher_filter(k, cbuf, pbuf)
    pbuf.close()
    cbuf.close()
    return plaintext

def decompact_peers(compactpeerstr):
    '''
    Decompact peers packed in compact form to a list

    @param compactpeerstr List of peers packed in compact form as string
    @return List
    '''
    if type(compactpeerstr) == StringType and len(compactpeerstr) % 6 == 0:
        peers = []
        for x in xrange(0, len(compactpeerstr), 6):
            ip = '.'.join([str(ord(i)) for i in compactpeerstr[x:x+4]])                 # if ip == '127.0.0.1': raise Exception()
            port = (ord(compactpeerstr[x+4]) << 8) | ord(compactpeerstr[x+5])
            peers.append((ip, port))
        return peers
    else:
        raise ValueError("Received peers %s are not compact" % compactpeerstr)


    
# Some helper functions
def generate_cs_keypair(keypair_filename=None, pubkey_filename=None):
    """
    Generate a keypair suitable for a Closed Swarm. Save them to the specified 
    file, if provided.

    @param keypair_filename File to save the keypair to
    @param pubkey_filename File to save the pubkey to
    @return Tuple (keypair, public key)
    Saves to the given files if specified, returns keypair, pubkey
    """
    keypair = permid.generate_keypair()
    if keypair_filename:
        permid.save_keypair(keypair, keypair_filename)

    # Save the public key in Base64 encoding
    pubkey = encodestring(str(keypair.pub().get_der())).replace("\n","")
    if pubkey_filename:
        permid.save_pub_key(keypair, pubkey_filename)
    
    # Return the public key unencoded
    pubkey = str(keypair.pub().get_der())
    return keypair, pubkey

def write_epoa_to_file(filename, epoa):
    """
    Dump the EPOA to the given file in serialized form
    """
    target = open(filename,"wb")
    target.write(epoa.serialize())
    return filename

def read_epoa_from_file(filename):
    """
    Read and return a EPOA object from a file. Throws exception if
    the file was not found or the POA could not be deserialized
    """
    if not os.path.exists(filename):
        raise Exception("File '%s' not found"%filename)
    
    data = open(filename,"rb").read()
    return EPOA.deserialize(data)

def write_reqservice_to_file(filename, reqservice):
    """
    Dump the ReqService to the given file in serialized form
    """
    target = open(filename,"wb")
    target.write(bencode(reqservice)) 
    return filename

def read_reqservice_from_file(filename):
    """
    Read and return a ReqService object from a file. Throws exception if
    the file was not found
    """
    if not os.path.exists(filename):
        raise Exception("File '%s' not found"%filename)
    
    data = open(filename,"rb").read()
    return bdecode(data)


# Some POA helpers
def trivial_get_epoa(path, perm_id, swarm_id):
    """
    Look for a POA file for the given permid,swarm_id
    """
    filename = encodestring(perm_id).replace("\n","")
    filename = filename.replace("/","")
    filename = filename.replace("\\","")

    t_id  = encodestring(swarm_id).replace("\n","")
    t_id = t_id.replace("/","")
    t_id = t_id.replace("/","")

    poa_path = os.path.join(path, filename + "." + t_id + ".poa")

    return read_epoa_from_file(poa_path)
        
def trivial_save_epoa(path, perm_id, swarm_id, poa):
    """
    Save POA
    """
    filename = encodestring(perm_id).replace("\n","")
    filename = filename.replace("/","")
    filename = filename.replace("\\","")

    t_id  = encodestring(swarm_id).replace("\n","")
    t_id = t_id.replace("/","")
    t_id = t_id.replace("/","")

    # if the path does not exist, try to create it
    if not os.path.exists(path):
        os.makedirs(path)

    poa_path = os.path.join(path, filename + "." + t_id + ".poa")
    
    return write_epoa_to_file(poa_path, poa)

def get_exec_time(rules_exec_time, expire_time):
    '''
    Finds the nearest time period (in seconds) when the evaluation of the 
    requested service properties should be done next. This is done by taking 
    into consideration the rules temporal conditions and the epoa expiry time.

    @rules_exec_time Integer containing time (in hours) till when a peer is 
    allowed to receive content
    @expire_time Integer containing time (in seconds) till when a POA is valid
    @return Integer 
    '''
    now_int = calendar.timegm(time.gmtime())
    now_seq = time.gmtime()

    exec_time = expire_time - now_int
    if rules_exec_time is not None:
        rules_exec_time = (rules_exec_time - 1) * 3600 + (60 - now_seq.tm_min) * 60 # transform rules_exec_time in seconds
        if rules_exec_time < exec_time:
            exec_time = rules_exec_time
    return exec_time


class EPOA(POA):
    """
    This class implements the Extended Proof-of-access (POA) credential. It 
    inherits the {@link POA} class, and extends its functionality with an 
    additional field called Rules. The Rules field contains the conditions 
    under which the credential holder is authorized by the credential issuer 
    to join the closed swarm.
    """    
    def __init__(self, torrent_id, torrent_pub_key, node_pub_key,
                 rules="", expire_time=0, signature=""):
        """
        Please note that the public keys are not contained encoded (they are 
        of type string),since they will be bencoded before transfering or 
        saving to disk.

        @param torrent_id Torrent infohash
        @param torrent_pub_key Public key of the credential issuer
        @param node_pub_key Public key of the credential holder
        @param rules Contains conditions under which the credential holder 
        is authorized by the credential issuer to join the closed swarm
        @param expire_time Expiry time of the credential as integer (This 
        field's type differs from its type in the parrent class - Float).
        
        @param signature Digital signature of the previous fields, signed by
        the credential issuer
        """
        # check if the expire_time is an Integer
        POA.__init__(self, torrent_id, torrent_pub_key, node_pub_key, 
                     expire_time=expire_time, signature=signature)
        self.rules = rules

    def serialize_to_list(self):
        """
        Serialize the extended POA fields to a list.

        @return List
        """
        return [self.torrent_id,
                self.torrent_pub_key,
                self.node_pub_key,
                self.rules,
                self.expire_time,
                self.signature]
        
    def deserialize_from_list(lst):
        """
        Deserialize an extended POA from a list of elements.

        @param lst List of elements
        @return {@link EPOA} object
        """
        if not lst or len(lst) < 6:
            raise InvalidEPOAException("Badlist")

        torrent_id = lst[0]
        torrent_pub_key = lst[1]
        node_pub_key = lst[2]
        rules = lst[3]
        expire_time = lst[4]
        signature = lst[5]
        return EPOA(torrent_id, torrent_pub_key,
                   node_pub_key, rules, expire_time, signature)

    deserialize_from_list = staticmethod(deserialize_from_list)
    
    def serialize(self):
        """
        Serialize an extended POA to a bencoded list of elements.

        @return String
        """
        return bencode(self.serialize_to_list())

    def deserialize(encoded):
        """
        Deserialize a serialized extended POA from a bencoded list of elements.

        @param encoded Bencoded list of extended POA fields
        @return {@link EPOA} object
        """
        if not encoded:
            raise InvalidEPOAException("Cannot deserialize nothing")
        
        try:
            lst = bdecode(encoded)
            if len(lst) < 6:
                raise InvalidEPOAException("Too few entries (got %d, "
                                          "expected 6)"%len(lst))
            return EPOA(lst[0], lst[1], lst[2], lst[3], 
                        expire_time=lst[4], signature=lst[5])
        except Exception, e:
            raise InvalidEPOAException("Deserialization failed (%s)"%e)

    deserialize = staticmethod(deserialize)

    def verify(self):
        """
        Verify the validity of the extended POA for the closed swarm. 
        In particular verify that the EPOA: has not expired, is signed by
        a correct public key
        """
        if self.expire_time <= calendar.timegm(time.gmtime()):
            raise InvalidEPOAException("EPOA has expired")

        try:
            lst = self.serialize_to_list()
            b_list = bencode(lst[:-1])
            digest = permid.sha(b_list).digest()
            pub = pub_key_from_der(self.torrent_pub_key)
            assert pub.verify_dsa_asn1(digest, self.signature)
        except Exception,e:
            raise InvalidEPOAException("EPOA has invalid signature")
        
        
    def sign(self, torrent_key_pair):
        """
        Sign the extended POA and populate the signature attribute.
        This method is used by the credential issuer.

        @param torrent_key_pair Public/private key pair of the credential issuer
        """
        lst = self.serialize_to_list()
        b_list = bencode(lst[:-1])
        digest = permid.sha(b_list).digest()

        self.signature = torrent_key_pair.sign_dsa_asn1(digest)


    def save(self, filename):
        """
        Save an {@link EPOA} object to a file.

        @param filename File to save the extended POA in
        @return String
        """
        target = open(filename,"wb")
        target.write(self.serialize())
        target.close()
        return filename

    def load(filename):
        """
        Read an {@link EPOA} object from a file.

        @param filename File to read the extended POA from
        @return An {@link EPOA} object
        """
        if not os.path.exists(filename):
            raise Exception("File '%s' not found"%filename)
    
        data = open(filename,"rb").read()
        return EPOA.deserialize(data)

    load = staticmethod(load)


def create_epoa(torrent_id, torrent_keypair, node_pub_key, rules, expire_time=0):
    """
    Create a Proof-of-access credential for a given node. Please note that 
    the keys should be provided as strings.

    @param torrent_id Torrent infohash
    @param torrent_keypair Public/private key pair of the credential issuer
    @param node_pub_key Public key of the credential holder
    @param rules Contains the conditions under which the credential holder 
    is authorized by the credential issuer to join the closed swarm 
    @param expire_time Expiry time of the credential as Integer
    @return An {@link EPOA} object
    """
    epoa = EPOA(torrent_id, 
                str(torrent_keypair.pub().get_der()),
                node_pub_key,
                rules,
                expire_time)
    epoa.sign(torrent_keypair)
    return epoa


class EnhancedClosedSwarm(ClosedSwarm):
    '''
    This class implements the functionality related to the message exchange 
    process
    '''

    def __init__(self, my_keypair, torrent_id, torrent_pubkeys, 
                 poa, reqservice, ecsconnection):
        '''
        @param my_keypair Private/public key pair of this node
        @param torrent_id Torrent infohash
        @param torrent_pubkeys List of public keys used by the credential 
        issuer for this torrent (These keys are Base64 encoded)
        @param poa Extended POA credential
        @param reqservice Requested service properties
        @param ecsconnection {@link ECS_Connection} object
        '''
        ClosedSwarm.__init__(self, my_keypair, torrent_id, torrent_pubkeys, poa)

        self.reqservice = reqservice
        self.ecsconnection = ecsconnection
        self.version = ecssettings.ECS_VERSION
        self.remote_poa = None
        self.remote_rules = None
        self.remote_reqservice = None
        self.remote_version = None
        self.info = None  
        self.peers = None
        self.have_available_connection = None
        self.iv = None
        self.key = None        
        '''
        self.exec_time variable contains the nearest time period of the next 
        verification/evaluation of: i) a request for service, or ii) a POA.
        In this way upload will be disabled to: i) a peer with expried POA, 
        or ii) a peer with request no longer according to authorization
        '''
        self.exec_time = None


    def _create_challenge_msg(self, msg_id=None):
        """
        Create a challenge message. This method can be called from both nodes.

        @param msg_id Unused parameter kept for compatibility reasons
        @return List
        Format:[VersionA, SwarmID, Na]
        """
        [self.my_nonce, my_nonce_bencoded] = permid.generate_challenge()
        # Serialize this message
        return [self.version,
                self.torrent_id,
                self.my_nonce]


    def b_create_challenge(self, cs_message):
        '''
        Peer B verifies the correctness of the received challenge message 
        and creates its challenge message.

        @param cs_message Challenge message
        Format:[VersionA, SwarmID, Na]
        @return List (Challenge message)
        Format:[VersionB, SwarmID, Nb]
        '''
        # Verify state
        assert self.state == self.IDLE

        # Verify received message  
        if len(cs_message) != 3:
            raise InvalidMessageException("Message 1: Invalid number of elements - expected %d, got %d"%(3, len(cs_message)))
        if cs_message[0] not in ecssettings.PROTOCOL_VERSION.keys():
            raise InvalidMessageException("Message 1: Unexisting protocol version")
        if cs_message[1] != self.torrent_id:
            raise InvalidMessageException("Message 1: Different SwarmID")
        if cs_message[2] is None:
            raise InvalidMessageException("Message 1: Missing Nonce")

        # Save vars
        self.remote_version = cs_message[0]
        self.remote_nonce = cs_message[2]

        # Update state
        self.state = self.EXPECTING_INITIATOR_RESPONSE

        # Create next message
        return self._create_challenge_msg()

    def _verify_signature_and_nonces(self, cs_message, public_key, nonceA, nonceB):
        '''
        Verify that message signature is correct and contains the previosely
        exchanged nonces. This provides message authentication and freshness.

        @param cs_message Received ECS message
        @param public_key Public key of the message sender (signer)
        @param nonceA Nonce generated by Peer A
        @param nonceB Nonce generated by Peer B
        '''
        cs_mess_sig = cs_message[-1]
        lst = [nonceA, nonceB] + cs_message[:-1]
        blst = bencode(lst)
        digest = permid.sha(blst).digest()
        try:
            pub = pub_key_from_der(public_key)
        except:
            raise Exception("Node's public key is no good...")       
        if not pub.verify_dsa_asn1(digest, cs_mess_sig):
            raise InvalidMessageException("Signaure and nonces verification failed")
        

    def a_provide_poa_message(self, cs_message):
        '''
        Peer A verifies the correctness of the received challenge message and 
        creates its POA message.

        @param cs_message Challenge message
        Format:[VersionB, SwarmID, Nb]
        @return List (POA message)
        Format:[PoaA, ReqServiceA, {Na, Nb, PoaA, ReqServiceA}privA]
        '''
        # Verify state
        assert self.state == self.EXPECTING_RETURN_CHALLENGE

        # Verify received message
        if len(cs_message) != 3:
            raise InvalidMessageException("Message 2: Invalid number of elements - expected %d, got %d"%(3, len(cs_message)))
        if cs_message[0] not in ecssettings.PROTOCOL_VERSION.keys():
            raise InvalidMessageException("Message 2: Unexisting protocol version")
        if cs_message[1] != self.torrent_id:
            raise InvalidMessageException("Message 2: Different SwarmID")
        if cs_message[2] is None:
            raise InvalidMessageException("Message 2: Missing Nonce")

        # Save vars
        self.remote_version = cs_message[0]
        self.remote_nonce = cs_message[2]
    
        # Update state
        self.state = self.SEND_INITIATOR_RESPONSE
    
        # Create next message
        message = [self.poa.serialize_to_list(), self.reqservice]
        lst = [self.my_nonce, self.remote_nonce] + message
        blst = bencode(lst)
        digest = permid.sha(blst).digest()
        sig = self.my_keypair.sign_dsa_asn1(digest)
        message.append(sig)

        return message


    def set_peers(self, peers):
        '''
        Set peers atribute, which will send as suggested members later.
        This method is prerequisite for b_provide_poa_message() method 
        defined below!!!

        @param peers List of peers' dns tuples (ip, port)
        '''
        self.peers = peers

    def set_have_available_connection(self, value):
        '''
        Set have_available_connection attribute, which denotes whether this peer
        can connect to the remote one.
        This method is prerequisite for b_provide_poa_message() method 
        defined below!!!

        @param value Boolean
        '''
        self.have_available_connection = value
    
    def b_provide_poa_message(self, cs_message):
        '''
        Peer B verifies the correctness of the received POA message and creates 
        its POA message.

        @param cs_message POA message
        Format:[PoaA, ReqServiceA, {Na, Nb, PoaA, ReqServiceA}privA]
        @return List (POA message)
        Format:[PoaB, InfoB, PeersB, IV, {Na, Nb, PoaB, InfoB, PeersB, IV]
        '''
        # Verify state
        assert self.state == self.EXPECTING_INITIATOR_RESPONSE
    
        # Verify received message
        if len(cs_message) != 3:
            raise InvalidMessageException("Message 3: Invalid number of elements - expected %d, got %d"%(3, len(cs_message)))
        # ReqService should be a list of lists, each of them with 2 elements
        if type(cs_message[1]) != ListType:
            raise InvalidMessageException("Message 3: Invalid ReqService field")
        elif len(cs_message[1]) > 0:
            for i in cs_message[1]:
                if not (type(i) == ListType and len(i) == 2):
                    raise InvalidMessageException("Message 3: Invalid ReqService field")
        self.remote_poa = EPOA.deserialize_from_list(cs_message[0])
        self._verify_signature_and_nonces(cs_message, self.remote_poa.node_pub_key, self.remote_nonce, self.my_nonce)
        
        # Save vars
        self.remote_reqservice = cs_message[1]
        self.remote_rules = self.remote_poa.rules

        # Verify credential
        if self.torrent_id != self.remote_poa.torrent_id:
            raise InvalidEPOAException("EPOA has different swarmID")
        try:
            # Since keys in torrent file are base64 encoded, torrent public key
            # from the extended POA needs to be also encoded before comparison
            assert((encodestring(self.remote_poa.torrent_pub_key).replace("\n", "") in self.torrent_pubkeys) == True)
        except:
            raise InvalidEPOAException("EPOA has different issuer's public key")
        self.remote_poa.verify()

        if self.have_available_connection:
            # Evaluate request for service
            self.remote_node_authorized, self.info, rules_exec_time = self._evaluate_request(self.remote_reqservice, self.remote_rules)

            if self.remote_node_authorized:
                self.exec_time = get_exec_time(rules_exec_time, self.remote_poa.expire_time)
                # Generate key and initialization vector.
                # Since both peers can compute this key using ECDH, only the IV is send with this message.
                self.key = self._generate_key(self.my_keypair, self.remote_poa.node_pub_key)
                self.iv = os.urandom(16)
            else:
                self.key = ""
                self.iv = ""

            if ecssettings.DEBUG_DEBUG:
                print >> sys.stderr, "Acting as Peer B:"
                print >> sys.stderr, "Exec time:", self.exec_time
                print >> sys.stderr, "Info: %d - %s" %(self.info, ecssettings.INFO_FIELD[self.info])
                print >> sys.stderr, "IV:", self.iv, "length:", len(self.iv)
                print >> sys.stderr, "Key:", self.key, "length:", len(self.key)

            # Update state
            self.state = self.COMPLETED

        else:
            self.info = 5
            self.key = ""
            self.iv = ""
    
        # Create next message
        message = [self.poa.serialize_to_list(), self.info, self.peers, self.iv]
        lst = [self.remote_nonce, self.my_nonce] + message
        blst = bencode(lst)
        digest = permid.sha(blst).digest()
        sig = self.my_keypair.sign_dsa_asn1(digest)
        message.append(sig)
    
        return message

    
    def a_check_poa_message(self, cs_message):
        '''
        Peer A verifies the correctness of the received POA message.

        @param cs_message POA message
        Format:[PoaB, InfoB, PeersB, IV, {Na, Nb, PoaB, InfoB, PeersB, IV}privB]
        '''
        # Verify state
        assert self.state == self.SEND_INITIATOR_RESPONSE
    
        # Verify message
        if len(cs_message) != 5:
            raise InvalidMessageException("Message 4: Invalid number of elements - expected %d, got %d"%(5, len(cs_message)))
        if cs_message[1] not in ecssettings.INFO_FIELD.keys():
            raise InvalidMessageException("Message 4: Undefined info field")
        try:
            peers = decompact_peers(cs_message[2])
        except:
            raise InvalidMessageException("Message 4: Invalid peers received")
        if len(cs_message[3]) not in [0,16]: # IV can be empty too, in case the message has Info = 5.
            raise InvalidMessageException("Message 4: Invalid initialization vector length - expected %d B, got %d B"%(16, len(cs_message[3])))
        self.remote_poa = EPOA.deserialize_from_list(cs_message[0])
        self._verify_signature_and_nonces(cs_message, self.remote_poa.node_pub_key, self.my_nonce, self.remote_nonce)

        # Verify credential
        if self.torrent_id != self.remote_poa.torrent_id:
            raise InvalidEPOAException("EPOA has different swarmID")
        try:
            # Since keys in torrent file are base64 encoded, torrent public key
            # from the extended POA needs to be also encoded before comparison
            assert((encodestring(self.remote_poa.torrent_pub_key).replace("\n", "") in self.torrent_pubkeys) == True)
        except:
            raise InvalidEPOAException("EPOA has different issuer's public key")
        self.remote_poa.verify()
    
        # Save vars
        self.info = cs_message[1]
        self.peers = peers
        self.iv = cs_message[3]
        self.remote_rules = self.remote_poa.rules

        if self.info == 1: # "Valid request"
            self.key = self._generate_key(self.my_keypair, self.remote_poa.node_pub_key)
            
        if ecssettings.DEBUG_DEBUG:
            print >> sys.stderr, "Remote Info: %d - %s" %(self.info, ecssettings.INFO_FIELD[self.info])
            if self.iv is not None:
                print >> sys.stderr, "IV:", self.iv, "length:", len(self.iv)
            if self.key is not None:
                print >> sys.stderr, "Key:", self.key, "length:", len(self.key)
        
        # Update state
        self.state = self.COMPLETED
        
        return True
        

    def b_create_info_message(self, info):
        '''
        Peer B creates an info message.

        @param info Info field value
        @return List (Info message)
        Format:[InfoB, {Na, Nb, InfoB}privB]
        '''
        # Verify state
        assert self.state == self.COMPLETED

        # Create the next message
        message = [info]
        lst = [self.remote_nonce, self.my_nonce] + message
        blst = bencode(lst)
        digest = permid.sha(blst).digest()
        sig = self.my_keypair.sign_dsa_asn1(digest)
        message.append(sig)
    
        return message
    
    def a_check_info_message(self, cs_message):
        '''
        Peer A verifies the correctness of the received info message.

        @param cs_message Info message
        Format:[InfoB, {Na, Nb, InfoB}privB]
        '''
        # Verify state
        assert self.state == self.COMPLETED

        # Verify received message
        if len(cs_message) != 2:
            raise InvalidMessageException("Message 5: Invalid number of elements - expected %d, got %d"%(2, len(cs_message)))
        if cs_message[0] not in ecssettings.INFO_FIELD.keys():
            raise InvalidMessageException("Message 5: Undefined info field")
        self._verify_signature_and_nonces(cs_message, self.remote_poa.node_pub_key, self.my_nonce, self.remote_nonce)
    
        # Set vars
        self.info = cs_message[0]

        if ecssettings.DEBUG_DEBUG:
            print >> sys.stderr, "Remote Info: %d - %s" %(self.info, ecssettings.INFO_FIELD[self.info])

        return True


    def _evaluate_request(self, reqservice, rules):
        '''
        Peer B evaluates (validates) the requested service properties by peer A,
        according to the rules in its credential.
        @param reqservice Requested service properties
        @param rules Rules field from the peer A's credential
        @return remote_authorized Is peer A authorized for the requested service?
        @return info Info field value
        @return rules_exec_time Time till the next evaluation/validation of the 
        requested service properties
        '''
        remote_priority = None

        try:
            ae = self.ecsconnection.ecs_swarm_manager.manager.authorization_engine
            result, auth_serv_prop_req, rules_exec_time = ae.evaluate_rules(self.ecsconnection, rules, reqservice)
            if ecssettings.DEBUG_DEBUG:
                print >> sys.stderr, result, auth_serv_prop_req, rules_exec_time

            # Get Info and Remote Node Authorized fields
            if result is None:
                info = 0
                remote_authorized = False
            elif result:
                info = 1
                remote_authorized = True
            else:
                remote_authorized = False
                if not auth_serv_prop_req:
                    info = 2
                else:
                    info = 3
            
            if remote_priority is not None:
                remote_priority = remote_priority
        except:
            remote_authorized = False
            info = 0
            rules_exec_time = None

        return remote_authorized, info, rules_exec_time


    def _generate_key(self, keypair, pubkey, size=ecssettings.AES_KEY_SIZE):
        '''
        Generate key that will be used for symmetric encryption/decryption of 
        the exchanged content. The key  will be used with AES in CBC mode. 
        It is derived from a shared secret computed according to Elliptic Curve 
        Diffie-Hellman (ECDH) protocol.

        @param keypair Node's keypair
        @param pubkey Public key of the remote node
        @param size Size of the generated key
        @return String
        '''
        pubkey = pub_key_from_der(pubkey)
        key = keypair.compute_dh_key(pubkey)
        if size > len(key):
            x, y = divmod(size, len(key))
            key = x*key + key[:y]
            return key
        if size < len(key):
            key = key[:size]
            return key
        else:
            return key


class ECS_Connection:
    '''
    This class implements the functionality for coordination of the message 
    exchange process of the ECS protocol. It accompanies a {@link Connection}
    object (from Tribler/Core/BitTornado/BT1/Connecter.py module).
    '''
    def __init__(self, ecs_sm, connection, torrent_id, keypair, torrent_pubkeys,
                 epoa, reqservice):
        '''
        @param ecs_sm {ECS_SwarmManager} object
        @param connection {@link Connection} object 
        (from Tribler/Core/BitTornado/BT1/Connecter.py module)
        @param torrent_id Torrent infohash
        @param keypair Private/public keypair of the node
        @param torrent_pubkeys List of public keys used by the credential 
        issuer for this torrent (These keys are Base64 encoded)
        @param epoa Extended POA redential of the node
        @param reqservice Requested service properties of the node
        '''
        self.ecs_swarm_manager = ecs_sm
        self.connection = connection
        self.torrent_id = torrent_id
        self.keypair = keypair
        self.torrent_pubkeys = torrent_pubkeys
        self.epoa = epoa
        self.reqservice = reqservice
        self.dl_ecs = None
        self.ul_ecs = None
        self.remote_priority = None
        self.locally_initiated = self.connection.is_locally_initiated()


    def start_ecs(self):
        '''
        Create an {@link EnhancedClosedSwarm} object and start the message 
        exchange process. 

        @return Challenge ECS message
        '''
        self.dl_ecs = EnhancedClosedSwarm(self.keypair, self.torrent_id, 
                                       self.torrent_pubkeys, self.epoa, 
                                       self.reqservice, self)
        cs_message = self.dl_ecs.a_create_challenge()
        cs_message = [CS_CHALLENGE_A] + cs_message       
        if ecssettings.DEBUG_PEER_DISCOVERY:
            print >> sys.stderr, "Pid: %d: initiates DL_ECS to ('%s', %d)." \
                % (os.getpid(), self.connection.get_ip(), self.connection.get_port())
            
        return cs_message

    def terminate_ecs(self):
        '''
        Terminate this ECS Connection: i) because remote node's request for 
        service is no longer valid or ii) in favour to other peer

        @return Info ECS message
        '''
        info = 4 # "No longer valid request or terminated in favour to other peer"
        message = self.ul_ecs.b_create_info_message(info)
        message = [CS_INFO_EXCHANGE_B] + message
        self.connection._send_cs_message(message)
        if ecssettings.DEBUG_PEER_DISCOVERY:
            print >> sys.stderr, "Pid: %d: terminates UL_ECS to ('%s', %d). Reason: %s." \
                % (os.getpid(), self.connection.get_ip(), self.connection.get_extend_listenport(), ecssettings.INFO_FIELD[4])
        self.terminate()
        return message


    def terminate(self):
        '''
        Terminate connection
        '''
        self.ecs_swarm_manager.terminate_connection(self.connection)


    def is_dl_ecs_completed(self):
        '''
        Return if ECS protocol is completed

        @return Boolean
        '''
        try:
            return self.dl_ecs.state == self.dl_ecs.COMPLETED
        except:
            return False

    def is_ul_ecs_completed(self):
        '''
        Return if ECS protocol is completed

        @return Boolean
        '''
        try:
            return self.ul_ecs.state == self.ul_ecs.COMPLETED
        except:
            return False

    def get_ul_key_iv(self):
        return self.ul_ecs.key, self.ul_ecs.iv

    def get_dl_key_iv(self):
        return self.dl_ecs.key, self.dl_ecs.iv

    
    def evaluate_callback(self):
        '''
        Callback function called when scheduling next verification/evaluation 
        of: i) a request for service, or ii) a POA.
        '''
        if self.ul_ecs.remote_poa.expire_time > calendar.timegm(time.gmtime()):
            self.ul_ecs.remote_node_authorized, info, rules_exec_time = self.ul_ecs._evaluate_request(self.ul_ecs.remote_reqservice, self.ul_ecs.remote_rules)
            self.ul_ecs.exec_time = get_exec_time(rules_exec_time, self.ul_ecs.remote_poa.expire_time)
            if ecssettings.DEBUG_DEBUG:
                print >> sys.stderr, "Exec time:", self.ul_ecs.exec_time 
            if self.ul_ecs.remote_node_authorized:
                self.connection.remote_is_authenticated = True
                self.ecs_swarm_manager.sched(self.evaluate_callback, self.ul_ecs.exec_time)
        else:
            self.terminate_ecs()


    def got_cs_message(self, cs_message):
        '''
        Process incomming ECS messages. This method is wrapper of the below
        _got_cs_message() method. It is called from the accompanied 
        {@link Connection} object.

        @param cs_message Incomming ECS message
        @return List or Boolean (Response)
        '''
        try:
            response = self._got_cs_message(cs_message)
        except Exception,e:
            response = None
            if ecssettings.DEBUG_ERROR:
                print >> sys.stderr, "Exception in message %s:" % ecssettings.IDS_TO_MESSAGES[cs_message[0]], e
            if ecssettings.DEBUG_PEER_DISCOVERY:
                print >> sys.stderr, "Pid: %d: terminates ECS to ('%s', %d). Reason: Invalid message received." \
                    % (os.getpid(), self.connection.get_ip(), self.connection.get_extend_listenport())
            self.terminate()
        return response
            

    def _got_cs_message(self, cs_message):
        '''
        Processes incoming ECS messages.

        @param cs_message Incomming ECS message
        @return List of Bool (Response)
        '''
        t = cs_message[0]
        response = None
        if ecssettings.DEBUG_DEBUG:
            print >> sys.stderr, "CS Message received:", cs_message
        if t == CS_CHALLENGE_A:
            # Received message 1 from peer A. Create message 2 as peer B and send it
            # Create upload ECS object
            self.ul_ecs = EnhancedClosedSwarm(self.keypair, self.torrent_id, self.torrent_pubkeys, self.epoa, self.reqservice, self)
            response = self.ul_ecs.b_create_challenge(cs_message[1:])
            response = [CS_CHALLENGE_B] + response
            if ecssettings.DEBUG_PEER_DISCOVERY:
                print >> sys.stderr, "Pid: %d: receives UL_ECS initiation from ('%s', %d). Number of ECS connections %d/%d." \
                    % (os.getpid(), self.connection.get_ip(), self.connection.get_extend_listenport(), self.ecs_swarm_manager.count_connections(), self.ecs_swarm_manager.max_ecs_peers)
            return response
            
        elif t == CS_CHALLENGE_B:
            # Received message 2 from peer B. Create message 3 as peer A and send it
            response = self.dl_ecs.a_provide_poa_message(cs_message[1:])
            response = [CS_POA_EXCHANGE_A] + response
            return response
            
        elif t == CS_POA_EXCHANGE_A:
            # Received message 3 from peer A. Create message 4 as peer B and send it
            # Parse the ReqService field and set the environment vars
            remote_reqservice = cs_message[2]
            remote_rules = cs_message[1][3]
            self.ecs_swarm_manager.authorization_engine.set_environment(self, remote_reqservice)
            # Get the remote peer's priority, since needed for other methods
            remote_max_priority = get_max_priority(remote_rules)
            free_connection = False
            have_free_connection = self.ecs_swarm_manager.have_free_connection()
            # Do I have an available connection? Can I provide the requested service?
            if not have_free_connection:
                ecs_c, current_min_priority = self.ecs_swarm_manager.get_min_priority_connection()
                if remote_max_priority > current_min_priority:
                    free_connection = True
            have_available_connection = have_free_connection or free_connection
            # Get suggested members
            peers = self.ecs_swarm_manager.suggest_members(remote_max_priority)
            self.ul_ecs.set_peers(peers)
            self.ul_ecs.set_have_available_connection(have_available_connection)
            response = self.ul_ecs.b_provide_poa_message(cs_message[1:])
            response = [CS_POA_EXCHANGE_B] + response
            if self.ul_ecs.remote_node_authorized:
                self.connection.remote_is_authenticated = True
                # Set key and iv
                # self.connection.key = self.ul_ecs.key
                # self.connection.iv = self.ul_ecs.iv
                self.remote_priority = remote_max_priority
                if free_connection:
                    ecs_c.terminate_ecs()
                # Schedule evaluations
                self.ecs_swarm_manager.sched(self.evaluate_callback, self.ul_ecs.exec_time)
            # else:
            #     # terminate connection?
            #     pass

            if ecssettings.DEBUG_PEER_DISCOVERY:
                print >> sys.stderr, "Pid: %d: completes UL_ECS to ('%s', %d). " \
                    % (os.getpid(), self.connection.get_ip(), self.connection.get_extend_listenport()) \
                    + "Info: %s, suggested peers: %s." % (ecssettings.INFO_FIELD[response[2]], str(decompact_peers(response[3])))
            return response

        elif t == CS_POA_EXCHANGE_B:
            # Received message 4 from peer B. Verify the message as peer A and proceed with BT
            # Veriry the credential
            response = self.dl_ecs.a_check_poa_message(cs_message[1:])
            # Check Info
            start_cons = False
            if self.dl_ecs.info == 1: # "Valid request"
                remote_max_priority = get_max_priority(self.dl_ecs.remote_rules)
                self.remote_priority = remote_max_priority
                # Save key and iv
                # self.connection.key = self.dl_ecs.key
                # self.connection.iv = self.dl_ecs.iv
                start_cons = True

            if self.dl_ecs.info == 5:
                start_cons = True
                remote_max_priority = get_max_priority(self.dl_ecs.remote_rules)
                self.ecs_swarm_manager.to_reconnect.append(((self.connection.get_ip(), self.connection.get_port()), remote_max_priority))
                
            if start_cons:
                # Start connection to suggested peers
                if len(self.dl_ecs.peers) > 0:
                    self.dl_ecs.peers.reverse()
                    peers_with_id = []
                    for dns in self.dl_ecs.peers:
                        peer_with_id = (dns, 0)
                        peers_with_id.append(peer_with_id)
                    if ecssettings.DEBUG_INFO:
                        print >> sys.stderr, "ECS: Starting connections to", len(peers_with_id)
                    if self.ecs_swarm_manager.connections_starter is not None:
                        self.ecs_swarm_manager.connections_starter(peers_with_id)
            # Define what to do for other self.dl_ecs.info if needed
            return response

        elif t == CS_INFO_EXCHANGE_B:
            # Received message 5 from peer B
            # Verify the message as peer A
            response = self.dl_ecs.a_check_info_message(cs_message[1:])
            return response

    def terminate_after_sending(self, cs_message): # Revise this
        '''
        Returns whether to terminate connection after sending specified message.

        @param cs_message ECS protocol message
        @return Boolean
        '''
        message_id = cs_message[0]
        info = cs_message[2]
        if info in [0, 2, 3, 5]:
            return True
        else:
            return False


class ECS_SwarmManager:
    '''
    ECS_SwarmManager implements the functionality for management of all ECS
    connections a node makes for a single closed swarm
    '''
    def __init__(self, manager, torrent_id, torrent_pubkeys, keypair, poa, reqservice):
        '''
        @param manager {@link ECS_Manager} object
        @param torrent_id Torrent infohash
        @param torrent_pubkeys List of public keys used by the credential 
        issuer for this torrent (These keys are Base64 encoded)
        @param keypair Public/private key pair of this node
        @param poa POA credential of this node
        @param reqservice Requested service properties of this node
        '''
        self.manager = manager
        self.torrent_id = torrent_id
        self.torrent_pubkeys = torrent_pubkeys
        self.keypair = keypair
        self.poa = poa
        self.reqservice = reqservice
        if self.poa is not None:
            self.my_priority = get_max_priority(self.poa.rules)
        self.connections = {} 
        '''
        self.connections format: 
             key: connection (Connecter.py/Connection)
             value: [ecsconnection (ECS_Connection), 
                     is ECS protocol completed (Boolean), 
                     remote_peer_priority (Integer)
                     direction] 
        '''
        self.authorization_engine = self.manager.authorization_engine
        self.max_ecs_peers = ecssettings.MAX_ECS_PEERS
        self.sched = None
        self.to_reconnect = []  # [(dns), priority]
        self.connections_starter = None


    def set_poa(self, poa):
        '''
        Set node's POA credential

        @param poa Node's credential
        '''
        self.poa = poa
        self.my_priority = get_max_priority(self.poa.rules)


    def set_reqservice(self, reqservice):
        '''
        Set node's requested service properties

        @param reqservice Node's requested service properties
        '''        
        self.reqservice = reqservice


    def set_scheduler(self, scheduler):
        '''
        Set scheduling function

        @param scheduler Scheduling function reference
        '''
        self.sched = scheduler
        self.sched(self.reconnect_peers_callback, 30)

    def set_connections_starter(self, starter):
        '''
        Set method for starting connections to received suggested closed swarms
        members.
        '''
        if self.connections_starter is not None:
            self.connections_starter = starter

    def reconnect_peers_callback(self):
        '''
        A callback function that will reschedule reconnection to all peers that 
        had greater priority than us and a lack of free connection.
        '''
        if len(self.to_reconnect) > 0:
            temp = sorted(self.to_reconnect, key=itemgetter(1), reverse=True)
            temp = map(lambda x: x[0], temp)
            peers_with_id = []
            for dns in temp:
                peer_with_id = (dns, 0)
                peers_with_id.append(peer_with_id)
            if ecssettings.DEBUG_INFO:
                print >> sys.stderr, "ECS: Starting connections to", len(peers_with_id)
            self.connections_starter(peers_with_id)
            self.to_reconnect = []
            self.sched(self.reconnect_peers_callback, 30)            


    def register_connection(self, connection):
        '''
        Register a {@link Connection}
        (from Tribler/Core/BitTornado/BT1/Connecter.py module).

        @param connection {@link Connection} object
        @return {@link ECS_Connection} object
        '''
        ecs = ECS_Connection(self, connection, self.torrent_id, self.keypair, self.torrent_pubkeys, self.poa, self.reqservice)
        self.connections[connection] = ecs
        return ecs

    def unregister_connection(self, connection):
        '''
        Terminate a {@link Connection} 
        (from Tribler/Core/BitTornado/BT1/Connecter.py module).
        @param connection {@link Connection} object
        '''
        if connection in self.connections:
            c = connection
            ip = c.get_ip()
            port = c.get_extend_listenport()
            if ecssettings.DEBUG_PEER_DISCOVERY:
                print >> sys.stderr, "Pid: %d: terminates ECS to ('%s', %d)."\
                    % (os.getpid(), connection.get_ip(), connection.get_extend_listenport())
            del self.connections[connection]

    def terminate_connection(self, connection):
        '''
        Terminate a {@link Connection} 
        (from Tribler/Core/BitTornado/BT1/Connecter.py module).

        @param connection {@link Connection} object
        '''
        connection.close()
        
    def suggest_members(self, remote_priority):
        '''
        Select other swarm members for suggesting to a remote node.

        @param remote_priority Priority of the remote node
        @return String
        '''
        max_suggested = ecssettings.MAX_SUGGESTED_MEMBERS
        compactpeers = []

        #First select peers from download connections
        comp_ecs_cons = filter(lambda x: self.connections[x].locally_initiated and self.connections[x].is_dl_ecs_completed(), self.connections.keys())
        comp_ecs_cons = sorted(comp_ecs_cons, key=lambda x: self.connections[x].remote_priority, reverse=True)
        # Create compact peers list of up to max_suggested peers
        pc = 0
        for c in comp_ecs_cons:
            ip = c.get_ip()
            port = c.get_extend_listenport()
            if port is None:
                continue
            compactpeer = compact_peer_info(ip, port)
            if compactpeer not in compactpeers:
                compactpeers.append(compactpeer)
                pc += 1
                if pc == max_suggested:
                    break

        #Then select peers from upload connections
        comp_ecs_cons = filter(lambda x: not self.connections[x].locally_initiated and self.connections[x].is_ul_ecs_completed(), self.connections.keys())
        comp_ecs_cons = sorted(comp_ecs_cons, key=lambda x: self.connections[x].remote_priority, reverse=True)
        # Create compact peers list of up to max_suggested peers
        pc = 0
        for c in comp_ecs_cons:
            ip = c.get_ip()
            port = c.get_extend_listenport()
            if port is None:
                continue
            compactpeer = compact_peer_info(ip, port)
            if compactpeer not in compactpeers:
                compactpeers.append(compactpeer)
                pc += 1
                if pc == max_suggested:
                    break

        if ecssettings.DEBUG_DEBUG:
            print >> sys.stderr, "Suggest peers:", compactpeers

        # Create compact representation of peers
        compactpeerstr = ''.join(compactpeers)
        return compactpeerstr

    def count_connections(self):
        '''
        Count current upload connections that have ECS protocol completed.

        @return Integer
        '''
        cons = filter(lambda x: self.connections[x].is_ul_ecs_completed(), self.connections.keys())
        return len(cons)

    def have_free_connection(self): 
        '''
        Check whether there is a free connection. 

        @return Boolean
        '''
        return self.count_connections() < self.max_ecs_peers 

    def get_min_priority_connection(self):
        '''
        Return the upload connection with minimal priority.

        @return Tuple ({@link ECS_Connection} object, Integer)
        '''
        min_priority = 100      # Large value for priority
        out_con = None
        cons = filter(lambda x: self.connections[x].is_ul_ecs_completed(), self.connections.keys())
        for c in cons:
            if self.connections[c].remote_priority < min_priority:
                min_priority = self.connections[c].remote_priority
                out_con = self.connections[c]
        return out_con, min_priority

    def set_max_ecs_peers(self, value):
        '''
        Set maximum number of upload ecs connections.

        @param value Integer 
        '''
        self.max_ecs_peers = value


class ECS_Manager:
    '''
    ECS_Manager is a singleton class that implements the functionality for 
    management of all ECS Swarm Managers
    '''
    __single = None
    
    def __init__(self, keypair=None, keypair_filename=None):
        '''
        Only one of the input parameters is needed for initialization.

        @param keypair Node's keypair
        @param keypair_filename File containing the node's keypair
        '''
        if ECS_Manager.__single:
            raise RuntimeError, "ECS Manager is Singleton"
        ECS_Manager.__single = self

        if keypair is not None:
            self.keypair = keypair
        else:
            if keypair_filename is not None:
                self.keypair_filename = keypair_filename
                self.keypair = read_cs_keypair(keypair_filename)
            else:
                raise Exception("Cannot start an ECS Manager: missing keypair")
        
        self.swarms = {}
        self.swarm_managers = {}
        '''
        self.swarms format: 
             key: torrent_id; value:[torrent_pubkeys, epoa, reqservice]
        self.swarm_managers format:
             key: torrent_id; value:[ecs_swarm_manager]
        '''
        # Initialize an Authorization Engine object
        self.authorization_engine = Authorization_Engine()

    def getInstance(*args, **kw):
        '''
        Initiate or return the single object of the {@link ECS_Manager} class

        @param Same parameters as for the above method are needed only for 
        initialization; otherwise empty
        @return {@link ECS_Manager} object
        '''
        if ECS_Manager.__single is None:
            ECS_Manager(*args, **kw)
        return ECS_Manager.__single
    getInstance = staticmethod(getInstance)

    def resetSingleton(self):
        """ For testing purposes """
        ECS_Manager.__single = None


    def register_torrent(self, torrent_id, torrent_pubkeys):
        '''
        Register a torrent of a closed swarm

        @param torrent_id Torrent infohash
        @param torrent_pubkeys List of public keys used by the credential 
        issuer for this torrent (These keys are Base64 encoded)
        '''
        epoa=None
        reqservice=None
        self.swarms[torrent_id] = (torrent_pubkeys, epoa, reqservice)
        if ecssettings.DEBUG_DEBUG:
            print >> sys.stderr, "Swarm configuration:", self.swarms
        ecs_sm = ECS_SwarmManager(self, torrent_id, torrent_pubkeys, self.keypair, epoa, reqservice)
        self.swarm_managers[torrent_id] = ecs_sm

    def get_swarm_manager(self, torrent_id):
        '''
        Return the {@link ECS_SwarmManager} object responsible for the specified
        torrent 

        @param torrent_id Torrent infohash
        @return {@link ECS_SwarmManager} object
        '''
        if torrent_id in self.swarms.keys():
            return self.swarm_managers[torrent_id]
        else:
            raise Exception("There is no registered torrent with infohash " + torrent_id)


