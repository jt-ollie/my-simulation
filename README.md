HF-SMDG Simulation — Version 1

This is the first functional model for the Hydro-Flow Smart Micro-Drainage Gate (HF-SMDG) GED104 prototype.

Version 1 includes

Rainfall input

Low / Medium / High debris

Zone A/B/C debris distribution

Three independent drainage zones

Simplified hydraulic flow: Q = Cd × A × sqrt(2gh)

Float activation at 4 cm and reset at 2 cm

Louver angle state

Debris redistribution to a side collection bay

Start / Pause / Step / Reset

Live water-depth graph

Fixed Grate vs HF-SMDG controlled comparison

Important limitation

This is a conceptual educational model. Some coefficients, especially debris arrival and redistribution, are intentionally simplified and are not field-calibrated.

Run

pip install -r requirements.txt

streamlit run app.py

Suggested first test

Rainfall: 120 mm/hr

Debris: High

Distribution: Zone A-heavy

Controlled comparison: 180 seconds

Version 2 can add the visual water/louver/debris animation and Figma-inspired styling.
