HF-SMDG Simulation — Version 3

Version 3 is the presentation-focused build of the Hydro-Flow Smart Micro-Drainage Gate (HF-SMDG) conceptual software prototype.

V3 upgrades

polished dark dashboard

six-step cause-and-effect strip

recognizable leaf and plastic-bag debris graphics

fixed grate visually separated from HF-SMDG

fixed grate explicitly shows NO FLOAT / ACTUATOR

HF-SMDG explicitly shows FLOAT: DOWN / RAISED

active louvers highlighted in amber

animated debris-redirection arrows

clearer Zone Inspector

styled event log

controlled-comparison explanation in plain language

debris-bay value renamed to cumulative blockage percentage-points (pp)

Recommended demonstration

Use:

Rainfall: 150 mm/hr

Debris Level: High

Debris Distribution: Zone A-heavy

Drain Type: HF-SMDG

Speed: 4×

Expected sequence:

Zone A accumulates debris fastest.

Local water rises in Zone A.

At 4 cm, Float A changes DOWN → RAISED.

Zone A louvers change 20° → 50°.

Yellow dashed arrows represent conceptual debris movement toward the side bay.

Zones B and C remain normal longer.

Then reset and run the same storm using Fixed Grate.

Finally, use the Controlled Comparison section.

Run locally

pip install -r requirements.txt
streamlit run app.py

Scientific transparency

The simplified drainage relationship is:

Q = Cd × A × sqrt(2gh)

The simulation is not CFD and is not field-calibrated.

The debris-bay value is cumulative blockage percentage-points (pp) conceptually removed from active inlet zones. It is not kilograms and does not represent measured debris mass.

Present the software as a working conceptual simulation of the proposed mechanism, not as proof of real-world flood-prevention efficiency.
