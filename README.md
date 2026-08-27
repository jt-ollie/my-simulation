HF-SMDG Simulation — Version 3.1

Version 3.1 is a stability and layout fix for Version 3.

Fix 1 — Live center simulation no longer flashes/disappears

Version 3 used components.html(), which renders the drain scene inside a separate
iframe. Streamlit reruns the app while the simulation is playing, so the iframe was
being destroyed and recreated repeatedly.

V3.1 uses st.html() to render the SVG scene inline as part of the normal page.

Fix 2 — "WHAT'S HAPPENING" text clipping

The old iframe had a fixed height. Inline rendering allows the explanation panel
to use its natural height. V3.1 also slightly increases the SVG height and caption
padding.

Fix 3 — Smoother playback

At 4× speed, Version 3 could rerun the entire page many times per second.

V3.1 refreshes the display at a stable rate and changes how much simulated time
advances per refresh:

0.5× = slower simulated time

1× = normal

2× = twice as fast

4× = four times as fast

The V2.1/V3 conceptual hydraulic logic is otherwise preserved.

Recommended test

Rainfall: 150 mm/hr

Debris: High

Distribution: Zone A-heavy

Drain Type: HF-SMDG

Speed: 4×

Run

pip install -r requirements.txt
streamlit run app.py

This remains an educational conceptual simulation, not CFD or a field-validated
engineering model.
