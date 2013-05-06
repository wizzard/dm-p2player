
import core.message as message
from core.node import Node
import core.ptime as time
import pickle

STATUS_PINGED = 'PINGED'
STATUS_OK = 'OK'
STATUS_FAIL = 'FAIL'

class ExperimentalManager:
    
    def __init__(self, my_id):
        self.my_id = my_id
        self._stop = False
        #TODO data structure to keep track of things
        self.pinged_ips = {}
        # this dict contains................ #TODO
        self.num_ok = 0
        self.num_fail = 0
        pass
        
         
    def on_query_received(self, msg):
        
                
            
        if not self._stop and msg.query =='find_node':
            #self._stop = True
            #self.pinged_ips[msg.src_node.ip] = msg.src_node.ip
            print '\nExperimentalModule got query (%s) from  node  %r =' % (msg.query ,  msg.src_node)
            
            if msg.src_node.ip not in self.pinged_ips:
                
                
                # prepare to ping to the node from which it got ping
                probe_query = message.OutgoingPingQuery(msg.src_node,
                                                    self.my_id,
                                                    ExpObj(msg.query))
                #self.pinged_ips[msg.src_node.ip] = True
                self.pinged_ips[msg.src_node.ip] = STATUS_PINGED
#                print 'ping send to ip address :  ' , self.pinged_ips['ip_address']
                
                return [probe_query]
    #return []
                               
                 
        
    def on_response_received(self, msg, related_query):
        if self.pinged_ips.get(msg.src_node.ip) == STATUS_PINGED:
            print 'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX'
        if related_query.experimental_obj:
            print "probe OK (%r) (%r)" % ( related_query.experimental_obj.value , msg.src_node )
            self.pinged_ips[msg.src_node.ip] = STATUS_OK
            elapsed_time = time.time() - related_query.experimental_obj.query_ts
            print 'RTT = ',elapsed_time
            self.num_ok += 1
            
        pass
           
    def on_timeout(self, related_query):
        if related_query.experimental_obj:
            elapsed_time = time.time() - related_query.experimental_obj.query_ts 
            print 'prove FAILED Due to Time-Out' ,related_query.experimental_obj.value
            print 'RTT = ',elapsed_time
            self.pinged_ips[related_query.dst_node.ip] = STATUS_FAIL
            self.num_fail += 1 
            
               
               
    def on_stop(self):
        
        fob=open('c:/Users/zinat/pythonworkspace/pymdht/plugins/ping_res.txt','w')
        for ip, status in self.pinged_ips.iteritems():
            fob.write('%s %s\n' % (ip, status))
        fob.close()
        
        # TODO print node.ip  port  node.id    ping_response(ok/fail)
        # count number of nodes which responses
        # create a file and store the data
        '''
        for self.pinged_ips['ip_address'],self.pinged_ips['status'] in self.pinged_ips.iteritems():
            if self.pinged_ips['status'] == 'OK':
                num_ok += 1
                print 'OK= ', num_ok
            elif self.pinged_ips['status'] == 'Fail':
                num_fail += 1 
                print 'Fail=', num_fail
            else:
                print 'fail'    
    
        pass
        
        '''
         
        
            
class ExpObj:
    def __init__(self, value):
        self.value = value
        self.query_ts = time.time()
        print 'Got query at Time :',self.query_ts
        pass
        
            
        
        