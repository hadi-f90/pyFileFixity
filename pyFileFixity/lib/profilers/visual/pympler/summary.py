"""A collection of functions to summarize object information.

This module provides several function which will help you to analyze object
information which was gathered. Often it is sufficient to work with aggregated
data instead of handling the entire set of existing objects. For example can a
memory leak identified simple based on the number and size of existing objects.

A summary contains information about objects in a table-like manner. Technically,
it is a list of lists. Each of these lists represents a row, whereas the first
column reflects the object type, the second column the number of objects, and
the third column the size of all these objects. This allows a simple table-like
output like the	following:

=============  ============  =============
       types     # objects     total size
=============  ============  =============
<type 'dict'>             2            560
 <type 'str'>             3            126
 <type 'int'>             4             96
<type 'long'>             2             66
<type 'list'>             1             40
=============  ============  =============

Another advantage of summaries is that they influence the system you analyze
only to a minimum. Working with references to existing objects will keep these
objects alive. Most of the times this is no desired behavior (as it will have
an impact on the observations). Using summaries reduces this effect greatly.

output representation
---------------------

The output representation of types is defined in summary.representations.
Every type defined in this dictionary will be represented as specified. Each
definition has a list of different representations. The later a representation
appears in this list, the higher its verbosity level. From types which are not
defined in summary.representations the default str() representation will be
used.

Per default, summaries will use the verbosity level 1 for any encountered type.
The reason is that several computations are done with summaries and rows have to
remain comparable. Therefore information which reflect an objects state,
e.g. the current line number of a frame, should not be included. You may add
more detailed information at higher verbosity levels than 1.
"""

import re
import sys
import types

from pympler.util import stringutils
# default to asizeof if sys.getsizeof is not available (prior to Python 2.6)
try:
    from sys import getsizeof as _getsizeof
except ImportError:
    from pympler.asizeof import flatsize
    _getsizeof = flatsize

representations = {}
def _init_representations():
    global representations
    if sys.hexversion < 0x2040000:
        classobj = [
            lambda c: "classobj(%s)" % repr(c),
        ]
        representations[types.ClassType] = classobj
        instance = [
            lambda f: "instance(%s)" % repr(f.__class__),
        ]
        representations[types.InstanceType] = instance
        instancemethod = [
            lambda i: "instancemethod (%s)" %\
                                      (repr(i.im_func)),
            lambda i: "instancemethod (%s, %s)" %\
                                      (repr(i.im_class), repr(i.im_func)),
        ]
        representations[types.MethodType] = instancemethod
    frame = [
        lambda f: "frame (codename: %s)" %\
                   (f.f_code.co_name),
        lambda f: "frame (codename: %s, codeline: %s)" %\
                   (f.f_code.co_name, f.f_code.co_firstlineno),
        lambda f: "frame (codename: %s, filename: %s, codeline: %s)" %\
                   (f.f_code.co_name, f.f_code.co_filename,\
                    f.f_code.co_firstlineno)
    ]
    representations[types.FrameType] = frame
    _dict = [
        lambda d: str(type(d)),
        lambda d: "dict, len=%s" % len(d),
    ]
    representations[dict] = _dict
    function = [
        lambda f: "function (%s)" % f.__name__,
        lambda f: "function (%s.%s)" % (f.__module, f.__name__),
    ]
    representations[types.FunctionType] = function
    _list = [
        lambda l: str(type(l)),
        lambda l: "list, len=%s" % len(l)
    ]
    representations[list] = _list
    module = [ lambda m: "module(%s)" % m.__name__ ]
    representations[types.ModuleType] = module
    _set = [
        lambda s: str(type(s)),
        lambda s: "set, len=%s" % len(s)
    ]
    representations[set] = _set

_init_representations()

def summarize(objects):
    """Summarize an objects list.

    Return a list of lists, whereas each row consists of::
      [str(type), number of objects of this type, total size of these objects].

    No guarantee regarding the order is given.

    """
    count = {}
    total_size = {}
    for o in objects:
        otype = _repr(o)
        if otype in count:
            count[otype] += 1
            total_size[otype] += _getsizeof(o)
        else:
            count[otype] = 1
            total_size[otype] = _getsizeof(o)
    return [[otype, value, total_size[otype]] for otype, value in count.items()]

def get_diff(left, right):
    """Get the difference of two summaries.

    Subtracts the values of the right summary from the values of the left
    summary.
    If similar rows appear on both sides, the are included in the summary with
    0 for number of elements and total size.
    If the number of elements of a row of the diff is 0, but the total size is
    not, it means that objects likely have changed, but not there number, thus
    resulting in a changed size.

    """
    res = []
    for row_r in right:
        found = False
        for row_l in left:
            if row_r[0] == row_l[0]:
                res.append([row_r[0], row_r[1] - row_l[1], row_r[2] - row_l[2]])
                found = True
        if not found:
            res.append(row_r)

    for row_l in left:
        found = any(row_l[0] == row_r[0] for row_r in right)
        if not found:
            res.append([row_l[0], -row_l[1], -row_l[2]])
    return res

def print_(rows, limit=15, sort='size', order='descending'):
    """Print the rows as a summary.

    Keyword arguments:
    limit -- the maximum number of elements to be listed
    sort  -- sort elements by 'size', 'type', or '#'
    order -- sort 'ascending' or 'descending'
    """
    localrows = [list(row) for row in rows]
    # input validation
    sortby = ['type', '#', 'size']
    if sort not in sortby:
        raise ValueError("invalid sort, should be one of" + str(sortby))
    orders = ['ascending', 'descending']
    if order not in orders:
        raise ValueError("invalid order, should be one of" + str(orders))
    # sort rows
    if sortby.index(sort) == 0:
        if order == "ascending":
            localrows.sort(key=lambda x: _repr(x[0]))
        elif order == "descending":
            localrows.sort(key=lambda x: _repr(x[0]), reverse=True)
    elif order == "ascending":
        localrows.sort(key=lambda x: x[sortby.index(sort)])
    elif order == "descending":
        localrows.sort(key=lambda x: x[sortby.index(sort)], reverse=True)
    # limit rows
    localrows = localrows[:limit]
    for row in localrows:
        row[2] = stringutils.pp(row[2])
    # print rows
    localrows.insert(0,["types", "# objects", "total size"])
    _print_table(localrows)

def _print_table(rows, header=True):
    """Print a list of lists as a pretty table.

    Keyword arguments:
    header -- if True the first row is treated as a table header

    inspired by http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/267662
    """
    border = "="
    # vertical delimiter
    vdelim = " | "
    # padding nr. of spaces are left around the longest element in the
    # column
    padding = 1
    # may be left,center,right
    justify = 'right'
    justify = {'left'   : str.ljust,
               'center' : str.center,
               'right'  : str.rjust}[justify.lower()]
    # calculate column widths (longest item in each col
    # plus "padding" nr of spaces on both sides)
    cols = zip(*rows)
    colWidths = [max(len(str(item))+2*padding for item in col) for col in cols]
    borderline = vdelim.join([w*border for w in colWidths])
    for row in rows:
        print(vdelim.join([justify(str(item),width) for (item,width) in zip(row,colWidths)]))
        if header:
            print(borderline)
            header=False


# regular expressions used by _repr to replace default type representations
type_prefix = re.compile(r"^<type '")
address = re.compile(r' at 0x[0-9a-f]+')
type_suffix = re.compile(r"'>$")

def _repr(o, verbosity=1):
    """Get meaning object representation.

    This function should be used when the simple str(o) output would result in
    too general data. E.g. "<type 'instance'" is less meaningful than
    "instance: Foo".

    Keyword arguments:
    verbosity -- if True the first row is treated as a table header

    """
    res = ""

    t = type(o)
    if (verbosity == 0) or (t not in representations):
        res = str(t)
    else:
        verbosity -= 1
        if len(representations[t]) < verbosity:
            verbosity = len(representations[t]) - 1
        res = representations[t][verbosity](o)

    res = address.sub('', res)
    res = type_prefix.sub('', res)
    res = type_suffix.sub('', res)

    return res

def _traverse(summary, function, *args):
    """Traverse all objects of a summary and call function with each as a
    parameter.

    Using this function, the following objects will be traversed:
    - the summary
    - each row
    - each item of a row
    """
    function(summary, *args)
    for row in summary:
        function(row, *args)
        for item in row:
            function(item, *args)

def _subtract(summary, o):
    """Remove object o from the summary by subtracting it's size."""
    found = False
    row = [_repr(o), 1, _getsizeof(o)]
    for r in summary:
        if r[0] == row[0]:
            (r[1], r[2]) = (r[1] - row[1], r[2] - row[2])
            found = True
    if not found:
        summary.append([row[0], -row[1], -row[2]])
    return summary

def _sweep(summary):
    """Remove all rows in which the total size and the total number of
    objects is zero.

    """
    return [row for row in summary if ((row[2] != 0) or (row[1] != 0))]


