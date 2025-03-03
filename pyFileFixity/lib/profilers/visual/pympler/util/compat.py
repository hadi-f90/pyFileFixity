"""
Compatibility layer to allow Pympler being used from Python 2.x and Python 3.x.
"""

import sys

# Version dependent imports

try:
    from StringIO import StringIO
    BytesIO = StringIO
except ImportError:
    from io import StringIO, BytesIO

try:
    import cPickle as pickle
except ImportError:
    import pickle #PYCHOK Python 3.0 module

try:
    from new         import instancemethod
except ImportError: # Python 3.0
    def instancemethod(*args):
        return args[0]

try:
    from HTMLParser import HTMLParser
except ImportError: # Python 3.0
    from html.parser import HTMLParser

try:
    from httplib import HTTPConnection
except ImportError: # Python 3.0
    from http.client import HTTPConnection

try:
    from urllib2 import Request, urlopen, URLError
except ImportError: # Python 3.0
    from urllib.request import Request, urlopen
    from urllib.error import URLError

try:
    import pympler.util.bottle2 as bottle
except (SyntaxError, ImportError):
    try:
        import pympler.util.bottle3 as bottle
    except (SyntaxError, ImportError): # Python 2.4
        bottle = None

# Helper functions

# Python 2.x expects strings when calling communicate and passing data via a
# pipe while Python 3.x expects binary (encoded) data. The following works with
# both:
#
#   p = Popen(..., stdin=PIPE)
#   p.communicate(encode4pipe("spam"))
#
encode4pipe = lambda s: s
if sys.hexversion >= 0x3000000:
    encode4pipe = lambda s: s.encode()

def object_in_list(obj, l):
    """Returns True if object o is in list.

    Required compatibility function to handle WeakSet objects.
    """
    return any(o is obj for o in l)
