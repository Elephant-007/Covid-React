import re
import operator
import heapq
from collections import namedtuple
from collections.abc import Sequence
from contextlib import contextmanager

from numba.core.utils import cached_property

import llvmlite.binding as llvm


class RecordLLVMPassTimings:
    """A helper context manager to track LLVM pass timings.
    """

    __slots__ = ["_data"]

    def __enter__(self):
        """Enables the pass timing in LLVM.
        """
        llvm.set_time_passes(True)
        return self

    def __exit__(self, exc_val, exc_type, exc_tb):
        """Reset timings and save report internally.
        """
        self._data = llvm.report_and_reset_timings()
        llvm.set_time_passes(False)
        return

    def get(self):
        """Retrieve timing data for processing.

        Returns
        -------
        timings : _ProcessedPassTimings
        """
        return _ProcessedPassTimings(self._data)


_PassTimingRecord = namedtuple(
    "_PassTimingRecord",
    [
        "user_time",
        "user_percent",
        "system_time",
        "system_percent",
        "user_system_time",
        "user_system_percent",
        "wall_time",
        "wall_percent",
        "pass_name",
    ],
)


def _adjust_timings(records):
    """Adjust timing records because of truncated information.

    Details: The percent information can be used to improve the timing
    information.

    Returns
    -------
    res : List[_PassTimingRecord]
    """
    total_rec = records[-1]
    assert total_rec.pass_name == "Total"  # guard for implementation error

    def make_adjuster(attr):
        time_attr = f"{attr}_time"
        percent_attr = f"{attr}_percent"
        time_getter = operator.attrgetter(time_attr)

        def adjust(d):
            """Compute percent x total_time = adjusted"""
            total = time_getter(total_rec)
            adjusted = total * d[percent_attr] * 0.01
            d[time_attr] = adjusted
            return d

        return adjust

    # Make adjustment functions for each field
    adj_fns = [
        make_adjuster(x) for x in ["user", "system", "user_system", "wall"]
    ]

    # Extract dictionaries from the namedtuples
    dicts = map(lambda x: x._asdict(), records)

    def chained(d):
        # Chain the adjustment functions
        for fn in adj_fns:
            d = fn(d)
        # Reconstruct the namedtuple
        return _PassTimingRecord(**d)

    return list(map(chained, dicts))


class _ProcessedPassTimings:
    """A class for processing raw timing report from LLVM.

    The processing is done lazily so we don't waste time processing unused
    timing information.
    """

    def __init__(self, raw_data):
        self._raw_data = raw_data

    def __bool__(self):
        return bool(self._raw_data)

    def get_raw_data(self):
        """Returns the raw string data.
        """
        return self._raw_data

    def get_total_time(self):
        """Compute the total time spend in all passes.
        """
        return self.list_records()[-1].wall_time

    def list_records(self):
        """Get the processed data for the timing report.

        Returns
        -------
        res : List[_PassTimingRecord]
        """
        return self._processed

    def list_top(self, n):
        """Returns the top(n) most time-consuming (by wall-time) passes.

        Parameters
        ----------
        n : int
            This limits the maximum number of items to show.
            This function will show the ``n`` most time-consuming passes.

        Returns
        -------
        res : List[_PassTimingRecord]
            Returns the top(n) most time-consuming passes in descending order.
        """
        records = self.list_records()
        key = operator.attrgetter("wall_time")
        return heapq.nlargest(n, records[:-1], key)

    def summary(self, topn=5):
        """Return a string summarizing the timing information.

        Parameters
        ----------
        topn : int
            This limits the maximum number of items to show.
            This function will show the ``topn`` most time-consuming passes.

        Returns
        -------
        res : str
        """
        buf = []
        ap = buf.append
        ap(f"Total {self.get_total_time():.4f}s")
        ap("Top timings:")
        for p in self.list_top(topn):
            ap(f"  {p.wall_time:.4f}s ({p.wall_percent:5}%) {p.pass_name}")
        return "\n".join(buf)

    @cached_property
    def _processed(self):
        """A cached property for lazily processing the data and returning it.

        See ``_process()`` for details.
        """
        return self._process()

    def _process(self):
        """Parses the raw string data from LLVM timing report and attempts
        to improve the data by recomputing the times
        (See `_adjust_timings()``).
        """

        def parse(raw_data):
            """A generator that parses the raw_data line-by-line to extract
            timing information for each pass.
            """
            lines = raw_data.splitlines()
            n = r"\s*((?:[0-9]+\.)?[0-9]+)"
            pat = f"\\s+{n}\\s*\\({n}%\\)" * 4 + r"\s*(.*)"

            line_iter = iter(lines)
            for ln in line_iter:
                m = re.match(pat, ln)
                if m is not None:
                    raw_data = m.groups()
                    rec = _PassTimingRecord(
                        *map(float, raw_data[:-1]), *raw_data[-1:]
                    )
                    yield rec
                    if rec.pass_name == "Total":
                        # "Total" means the report has ended
                        break
            # Check that we have reach the end of the report
            remaining = '\n'.join(line_iter)
            if remaining:
                raise ValueError(
                    f"unexpected text after parser finished:\n{remaining}"
                )

        # Parse raw data
        records = list(parse(self._raw_data))
        return _adjust_timings(records)


_NamedTimings = namedtuple("_NamedTimings", ["name", "timings"])


class PassTimingsCollection(Sequence):
    """A collection of pass timings.
    """

    def __init__(self, name):
        self._name = name
        self._records = []

    @contextmanager
    def record(self, name):
        """Record timings

        See also ``RecordLLVMPassTimings``

        Parameters
        ----------
        name : str
            Name for the records.
        """
        with RecordLLVMPassTimings() as timings:
            yield
        rec = timings.get()
        # Only keep non-empty records
        if rec:
            self.append(name, rec)

    def append(self, name, timings):
        """Append timing records

        Parameters
        ----------
        name : str
            Name for the records.
        timings : _ProcessedPassTimings
            the timing records.
        """
        self._records.append(_NamedTimings(name, timings))

    def __getitem__(self, i):
        """Get the i-th timing record.

        Returns
        -------
        res : _NamedTimings
        """
        return self._records[i]

    def __len__(self):
        """Length of this collection.
        """
        return len(self._records)

    def __str__(self):
        buf = []
        ap = buf.append
        ap(f"Printing pass timings for {self._name}")
        for r in self._records:
            ap(f"== {r.name}")
            ap(r.timings.summary())
        return "\n".join(buf)
