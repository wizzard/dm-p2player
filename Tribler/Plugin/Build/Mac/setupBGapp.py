# Written by Riccardo Petrocco
# see LICENSE.txt for license information
#
# This script builds SwarmPlugin FF plugin
#
#

import os
from distutils.util import get_platform
import sys,os,platform,shutil

from plistlib import Plist

from setuptools import setup
import py2app # Not a superfluous import!

from Tribler.__init__ import LIBRARYNAME
from Tribler.Plugin.__init__ import PRODUCTNAME


def includedir( srcpath, dstpath = None ):
    """ Recursive directory listing, filtering out svn files. """

    total = []

    cwd = os.getcwd()
    os.chdir( srcpath )

    if dstpath is None:
        dstpath = srcpath

    for root,dirs,files in os.walk( "." ):
        if '.svn' in dirs:
            dirs.remove('.svn')

        for f in files:
            total.append( (root,f) )

    os.chdir( cwd )

    # format: (targetdir,[file])
    # so for us, (dstpath/filedir,[srcpath/filedir/filename])
    return [("%s/%s" % (dstpath,root),["%s/%s/%s" % (srcpath,root,f)]) for root,f in total]

def filterincludes( l, f ):
    """ Return includes which pass filter f. """

    return [(x,y) for (x,y) in l if f(y[0])]


# modules to include into bundle
includeModules=["encodings.hex_codec","encodings.utf_8","encodings.latin_1","xml.sax", "email.iterators"]

# ----- build the app bundle
mainfile = os.path.join(LIBRARYNAME,'Plugin','SwarmEngine.py')

setup(
    setup_requires=['py2app'],
    name=PRODUCTNAME,
    app=[mainfile],
    options={ 'py2app': {
        'argv_emulation': True,
        'includes': includeModules,
        'excludes': ["Tkinter","Tkconstants","tcl"],
        'iconfile': LIBRARYNAME+'/Plugin/Build/Mac/'+PRODUCTNAME+'.icns',
        'plist': Plist.fromFile(LIBRARYNAME+'/Plugin/Build/Mac/Info.plist'),
        'resources':
             [LIBRARYNAME+"/Images/"+PRODUCTNAME+"Icon.ico",
             LIBRARYNAME+"/Plugin/Build/Mac/"+PRODUCTNAME+"Doc.icns",
             (LIBRARYNAME+"/Plugin", [LIBRARYNAME+"/Plugin/opensearch.xml"]),
             (LIBRARYNAME+"/Plugin", [LIBRARYNAME+"/Plugin/favicon.ico"]),
             (LIBRARYNAME+"/Core", [LIBRARYNAME+"/Core/superpeer.txt"]),
           ]
        # add images
        + includedir( LIBRARYNAME+"/Images" )

        # add Web UI files
        + includedir( LIBRARYNAME+"/WebUI" )

        # add RichMetadata xml samples
        + includedir( LIBRARYNAME+"/../JSI/RichMetadata/conf" )

        # schema_sdb_vX.sql
        + filterincludes( includedir( LIBRARYNAME+"/" ), lambda x: x.endswith(".sql") )

    } }
)



