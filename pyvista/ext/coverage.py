"""Modified sphinx.ext.coverage module.

Check Python modules and C API for coverage.  Mostly written by Josip
Dzolonga for the Google Highly Open Participation contest.

Modified slightly for ``pyvista``.

:copyright: See `Sphinx copyright <https://github.com/sphinx-doc/sphinx>`_.
:license: See `Sphinx license <https://github.com/sphinx-doc/sphinx>`_.

"""

from __future__ import annotations

from importlib import import_module
import inspect
from os import path
from pathlib import Path
import pickle
import re
from re import Pattern
from typing import IO
from typing import TYPE_CHECKING
from typing import Any

import sphinx
from sphinx.builders import Builder
from sphinx.locale import __
from sphinx.util import logging
from sphinx.util.console import red
from sphinx.util.inspect import safe_getattr

if TYPE_CHECKING:
    from sphinx.application import Sphinx

logger = logging.getLogger(__name__)


# utility
def write_header(f: IO, text: str, char: str = '-') -> None:
    f.write(text + '\n')
    f.write(char * len(text) + '\n')


def compile_regex_list(name: str, exps: str) -> list[Pattern]:
    lst = []
    for exp in exps:
        try:
            lst.append(re.compile(exp))
        except Exception:
            logger.warning(__('invalid regex %r in %s'), exp, name)
    return lst


def method_from_obj(obj_name):
    """Return the class and method from an object name.

    pyvista.core.filters.poly_data.PolyDataFilters.boolean_add

    becomes

    boolean_add

    """
    return '.'.join(obj_name.split('.')[-1:])


class CoverageBuilder(Builder):
    """Evaluates coverage of code in the documentation."""

    name = 'coverage'
    epilog = __(
        'Testing of coverage in the sources finished, look at the '
        'results in %(outdir)s' + path.sep + 'python.txt.',
    )

    def init(self) -> None:
        self.c_sourcefiles: list[str] = []
        for pattern in self.config.coverage_c_path:
            full_pattern = str(Path(self.srcdir) / pattern)
            self.c_sourcefiles.extend(Path(self.srcdir).glob(full_pattern))

        self.c_regexes: list[tuple[str, Pattern]] = []
        for name, exp in self.config.coverage_c_regexes.items():
            try:
                self.c_regexes.append((name, re.compile(exp)))
            except Exception:
                logger.warning(__('invalid regex %r in coverage_c_regexes'), exp)

        self.c_ignorexps: dict[str, list[Pattern]] = {}
        for name, exps in self.config.coverage_ignore_c_items.items():
            self.c_ignorexps[name] = compile_regex_list('coverage_ignore_c_items', exps)
        self.mod_ignorexps = compile_regex_list(
            'coverage_ignore_modules',
            self.config.coverage_ignore_modules,
        )
        self.cls_ignorexps = compile_regex_list(
            'coverage_ignore_classes',
            self.config.coverage_ignore_classes,
        )
        self.fun_ignorexps = compile_regex_list(
            'coverage_ignore_functions',
            self.config.coverage_ignore_functions,
        )
        self.py_ignorexps = compile_regex_list(
            'coverage_ignore_pyobjects',
            self.config.coverage_ignore_pyobjects,
        )
        self.add_modules = self.config.coverage_additional_modules

    def get_outdated_docs(self) -> str:
        return 'coverage overview'

    def write(self, *ignored: Any) -> None:  # noqa: ARG002
        self.py_undoc: dict[str, dict[str, Any]] = {}
        self.build_py_coverage()
        self.write_py_coverage()

        self.c_undoc: dict[str, set[tuple[str, str]]] = {}
        self.build_c_coverage()
        self.write_c_coverage()

    def build_c_coverage(self) -> None:
        # Fetch all the info from the header files
        c_objects = self.env.domaindata['c']['objects']
        for filename in self.c_sourcefiles:
            undoc: set[tuple[str, str]] = set()
            with Path(filename).open() as f:
                for line in f:
                    for key, regex in self.c_regexes:
                        match = regex.match(line)
                        if match:
                            name = match.groups()[0]
                            if name not in c_objects:
                                for exp in self.c_ignorexps.get(key, []):
                                    if exp.match(name):
                                        break
                                else:
                                    undoc.add((key, name))
                            continue
            if undoc:
                self.c_undoc[filename] = undoc

    def write_c_coverage(self) -> None:
        output_file = str(Path(self.outdir) / 'c.txt')
        with Path(output_file).open('w') as op:
            if self.config.coverage_write_headline:
                write_header(op, 'Undocumented C API elements', '=')
            op.write('\n')

            for filename, undoc in self.c_undoc.items():
                write_header(op, filename)
                for typ, name in sorted(undoc):
                    op.write(f' * {name:<50} [{typ:>9}]\n')
                    if self.config.coverage_show_missing_items:
                        if self.app.quiet or self.app.warningiserror:
                            logger.warning(
                                __('undocumented c api: %s [%s] in file %s'),
                                name,
                                typ,
                                filename,
                            )
                        else:
                            logger.info(
                                red('undocumented  ')
                                + 'c   '
                                + 'api       '
                                + f'{name + f" [{typ:>9}]":<30}'
                                + red(' - in file ')
                                + filename,
                            )
                op.write('\n')

    def ignore_pyobj(self, full_name: str) -> bool:
        return any(exp.search(full_name) for exp in self.py_ignorexps)

    def build_py_coverage(self) -> None:
        objects = self.env.domaindata['py']['objects']

        # objects are sometimes not referenced in the docs as they are
        # in the source.  Here we simply grab the method and class of
        # the object
        abbr_names = set()
        for obj_name in objects:
            # only include method and class
            abbr_names.add(method_from_obj(obj_name))

        modules = self.env.domaindata['py']['modules']
        for mod_name in self.add_modules:
            modules[mod_name] = None

        skip_undoc = self.config.coverage_skip_undoc_in_source

        for mod_name in modules:
            ignore = False
            for exp in self.mod_ignorexps:
                if exp.match(mod_name):
                    ignore = True
                    break
            if ignore or self.ignore_pyobj(mod_name):
                continue

            try:
                mod = import_module(mod_name)
            except ImportError as err:
                logger.warning(__('module %s could not be imported: %s'), mod_name, err)
                self.py_undoc[mod_name] = {'error': err}
                continue

            funcs = []
            classes: dict[str, list[str]] = {}

            for name, obj in inspect.getmembers(mod):
                # diverse module attributes are ignored:
                if name[0] == '_':
                    # begins in an underscore
                    continue
                if not hasattr(obj, '__module__'):
                    # cannot be attributed to a module
                    continue
                if obj.__module__ != mod_name:
                    # is not defined in this module
                    continue

                full_name = f'{mod_name}.{name}'
                if self.ignore_pyobj(full_name):
                    continue

                if inspect.isfunction(obj):
                    if full_name not in objects:
                        for exp in self.fun_ignorexps:
                            if exp.match(name):
                                break
                        else:
                            if skip_undoc and not obj.__doc__:
                                continue
                            funcs.append(name)
                elif inspect.isclass(obj):
                    for exp in self.cls_ignorexps:
                        if exp.match(name):
                            break
                    else:
                        if full_name not in objects:
                            if skip_undoc and not obj.__doc__:
                                continue
                            # not documented at all
                            classes[name] = []
                            continue

                        attrs: list[str] = []

                        for attr_name in dir(obj):
                            if attr_name not in obj.__dict__:
                                continue
                            try:
                                attr = safe_getattr(obj, attr_name)
                            except AttributeError:
                                continue
                            if not (inspect.ismethod(attr) or inspect.isfunction(attr)):
                                continue
                            if attr_name[0] == '_':
                                # starts with an underscore, ignore it
                                continue
                            if skip_undoc and not attr.__doc__:
                                # skip methods without docstring if wished
                                continue
                            full_attr_name = f'{full_name}.{attr_name}'
                            if self.ignore_pyobj(full_attr_name):
                                continue
                            if full_attr_name not in objects:
                                # it's possible object is abbreviated
                                abbr_name = method_from_obj(obj_name)
                                if abbr_name not in abbr_names:
                                    attrs.append(attr_name)
                        if attrs:
                            # some attributes are undocumented
                            classes[name] = attrs

            self.py_undoc[mod_name] = {'funcs': funcs, 'classes': classes}

    def write_py_coverage(self) -> None:
        output_file = str(Path(self.outdir) / 'python.txt')
        failed = []
        with Path(output_file).open('w') as op:
            if self.config.coverage_write_headline:
                write_header(op, 'Undocumented Python objects', '=')
            keys = sorted(self.py_undoc.keys())
            for name in keys:
                undoc = self.py_undoc[name]
                if 'error' in undoc:
                    failed.append((name, undoc['error']))
                else:
                    if not undoc['classes'] and not undoc['funcs']:
                        continue

                    write_header(op, name)
                    if undoc['funcs']:
                        op.write('Functions:\n')
                        op.writelines(' * %s\n' % x for x in undoc['funcs'])  # noqa: UP031
                        if self.config.coverage_show_missing_items:
                            if self.app.quiet or self.app.warningiserror:
                                for func in undoc['funcs']:
                                    logger.warning(
                                        __('undocumented python function: %s :: %s'),
                                        name,
                                        func,
                                    )
                            else:
                                for func in undoc['funcs']:
                                    logger.info(
                                        red('undocumented  ')
                                        + 'py  '
                                        + 'function  '
                                        + f'{func:<30}'
                                        + red(' - in module ')
                                        + name,
                                    )
                        op.write('\n')
                    if undoc['classes']:
                        op.write('Classes:\n')
                        for class_name, methods in sorted(undoc['classes'].items()):
                            if not methods:
                                op.write(' * %s\n' % class_name)  # noqa: UP031
                                if self.config.coverage_show_missing_items:
                                    if self.app.quiet or self.app.warningiserror:
                                        logger.warning(
                                            __('undocumented python class: %s :: %s'),
                                            name,
                                            class_name,
                                        )
                                    else:
                                        logger.info(
                                            red('undocumented  ')
                                            + 'py  '
                                            + 'class     '
                                            + f'{class_name:<30}'
                                            + red(' - in module ')
                                            + name,
                                        )
                            else:
                                op.write(
                                    ' * %s -- missing methods:\n\n' % class_name,  # noqa: UP031
                                )
                                op.writelines('   - %s\n' % x for x in methods)  # noqa: UP031
                                if self.config.coverage_show_missing_items:
                                    if self.app.quiet or self.app.warningiserror:
                                        for meth in methods:
                                            logger.warning(
                                                __('undocumented python method: %s :: %s :: %s'),
                                                name,
                                                class_name,
                                                meth,
                                            )
                                    else:
                                        for meth in methods:
                                            logger.info(
                                                red('undocumented  ')
                                                + 'py  '
                                                + 'method    '
                                                + f'{class_name + "." + meth:<30}'
                                                + red(' - in module ')
                                                + name,
                                            )
                        op.write('\n')

            if failed:
                write_header(op, 'Modules that failed to import')
                op.writelines(' * %s -- %s\n' % x for x in failed)  # noqa: UP031

    def finish(self) -> None:
        """Dump the coverage data to a pickle file too."""
        picklepath = str(Path(self.outdir) / 'undoc.pickle')
        with Path(picklepath).open('wb') as dumpfile:
            pickle.dump((self.py_undoc, self.c_undoc), dumpfile)


def setup(app: Sphinx) -> dict[str, Any]:
    app.add_builder(CoverageBuilder)
    app.add_config_value('coverage_additional_modules', [], False)
    app.add_config_value('coverage_ignore_modules', [], False)
    app.add_config_value('coverage_ignore_functions', [], False)
    app.add_config_value('coverage_ignore_classes', [], False)
    app.add_config_value('coverage_ignore_pyobjects', [], False)
    app.add_config_value('coverage_c_path', [], False)
    app.add_config_value('coverage_c_regexes', {}, False)
    app.add_config_value('coverage_ignore_c_items', {}, False)
    app.add_config_value('coverage_write_headline', True, False)
    app.add_config_value('coverage_skip_undoc_in_source', False, False)
    app.add_config_value('coverage_show_missing_items', False, False)
    return {'version': sphinx.__display_version__, 'parallel_read_safe': True}
