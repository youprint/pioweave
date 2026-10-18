# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Helpers shared by the tests: a TestCase with a temporary directory, and parametrized tests that are generated as one method per case, so that a failure names the case."""
import itertools
import functools
import pathlib
import re
import tempfile
import unittest

__all__ = ["TestCase", "parametrize", "cases"]


class TestCase(unittest.TestCase):
    """unittest.TestCase with `self.tmp_path`: a fresh temporary directory (a pathlib.Path) created on first use and removed when the test ends."""
    @property
    def tmp_path(self):
        path = self.__dict__.get("_tmp_path")
        if path is None:
            tmp = tempfile.TemporaryDirectory(prefix="pioweave_test_")
            self.addCleanup(tmp.cleanup)
            path = self.__dict__["_tmp_path"] = pathlib.Path(tmp.name)
        return path


def _label(value, name, index):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return re.sub(r"\W+", "_", str(value)).strip("_") or "%s%d" % (name, index)
    return "%s%d" % (name, index)


def parametrize(names, values, ids=None):
    """Mark a test method as parametrized; `@cases` on the class then replaces it with one method per case, named `<method>_<id>`.

    `names` is a comma separated string of the argument names, `values` a list of values (of tuples when there are several names) and `ids` an optional list of
    labels. Several parametrize marks on one method give every combination."""
    names = [n.strip() for n in names.split(",")]

    def mark(fn):
        rows = [(v,) if len(names) == 1 else tuple(v) for v in values]
        labels = list(ids) if ids is not None else ["_".join(_label(v, n, i) for n, v in zip(names, row)) for i, row in enumerate(rows)]
        assert len(labels) == len(rows), "one id per case"
        fn.__dict__.setdefault("_parameters", []).append([(dict(zip(names, row)), label) for row, label in zip(rows, labels)])
        return fn
    return mark


def cases(cls):
    """Class decorator: turn every method marked with `parametrize` into one test method per case."""
    for name, fn in list(vars(cls).items()):
        marks = getattr(fn, "_parameters", None)
        if not marks:
            continue
        delattr(cls, name)
        for combo in itertools.product(*marks):
            kwargs = {k: v for part, _ in combo for k, v in part.items()}
            label = "_".join(re.sub(r"\W+", "_", str(l)) for _, l in combo)

            def method(self, _fn=fn, _kwargs=kwargs):
                return _fn(self, **_kwargs)
            method = functools.wraps(fn)(method)
            method.__dict__.pop("_parameters", None)
            method.__name__ = method.__qualname__ = "%s_%s" % (name, label)
            assert not hasattr(cls, method.__name__), "duplicate test id " + method.__name__
            setattr(cls, method.__name__, method)
    return cls
