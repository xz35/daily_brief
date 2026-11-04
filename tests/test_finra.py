"""
test_finra.py — offline unit tests for finra_fetcher record handling.

Run:    python tests/test_finra.py
Live:   python finra_fetcher.py --validate   (needs FINRA_API_* credentials)
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finra_fetcher import (
    _is_reportable, _looks_hy, _matches_edgar, _prune_seen, _to_deal_dict,
)


def _future(years):
    return (datetime.today() + timedelta(days=int(years * 365.25))).strftime("%Y-%m-%d")


def _recent(days_ago=5):
    return (datetime.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


BASE = {
    "cusip": "037833AK6",
    "issuerName": "APPLE INC",
    "couponRate": 4.85,
    "maturityDate": _future(10),
    "moodysRating": "Aa1",
    "standardAndPoorsRating": "AA+",
    "moodyRatingDate": _recent(3),
    "standardAndPoorsRatingDate": _recent(3),
    "traceGradeCode": "Investment Grade",
    "is144A": "N",
    "isConvertible": "N",
    "isCallable": "Y",
    "nextCallDate": _future(9.5),
    "productSubTypeCode": "CORP",
    "industryGroup": "Technology",
    "systemControlDate": _recent(1),
}


def run():
    failures = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    # Reportable happy path
    keep, why = _is_reportable(dict(BASE))
    check("fresh IG corporate is reportable", keep)

    # Rejections
    check("convertible rejected",
          not _is_reportable({**BASE, "isConvertible": "Y"})[0])
    check("agency subtype rejected",
          not _is_reportable({**BASE, "productSubTypeCode": "AGCY"})[0])
    check("matured bond rejected",
          not _is_reportable({**BASE, "maturityDate": "2020-01-01"})[0])
    check("sub-year tenor rejected",
          not _is_reportable({**BASE, "maturityDate":
                              (datetime.today() + timedelta(days=100)).strftime("%Y-%m-%d")})[0])
    check("missing coupon rejected",
          not _is_reportable({**BASE, "couponRate": None})[0])
    check("stale rating dates rejected (existing bond, not new issue)",
          not _is_reportable({**BASE, "moodyRatingDate": "2021-05-01",
                              "standardAndPoorsRatingDate": "2021-05-02"})[0])
    check("pure HY rejected",
          not _is_reportable({**BASE, "traceGradeCode": "", "moodysRating": "B1",
                              "standardAndPoorsRating": "B+"})[0])

    # HY classification
    check("Baa3/BBB- is IG", not _looks_hy("", "Baa3", "BBB-"))
    check("Ba1/BB+ is HY", _looks_hy("", "Ba1", "BB+"))
    check("split-rated Baa3/BB+ kept (crossover)", not _looks_hy("", "Baa3", "BB+"))
    check("traceGradeCode High Yield wins", _looks_hy("High Yield", "", ""))
    check("B+ is HY, BBB+ is not", _looks_hy("", "", "B+") and not _looks_hy("", "", "BBB+"))
    check("no ratings defaults to not-HY (kept; IG filter is elsewhere)",
          not _looks_hy("", "", ""))

    # 144A record is the whole point — must be reportable
    keep, _ = _is_reportable({**BASE, "is144A": "Y"})
    check("144A IG deal reportable", keep)

    # Deal mapping
    deal = _to_deal_dict({**BASE, "is144A": "Y"})
    check("mapping: issuer title-cased", deal["issuer"] == "Apple Inc")
    check("mapping: coupon formatted", deal["coupon"] == "4.85%")
    check("mapping: tenor approx 10", deal["tenor"] == "10")
    check("mapping: ratings dict", deal["ratings"] == {"moodys": "Aa1", "sp": "AA+"})
    check("mapping: 144A flagged in source", "144A" in deal["source"])
    check("mapping: size is None (not published)", deal["size"] is None)

    # EDGAR dedup
    check("dedup: multi-word issuer match",
          _matches_edgar("HCA HEALTHCARE INC", ["Hca Healthcare, Inc."]))
    check("dedup: single-word issuer match",
          _matches_edgar("META PLATFORMS INC", ["Meta Platforms, Inc."]))
    check("dedup: different issuer no match",
          not _matches_edgar("CATERPILLAR FINL SVCS", ["Hca Healthcare, Inc."]))

    # Seen-file pruning
    pruned = _prune_seen([
        {"cusip": "OLD", "first_seen": "2024-01-01"},
        {"cusip": "NEW", "first_seen": _recent(10)},
        {"cusip": "NEW", "first_seen": _recent(10)},   # duplicate
    ])
    check("prune: old dropped, duplicate collapsed",
          len(pruned) == 1 and pruned[0]["cusip"] == "NEW")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("All tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
