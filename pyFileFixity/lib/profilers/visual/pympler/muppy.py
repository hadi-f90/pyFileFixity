import gc

from pympler import summary
from pympler.util import compat

from inspect import isframe, stack

# default to asizeof if sys.getsizeof is not available (prior to Python 2.6)
try:
    from sys import getsizeof as _getsizeof
except ImportError:
    from pympler.asizeof import flatsize
    _getsizeof = flatsize

__TPFLAGS_HAVE_GC = 1<<14

def get_objects(remove_dups=True, include_frames=False):
    """Return a list of all known objects excluding frame objects.

    If (outer) frame objects shall be included, pass `include_frames=True`.  In
    order to prevent building reference cycles, the current frame object (of
    the caller of get_objects) is ignored. This will not prevent creating
    reference cycles if the object list is passed up the call-stack. Therefore,
    frame objects are not included by default.

    Keyword arguments:
    remove_dups -- if True, all duplicate objects will be removed.
    include_frames -- if True, includes frame objects.
    """
    gc.collect()

    # Do not initialize local variables before calling gc.get_objects or those
    # will be included in the list. Furthermore, ignore frame objects to
    # prevent reference cycles.
    tmp = gc.get_objects()
    tmp = [o for o in tmp if not isframe(o)]

    res = []
    for o in tmp:
        # gc.get_objects returns only container objects, but we also want
        # the objects referenced by them
        refs = get_referents(o)
        for ref in refs:
            if not _is_containerobject(ref):
                # we already got the container objects, now we only add
                # non-container objects
                res.append(ref)
    res.extend(tmp)
    if remove_dups:
        res = _remove_duplicates(res)

    if include_frames:
        for sf in stack()[2:]:
            res.append(sf[0])
    return res

def get_size(objects):
    """Compute the total size of all elements in objects."""
    res = 0
    for o in objects:
        try:
            res += _getsizeof(o)
        except AttributeError:
            print("IGNORING: type=%s; o=%s" % (str(type(o)), str(o)))
    return res

def get_diff(left, right):
    """Get the difference of both lists.

    The result will be a dict with this form {'+': [], '-': []}.
    Items listed in '+' exist only in the right list,
    items listed in '-' exist only in the left list.

    """
    res = {'+': [], '-': []}

    def partition(objects):
        """Partition the passed object list."""
        res = {}
        for o in objects:
            t = type(o)
            if type(o) not in res:
                res[t] = []
            res[t].append(o)
        return res

    def get_not_included(foo, bar):
        """Compare objects from foo with objects defined in the values of
        bar (set of partitions).
        Returns a list of all objects included in list, but not dict values.
        """
        res = []
        for o in foo:
            if not compat.object_in_list(type(o), bar):
                res.append(o)
            elif not compat.object_in_list(o, bar[type(o)]):
                res.append(o)
        return res

    # Create partitions of both lists. This will reduce the time required for
    # the comparison
    left_objects = partition(left)
    right_objects = partition(right)
    # and then do the diff
    res['+'] = get_not_included(right, left_objects)
    res['-'] = get_not_included(left, right_objects)
    return res

def sort(objects):
    """Sort objects by size in bytes."""
    objects = sorted(objects, key=_getsizeof)
    return objects

def filter(objects, Type=None, min=-1, max=-1):    #PYCHOK muppy filter
    """Filter objects.

    The filter can be by type, minimum size, and/or maximum size.

    Keyword arguments:
    Type -- object type to filter by
    min -- minimum object size
    max -- maximum object size

    """
    if min > max:
        raise ValueError("minimum must be smaller than maximum")
    res = [o for o in objects if isinstance(o, Type)] if Type is not None else []

    if min > -1:
        res = [o for o in res if _getsizeof(o) < min]
    if max > -1:
        res = [o for o in res if _getsizeof(o) > max]
    return res

def get_referents(object, level=1):
    """Get all referents of an object up to a certain level.

    The referents will not be returned in a specific order and
    will not contain duplicate objects. Duplicate objects will be removed.

    Keyword arguments:
    level -- level of indirection to which referents considered.

    This function is recursive.

    """
    res = gc.get_referents(object)
    level -= 1
    if level > 0:
        for o in res:
            res.extend(get_referents(o, level))
    res = _remove_duplicates(res)
    return res

def _get_usage(function, *args):
    """Test if more memory is used after the function has been called.

    The function will be invoked twice and only the second measurement will be
    considered. Thus, memory used in initialisation (e.g. loading modules)
    will not be included in the result. The goal is to identify memory leaks
    caused by functions which use more and more memory.

    Any arguments next to the function will be passed on to the function
    on invocation.

    Note that this function is currently experimental, because it is not
    tested thoroughly and performs poorly.

    """
    # The usage of a function is calculated by creating one summary of all
    # objects before the function is invoked and afterwards. These summaries
    # are compared and the diff is returned.
    # This function works in a 2-steps process. Before the actual function is
    # invoked an empty dummy function is measurement to identify the overhead
    # involved in the measuring process. This overhead then is subtracted from
    # the measurement performed on the passed function. The result reflects the
    # actual usage of a function call.
    # Also, a measurement is performed twice, allowing the adjustment to
    # initializing things, e.g. modules

    res = None

    def _get_summaries(function, *args):
        """Get a 2-tuple containing one summary from before, and one summary
        from after the function has been invoked.

        """
        s_before = summary.summarize(get_objects())
        function(*args)
        s_after = summary.summarize(get_objects())
        return (s_before, s_after)

    def _get_usage(function, *args):
        """Get the usage of a function call.
        This function is to be used only internally. The 'real' get_usage
        function is a wrapper around _get_usage, but the workload is done
        here.

        """
        res = []
        # init before calling
        (s_before, s_after) = _get_summaries(function, *args)
        # ignore all objects used for the measurement
        ignore = []
        if s_before != s_after:
            ignore.append(s_before)
        for row in s_before:
            # ignore refs from summary and frame (loop)
            if len(gc.get_referrers(row)) == 2:
                ignore.append(row)
            for item in row:
                # ignore refs from summary and frame (loop)
                if len(gc.get_referrers(item)) == 2:
                    ignore.append(item)
        for o in ignore:
            s_after = summary._subtract(s_after, o)
        res = summary.get_diff(s_before, s_after)
        return summary._sweep(res)

    # calibrate; twice for initialization
    def noop(): pass
    offset = _get_usage(noop)
    offset = _get_usage(noop)
    # perform operation twice to handle objects possibly used in
    # initialisation
    tmp = _get_usage(function, *args)
    tmp = _get_usage(function, *args)
    tmp = summary.get_diff(offset, tmp)
    tmp = summary._sweep(tmp)
    if len(tmp) != 0:
        res = tmp
    return res

def _is_containerobject(o):
    """Is the passed object a container object."""
    return type(o).__flags__ & __TPFLAGS_HAVE_GC != 0

def _remove_duplicates(objects):
    """Remove duplicate objects.

    Inspired by http://www.peterbe.com/plog/uniqifiers-benchmark

    """
    seen = {}
    result = []
    for item in objects:
        marker = id(item)
        if marker in seen:
            continue
        seen[marker] = 1
        result.append(item)
    return result

def print_summary():
    """Print a summary of all known objects."""
    summary.print_(summary.summarize(get_objects()))
