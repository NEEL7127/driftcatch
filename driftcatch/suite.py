"""Running one test body across many recorded scenarios.

A behaviour suite grows the same way every time. You start with one scenario,
then you want the refund path and the escalation path and the "customer is
rude" path, and within a week you have forty test functions that differ only in
their input and their recording name. Copy-paste is the usual answer, and it is
the reason the fortieth scenario never gets the assertion somebody added to the
third.

A ``Suite`` is a table of cases plus one body. Each case opens its own take and
therefore its own recording, named ``<suite>_<case>``, so the goldens stay
separate and a diff still points at exactly one scenario. The ``check`` callback
runs after every case, which is where a policy that must hold everywhere - the
learned invariants, a cost ceiling, a forbidden tool - belongs. Adding it once
applies it to the fortieth scenario too.

Deliberately *not* a runner. The body still calls your agent, exactly as a
plain test would. DriftCatch does not own your agent, does not know how to
invoke it, and this module does not change that: it removes the repetition
around the call, not the call.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from driftcatch.errors import ConfigError
from driftcatch.recording.io import validate_name
from driftcatch.take import Take, take

__all__ = ["Suite"]

F = TypeVar("F", bound=Callable[..., Any])

#: The parameter the suite injects. Named defensively: it shares a namespace
#: with the user's own fixtures, and a collision would be baffling to debug.
_PARAM = "_driftcatch_case"


class Suite:
    """A table of scenarios sharing one test body and one policy.

    ``cases`` maps a case name to whatever the body needs - a string, a dict of
    keyword arguments, a dataclass. It is passed through untouched, because the
    body is the only thing that knows what a case means.

    Recording names are ``<suite>_<case>`` and are validated up front rather
    than when the test runs. A name that cannot become a filename is a mistake
    in the table, and finding out at collection time beats finding out on the
    twelfth case of a live run.
    """

    def __init__(
        self,
        name: str,
        cases: Mapping[str, Any],
        *,
        check: Callable[[Take], None] | None = None,
        **take_options: Any,
    ) -> None:
        if not cases:
            raise ConfigError(
                f"suite {name!r} has no cases. A suite with an empty table would "
                f"collect zero tests and report green, which looks exactly like a "
                f"suite that passed."
            )
        self.name = validate_name(name)
        self.cases = dict(cases)
        self.check = check
        self.take_options = take_options

        for case in self.cases:
            # Validate every recording name now. The alternative is a run that
            # gets eleven cases in before refusing the twelfth.
            validate_name(self.recording_name(case))

    def recording_name(self, case: str) -> str:
        """The golden this case records to."""
        return f"{self.name}_{case}"

    @property
    def names(self) -> list[str]:
        """Every recording this suite owns, for status and coverage reporting."""
        return [self.recording_name(case) for case in self.cases]

    def parametrize(self, fn: F) -> F:
        """Turn one test body into one test per case.

        The body is called as ``fn(take, case, **fixtures)``. Any parameter it
        declares beyond those two is left in the generated signature, so pytest
        still supplies ordinary fixtures such as ``tmp_path``.
        """
        import pytest

        original = inspect.signature(fn)
        declared = list(original.parameters.values())
        if len(declared) < 2:
            raise ConfigError(
                f"the body of suite {self.name!r} must accept (take, case); "
                f"{fn.__name__}{original} does not. The take is the open recording "
                f"and the case is the row from the table."
            )
        passthrough = declared[2:]

        def wrapper(**kwargs: Any) -> Any:
            case_name = kwargs.pop(_PARAM)
            payload = self.cases[case_name]
            with take(self.recording_name(case_name), **self.take_options) as opened:
                result = fn(opened, payload, **kwargs)
            # After the take closes, so the trajectory is complete. A check that
            # ran inside would be asserting against a half-finished run.
            if self.check is not None:
                self.check(opened)
            return result

        # Not functools.wraps: it sets __wrapped__, and pytest follows that when
        # it looks for fixtures. It would see the body's own (take, case) and
        # try to resolve fixtures by those names, which do not exist.
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__module__ = fn.__module__
        wrapper.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [
                inspect.Parameter(_PARAM, inspect.Parameter.KEYWORD_ONLY),
                *[p.replace(kind=inspect.Parameter.KEYWORD_ONLY) for p in passthrough],
            ]
        )

        marked = pytest.mark.parametrize(_PARAM, list(self.cases), ids=list(self.cases))(wrapper)
        return marked  # type: ignore[no-any-return]

    def __repr__(self) -> str:
        return f"Suite({self.name!r}, {len(self.cases)} cases)"
