// -*- coding: utf-8 -*-
// vi:si:et:sw=2:sts=2:ts=2
/*
  TribeChannel - Torrent video for <video>

  = General Operation =
   
  On using a tribe:// URL a TribeChannel object is created by
  TribeProtocolHandler. This nsIChannel implementing object is passed the URL
  via setTorrentUrl(). Next, it is passed a nsIStreamListener object via 
  asyncOpen by the video element handler. The channel should call this 
  topListener's onStartRequest, onDataAvailable and onStopRequest methods when 
  data from the P2P engine comes in.
  
  First thing that asyncOpen does is contact the P2P engine. If this fails,
  it launches the engine via startBackgroundProcess. For Python, once a control
  connection is established, it sends START and waits for PLAY. On PLAY it 
  opens a data connection to the P2P engine's internal HTTP server and
  passes the data onto the topListener. For Swift, when the engine is assumed
  running we send a GET /roothash to its HTTPGW server, and forward to the
  topListener.
  
  = HTTP failover =
  
  This code supports pre-playback and intra-playback failover to HTTP. 
  Pre-playback failover means we generate the right errors towards the topListener
  such that its code will start trying other <source> elements specified in
  the <video> element. This is called pre-playback failover because it only
  works when the P2P engine has not delivered any data to the topListener
  (e.g., engine startup failure, or .tstream not found, etc.). 
  
  If the P2P engine did send some data but fails before sending all, we
  can do intra-playback failover. For this, the alternative HTTP URL
  must have been passed here via the tribe URL using the 
  "tribe://<torrenturl>|<fallbackurl>" syntax. If that fallbackurl is available
  we will open a connection to it on error and retrieve the missing data
  using a HTTP Range request, and forward it to the topListener.
  
  = Making Metadata Addressable (via XmlHttpRequest) =
  
  Arno, 2011-01-13: For P2P-Next we also want to make the video's metadata 
  addressable via the tribe:// URL scheme and to be able to 
  retrieve it. Three mechanisms can be used for retrieval. The first is using a 
  tribe:// URL as source for an <iframe>. 
  
  The second mechanism that can be used is JavaScript's XmlHttpRequest. 
  To get this to work we need to work around the Cross-Site Scripting
  (XSS) protection of Firefox. To this extent, I make the TribeChannel also
  implement the nsIHttpChannel interface. This FakeHttpChannel interface
  gives the right answers to appease the XSS checks. Furthermore, I had
  to employ a nasty hack. The nsXMLHttpRequest implementation of Firefox,
  see   
  http://mxr.mozilla.org/mozilla-central/source/content/base/src/nsXMLHttpRequest.cpp#1915
  
  checks whether the request parameter on onStart/onStop/onDataAvailable is
  actually the same pointer as the nsIChannel object on which it called 
  asyncOpen. In other words, the request parameter is actually also an nsIChannel
  instance. (Somewhat documented in https://developer.mozilla.org/en/NsIRequest)
  In our case, the nsIChannel object on which it called asyncOpen is
  simply the TribeChannel instance used. So for XmlHttpRequests I don't use
  the request parameter that the P2P data connection gives me, but I pass _this.
  See // XMLHTTP labeled code. 
  
  This hack works fine for XmlHttpRequest's and also for <object src="">
  but for stability of <video src=""> I don't want to make it the default.
  Hence, the use of the hack is user configurable. To enable it, users
  need to add ";xmlhttp" to the tribe:// URL.

  Third mechanism is using <object data="tribe://">. This works, but for some
  reason only with the hack enabled. Otherwise, the request gets cancelled at a
  higher level, and our calls to  topListener::onStartRequest throw 
  NS_BINDING_ABORTED exceptions.

  Written by Jan Gerber, Riccardo Petrocco, Arno Bakker
  see LICENSE.txt for license information
 */

Components.utils.import("resource://gre/modules/XPCOMUtils.jsm");

const Cc = Components.classes;
const Ci = Components.interfaces;

var tribeLoggingEnabled = true;

function LOG(aMsg) {
  if (tribeLoggingEnabled)
  {
    aMsg = ("*** Tribe : " + aMsg + "\n");
    Cc["@mozilla.org/consoleservice;1"].getService(Ci.nsIConsoleService).logStringMessage(aMsg);
    dump(aMsg);
  }
}


function TribeChannel() {
  this.wrappedJSObject = this;
  this.prefService = Cc["@mozilla.org/preferences-service;1"].getService(Ci.nsIPrefBranch).QueryInterface(Ci.nsIPrefService);
  try {
    tribeLoggingEnabled = this.prefService.getBoolPref("tribe.logging.enabled");
  } catch (e) {}

}

TribeChannel.prototype =
{
  classDescription: "Tribe channel",
  classID: Components.ID("68bfe8e9-c7ec-477d-a26c-2391333a7a24"),
  contractID: "@p2pnext.org/tribe/channel;1",
  QueryInterface: XPCOMUtils.generateQI([Ci.tribeIChannel,
                                         Ci.nsIChannel,
                                         Ci.nsIHttpChannel,    //XMLHTTP
                                         Ci.nsIUploadChannel2, //XMLHTTP
                                         Ci.nsISupports]),
  _xpcom_factory : TribeChannelFactory,
  init: false,
  exit: false,
  backend: 'python',
  running: false,
  torrent_url: '',  // URL of .tstream file
  swift_url: '',    // Swift URL (either torrent_url or swift_url is set)
  fallback_url: '', // URL of identical content offered via HTTP
  swift_http_port: 0, 
  is_xmlhttp_req: false,  //XMLHTTP
  setTorrentUrl: function(url) {
	/* Format:
	 * BT: 
	 *     tribe://torrenturl
	 *     where torrenturl is full URL of torrent file, unescaped.
	 * Swift:
	 *     tribe://tracker/roothash@duration
	 *  or
	 *     tribe://tracker/roothash@duration|fallbackurl
	 *     where fallbackurl is the full URL of the HTTP equivalent, unescaped
	 *     
	 * Note: tribe:// is already stripped from url parameter.
	 */
	LOG("setTorrentURL: enter " + url );
    
    // fallbackurl provided?
    var pidx = url.indexOf('|');
    if (pidx == -1)
    {
    	p2purl = url;
    }
    else
    {
    	p2purl = url.substr(0,pidx);
    	this.fallback_url = url.substr(pidx+1);
    }
    	
    
    // Torrent URL or Swift?
    if (p2purl.lastIndexOf('@')-p2purl.lastIndexOf('/') == 41) // Format /root hash@xcontentdur
    {
        this.backend = 'swift';
        this.swift_url = p2purl;
        this.swift_http_port = 8000+Math.floor(Math.random()*50000);
        LOG("setTorrentURL: Parsed Swift URL " + this.swift_url + " fallback " + this.fallback_url );
    }
    else
    {
        this.backend = 'python';
        this.torrent_url = p2purl;
        LOG("setTorrentURL: Parsed Torrent URL " + this.torrent_url +  " fallback " + this.fallback_url);
    }

    // XMLHTTP
    var metaidx = url.indexOf(';xmlhttp');
    if (metaidx != -1)
    {
        LOG("setTorrentURL: XmlHttpRequest workaround enabled");
        this.is_xmlhttp_req = true;
        
        
        // STRIP
        this.torrent_url = this.torrent_url.substr(0,this.torrent_url.length-";xmlhttp".length);
        LOG("setTorrentURL: Stripped Torrent URL " + this.torrent_url);
        
	    this.initializeFakeURIs();        
    	// LOG("setTorrenURL: Fake URI is " + this.URI.spec + " origi is " + this.originalURI.spec );
    }
  },
  cancel: function(aStatus) 
  {
      LOG("cancel called");
      // Arno, 2012-02-21: Calling onBGError() and thus asyncOpen after the
      // content has already played crashes Firefox >= 9 (at least). This
      // despite that fact that shutdown() is called before the onStopRequest()
      // and that shutdown() does this.exit=true which should prevent the call
      // to onBGError().
      //
      // What seems to work is set this.exit=true here. Which is for some 
      // reason is equivalent to _this.exit below, although I don't see
      // this cancel() being called in the logs. Go figure!
      //
      this.exit = true; 
  },
  shutdown: function() 
  {
      LOG("shutdown called\n");
      // Arno, 2012-02-21: For some reason the exit here is not the same as _this.exit below?!
      this.exit = true;	 
      if (this.backend == 'python')
      {
          var msg = 'SHUTDOWN\r\n';
      	  this.outputStream.write(msg, msg.length);

          //this.outputStream.close();
          //this.inputStream.close();
      }
      this.transport.close(Components.results.NS_OK);
  },
  asyncOpen: function(aListener, aContext)
  {
    /*
     Video element handler opens TribeChannel. First establish control connection
     to P2P engine, launching it if not already running. Then send START command
     and wait for PLAY command. Then read data from HTTP data connection and
     pass on to video element handler (Python backend). For swift, launch and 
     send GET /roothash to Swift HTTPGW and forward.
     */
    var _this = this;
    
    LOG('asyncOpen: enter\n');

    if (this.init) 
    {
        LOG('asyncOpen called again\n');
        throw Components.results.NS_ERROR_ALREADY_OPENED;
    }
    
    // Create P2P engine control connection in complex manner!
    this.init = true;
    var socketTransportService = Cc["@mozilla.org/network/socket-transport-service;1"].getService(Ci.nsISocketTransportService);
    
    var hostIPAddr = "127.0.0.1";
    var hostPort = "62063"; // Arno, 2010-08-10: SwarmPlayer independent from SwarmPlugin
    if (this.backend == 'swift')
        hostPort = "62481"; // dummy hack coexistence
    try 
    {
        hostIPAddr = this.prefService.getCharPref("tribe.host.ipaddr");
    } 
    catch (e) {}
    try 
    {
        hostPort = this.prefService.getCharPref("tribe.host.port");
    } 
    catch (e) {}

    this.transport = socketTransportService.createTransport(null, 0, hostIPAddr, hostPort, null);
    // Alright to open streams here as they are non-blocking by default
    this.outputStream = this.transport.openOutputStream(0,0,0);
    this.inputStream = this.transport.openInputStream(0,0,0);

    /* Arno, 2010-06-15: Let player inform BG process about capabilities
       to allow sharing of BGprocess between SwarmTransport and SwarmPlugin
       (the latter has pause capability)
     */
    var msg = 'SUPPORTS VIDEVENT_START\r\n';
    msg = msg + 'START ' + this.torrent_url + '\r\n'; // concat, strange async interface
    
    // This write causes an NS_ERROR_CONNECTION_REFUSED to be thrown if
    // the P2P engine is not yet running. ctrlListener is used to handle
    // this connection attempt. The connection is linked to the ctrlListener
    // below, see pump. 
    this.outputStream.write(msg, msg.length);

    /*
      Listener interface to handle control connection to P2P engine,
      and to launch it if necessary. For Swift there is no control connection,
      we just launch a new instance of the swift engine on a random port for
      each video, currently and do a HTTP GET to the httpgw part of the engine.
     */
    var ctrlListener = 
    {
      onStartRequest: function(request, context) {},
      onStopRequest: function(request, context, status) 
      {
        LOG("ctrlList: onStopRequest " + _this.running );
        if (status == Components.results.NS_ERROR_CONNECTION_REFUSED) 
        {
          // Failed to establish control connection, P2P engine not running.
          if (_this.backend == 'swift' && _this.running == true)
              return;

          if (!_this.startBackgroundDaemon())
          {
              this.onBGError();
              return;
          }
          
          // Now wait a while to give engine time to start and then retry
          // to establish control connection (Python) or GET /roothash (swift)
          //
          _this.running=true;
          // swift backend
          if (_this.backend == 'swift')
          {
              // After it started, send GET /roothash@contendur to swift process
              var hashidx = _this.swift_url.indexOf('/')+1;
              var video_url = 'http://127.0.0.1:'+_this.swift_http_port+'/' ;
              video_url = video_url + _this.swift_url.substr(hashidx,_this.swift_url.length-hashidx);
	      
              // Give process time to start and listen
              var timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
              timer.initWithCallback(function() { ctrlListener.onPlay(video_url); },
                                 1000, Ci.nsITimer.TYPE_ONE_SHOT);
          }
          else
          {
              _this.init=false;
              LOG("ctrlList: onStopRequest: Recalling asyncOpen in 1 s" );
              // Retry control connect after 1 sec
              var timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
              timer.initWithCallback(function() { _this.asyncOpen(aListener, aContext) },
                                 1000, Ci.nsITimer.TYPE_ONE_SHOT);
          }
        }
        else
        {
            LOG('ctrlList: BackgroundProcess closed Control connection\n');
            if (!_this.exit) {
            	LOG('ctrlList: calling onBGError\n');
                this.onBGError();
            }
        }
      },
      onDataAvailable: function(request, context, inputStream, offset, count) 
      {
        // Called when data received from control connection (Python)
      
        var sInputStream = Cc["@mozilla.org/scriptableinputstream;1"].createInstance(Ci.nsIScriptableInputStream);
        sInputStream.init(inputStream);

        var s = sInputStream.read(count).split('\r\n');
        
        for(var i=0;i<s.length;i++) 
        {
          var cmd = s[i];
          if (cmd.substr(0,4) == 'PLAY') 
          {
            var video_url = cmd.substr(5);
            this.onPlay(video_url);
            break;
          }
          if (cmd.substr(0,5) == "ERROR") 
          {
            LOG('ERROR in BackgroundProcess\n');
            this.onBGError();
            break;
          }
        }
      },
      onBGError: function() 
      {
          // Arno: It's hard to figure out how to throw an exception here
          // that causes FX to fail over to alternative <source> elements
          // inside the <video> element. The hack that appears to work is
          // to create a Channel to some URL that doesn't exist.
    	  //
    	  // 2010-11-22, This implements pre-play fallback, as Firefox won't 
    	  // fall back to another <source> when the first source has delivered
    	  // some data. See onPlay for intra-play fallback.
          //
          
          LOG("onBGError: AFTER shutdown?");
          
          var fake_video_url = 'http://127.0.0.1:6877/createxpierror.html';
          var ios = Cc["@mozilla.org/network/io-service;1"].getService(Ci.nsIIOService);
          var video_channel = ios.newChannel(fake_video_url, null, null);
          video_channel.asyncOpen(aListener, aContext);
      },
      onPlay: function(video_url) {
          /*
    	    Start playback of P2P-delivered video, i.e. connect to P2P engine's
    	    internal HTTP server, retrieve data and forward it to topListener.
    	   */
    	  LOG('PLAY !!!!!! '+video_url+'\n');
    	
    	  var dataChannel = null;
    	  if (_this.is_xmlhttp_req)
    	  {
    	  	  // XMLHTTP, topListener is XmlHttpRequest, enable hack
    	  	  dataChannel = new XmlHttpForwardChannel(_this,video_url);
    	  }
    	  else if (_this.fallback_url == '')
    	  {
			  // No HTTP fallback, deliver content directly to topListener
			  dataChannel = new P2PDataForwardChannel(video_url);
    	  }
    	  else
    	  {
			  // Intra-playback HTTP fallback available. I.e. fallback to HTTP 
			  // when P2P engine has already delivered some data. 
			  //
    	  	  dataChannel = new P2PDataFallbackForwardChannel(video_url,_this.fallback_url);
    	  }

       	  dataChannel.asyncOpen(aListener, aContext);
       	  
          //Cleanup TribeChannel if window is closed
          var windowMediator = Cc["@mozilla.org/appshell/window-mediator;1"].getService(Ci.nsIWindowMediator);
          var nsWindow = windowMediator.getMostRecentWindow("navigator:browser");
          nsWindow.content.addEventListener("unload", function() { _this.shutdown() }, false);
      },
    };
    // Open control connection
    var pump = Cc["@mozilla.org/network/input-stream-pump;1"].createInstance(Ci.nsIInputStreamPump);
    pump.init(this.inputStream, -1, -1, 0, 0, false);
    pump.asyncRead(ctrlListener, null);
  },
  startBackgroundDaemon: function() 
  {
      try 
      {
            LOG('BackgroundProcess safe start');
            this.safeStartBackgroundDaemon();
            return true;
      } 
      catch (e) 
      {
          LOG('BackgroundProcess could not be started\n' + e );
          return false;
      }
  },
  safeStartBackgroundDaemon: function() 
  {
    var osString = Cc["@mozilla.org/xre/app-info;1"]
                     .getService(Components.interfaces.nsIXULRuntime).OS;  
    var bgpath = "";
    if (this.backend == 'python')
    {
        if (osString == "WINNT")
            bgpath = 'SwarmEngine.exe';
        else if (osString == "Darwin")
            bgpath = "SwarmPlayer.app/Contents/MacOS/SwarmPlayer";
        else
            bgpath = 'swarmengined';
    }
    else
    {
        // swift backend
        if (osString == "WINNT")
            bgpath = 'swift.exe';
        else if (osString == "Darwin")
            bgpath = "swift"; 
        else
            bgpath = 'swift';
        var urlarg = this.swift_url.substr(0,this.swift_url.indexOf('/'));
    }
   
    var file = __LOCATION__.parent.parent.QueryInterface(Ci.nsILocalFile);
    file.appendRelativePath('bgprocess');
    file.appendRelativePath(bgpath);

    // Arno, 2010-06-16: Doesn't work on Ubuntu with /usr/share/xul-ext* install      
    try {
        file.permissions = 0755;
    } catch (e) {}
    var process = Cc["@mozilla.org/process/util;1"].createInstance(Ci.nsIProcess);
    process.init(file);
    var args = [];
    if (this.backend == 'python')
    {
        if (tribeLoggingEnabled && osString != "Darwin")
          args.push('debug');
    }
    else
    {
      // swift backend
      args.push('-t');
      args.push(urlarg);
      args.push('-g');
      args.push('0.0.0.0:'+this.swift_http_port);
      args.push('-w');
      // debugging on
      //if (tribeLoggingEnabled && osString != "Darwin")
      //{
      //    args.push('-D');
      //    args.push('log.log'); //dummy argument?
      //}
    }
    process.run(false, args, args.length);
  },
} 
// IMPORTANT: More methods added to TribeChannel below, see TribeFakeHttpChannel.


/*
 * Channel that establishes data connection (=HTTP) with P2P engine and forwards
 * the received data to the topListener. Simple case for P2P video.
 */
function P2PDataForwardChannel(video_url)
{
   this.video_url = video_url;
}

P2PDataForwardChannel.prototype = 
{
    asyncOpen: function(aListener,aContext)
    {
        // Create data connection to P2P engine
        var ios = Cc["@mozilla.org/network/io-service;1"].getService(Ci.nsIIOService);
        var video_channel = ios.newChannel(this.video_url, null, null);
        video_channel.asyncOpen(aListener, aContext);
    }
};


/* 
 * Channel that establishes data connection (=HTTP) with P2P engine and forwards
 * the received data to the topListener. Should the P2P engine fail to deliver
 * all data, this channel falls back to an alternative HTTP source to retrieve
 * the missing data. Case is P2P video with HTTP fallback available.
 */ 
function P2PDataFallbackForwardChannel(video_url,fallback_url) {

    this.video_url = video_url;
    this.fallback_url = fallback_url;

    this.topListener = null;
    
    // Last offset delivered to upper layer
    this.lastoffset = 0;
    
    // Swift HTTPGW currently returns Content-Length rounded up to nearest KB, or
	// exact length. Property of swift protocol. FIXME
	this.fuzzy_len = 0;
	
    // Arno, 2010-11-10: I once noticed swift delivering data after onStop. Protect.
    this.swiftstopped = false;

    this.video_httpchan = null;
}

P2PDataFallbackForwardChannel.prototype =
{
    asyncOpen: function(aListener,aContext)
    {
        this.topListener = aListener;
        
        // Create data connection to P2P engine
        var ios = Cc["@mozilla.org/network/io-service;1"].getService(Ci.nsIIOService);
        var video_channel = ios.newChannel(this.video_url, null, null);
        this.video_httpchan = video_channel.QueryInterface(Components.interfaces.nsIHttpChannel);        
        
        // Use ourselves as listener
        video_channel.asyncOpen(this, aContext);
    },
    onStartRequest: function(request, context) 
    {
        // Established data connection to P2P engine?
        LOG("DataFwd: onStart: status " + request.status + " name " + request.name + "\n");
        if (request.status == Components.results.NS_OK)
        {
            // Yes, we have connection
            try
        	{
        	    LOG("DataFwd: onStart: Content-Length: " + this.video_httpchan.getResponseHeader("Content-Length") + "\n");
        	    // Save the Content-Length such that we know what part we're
        	    // missing when the P2P engine fails partway thru.
        		this.fuzzy_len = parseInt(this.video_httpchan.getResponseHeader("Content-Length"));
        	}
        	catch(e)
        	{
        	    LOG("DataFwd: onStart: Content-Length not avail: " + e );
        	}
        }
        // Must always be called! Be mindful of exception above this!
        LOG("DataFwd: Calling topListener::onStart");
        this.topListener.onStartRequest(request,context);
     },
     onStopRequest: function(request, context, status) 
     {
         // Data connection to P2P engine closed
         LOG("DataFwd: onStop: status " + status + "\n");
         this.swiftstopped = true;
   
         // Did we get all data? If not, do intra-playback fallback to HTTP
         LOG("DataFwd: onStop: delivered all? Sent " + this.lastoffset + " expected fzlen " + this.fuzzy_len );
         if (this.lastoffset < this.fuzzy_len)
         {
        	 // Possibly still data to send (not sure due to fuzzy swift
        	 // content-length). Fail over to HTTP server, at lastoffset
        	 // using Range: request.
        	 LOG("DataFwd: onStop: Attempting to get missing data from HTTP fallback");
        	 
        	 var httpfbChannel = new HttpFallbackChannel(this.fallback_url,this.lastoffset);
        	 httpfbChannel.asyncOpen(this.topListener,context);
         }
         else
         {
        	 // Either sent all, or nothing (this.lastoffset == 0), but
        	 // this can be handled the same, just stop. Causes
        	 // failover to next <source> element in <video> element in latter 
        	 // case. In other words, by stopping here we use pre-playback 
        	 // failover instead of more complex intra-playback.
        	 //
        	 LOG("DataFwd: onStop: sent complete asset, no intra-playback failover" );
        	 LOG("DataFwd: Calling topListener::onStop");
       	     this.topListener.onStopRequest(request,context,Components.results.NS_OK);
         }
     },
     onDataAvailable: function(request, context, inputStream, offset, count) 
     {
         // Receiving content on data connection to P2P engine, forward.
         LOG("DataFwd: onData: off " + offset + " count " + count + " stop " + this.swiftstopped );
         if (!this.swiftstopped)
         {
             // LOG("DataFwd: Calling topListener:onData");
             this.topListener.onDataAvailable(request,context,inputStream,offset,count);
             
             this.lastoffset = offset+count;
         }
     }
};
          

/*
 * Channel that establishes HTTP connection with fallback HTTP URL and forwards
 * the received data to the topListener.
 */
function HttpFallbackChannel(fallback_url,lastoffset)
{
   this.fallback_url = fallback_url;
   this.lastoffset = lastoffset;

   this.topListener = null;
   this.fvideo_httpchan = null;
}

HttpFallbackChannel.prototype = 
{
    asyncOpen: function(aListener,aContext)
    {
        this.topListener = aListener;

        //var fvideo_url = "http://upload.wikimedia.org/wikipedia/en/0/07/Sintel_excerpt.OGG";
        //var fvideo_url = "http://127.0.0.1:8061/Sintel_excerpt.OGG";
        var fvideo_url = this.fallback_url;

	    LOG("HttpAltFwd: asyncOpen: URL " + fvideo_url );
        
        var fios = Cc["@mozilla.org/network/io-service;1"].getService(Ci.nsIIOService);
        var fvideo_channel = fios.newChannel(fvideo_url, null, null);
        this.fvideo_httpchan = fvideo_channel.QueryInterface(Components.interfaces.nsIHttpChannel);

        var rangestr = "bytes="+this.lastoffset+"-";
        this.fvideo_httpchan.setRequestHeader("Range", rangestr, false );
         
        // Use ourselves as listener
        fvideo_channel.asyncOpen(this,aContext);
    },
    onStartRequest: function(request, context) 
    {
        // Connection to fallback established? 
        LOG("HttpAltFwd: onStart: status " + request.status);
         
        if (request.status == Components.results.NS_OK)
        {
            // Connection to fallback succeeded
            if (this.fvideo_httpchan.responseStatus == 206)
            {
                // Got range reply we wanted, good. (Don't communicate onStart to 
                // topListener, remember, we're transparently failing over) 
                LOG("HttpAltFwd: onStart: Content-Range: " + this.fvideo_httpchan.getResponseHeader("Content-Range") + "\n");
            }
            else
            {
                // Not a range reply :-(
            	// Because of the fuzzy swift content-length we may have 
            	// requested more data than there is. In that case, the HTTP 
            	// server should respond with 416, range-req not satisfiable.
            	// upload.wikipedia.org, however, appears to return 200?!
            	// 
            	// Close HTTP conn and tell reader we're done.
            	//
            	LOG("HttpAltFwd: onStart: Bad HTTP response, aborting failover " + this.fvideo_httpchan.responseStatus);
            	request.cancel(Components.results.NS_OK);
            }
        }
        else
        {
            // Error contacting fallback server, e.g. NS_ERROR_CONNECTION_REFUSED
        	LOG("HttpAltFwd: onStart: Error contacting HTTP fallback server, aborting failover.");
        	request.cancel(Components.results.NS_OK);
        }
    },
    onStopRequest: function(request, context, status) 
    {
        // Connection to fallback closed
        LOG("HttpAltFwd: onStop\n");
        // Tell sink that we're definitely done
        LOG("HttpAltFwd: onStop: Calling topListener::onStop\n");
        this.topListener.onStopRequest(request,context,status);
    },
    onDataAvailable: function(request, context, inputStream, offset, count) 
    {
        // Send replacement data from HTTP to sink
        LOG("HttpAltFwd: onData: off " + offset + " count " + count );
        this.topListener.onDataAvailable(request,context,inputStream,offset,count);
    }
}; 
  




/* 
 * XMLHTTP: Channel that establishes data connection (=HTTP) with P2P engine to
 * retrieve metadata on behalf of topListener that is serving an XmlHTTPRequest.
 * Apply special hacks for that.
 */ 
function XmlHttpForwardChannel(tribe_chan,video_url) 
{
    this.tribe_chan = tribe_chan; 
    
    this.video_url = video_url;
    this.topListener = null;
    this.video_httpchan = null;
}

XmlHttpForwardChannel.prototype =
{
    asyncOpen: function(aListener,aContext)
    {
        this.topListener = aListener;
        
        // Create data connection to P2P engine
        var ios = Cc["@mozilla.org/network/io-service;1"].getService(Ci.nsIIOService);
        var video_channel = ios.newChannel(this.video_url, null, null);
        this.video_httpchan = video_channel.QueryInterface(Components.interfaces.nsIHttpChannel);        
        
        // Use ourselves as listener
        video_channel.asyncOpen(this, aContext);
    },
    onStartRequest: function(request, context) 
    {
        // Established data connection to P2P engine?
        LOG("XmlFwd: onStart: status " + request.status + " name " + request.name + "\n");
        //XMLHTTP
        this.tribe_chan.setBGHttpRequest(request);
        
        if (request.status == Components.results.NS_OK)
        {
            try
        	{
       		    LOG("XmlFwd: onStart: Content-Type: " + this.video_httpchan.getResponseHeader("Content-Type") + "\n");
        		  
        		// XMLHTTP: User of TribeChannel sets nsIChannel.contentType
        		// to application/xml. Don't know why there is the 
        		// contentType at channel level when there are also 
        		// setRequestHeader and setResponseHeader. Anyhew, 
        		// if not reset, the calling XMLHttpRequest.cpp will 
        		// try to interpret our reply as XML, writing an 
        		// "syntax error" to Firefox's Error console if it's 
        		// not.
        		//
        		ctype = this.video_httpchan.getResponseHeader("Content-Type");
        		this.tribe_chan.specialSetContentType(ctype);
        	}
        	catch(e)
        	{
        	    LOG("XmlFwd: onStart: Content-Type not avail: " + e );
        	}
        }
        // Must always be called! Be mindful of exception above this!
        LOG("XmlFwd: Calling topListener::onStart");
        // Hack: replace request param with TribeChannel
        this.topListener.onStartRequest(this.tribe_chan,context);
     },
     onStopRequest: function(request, context, status) 
     {
         // Data connection to P2P engine closed
         LOG("XmlFwd: onStop: status " + status + "\n");
         LOG("XmlFwd: Calling topListener::onStop");
         // Hack: replace request param with TribeChannel
         this.topListener.onStopRequest(this.tribe_chan,context,Components.results.NS_OK);
     },
     onDataAvailable: function(request, context, inputStream, offset, count) 
     {
         // Receiving content on data connection to P2P engine, forward.
         LOG("XmlFwd: onData: off " + offset + " count " + count );
         LOG("XmlFwd: Calling topListener::onData");
         // Hack: replace request param with TribeChannel
         this.topListener.onDataAvailable(this.tribe_chan,context,inputStream,offset,count);
     }
};




/*
 XMLHTTP: Mix-in class for TribeChannel that provides a fake nsIHttpChannel 
 and nsIRequest interface, that we need for metadata retrieval via 
 XmlHttpRequest support.
 */
TribeFakeHttpChannel =
{
    // Utility functions
    bg_http_request: null,  
    setBGHttpRequest: function(request)
    {
        LOG("FakeHttp: setBGRequest: Saving response headers from P2P engine to pass off as our own.");
    	this.bg_http_request = request;
    },
    specialSetContentType: function(ctype)
    {
        LOG("FakeHttp: SpecialSetContentType: " + ctype );
    	this.contentType = ctype;
    },
    initializeFakeURIs: function()
    {
        // TribeChannel.URI and TribeChannel.originalURI need to be set to pass
        // some XmlHttpRequest tests.
        // TODO: randomize?
        var fakeport = Math.floor(Math.random()*50000);
        var fakeuri1 = Cc["@mozilla.org/network/simple-uri;1"].createInstance(Ci.nsIURI);
        fakeuri1.spec = "http://127.0.0.1:"+fakeport+"/fakeurl.html";
        var fakeuri2 = Cc["@mozilla.org/network/simple-uri;1"].createInstance(Ci.nsIURI);
        fakeuri2.spec = "http://127.0.0.1:"+fakeport+"/fakeorigurl.html";
    
        this.URI = fakeuri1;
        this.originalURI = fakeuri2;
    },
	//
    //nsIHttpChannel implementation
    //
    requestMethod: 'GET',
    referrer: null,
    getRequestHeader: function(aHeader) 
    {
        LOG("FakeHTTP: getRequestHeader "+ aHeader );
        if (aHeader == "Content-Type")
        { 
            LOG("FakeHTTP: getRequestHeader: content" );
            // Arno: returning other than text/plain gives MALFORMED_URI error in FF4.
            //return "text/plain";
            //return null;
            throw Components.Exception("Arno requestHeader not set", Cr.NS_ERROR_NOT_AVAILABLE);
        }
        else
        {
            LOG("FakeHTTP: getRequestHeader: empty" );
            return "bla";
        }
    },
    setRequestHeader: function(aHeader,aValue,aMerge)
    {
  	    LOG("FakeHTTP: setRequestHeader "+ aHeader + " val " + aValue + " aMerge " + aMerge );
    },
    visitRequestHeaders: function(aVisitor)
    {
        LOG("FakeHTTP: visitRequestHeaders: " + aVisitor );
    },
    allowPipelining: true,
    redirectionLimit: 100,
    responseStatus: 200,
    responseStatusText: 'ARNORESPONSESTATUSTEXT',
    requestSucceeded: true,
    getResponseHeader: function(aHeader)
    {
        LOG("FakeHTTP: getResponseHeader "+ aHeader );
        if (this.bg_http_request != null)
        {
            // Arno, 2011-01-12: May throw NS_ERROR_NOT_AVAILABLE exception
            // if header not set.
            aValue = this.bg_http_request.getResponseHeader(aHeader);
            LOG("FakeHTTP: getResponseHeader: value is " + aValue);
            return aValue;
        }
        else
        {
            LOG("FakeHTTP: getResponseHeader: is not meta request, throw exception");
            throw Components.Exception("Arno BG HTTP requestHeader not set", Cr.NS_ERROR_NOT_AVAILABLE);
        }
    },
    setResponseHeader: function(aHeader,aValue,aMerge)
    {
        LOG("FakeHTTP: setResponseHeader "+ aHeader + " val " + aValue + " aMerge " + aMerge );
    },
    visitResponseHeaders: function(aVisitor)
    {
        LOG("FakeHTTP: visitResponseHeaders: " + aVisitor );
    },
    isNoStoreResponse: function()
    {
        LOG("FakeHTTP: isNoStoreReponse" );
    	return true;
    },
    isNoCacheResponse: function()
    {
        LOG("FakeHTTP: isNoCacheReponse" );
    	return true;
    },
    //
    // nsIRequest interface
    //
    isPending: function()
    {
        LOG("FakeHTTP: nsIRequest: isPending()" );
    	return false;
    },
    resume: function()
    {
        LOG("FakeHTTP: nsIRequest: resume()" );
    }, 
    suspend: function()
    {
        LOG("FakeHTTP: nsIRequest: suspend()" );
    } 
};
// Do mix
for (prop in TribeFakeHttpChannel)
{
   TribeChannel.prototype[prop] = TribeFakeHttpChannel[prop];
}



var TribeChannelFactory =
{
  createInstance: function (outer, iid)
  {
    if (outer != null)
      throw Components.results.NS_ERROR_NO_AGGREGATION;

    if (!iid.equals(Ci.tribeIChannel) &&
        !iid.equals(Ci.nsIChannel) &&
        !iid.equals(Ci.nsIHttpChannel) &&
        !iid.equals(Ci.nsIUploadChannel2) &&
        !iid.equals(Ci.nsISupports) )
      throw Components.results.NS_ERROR_NO_INTERFACE;

    var tc =  new TribeChannel();
    var tcid = tc.QueryInterface(iid);
    return tcid;
  }
};

/**
* XPCOMUtils.generateNSGetFactory was introduced in Mozilla 2 (Firefox 4).
* XPCOMUtils.generateNSGetModule is for Mozilla 1.9.2 (Firefox 3.6).
*/
if (XPCOMUtils.generateNSGetFactory)
    var NSGetFactory = XPCOMUtils.generateNSGetFactory([TribeChannel]);
else
    var NSGetModule = XPCOMUtils.generateNSGetModule([TribeChannel]);

