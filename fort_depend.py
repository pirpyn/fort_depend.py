#!/usr/bin/env python3

from __future__ import print_function

import os
import sys
import warnings
import re
import argparse
import contextlib

# Python 2/3 compatibility
try:
    input = raw_input
except NameError:
    pass

# Terminal colours
# from colorama import Fore

### FROM smartopen.py
# from .smartopen import smart_open
@contextlib.contextmanager
def smart_open(filename, mode='Ur'):
    """Open stdin or stdout using a contextmanager

    From: http://stackoverflow.com/a/29824059/2043465
    """
    if filename == '-':
        if mode is None or mode == '' or 'r' in mode:
            fh = sys.stdin
        else:
            fh = sys.stdout
    else:
        fh = open(filename, mode)
    try:
        yield fh
    finally:
        if filename is not '-':
            fh.close()
### END smartopen.py

### FROM units.py
#from .units import FortranFile, FortranModule

UNIT_REGEX = re.compile("^\s*(?P<unit_type>module(?!\s+procedure)|program)\s*(?P<modname>\w*)",
                        re.IGNORECASE)
END_REGEX = re.compile("^\s*end\s*(?P<unit_type>module|program)\s*(?P<modname>\w*)?",
                       re.IGNORECASE)
USE_REGEX = re.compile("""^\s*use
(\s*,\s*intrinsic\s*)?(\s*::\s*|\s+)  # Valid separators between "use" and module name
(?P<moduse>\w*)                       # The module name
\s*(, )?\s*(only)?\s*(:)?.*?$         # Stuff that might follow the name
""",
                       re.IGNORECASE | re.VERBOSE)


class FortranFile:
    """The modules and dependencies of a Fortran source file

    Args:
        filename: Source file
        macros: Dict of preprocessor macros to be expanded
        readfile: Read and process the file [True]
    """
    def __init__(self, filename=None, macros=None, readfile=True):
        self.filename = filename
        self.uses = None
        self.modules = None
        self.depends_on = None

        if readfile:
            with smart_open(self.filename, 'r') as f:
                contents = f.readlines()

            self.modules = self.get_modules(contents, macros)
            self.uses = self.get_uses()

    def __str__(self):
        return self.filename

    def __repr__(self):
        return "FortranFile('{}')".format(self.filename)

    def get_modules(self, contents, macros=None):
        """Return all the modules or programs that are in the file

        Args:
            contents: Contents of the source file
        """

        contains = {}
        found_units = []
        starts = []
        ends = []

        for num, line in enumerate(contents):
            unit = re.match(UNIT_REGEX, line)
            end = re.match(END_REGEX, line)
            if unit:
                found_units.append(unit)
                starts.append(num)
            if end:
                ends.append(num)

        if found_units:
            if (len(found_units) != len(starts)) or (len(starts) != len(ends)):
                error_string = ("Unmatched start/end of modules in {} ({} begins/{} ends)"
                                .format(self.filename, len(starts), len(ends)))
                raise ValueError(error_string)
            for unit, start, end in zip(found_units, starts, ends):
                name = unit.group('modname')
                contains[name] = FortranModule(unit_type=unit.group('unit_type'),
                                               name=name,
                                               source_file=self,
                                               text=(contents, start, end),
                                               macros=macros)

        # Remove duplicates before returning
        return contains

    def get_uses(self):
        """Return a sorted list of the modules this file USEs
        """

        if self.modules is None:
            return []

        # flatten list of lists
        return sorted(set([mod for module in self.modules.values()
                           for mod in module.uses]))

class FortranModule:
    """A Fortran Module or Program

    unit_type: 'module' or 'program'
    name: Name of the module/program
    source_file: Name of the file containing the module/program
    text: Tuple containing source_file contents, and start and end lines of the module
    macros: Any defined macros
    """
    def __init__(self, unit_type, name, source_file=None, text=None, macros=None):
        self.unit_type = unit_type.strip().lower()
        self.name = name.strip().lower()

        if source_file is not None:
            self.source_file = source_file
            self.defined_at = text[1]
            self.end = text[2]

            self.uses = self.get_uses(text[0], macros)
        else:
            self.source_file = FortranFile(filename='empty',
                                           readfile=False)

    def __str__(self):
        return self.name

    def __repr__(self):
        return "FortranModule({}, '{}', '{}')".format(self.unit_type, self.name,
                                                      self.source_file.filename)

    def get_uses(self, contents, macros=None):
        """Return which modules are used in the file after expanding macros

        Args:
            contents: Contents of the source file
            macros: Dict of preprocessor macros to be expanded
        """

        uses = []

        for line in contents[self.defined_at:self.end]:
            found = re.match(USE_REGEX, line)
            if found:
                uses.append(found.group('moduse').strip())

        # Remove duplicates
        uniq_mods = list(set(uses))

        if macros is not None:
            for i, mod in enumerate(uniq_mods):
                for k, v in macros.items():
                    if re.match(k, mod, re.IGNORECASE):
                        uniq_mods[i] = mod.replace(k, v)

        return uniq_mods

    ### END units.py

### DONT NEED
# from .graph import Graph

# If graphviz is not installed, graphs can't be produced
# try:
#     import graphviz as gv
#     has_graphviz = True
# except ImportError:
#     has_graphviz = False



class FortranProject:
    def __init__(self, name=None, exclude_files=None, files=None, ignore_modules=None,
                 macros=None, verbose=False):
        """Create a list of FortranFile objects

        Args:
            name: Name of the project (default: name of current directory)
            exclude_files: List of files to exclude
            files: List of files to include (default: all in current directory)
            ignore_modules: List of module names to ignore_mod
            macros: Dictionary of module names and replacement values
            verbose: Print more messages (default: False)
        """

        if name is None:
            self.name = os.path.basename(os.getcwd())
        else:
            self.name = name

        if files is None:
            files = self.get_source()
        elif not isinstance(files, list):
            files = [files]

        if exclude_files is not None:
            if not isinstance(exclude_files, list):
                exclude_files = [exclude_files]
            files = set(files) - set(exclude_files)

        self.files = {filename: FortranFile(filename, macros)
                      for filename in files}
        self.modules = self.get_modules()

        self.remove_ignored_modules(ignore_modules)

        self.depends_by_module = self.get_depends_by_module(verbose)
        self.depends_by_file = self.get_depends_by_file(verbose)

    def get_source(self, extensions=None):
        """Return all files ending with any of extensions (defaults to
        [".f90", ".F90"])
        """

        if extensions is None:
            extensions = [".f90", ".F90"]
        elif not isinstance(extensions, list):
            extensions = [extensions]

        tmp = os.listdir(".")
        files = []
        for ext in extensions:
            files.extend([x for x in tmp if x.endswith(ext)])

        return files

    def get_modules(self):
        """Merge dicts of FortranModules from list of FortranFiles
        """

        mod_dict = {}
        for source_file in self.files.values():
            mod_dict.update(source_file.modules)
        return mod_dict

    def get_depends_by_module(self, verbose=False):
        """Get the dependencies of each file in file_list
        """
        depends = {}
        for module in self.modules.values():
            graph = []
            for used_mod in module.uses:
                try:
                    graph.append(self.modules[used_mod])
                except KeyError:
                    new_module = FortranModule(unit_type='module',
                                               name=used_mod)
                    graph.append(new_module)

                    print("Error module " + used_mod + " not defined in any files. Creating empty ",file=sys.stderr)
                    # print(Fore.RED + "Error" + Fore.RESET + " module " +
                    #       Fore.GREEN + used_mod + Fore.RESET +
                    #       " not defined in any files. Creating empty ",
                    #       file=sys.stderr)
            depends[module] = sorted(graph,
                                     key=lambda f: f.source_file.filename)

        if verbose:
            for module_ in sorted(depends.keys(), key=lambda f: f.source_file.filename):
                print( module_.name + " depends on :")
                # print(Fore.GREEN + module_.name + Fore.RESET +
                #       " depends on :" + Fore.BLUE)
                for dep in depends[module_]:
                    print("\t" + dep.name)
                # print(Fore.RESET)

        return depends

    def get_depends_by_file(self, verbose=False):
        """Get the dependencies of each file in file_list
        """
        depends = {}
        for source_file in self.files.values():
            graph = []
            for mod in source_file.uses:
                try:
                    mod_file = self.modules[mod].source_file
                    # Don't add self as a dependency
                    if mod_file.filename.lower() == source_file.filename.lower():
                        continue
                    graph.append(mod_file)
                except KeyError:
                    print("Error module " + mod + " not defined in any files. Skipping...",file=sys.stderr)
                    # print(Fore.RED + "Error" + Fore.RESET + " module " + Fore.GREEN +
                    #       mod + Fore.RESET + " not defined in any files. Skipping...",
                    #       file=sys.stderr)
            depends[source_file] = sorted(graph,
                                          key=lambda f: f.filename)

        if verbose:
            for file_ in sorted(depends.keys(), key=lambda f: f.filename):
                print(file_.filename + " depends on :")
                # print(Fore.GREEN + file_.filename + Fore.RESET +
                #       " depends on :" + Fore.BLUE)
                for dep in depends[file_]:
                    print("\t" + dep.filename)
                #print(Fore.RESET)

        return depends

    def write_depends(self, filename="makefile.dep", overwrite=False, build=''):
        """Write the dependencies to file

        Args:
            filename: Name of the output file
            overwrite: Overwrite existing dependency file [False]
            build: Directory to prepend to filenames
        """
        # Test file doesn't exist
        if os.path.exists(filename):
            if not(overwrite):
                print("Warning: file '{}' exists.".format(filename))
                # print(Fore.RED + "Warning: file '{}' exists.".format(filename) +
                #       Fore.RESET)
                opt = input("Overwrite? Y... for yes.")
                if opt.lower().startswith("y"):
                    pass
                else:
                    return

        with smart_open(filename, 'w') as f:
            f.write('# This file is generated automatically. DO NOT EDIT!\n')
            alpha_list = sorted(self.depends_by_file.keys(),
                                key=lambda f: f.filename)
            for file_ in alpha_list:
                _, filename = os.path.split(file_.filename)
                objectname = os.path.splitext(filename)[0] + ".o"
                listing = "\n{} : ".format(os.path.join(build, objectname))
                for dep in self.depends_by_file[file_]:
                    _, depfilename = os.path.split(dep.filename)
                    depobjectname = os.path.splitext(depfilename)[0] + ".o"
                    listing += " \\\n\t{}".format(os.path.join(build, depobjectname))
                listing += "\n"
                f.write(listing)

    def make_graph(self, filename=None, format='svg', view=True):
        """Draw a graph of the project using graphviz

        Args:
            filename: Name of the output file
            format: Image format
            view: Immediately display the graph [True]
        """
        if not has_graphviz:
            warnings.warn("graphviz not installed: can't make graph",
                          RuntimeWarning)
            return

        if filename is None:
            filename = self.name + ".dot"

        graph = Graph(self.depends_by_module, filename=filename,
                      format=format, view=view)
        graph.draw()

    def remove_ignored_modules(self, ignore_modules=None):
        """Remove the modules in iterable ignore_modules from
        all dependencies
        """
        if ignore_modules is None:
            return
        elif not isinstance(ignore_modules, list):
            ignore_modules = [ignore_modules]

        # Remove from module dict
        for ignore_mod in ignore_modules:
            self.modules.pop(ignore_mod, None)
            # Remove from 'used' modules
            for module in self.modules.values():
                try:
                    module.uses.remove(ignore_mod)
                except ValueError:
                    pass
            # Remove from 'used' files
            for source_file in self.files.values():
                try:
                    source_file.uses.remove(ignore_mod)
                except ValueError:
                    pass


### FROM __main__.py
def main(args=None):
    """Run the module as a script

    """
    # Add command line arguments
    parser = argparse.ArgumentParser(description='Generate Fortran dependencies')
    parser.add_argument('-f', '--files', nargs='+', help='Files to process')
    parser.add_argument('-D', nargs='+', action='append', metavar='NAME=DESCRIPTION',
                        help="The macro NAME is replaced by DEFINITION in 'use' statements")
    parser.add_argument('-b', '--build', nargs=1, default='',
                        help='Build Directory (prepended to all files in output)')
    parser.add_argument('-o', '--output', nargs=1, help='Output file')
    # parser.add_argument('-g', '--graph', action='store_true',
    #                     help='Make a graph of the project')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='explain what is done')
    parser.add_argument('-w', '--overwrite', action='store_true',
                        help='Overwrite output file without warning')
    # parser.add_argument('-c', '--colour', action='store_true',
    #                     help='Print in colour')
    parser.add_argument('-e', '--exclude-files', nargs='+', default=None,
                        help='Files to exclude')
    parser.add_argument('-i', '--ignore-modules', nargs='+', default=None,
                        help='Modules to ignore')

    # Parse the command line arguments
    args = parser.parse_args()

    # Assemble a dictionary out of the macro definitions
    macros = {}
    if args.D:
        for arg in args.D:
            for var in arg:
                temp = var.split('=')
            macros[temp[0]] = temp[1]

    output = args.output[0] if args.output else None
    build = args.build[0] if args.build else ''

    # # Sorts out the terminal colours on Windows
    # strip_colours = not args.colour
    # colorama.init(strip=strip_colours)

    project = FortranProject(files=args.files, exclude_files=args.exclude_files,
                             ignore_modules=args.ignore_modules,
                             macros=macros, verbose=args.verbose)

    if output is not None:
        project.write_depends(filename=output, overwrite=args.overwrite, build=build)

    # if args.graph:
    #     project.make_graph()

# Script
if __name__ == "__main__":
    main()
