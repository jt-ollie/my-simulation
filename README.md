HF-SMDG Simulation — Version 3.2

V3.2 fixes the UI regression that appeared after switching the live simulation
from an iframe to inline st.html() rendering.

What caused the problem

The scene's CSS included broad selectors such as:

svg { ... }

.title { ... }

.badge { ... }

body { ... }

Inside st.html(), those styles are no longer isolated in an iframe. They can
affect Streamlit's own interface.

That is why V3.1 could show:

an enormous selectbox arrow

a giant help / question-mark icon

distorted metric cards

large blank spaces

the center simulation appearing to vanish or shift

V3.2 fix

All visual-scene CSS is now scoped under a unique .hfscene-root container.

For example:

svg { ... }

became:

.hfscene-root svg { ... }

This preserves the stable inline rendering from V3.1 without affecting the rest
of the Streamlit page.

V3.2 also:

keeps the unclipped "WHAT'S HAPPENING" panel

keeps inline rendering to avoid iframe flashing

reduces display refresh to about 3 Hz for steadier playback

preserves the V2.1/V3 simulation logic

Recommended test

Rainfall: 150 mm/hr

Debris: High

Distribution: Zone A-heavy

Drain Type: HF-SMDG

Speed: 4×

The expected behavior remains:

Zone A becomes the first locally activated zone while B and C remain normal longer.

Run

pip install -r requirements.txt
streamlit run app.py

This remains an educational conceptual simulation, not CFD or a field-validated
engineering model.
