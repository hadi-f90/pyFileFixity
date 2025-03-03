"""
The `ClassTracker` is a facility delivering insight into the memory distribution
of a Python program. It can introspect memory consumption of certain classes and
objects. Facilities are provided to track and size individual objects or all
instances of certain classes. Tracked objects are sized recursively to provide
an overview of memory distribution between the different tracked objects.
"""

from inspect import stack, isclass
from threading import Thread, Lock
from time import sleep, time
from weakref import ref as weakref_ref

from pympler.classtracker_stats import ConsoleStats
from pympler.util.compat import instancemethod
from pympler.util.stringutils import safe_repr

import pympler.asizeof as asizeof
import pympler.process


__all__ = ["ClassTracker"]

# Fixpoint for program start relative time stamp.
_local_start = time()


class _ClassObserver(object):
    """
    Stores options for tracked classes.
    The observer also keeps the original constructor of the observed class.
    """
    __slots__ = ('init', 'name', 'detail', 'keep', 'trace')

    def __init__(self, init, name, detail, keep, trace):
        self.init = init
        self.name = name
        self.detail = detail
        self.keep = keep
        self.trace = trace

    def modify(self, name, detail, keep, trace):
        self.name = name
        self.detail = detail
        self.keep = keep
        self.trace = trace


def _get_time():
    """
    Get a timestamp relative to the program start time.
    """
    return time() - _local_start

class TrackedObject(object):
    """
    Stores size and lifetime information of a tracked object. A weak reference is
    attached to monitor the object without preventing its deletion.
    """
    __slots__ = ("ref", "id", "repr", "name", "birth", "death", "trace",
        "snapshots", "_resolution_level", "__dict__")

    def __init__(self, instance, resolution_level=0, trace=False):
        """
        Create a weak reference for 'instance' to observe an object but which
        won't prevent its deletion (which is monitored by the finalize
        callback). The size of the object is recorded in 'snapshots' as
        (timestamp, size) tuples.
        """
        self.ref = weakref_ref(instance, self.finalize)
        self.id = id(instance)
        self.repr = ''
        self.name = str(instance.__class__)
        self.birth = _get_time()
        self.death = None
        self._resolution_level = resolution_level
        self.trace = None

        if trace:
            self._save_trace()

        initial_size = asizeof.basicsize(instance) or 0
        size = asizeof.Asized(initial_size, initial_size)
        self.snapshots = [(self.birth, size)]


    def __getstate__(self):
        """
        Make the object serializable for dump_stats. Read the available slots
        and store the values in a dictionary. Derived values (stored in the
        dict) are not pickled as those can be reconstructed based on the other
        data. References cannot be serialized, ignore 'ref' as well.
        """
        return {
            name: getattr(self, name)
            for name in getattr(TrackedObject, '__slots__', ())
            if hasattr(self, name) and name not in ['ref', '__dict__']
        }


    def __setstate__(self, state):
        """
        Restore the state from pickled data. Needed because a slotted class is
        used.
        """
        for key, value in list(state.items()):
            setattr(self, key, value)


    def _save_trace(self):
        """
        Save current stack trace as formatted string.
        """
        stack_trace = stack()
        try:
            self.trace = []
            for frm in stack_trace[5:]: # eliminate our own overhead
                self.trace.insert(0, frm[1:])
        finally:
            del stack_trace

    def track_size(self, ts, sizer):
        """
        Store timestamp and current size for later evaluation.
        The 'sizer' is a stateful sizing facility that excludes other tracked
        objects.
        """
        obj = self.ref()
        self.snapshots.append(
            (ts, sizer.asized(obj, detail=self._resolution_level))
        )
        if obj is not None:
            self.repr = safe_repr(obj, clip=128)

    def get_max_size(self):
        """
        Get the maximum of all sampled sizes.
        """
        return max(s.size for (_, s) in self.snapshots)

    def get_size_at_time(self, timestamp):
        """
        Get the size of the object at a specific time (snapshot).
        If the object was not alive/sized at that instant, return 0.
        """
        size = 0
        for (t, s) in self.snapshots:
            if t == timestamp:
                size = s.size
        return size

    def set_resolution_level(self, resolution_level):
        """
        Set resolution level to a new value. The next size estimation will
        respect the new value. This is useful to set different levels for
        different instances of tracked classes.
        """
        self._resolution_level = resolution_level

    def finalize(self, ref): #PYCHOK required to match callback
        """
        Mark the reference as dead and remember the timestamp.  It would be
        great if we could measure the pre-destruction size.  Unfortunately, the
        object is gone by the time the weakref callback is called.  However,
        weakref callbacks are useful to be informed when tracked objects died
        without the need of destructors.

        If the object is destroyed at the end of the program execution, it's not
        possible to import modules anymore. Hence, the finalize callback just
        does nothing (self.death stays None).
        """
        try:
            self.death = _get_time()
        except Exception: # pragma: no cover
            pass


class PeriodicThread(Thread):
    """
    Thread object to take snapshots periodically.
    """

    def __init__(self, tracker, interval, *args, **kwargs):
        """
        Create thread with given interval and associated with the given
        tracker.
        """
        self.interval = interval
        self.tracker = tracker
        self.stop = False
        super(PeriodicThread, self).__init__(*args, **kwargs)

    def run(self):
        """
        Loop until a stop signal is set.
        """
        self.stop = False
        while not self.stop:
            self.tracker.create_snapshot()
            sleep(self.interval)


class Snapshot(object):
    """Sample sizes of objects and the process at an instant."""

    def __init__(self):
        """Initialize process-wide size information."""
        self.tracked_total = 0
        self.asizeof_total = 0
        self.overhead = 0
        self.timestamp = None
        self.system_total = None
        self.desc = None


    @property
    def total(self):
        """
        Return the total (virtual) size of the process in bytes. If process
        information is not available, get the best number available, even if it
        is a poor approximation of reality.
        """
        if self.system_total.available:
            return self.system_total.vsz
        elif self.asizeof_total: # pragma: no cover
            return self.asizeof_total
        else: # pragma: no cover
            return self.tracked_total


    @property
    def label(self):
        """Return timestamped label for this snapshot, or a raw timestamp."""
        if not self.desc:
            return "%.3fs" % self.timestamp
        return "%s (%.3fs)" % (self.desc, self.timestamp)


class ClassTracker(object):

    def __init__(self, stream=None):
        """
        Creates a new `ClassTracker` object.

        :param stream: Output stream to use when printing statistics via
            ``stats``.
        """
        # Dictionaries of TrackedObject objects associated with the actual
        # objects that are tracked. 'index' uses the class name as the key and
        # associates a list of tracked objects. It contains all TrackedObject
        # instances, including those of dead objects.
        self.index = {}

        # 'objects' uses the id (address) as the key and associates the tracked
        # object with it. TrackedObject's referring to dead objects are replaced
        # lazily, i.e. when the id is recycled by another tracked object.
        self.objects = {}

        # List of `Snapshot` objects.
        self.snapshots = []

        # Keep objects alive by holding a strong reference.
        self._keepalive = []

        # Dictionary of class observers identified by classname.
        self._observers = {}

        # Thread object responsible for background monitoring
        self._periodic_thread = None

        self._stream = stream


    @property
    def stats(self):
        """
        Return a ``ConsoleStats`` instance initialized with the current state
        of the class tracker.
        """
        return ConsoleStats(tracker=self, stream=self._stream)


    def _tracker(self, _observer_, _self_, *args, **kwds):
        """
        Injected constructor for tracked classes.
        Call the actual constructor of the object and track the object.
        Attach to the object before calling the constructor to track the object with
        the parameters of the most specialized class.
        """
        self.track_object(_self_,
                          name=_observer_.name,
                          resolution_level=_observer_.detail,
                          keep=_observer_.keep,
                          trace=_observer_.trace)
        _observer_.init(_self_, *args, **kwds)


    def _inject_constructor(self, cls, func, name, resolution_level, keep,
                            trace):
        """
        Modifying Methods in Place - after the recipe 15.7 in the Python
        Cookbook by Ken Seehof. The original constructors may be restored
        later.
        """
        try:
            constructor = cls.__init__
        except AttributeError:
            def constructor(self, *_args, **_kwargs):
                pass

        # Possible name clash between keyword arguments of the tracked class'
        # constructor and the curried arguments of the injected constructor.
        # Therefore, the additional argument has a 'magic' name to make it less
        # likely that an argument name clash occurs.
        self._observers[cls] = _ClassObserver(constructor,
                                              name,
                                              resolution_level,
                                              keep,
                                              trace)
        cls.__init__ = instancemethod(
            lambda *args, **kwds: func(self._observers[cls], *args, **kwds),
            None,
            cls
        )


    def _is_tracked(self, cls):
        """
        Determine if the class is tracked.
        """
        return cls in self._observers


    def _track_modify(self, cls, name, detail, keep, trace):
        """
        Modify settings of a tracked class
        """
        self._observers[cls].modify(name, detail, keep, trace)


    def _restore_constructor(self, cls):
        """
        Restore the original constructor, lose track of class.
        """
        cls.__init__ = self._observers[cls].init
        del self._observers[cls]


    def track_change(self, instance, resolution_level=0):
        """
        Change tracking options for the already tracked object 'instance'.
        If instance is not tracked, a KeyError will be raised.
        """
        tobj = self.objects[id(instance)]
        tobj.set_resolution_level(resolution_level)


    def track_object(self, instance, name=None, resolution_level=0, keep=False, trace=False):
        """
        Track object 'instance' and sample size and lifetime information.
        Not all objects can be tracked; trackable objects are class instances and
        other objects that can be weakly referenced. When an object cannot be
        tracked, a `TypeError` is raised.

        :param resolution_level: The recursion depth up to which referents are
            sized individually. Resolution level 0 (default) treats the object
            as an opaque entity, 1 sizes all direct referents individually, 2
            also sizes the referents of the referents and so forth.
        :param keep: Prevent the object's deletion by keeping a (strong)
            reference to the object.
        """

        # Check if object is already tracked. This happens if track_object is
        # called multiple times for the same object or if an object inherits
        # from multiple tracked classes. In the latter case, the most
        # specialized class wins.  To detect id recycling, the weak reference
        # is checked. If it is 'None' a tracked object is dead and another one
        # takes the same 'id'.
        if id(instance) in self.objects and \
            self.objects[id(instance)].ref() is not None:
            return

        tobj = TrackedObject(instance, resolution_level=resolution_level, trace=trace)

        if name is None:
            name = instance.__class__.__name__
        if name not in self.index:
            self.index[name] = []
        self.index[name].append(tobj)
        self.objects[id(instance)] = tobj

        if keep:
            self._keepalive.append(instance)


    def track_class(self, cls, name=None, resolution_level=0, keep=False, trace=False):
        """
        Track all objects of the class `cls`. Objects of that type that already
        exist are *not* tracked. If `track_class` is called for a class already
        tracked, the tracking parameters are modified. Instantiation traces can be
        generated by setting `trace` to True.
        A constructor is injected to begin instance tracking on creation
        of the object. The constructor calls `track_object` internally.

        :param cls: class to be tracked, may be an old-style or a new-style class
        :param name: reference the class by a name, default is the concatenation of
            module and class name
        :param resolution_level: The recursion depth up to which referents are
            sized individually. Resolution level 0 (default) treats the object
            as an opaque entity, 1 sizes all direct referents individually, 2
            also sizes the referents of the referents and so forth.
        :param keep: Prevent the object's deletion by keeping a (strong)
            reference to the object.
        :param trace: Save instantiation stack trace for each instance
        """
        if not isclass(cls):
            raise TypeError("only class objects can be tracked")
        if name is None:
            name = cls.__module__ + '.' + cls.__name__
        if self._is_tracked(cls):
            self._track_modify(cls, name, resolution_level, keep, trace)
        else:
            self._inject_constructor(cls, self._tracker, name, resolution_level, keep, trace)


    def detach_class(self, cls):
        """
        Stop tracking class 'cls'. Any new objects of that type are not
        tracked anymore. Existing objects are still tracked.
        """
        self._restore_constructor(cls)


    def detach_all_classes(self):
        """
        Detach from all tracked classes.
        """
        classes = list(self._observers.keys())
        for cls in classes:
            self.detach_class(cls)


    def detach_all(self):
        """
        Detach from all tracked classes and objects.
        Restore the original constructors and cleanse the tracking lists.
        """
        self.detach_all_classes()
        self.objects.clear()
        self.index.clear()
        self._keepalive[:] = []


    def clear(self):
        """
        Clear all gathered data and detach from all tracked objects/classes.
        """
        self.detach_all()
        self.snapshots[:] = []

#
# Background Monitoring
#

    def start_periodic_snapshots(self, interval=1.0):
        """
        Start a thread which takes snapshots periodically. The `interval` specifies
        the time in seconds the thread waits between taking snapshots. The thread is
        started as a daemon allowing the program to exit. If periodic snapshots are
        already active, the interval is updated.
        """
        if not self._periodic_thread:
            self._periodic_thread = PeriodicThread(self, interval, name='BackgroundMonitor')
            self._periodic_thread.setDaemon(True)
            self._periodic_thread.start()
        else:
            self._periodic_thread.interval = interval

    def stop_periodic_snapshots(self):
        """
        Post a stop signal to the thread that takes the periodic snapshots. The
        function waits for the thread to terminate which can take some time
        depending on the configured interval.
        """
        if self._periodic_thread and self._periodic_thread.isAlive():
            self._periodic_thread.stop = True
            self._periodic_thread.join()
            self._periodic_thread = None

#
# Snapshots
#

    snapshot_lock = Lock()

    def create_snapshot(self, description='', compute_total=False):
        """
        Collect current per instance statistics and saves total amount of
        memory associated with the Python process.

        If `compute_total` is `True`, the total consumption of all objects
        known to *asizeof* is computed. The latter might be very slow if many
        objects are mapped into memory at the time the snapshot is taken.
        Therefore, `compute_total` is set to `False` by default.

        The overhead of the `ClassTracker` structure is also computed.

        Snapshots can be taken asynchronously. The function is protected with a
        lock to prevent race conditions.
        """

        try:
            # TODO: It is not clear what happens when memory is allocated or
            # released while this function is executed but it will likely lead
            # to inconsistencies. Either pause all other threads or don't size
            # individual objects in asynchronous mode.
            self.snapshot_lock.acquire()

            timestamp = _get_time()

            sizer = asizeof.Asizer()
            objs = [tobj.ref() for tobj in list(self.objects.values())]
            sizer.exclude_refs(*objs)

            # The objects need to be sized in a deterministic order. Sort the
            # objects by its creation date which should at least work for non-parallel
            # execution. The "proper" fix would be to handle shared data separately.
            tracked_objects = list(self.objects.values())
            tracked_objects.sort(key=lambda x: x.birth)
            for tobj in tracked_objects:
                tobj.track_size(timestamp, sizer)

            snapshot = Snapshot()

            snapshot.timestamp = timestamp
            snapshot.tracked_total = sizer.total
            if compute_total:
                snapshot.asizeof_total = asizeof.asizeof(all=True, code=True)
            snapshot.system_total = pympler.process.ProcessMemoryInfo()
            snapshot.desc = str(description)

            # Compute overhead of all structures, use sizer to exclude tracked objects(!)
            snapshot.overhead = 0
            if snapshot.tracked_total:
                snapshot.overhead = sizer.asizeof(self)
                if snapshot.asizeof_total:
                    snapshot.asizeof_total -= snapshot.overhead

            self.snapshots.append(snapshot)

        finally:
            self.snapshot_lock.release()
