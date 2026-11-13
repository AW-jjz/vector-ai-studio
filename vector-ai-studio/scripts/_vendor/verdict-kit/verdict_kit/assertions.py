"""Assertion harness with a self-counting guard.

The pattern this encodes is the one the original skill learned the hard way:

* an assertion is three-valued: ``True`` pass, ``False`` fail, ``None`` not
  evaluated because an optional capability is absent;
* a skip is only honest for an id declared in ``capability_skips``;
* the suite counts itself, so deleting or duplicating an assertion turns the
  count guard red instead of silently shrinking coverage;
* ids must be unique -- counting alone misses "delete one, duplicate another".

The expected count is injected. It is never a constant in this library.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Assertion:
    id: str
    ok: bool | None
    detail: str = ""
    skipped: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "ok": self.ok, "detail": self.detail,
                "skipped": self.skipped}


@dataclass
class HarnessReport:
    status: str                 # ok | partial | fail
    passed: int
    failed: list[str]
    skipped: list[str]
    assertions: list[Assertion]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only for a complete run. A partial run is not a pass."""
        return self.status == "ok"

    @property
    def acceptable(self) -> bool:
        """Not failed: a partial run may still be shippable with disclosure."""
        return self.status != "fail"

    @property
    def exit_code(self) -> int:
        # 1 = a guard failed; 3 = some guard could not run. Never 0 for partial.
        return 1 if self.failed else (3 if self.skipped else 0)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "passed": self.passed,
            "failed": len(self.failed),
            "skipped": list(self.skipped),
            "partial": bool(self.skipped),
            "assertions": [a.as_dict() for a in self.assertions],
            "problems": list(self.problems),
        }


class Harness:
    """Collect assertions, then finalize with two meta-assertions.

    ``expected`` is the number a complete run must leave behind, INCLUDING the
    two meta-assertions when they are enabled. That is exactly the contract
    A60 enforces; the caller owns the number and must bump it when adding a
    guard.
    """

    def __init__(
        self,
        expected: int,
        capability_skips=(),
        *,
        count_id: str = "A60-assertion-count-declared",
        skip_id: str = "A76-capability-skips-are-declared",
        emit_skip_guard: bool = True,
    ):
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
            raise ValueError("expected must be a positive integer")
        self.expected = expected
        self.capability_skips = frozenset(str(x) for x in capability_skips)
        self.count_id = count_id
        self.skip_id = skip_id
        self.emit_skip_guard = bool(emit_skip_guard)
        self._items: list[Assertion] = []
        self._final: HarnessReport | None = None

    def add(self, aid: str, ok, detail: str = "", skipped: bool = False) -> None:
        if self._final is not None:
            raise RuntimeError("cannot add assertions after finalize()")
        if not str(aid).strip():
            raise ValueError("assertion id must not be empty")
        state = None if skipped else bool(ok)
        self._items.append(Assertion(str(aid), state, str(detail)[:220], bool(skipped)))

    def finalize(self) -> HarnessReport:
        if self._final is not None:
            raise RuntimeError("finalize() already called")

        observed = list(self._items)
        extra_skips = sorted({a.id for a in observed if a.skipped}
                             - self.capability_skips)
        problems: list[str] = []
        meta: list[Assertion] = []

        if self.emit_skip_guard:
            meta.append(Assertion(
                self.skip_id, not extra_skips,
                "skipped=" + (",".join(a.id for a in observed if a.skipped) or "none")
                + (" undeclared=" + ",".join(extra_skips) if extra_skips else "")))
            if extra_skips:
                problems.append("undeclared capability skips: " + ",".join(extra_skips))

        ids = [a.id for a in observed] + [m.id for m in meta]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        ran = len(observed) + len(meta) + 1  # + the count guard itself
        count_ok = ran == self.expected and not duplicates
        count_detail = "declared=" + str(self.expected) + " ran=" + str(ran)
        if duplicates:
            count_detail += " duplicated=" + ",".join(duplicates)
        meta.append(Assertion(self.count_id, count_ok, count_detail))
        if not count_ok:
            problems.append(
                "assertion count mismatch: declared=" + str(self.expected)
                + " ran=" + str(ran)
                + (" duplicated=" + ",".join(duplicates) if duplicates else ""))

        all_items = observed + meta
        failed = [a.id for a in all_items if a.ok is False]
        skipped = [a.id for a in all_items if a.skipped]
        status = "fail" if failed else ("partial" if skipped else "ok")
        self._final = HarnessReport(
            status=status,
            passed=sum(1 for a in all_items if a.ok is True),
            failed=failed,
            skipped=skipped,
            assertions=all_items,
            problems=problems,
        )
        return self._final
