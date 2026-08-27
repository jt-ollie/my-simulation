HF-SMDG Simulation — Version 3.4

V3.4 fixes the startup NameError that occurred inside the PNG renderer.

The V3.3 error

The traceback stopped at:

angle = math.atan2(...)

inside the _arrow() drawing helper.

The V3.3 source included import math, but the deployed Streamlit runtime still
reported math as undefined while rendering the image. Because the image is drawn
immediately when the page loads, the error prevented the center simulation from
appearing and also prevented normal use of the app.

V3.4 fix

The renderer no longer relies on the math module object at all.

Changes:

hydraulic math.sqrt(...) -> direct sqrt(...) import

arrow heads are calculated with vector arithmetic instead of atan2, sin, or cos

louver orientations use precomputed values for the only two model angles:

20 degrees normal

50 degrees active

This makes the PNG renderer much simpler and removes the exact failure point shown
in the Streamlit traceback.

Recommended test

Before pressing Start, the center drain picture should already be visible.

Then use:

Rainfall: 150 mm/hr

Debris: High

Distribution: Zone A-heavy

Drain Type: HF-SMDG

Speed: 4×

Expected result: Zone A should activate first while Zones B and C remain normal
longer.

Run

pip install -r requirements.txt
streamlit run app.py

The hydraulic behavior remains the tuned V2.1/V3 conceptual model. It is not CFD
and is not field-validated engineering performance.
