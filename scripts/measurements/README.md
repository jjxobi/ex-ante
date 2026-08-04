# Measurement scripts

Every number quoted in `DECISIONS.md` and in the design spec was produced by a
script in this directory, run on 2026-08-04 against GeoNet Quake Search with
bounding box `163.60840,-49.18170,182.98828,-32.28713`.

They are exploratory scripts, not pipeline code. They are committed so that any
figure in the documentation can be regenerated and checked rather than taken on
trust. They cache their downloads next to themselves, so a rerun is cheap and
does not hammer GeoNet.

Run any of them with plain Python 3.12. They have no dependencies outside the
standard library, which is deliberate: a reader should be able to check the
numbers without building an environment first.

| Script | What it establishes |
|---|---|
| `probe.py` | Depth histogram and per-stratum frequency-magnitude distributions |
| `depth.py` | Fixed-depth contamination: the 33 km, 12 km and 5 km piles |
| `verify.py` | Revision-lag shape, Mc stationarity, and the rejected variance criterion |
| `kermadec.py` | Regional completeness, the measurement that forced the exclusion |
| `kermadec_stability.py` | That Kermadec completeness swings 1.6 magnitude units over time |
| `region_mc.py` | Completeness on a spatial grid, and candidate regions priced |
| `mainland.py` | Rates and completeness after excluding the failing region |
| `freeze.py` | The pre-committed threshold rule, executed |
| `region_rule.py` | The final region rule and the Silverman-bandwidth depth boundary |

## Reading order

Start with `kermadec.py` and `kermadec_stability.py`. Those two produced the
finding that changed the design: the catalogue is not complete over a large part
of the region originally specified, and that incompleteness is not stable enough
for the usual cancellation argument to rescue it.

`verify.py` is worth reading second, because it contains the criterion that was
tried and rejected. Section D3 of `DECISIONS.md` records why.
