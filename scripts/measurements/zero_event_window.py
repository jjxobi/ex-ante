"""What does a consistency test return for a window with no observed events?

pyCSEP's poisson_evaluations line 643 computes log(forecast_data * scale) where
scale = n_obs / n_fore. With zero observed events scale is 0, so every bin
becomes log(0), which is negative infinity. Line 497 divides by a forecast
standard deviation that degenerates when there are too few events.

This is not an exotic case. The daily forecast expects roughly 1.6 in-region
shallow events, so a Poisson draw gives zero on about 20 percent of days. Every
one of those days would go through this path.

The project already accepts that quiet windows carry little information; D6 and
the accepted-risks section say so plainly. Carrying little information is not
the same as returning a number that is silently meaningless, and the difference
has to be visible on a scoreboard.
"""

import datetime as dt
import math
import sys
import warnings

sys.path.insert(0, "src")
from eq import baseline, expander, paths, score, storage  # noqa: E402


def describe(value) -> str:
    if value is None:
        return "None"
    if isinstance(value, (tuple, list)):
        return "(" + ", ".join(describe(v) for v in value) + ")"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "-inf" if value < 0 else "+inf"
        return f"{value:.6g}"
    return str(value)


def main() -> int:
    fixture = storage.read_parquet(
        paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
    )
    start = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=7)

    for stratum in ("shallow", "deep"):
        fitted = baseline.fit(fixture, stratum)
        separable = baseline.forecast(fitted, start, end)
        dense = expander.expand(separable)
        expected = sum(separable["rates"].values())

        print("=" * 66)
        print(f"{stratum}: window with ZERO observed events")
        print("=" * 66)
        print(f"  expected count: {expected:.4f}")
        print(f"  probability of observing zero: {math.exp(-expected):.3%}")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = score.score(dense, [], start, end)
        messages = {str(w.message) for w in caught}

        for name in ("n_test", "s_test", "m_test", "l_test"):
            test = getattr(result, name)
            print(
                f"  {name:<8} statistic={describe(test.observed_statistic):>12}"
                f"   quantile={describe(test.quantile)}"
            )
        if messages:
            print("  warnings raised:")
            for message in sorted(messages):
                print(f"    {message}")

        broken = []
        for name in ("s_test", "m_test", "l_test"):
            test = getattr(result, name)
            stat = test.observed_statistic
            if isinstance(stat, float) and (math.isnan(stat) or math.isinf(stat)):
                broken.append(name)
        print(f"  tests returning a non finite statistic: {broken or 'none'}")
        print()

    print("=" * 66)
    print("Why this matters")
    print("=" * 66)
    print("  A daily forecast expecting about 1.6 events sees zero on roughly")
    print("  20 percent of days. If those windows return non finite statistics,")
    print("  a fifth of the daily scoreboard is meaningless, and it must be")
    print("  rendered as such rather than as a number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
