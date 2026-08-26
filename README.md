HF-SMDG Simulation — Version 2.1

Version 2.1 is a model-tuning update to Version 2.

Why V2.1 was needed

In Version 2, the nominal drain opening per zone was too small for the 150 mm/hr
demonstration storm. Even a nearly clean zone could not drain incoming runoff fast
enough, so Zones A, B, and C all reached the 4 cm float trigger.

That weakened the invention's most important behavior:

localized blockage → localized response

What changed

ZONE_OPEN_AREA_M2

V2: 0.0045 m²

V2.1: 0.0100 m²

At the 4 cm trigger depth, a clean zone can now drain roughly enough water to
keep pace with its share of the 150 mm/hr demonstration storm.

A heavily blocked zone loses enough effective opening area that it backs up first.

Expected V2.1 demonstration

Use:

Rainfall: 150 mm/hr

Debris Level: High

Debris Distribution: Zone A-heavy

Drain Type: HF-SMDG

Speed: 4×

Expected conceptual sequence:

Zone A accumulates debris faster than B and C.

Zone A water rises toward 4 cm.

Around the first ~1–2 simulated minutes, Zone A activates first.

Zone A's float rises and louvers rotate to 50°.

B and C remain normal for substantially longer because they are less obstructed.

Zone A redirects some floating debris to the collection bay.

The model recalculates Zone A drainage.

For a 180-second controlled comparison under these default conditions, the model
should show a clearer difference between the fixed grate and HF-SMDG.

Scientific limitation

The hydraulic opening-flow equation is a real simplified relationship:

Q = Cd × A × sqrt(2gh)

However, the chosen opening area, debris-arrival rate, and debris-redistribution
coefficient are conceptual model parameters, not field-calibrated engineering data.

V2.1 is designed to make the proposed mechanism's logic observable; it is not a
real-world performance prediction.

Run locally

pip install -r requirements.txt
streamlit run app.py
