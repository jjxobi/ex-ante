import pytest

SAMPLE_HEADER = (
    "publicid,eventtype,origintime,modificationtime,longitude,latitude,magnitude,"
    "depth,magnitudetype,depthtype,evaluationmethod,evaluationstatus,evaluationmode,"
    "earthmodel,usedphasecount,usedstationcount,magnitudestationcount,minimumdistance,"
    "azimuthalgap,originerror,magnitudeuncertainty"
)

SAMPLE_ROWS = [
    (
        "2026p083320,earthquake,2026-01-31T19:53:16.616Z,2026-03-02T21:59:29.607Z,"
        "177.6536407470703,-37.31378936767578,3.213517159669955,35.041107177734375,"
        "MLv,,LOCSAT,confirmed,manual,iasp91,52,35,23,0.45,186.14,0.56,0.22"
    ),
    (
        "2026p083039,earthquake,2026-01-31T17:23:39.040Z,2026-03-02T21:20:11.603Z,"
        "-179.5,-44.46416473388672,3.159493173743447,5,"
        "MLv,operator assigned,LOCSAT,confirmed,manual,iasp91,42,30,13,0.36,39.58,0.68,0.17"
    ),
]


@pytest.fixture
def sample_csv() -> str:
    return SAMPLE_HEADER + "\n" + "\n".join(SAMPLE_ROWS) + "\n"
