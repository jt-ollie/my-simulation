HF-SMDG Simulation — Version 2

Version 2 turns the V1 calculation engine into a more visual working simulation.

New in V2

Large central drain scene

Visible rising/falling water level

Three visible zones (A, B, C)

Visible float state and louver angle

Debris shown over each zone

Active-zone arrows toward a side debris bay

Plain-language “What's happening” explanation

Event log for blockage, float activation, louver movement, and reset

Fewer raw numbers in the main view

Controlled Fixed Grate vs HF-SMDG comparison retained

Recommended first demo

Rainfall: 150 mm/hr

Debris: High

Distribution: Zone A-heavy

Drain type: HF-SMDG

Speed: 4×

Watch Zone A build blockage and water depth, then activate at 4 cm.

Run locally

pip install -r requirements.txt
streamlit run app.py

Scientific limitation

This is a conceptual educational prototype, not a field-calibrated engineering model. The hydraulic relationship is real, while debris arrival and redistribution use simplified parameters for demonstrating the proposed mechanism.
