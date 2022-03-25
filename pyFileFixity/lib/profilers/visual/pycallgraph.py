#!/usr/bin/env python
"""
pycallgraph

U{http://pycallgraph.slowchop.com/}

Copyright Gerald Kaszuba 2007

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
"""

__version__ = '0.5.2'
__author__ = 'Gerald Kaszuba'

import inspect
import sys
import math
import os
import re
import tempfile
import time
from distutils import sysconfig

# Initialise module variables.
# TODO Move these into settings
trace_filter = None
time_filter = None


def colourize_node(calls, total_time):
    value = float(total_time * 2 + calls) / 3
    return '%f %f %f' % (value / 2 + .5, value, 0.9)


def colourize_edge(calls, total_time):
    value = float(total_time * 2 + calls) / 3
    return '%f %f %f' % (value / 2 + .5, value, 0.7)


def reset_settings():
    global settings
    global graph_attributes
    global __version__

    settings = {
        'node_attributes': {
           'label': r'%(func)s\ncalls: %(hits)i\ntotal time: %(total_time)f',
           'color': '%(col)s',
        },
        'node_colour': colourize_node,
        'edge_colour': colourize_edge,
        'dont_exclude_anything': False,
        'include_stdlib': True,
    }

    # TODO: Move this into settings
    graph_attributes = {
        'graph': {
            'fontname': 'Verdana',
            'fontsize': 7,
            'fontcolor': '0 0 0.5',
            'label': r'Generated by Python Call Graph v%s\n' \
                r'http://pycallgraph.slowchop.com' % __version__,
        },
        'node': {
            'fontname': 'Verdana',
            'fontsize': 7,
            'color': '.5 0 .9',
            'style': 'filled',
            'shape': 'rect',
        },
        'edge': {
            'fontname': 'Verdana',
            'fontsize': 7,
            'color': '0 0 0',
        }
    }


def reset_trace():
    """Resets all collected statistics. This is run automatically by
    start_trace(reset=True) and when the module is loaded.
    """
    global call_dict
    global call_stack
    global func_count
    global func_count_max
    global func_time
    global func_time_max
    global call_stack_timer

    call_dict = {}

    # current call stack
    call_stack = ['__main__']

    # counters for each function
    func_count = {}
    func_count_max = 0

    # accumative time per function
    func_time = {}
    func_time_max = 0

    # keeps track of the start time of each call on the stack
    call_stack_timer = []


class PyCallGraphException(Exception):
    """Exception used for pycallgraph"""
    pass


class GlobbingFilter(object):
    """Filter module names using a set of globs.

    Objects are matched against the exclude list first, then the include list.
    Anything that passes through without matching either, is excluded.
    """

    def __init__(self, include=None, exclude=None, max_depth=None,
                 min_depth=None):
        if include is None and exclude is None:
            include = ['*']
            exclude = []
        elif include is None:
            include = ['*']
        elif exclude is None:
            exclude = []
        self.include = include
        self.exclude = exclude
        self.max_depth = max_depth or 9999 if max_depth is None else max_depth
        self.min_depth = 0 if min_depth is None else min_depth or 0

    def __call__(self, stack, module_name=None, class_name=None,
                 func_name=None, full_name=None):
        from fnmatch import fnmatch
        if len(stack) > self.max_depth:
            return False
        if len(stack) < self.min_depth:
            return False
        for pattern in self.exclude:
            if fnmatch(full_name, pattern):
                return False
        return any(fnmatch(full_name, pattern) for pattern in self.include)


def is_module_stdlib(file_name):
    """Returns True if the file_name is in the lib directory."""
    # TODO: Move these calls away from this function so it doesn't have to run
    # every time.
    lib_path = sysconfig.get_python_lib()
    path = os.path.split(lib_path)
    if path[1] == 'site-packages':
        lib_path = path[0]
    return file_name.lower().startswith(lib_path.lower())


def start_trace(reset=True, filter_func=None, time_filter_func=None):
    """Begins a trace. Setting reset to True will reset all previously recorded
    trace data. filter_func needs to point to a callable function that accepts
    the parameters (call_stack, module_name, class_name, func_name, full_name).
    Every call will be passed into this function and it is up to the function
    to decide if it should be included or not. Returning False means the call
    will be filtered out and not included in the call graph.
    """
    global trace_filter
    global time_filter
    if reset:
        reset_trace()

    trace_filter = filter_func or GlobbingFilter(exclude=['pycallgraph.*'])
    time_filter = time_filter_func or GlobbingFilter()
    sys.settrace(tracer)


def stop_trace():
    """Stops the currently running trace, if any."""
    sys.settrace(None)


def tracer(frame, event, arg):
    """This is an internal function that is called every time a call is made
    during a trace. It keeps track of relationships between calls.
    """
    global func_count_max
    global func_count
    global trace_filter
    global time_filter
    global call_stack
    global func_time
    global func_time_max

    if event == 'call':
        keep = True
        code = frame.f_code

        # Stores all the parts of a human readable name of the current call.
        full_name_list = []

        # Work out the module name
        module = inspect.getmodule(code)
        if module:
            module_name = module.__name__
            module_path = module.__file__
            if not settings['include_stdlib'] \
                and is_module_stdlib(module_path):
                keep = False
            if module_name == '__main__':
                module_name = ''
        else:
            module_name = ''
        if module_name:
            full_name_list.append(module_name)

        # Work out the class name.
        try:
            class_name = frame.f_locals['self'].__class__.__name__
            full_name_list.append(class_name)
        except (KeyError, AttributeError):
            class_name = ''

        # Work out the current function or method
        func_name = code.co_name
        if func_name == '?':
            func_name = '__main__'
        full_name_list.append(func_name)

        # Create a readable representation of the current call
        full_name = '.'.join(full_name_list)

        # Load the trace filter, if any. 'keep' determines if we should ignore
        # this call
        if keep and trace_filter:
            keep = trace_filter(call_stack, module_name, class_name,
                func_name, full_name)

        # Store the call information
        if keep:

            fr = call_stack[-1] if call_stack else None
            if fr not in call_dict:
                call_dict[fr] = {}
            if full_name not in call_dict[fr]:
                call_dict[fr][full_name] = 0
            call_dict[fr][full_name] += 1

            if full_name not in func_count:
                func_count[full_name] = 0
            func_count[full_name] += 1
            if func_count[full_name] > func_count_max:
                func_count_max = func_count[full_name]

            call_stack.append(full_name)
            call_stack_timer.append(time.time())

        else:
            call_stack.append('')
            call_stack_timer.append(None)

    if event == 'return' and call_stack:
        full_name = call_stack.pop(-1)
        t = call_stack_timer.pop(-1) if call_stack_timer else None
        if t and time_filter(stack=call_stack, full_name=full_name):
            if full_name not in func_time:
                func_time[full_name] = 0
            call_time = (time.time() - t)
            func_time[full_name] += call_time
            if func_time[full_name] > func_time_max:
                func_time_max = func_time[full_name]

    return tracer


def _frac_calculation(func, count):
    global func_count_max
    global func_time
    global func_time_max
    calls_frac = float(count) / func_count_max
    try:
        total_time = func_time[func]
    except KeyError:
        total_time = 0
    try:
        total_time_frac = float(total_time) / func_time_max
    except ZeroDivisionError:
        total_time_frac = 0
    return calls_frac, total_time_frac, total_time


def get_dot(stop=True):
    """Returns a string containing a DOT file. Setting stop to True will cause
    the trace to stop.
    """
    defaults = []
    nodes    = []
    edges    = []


    # define default attributes
    for comp, comp_attr in graph_attributes.items():
        attr = ', '.join( '%s = "%s"' % (attr, val)
                         for attr, val in comp_attr.items() )
        defaults.append( '\t%(comp)s [ %(attr)s ];\n' % locals() )

    # define nodes
    for func, hits in func_count.items():
        calls_frac, total_time_frac, total_time = _frac_calculation(func, hits)
        col = settings['node_colour'](calls_frac, total_time_frac)
        attribs = ['%s="%s"' % a for a in settings['node_attributes'].items()]
        node_str = '"%s" [%s];' % (func, ', '.join(attribs))
        nodes.append( node_str % locals() )

    # define edges
    for fr_key, fr_val in call_dict.items():
        if not fr_key: continue
        for to_key, to_val in fr_val.items():
            calls_frac, total_time_frac, totla_time = \
                _frac_calculation(to_key, to_val)
            col = settings['edge_colour'](calls_frac, total_time_frac)
            edge = '[ color = "%s", label="%s" ]' % (col, to_val)
            edges.append('"%s"->"%s" %s;' % (fr_key, to_key, edge))

    defaults = '\n\t'.join( defaults )
    nodes    = '\n\t'.join( nodes )
    edges    = '\n\t'.join( edges )

    dot_fmt = ("digraph G {\n"
               "	%(defaults)s\n\n"
               "	%(nodes)s\n\n"
               "	%(edges)s\n}\n"
              )
    return dot_fmt % locals()


def get_gdf(stop=True):
    """Returns a string containing a GDF file. Setting stop to True will cause
    the trace to stop.
    """
    ret = ['nodedef>name VARCHAR, label VARCHAR, hits INTEGER, ' + \
            'calls_frac DOUBLE, total_time_frac DOUBLE, ' + \
            'total_time DOUBLE, color VARCHAR, width DOUBLE']
    for func, hits in func_count.items():
        calls_frac, total_time_frac, total_time = _frac_calculation(func, hits)
        col = settings['node_colour'](calls_frac, total_time_frac)
        color = ','.join([str(round(float(c) * 255)) for c in col.split()])
        ret.append('%s,%s,%s,%s,%s,%s,\'%s\',%s' % (func, func, hits, \
                    calls_frac, total_time_frac, total_time, color, \
                    math.log(hits * 10)))
    ret.append('edgedef>node1 VARCHAR, node2 VARCHAR, color VARCHAR')
    for fr_key, fr_val in call_dict.items():
        if fr_key == '':
            continue
        for to_key, to_val in fr_val.items():
            calls_frac, total_time_frac, total_time = \
                _frac_calculation(to_key, to_val)
            col = settings['edge_colour'](calls_frac, total_time_frac)
            color = ','.join([str(round(float(c) * 255)) for c in col.split()])
            ret.append('%s,%s,\'%s\'' % (fr_key, to_key, color))
    ret = '\n'.join(ret)
    return ret


def save_dot(filename):
    """Generates a DOT file and writes it into filename."""
    open(filename, 'w').write(get_dot())


def save_gdf(filename):
    """Generates a GDF file and writes it into filename."""
    open(filename, 'w').write(get_gdf())


def make_graph(filename, format=None, tool=None, stop=None):
    """This has been changed to make_dot_graph."""
    raise PyCallGraphException( \
        'make_graph is depricated. Please use make_dot_graph')


def make_dot_graph(filename, format='png', tool='dot', stop=True):
    """Creates a graph using a Graphviz tool that supports the dot language. It
    will output into a file specified by filename with the format specified.
    Setting stop to True will stop the current trace.
    """
    if stop:
        stop_trace()

    dot_data = get_dot()

    # normalize filename
    regex_user_expand = re.compile('\A~')
    if regex_user_expand.match(filename):
        filename = os.path.expanduser(filename)
    else:
        filename = os.path.expandvars(filename)  # expand, just in case

    if format == 'dot':
        with open(filename, 'w') as f:
            f.write(dot_data)
    else:
        # create a temporary file to be used for the dot data
        fd, tempname = tempfile.mkstemp()
        with os.fdopen(fd, 'w') as f:
            f.write(dot_data)

        cmd = '%(tool)s -T%(format)s -o%(filename)s %(tempname)s' % locals()
        try:
            ret = os.system(cmd)
            if ret:
                raise PyCallGraphException( \
                    'The command "%(cmd)s" failed with error ' \
                    'code %(ret)i.' % locals())
        finally:
            os.unlink(tempname)


def make_gdf_graph(filename, stop=True):
    """Create a graph in simple GDF format, suitable for feeding into Gephi,
    or some other graph manipulation and display tool. Setting stop to True
    will stop the current trace.
    """
    if stop:
        stop_trace()

    try:
        f = open(filename, 'w')
        f.write(get_gdf())
    finally:
        if f: f.close()


def simple_memoize(callable_object):
    """Simple memoization for functions without keyword arguments.

    This is useful for mapping code objects to module in this context.
    inspect.getmodule() requires a number of system calls, which may slow down
    the tracing considerably. Caching the mapping from code objects (there is
    *one* code object for each function, regardless of how many simultaneous
    activations records there are).

    In this context we can ignore keyword arguments, but a generic memoizer
    ought to take care of that as well.
    """

    cache = dict()

    def wrapper(*rest):
        if rest not in cache:
            cache[rest] = callable_object(*rest)
        return cache[rest]

    return wrapper


settings = {}
graph_attributes = {}
reset_settings()
reset_trace()
inspect.getmodule = simple_memoize(inspect.getmodule)

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
