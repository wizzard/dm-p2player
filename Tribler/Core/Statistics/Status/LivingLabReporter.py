# Written by Njaal Borch
# see LICENSE.txt for license information

#
# Arno TODO: Merge with Core/Statistics/Status/*
#

import time
import sys
from types import DictType,ListType
from traceback import print_exc


import httplib

import XmlPrinter
import xml.dom.minidom

import Status
from Tribler.Core.Utilities.timeouturlopen import find_proxy
from Tribler.Core.BitTornado.__init__ import version_id

STRESSTEST = False
DEBUG = False


class LivingLabPeriodicReporter(Status.PeriodicStatusReporter):
    """
    This reporter creates an XML report of the status elements
    that are registered and sends them using an HTTP Post at
    the given interval.  Made to work with the P2P-Next lab.
    """
    
    host = "p2pnext-statistics.comp.lancs.ac.uk"
    #path = "/testpost/"
    path = "/post/"
    
    def __init__(self, name, frequency, id, error_handler=None,
                 print_post=False, ext_ip_callback=None, activity_callback=None, report_if_no_events=False):
        """
        Periodically report to the P2P-Next living lab status service

        name: The name of this reporter (ignored)
        frequency: How often (in seconds) to report
        id: The ID of this device (e.g. permid)
        error_handler: Optional error handler that will be called if the
        port fails
        print_post: Print post to stderr when posting to the lab (largely
        useful for debugging)
        
        """
        Status.PeriodicStatusReporter.__init__(self,
                                               name,
                                               frequency,
                                               error_handler)
        self.device_id = id
        self.print_post = print_post 
        self.num_reports = 0
        self.ext_ip_callback = ext_ip_callback
        self.activity_callback = activity_callback
        self.report_if_no_events = report_if_no_events
        
        self.post_errors = 0

    def new_element(self, doc, name, value):
        """
        Helper function to save some lines of code
        """

        element = doc.createElement(name)
        if value is not None:
            value = doc.createTextNode(str(value))
            element.appendChild(value)
        return element
        
    def report(self):
        """
        Create the report in XML and send it
        """

        print >>sys.stderr,"LivingLabReporter: report"

        # Create the report
        doc = xml.dom.minidom.Document()
        root = doc.createElement("nextsharedata")
        doc.appendChild(root)
        
        # Create the header
        header = doc.createElement("header")
        root.appendChild(header)
        header.appendChild(self.new_element(doc, "deviceid", self.device_id))
        header.appendChild(self.new_element(doc, "timestamp",
                                           long(round(time.time()))))
        
        # Arno, 2011-02-03: Following ULANC spec 1-2-2011
        if self.ext_ip_callback is not None:
            ip = self.ext_ip_callback()
        else:
            ip = "127.0.0.1"
        header.appendChild(self.new_element(doc, "ipaddr", ip))    
        
        version = version_id
        header.appendChild(self.new_element(doc, "swversion", version))
        
        # Arno, 2011-02-03: Following ULANC spec 1-2-2011, bogus STB entries
        header.appendChild(self.new_element(doc, "powerstate", "powered"))
        if self.activity_callback is not None:
            active = self.activity_callback()
            actstr = "playing" if active else "idle"
        else:
            actstr = "playing"
        header.appendChild(self.new_element(doc, "activity", actstr))
        
        # TODO
        header.appendChild(self.new_element(doc, "uptime", "0"))
        # TODO
        header.appendChild(self.new_element(doc, "total_ram", "0"))
        # TODO: use wx.GetFreeMemory()
        header.appendChild(self.new_element(doc, "free_ram", "0"))
        # TODO
        header.appendChild(self.new_element(doc, "cpu_load", "0.0"))
        header.appendChild(self.new_element(doc, "temperature", "0"))
        header.appendChild(self.new_element(doc, "num_processes", "0"))
        header.appendChild(self.new_element(doc, "num_keypresses", "0"))
        header.appendChild(self.new_element(doc, "post_errors", self.post_errors))
        header.appendChild(self.new_element(doc, "post_timeouts", "0"))

         

        elements = self.get_elements()
        if len(elements) > 0:
        
            # Now add the status elements
            if len(elements) > 0:
                report = doc.createElement("event")
                root.appendChild(report)

                report.appendChild(self.new_element(doc, "attribute",
                                                   "statusreport"))
                report.appendChild(self.new_element(doc, "timestamp",
                                                   long(round(time.time()))))
                for element in elements:
                    print element.__class__
                    report.appendChild(self.new_element(doc,
                                                       element.get_name(),
                                                       element.get_value()))

        events = self.get_events()
        if len(events) > 0:
            for event in events:
                report = doc.createElement(event.get_type())
                root.appendChild(report)
                report.appendChild(self.new_element(doc, "attribute",
                                                   event.get_name()))
                if event.__class__ == Status.EventElement:
                    report.appendChild(self.new_element(doc, "timestamp",
                                                       event.get_time()))
                elif event.__class__ == Status.RangeElement:
                    report.appendChild(self.new_element(doc, "starttimestamp",
                                                       event.get_start_time()))
                    
                    report.appendChild(self.new_element(doc, "endtimestamp",
                                                       event.get_end_time()))
                    
                # Arno, 2011-02-03: Support for event with dict as value
                self.add_list_to_report(doc, report, event.get_values())
                    
        if not self.report_if_no_events and len(elements) == 0 and len(events) == 0:
            
            print >>sys.stderr,"LivingLabReporter: report: No events to report"
            
            return # Was nothing here for us
        
        # all done
        xml_printer = XmlPrinter.XmlPrinter(root)
        if self.print_post:
            print >> sys.stderr, xml_printer.to_pretty_xml()
        xml_str = xml_printer.to_xml()

        # Now we send this to the service using a HTTP POST
        self.post(xml_str)
        
        ##print >>sys.stderr,"POSTING TO ULANC",xml_str

    def add_dict_to_report(self,doc,parent,d):
        for key,value in d.iteritems():
            if type(value) == DictType:
                ne = self.new_element(doc, key, None)
                self.add_dict_to_report(doc,ne,value)
            elif type(value) == ListType:
                self.add_list_to_report(doc, parent, value)
            else:
                ne = self.new_element(doc, key, value)
            parent.appendChild(ne)
                
    def add_list_to_report(self,doc,parent,values):
        for value in values:
            if type(value) == DictType:
                self.add_dict_to_report(doc, parent, value)
            elif type(value) == ListType:
                self.add_list_to_report(doc, parent, value)
            else:
                parent.appendChild(self.new_element(doc, "value", value))

    def post(self, xml_str):
        """
        Post a status report to the living lab using multipart/form-data
        This is a bit on the messy side, but it does work
        """

        #print >>sys.stderr, xml_str
        
        self.num_reports += 1
        
        boundary = "------------------ThE_bOuNdArY_iS_hErE_$"
        # headers = {"Host":self.host,
        #            "User-Agent":"NextShare status reporter 2009.4",
        #            "Content-Type":"multipart/form-data; boundary=" + boundary}

        # Arno, 2011-05-25: Must not end in double --, apparently (can't find spec).
        base = ["--" + boundary ]
        base.append('Content-Disposition: form-data; name="NextShareData"; filename="NextShareData"')
        base.append("Content-Type: text/xml")
        base.append("")
        base.append(xml_str)
        base.append("--" + boundary + "--")
        base.append("")
        base.append("")
        body = "\r\n".join(base)

        # Arno, 2010-03-09: Make proxy aware and use modern httplib classes
        wanturl = 'http://'+self.host+self.path
        proxyhost = find_proxy(wanturl)
        if proxyhost is None:
            desthost = self.host
            desturl = self.path
        else:
            desthost = proxyhost
            desturl = wanturl

        h = httplib.HTTPConnection(desthost)
        h.putrequest("POST", desturl)

        # 08/11/10 Boudewijn: do not send Host, it is automatically
        # generated from h.putrequest.  Sending it twice causes
        # invalid HTTP and Virtual Hosts to
        # fail.
        #h.putheader("Host",self.host)

        h.putheader("User-Agent","NextShare status reporter 2010.3")
        h.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        h.putheader("Content-Length",str(len(body)))
        h.endheaders()
        h.send(body)

        resp = h.getresponse()
        if DEBUG:
            # print >>sys.stderr, "LivingLabReporter:\n", xml_str
            print >>sys.stderr, "LivingLabReporter:", `resp.status`, `resp.reason`, "\n", resp.getheaders(), "\n", resp.read().replace("\\n", "\n")

        if resp.status != 200:
            self.post_errors += 1
            if self.error_handler:
                try:
                    self.error_handler(resp.status, resp.read())
                except Exception, e:
                    pass
            else:
                print >> sys.stderr, "Error posting but no error handler:", resp.status
                print >> sys.stderr, resp.read()
        

if __name__ == "__main__":
    """
    Small test routine to check an actual post (unittest checks locally)
    """

    status = Status.get_status_holder("UnitTest")
    def test_error_handler(code, message):
        """
        Test error-handler
        """
        print "Error:", code, message
        
    reporter = LivingLabPeriodicReporter("Living lab test reporter",
                                         1.0, test_error_handler)
    status.add_reporter(reporter)
    s = status.create_status_element("TestString", "A test string")
    s.set_value("Hi from Njaal")

    time.sleep(2)

    print "Stopping reporter"
    reporter.stop()

    print "Sent %d reports"% reporter.num_reports
