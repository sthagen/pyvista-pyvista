"""Supporting functions for documentation build."""

from __future__ import annotations

import inspect
import os
import os.path as op
import sys


def linkcode_resolve(domain: str, info: dict[str, str], edit: bool = False) -> str | None:  # noqa: FBT001, FBT002
    """Determine the URL corresponding to a Python object.

    Parameters
    ----------
    domain : str
        Only useful when 'py'.

    info : dict
        With keys "module" and "fullname".

    edit : bool, default=False
        Jump right to the edit page.

    Returns
    -------
    str
        The code URL. Empty string if there is no valid link.

    Notes
    -----
    This function is used by the `sphinx.ext.linkcode` extension to create the "[Source]"
    button whose link is edited in this function.

    This has been adapted to deal with our "verbose" decorator.

    Adapted from mne (mne/utils/docs.py), which was adapted from SciPy (doc/source/conf.py).

    """
    import pyvista  # noqa: PLC0415

    if domain != 'py':
        return None

    modname = info['module']
    fullname = info['fullname']

    # Little clean up to avoid pyvista.pyvista
    if fullname.startswith(modname):
        fullname = fullname[len(modname) + 1 :]

    submod = sys.modules.get(modname)
    if submod is None:
        return None

    obj = submod
    for part in fullname.split('.'):
        try:
            obj = getattr(obj, part)
        except Exception:
            return None

    # deal with our decorators properly
    while hasattr(obj, 'fget'):
        obj = obj.fget

    # deal with wrapped object
    while hasattr(obj, '__wrapped__'):
        obj = obj.__wrapped__
    try:
        fn = inspect.getsourcefile(obj)
    except Exception:  # pragma: no cover
        fn = None

    if not fn:  # pragma: no cover
        try:
            fn = inspect.getsourcefile(sys.modules[obj.__module__])
        except Exception:
            return None
        return None

    fn = op.relpath(fn, start=op.dirname(pyvista.__file__))  # noqa: PTH120
    fn = '/'.join(op.normpath(fn).split(os.sep))  # in case on Windows # noqa: PTH206

    try:
        source, lineno = inspect.getsourcelines(obj)
    except Exception:  # pragma: no cover
        lineno = None

    linespec = f'#L{lineno}-L{lineno + len(source) - 1}' if lineno and not edit else ''

    if 'dev' in pyvista.__version__:
        kind = 'main'
    else:  # pragma: no cover
        kind = f'release/{".".join(pyvista.__version__.split(".")[:2])}'

    blob_or_edit = 'edit' if edit else 'blob'

    return f'http://github.com/pyvista/pyvista/{blob_or_edit}/{kind}/pyvista/{fn}{linespec}'


def pv_html_page_context(  # noqa: PLR0917
    app,  # noqa: ARG001
    pagename: str,
    templatename: str,  # noqa: ARG001
    context,
    doctree,  # noqa: ARG001
) -> None:  # pragma: no cover
    """Add a function for returning an "edit this page" link pointing to `main`.

    This is specific to PyVista to ensure that the "edit this page" link always
    goes to the right page, specifically for:

    - Gallery examples
    - Autosummary examples (using _autosummary)

    """

    def fix_edit_link_button(link: str) -> str | None:
        """Transform "edit on github" links to the correct url.

        This is specific to PyVista to ensure that the "edit this page" link
        always goes to the right page, specifically for:

        - Gallery examples
        - Autosummary examples (using _autosummary)

        Parameters
        ----------
        link : str
            The link to the github edit interface.

        Returns
        -------
        str
            The link to the tip of the main branch for the same file.

        """
        if pagename.startswith('examples') and 'index' not in pagename:
            # This is a gallery example.

            # We can get away with directly using the pagename since "examples"
            # in the pagename is the same as the "examples" directory in the
            # repo
            return f'http://github.com/pyvista/pyvista/edit/main/{pagename}.py'
        elif '_autosummary' in pagename:
            # This is an API example
            fullname = pagename.split('_autosummary')[1][1:]
            return linkcode_resolve('py', {'module': 'pyvista', 'fullname': fullname}, edit=True)
        else:
            return link

    context['fix_edit_link_button'] = fix_edit_link_button
