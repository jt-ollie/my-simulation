import math
import time
from html import escape

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="HF-SMDG V2", page_icon="🌧️", layout="wide")

# -----------------------------
# Simplified conceptual model
# -----------------------------
G = 9.81
CD = 0.62
ZONE_OPEN_AREA_M2 = 0.0045
ZONE_SURFACE_AREA_M2 = 1.50
CATCHMENT_AREA_M2 = 300.0
FLOAT_TRIGGER_M = 0.040
FLOAT_RESET_M = 0.020
NORMAL_ANGLE = 20
ACTIVE_ANGLE = 50
FLOOD_THRESHOLD_M = 0.080
MAX_DISPLAY_DEPTH_M = 0.120
MAX_BLOCKAGE = 95.0

DEBRIS_RATE = {"Low": 6.0, "Medium": 14.0, "High": 28.0}
DISTRIBUTIONS = {
    "Even": [1/3, 1/3, 1/3],
    "Zone A-heavy": [0.60, 0.25, 0.15],
    "Zone B-heavy": [0.20, 0.60, 0.20],
    "Zone C-heavy": [0.15, 0.25, 0.60],
}


def make_zone(name):
    return {
        "name": name,
        "water_l": 0.0,
        "depth_m": 0.0,
        "blockage": 0.0,
        "float_active": False,
        "angle": NORMAL_ANGLE,
        "flow_lps": 0.0,
        "logged": set(),
    }


def initial_state():
    return {
        "running": False,
        "time_s": 0,
        "zones": [make_zone("A"), make_zone("B"), make_zone("C")],
        "debris_bay": 0.0,
        "total_drained": 0.0,
        "seconds_above_flood": 0,
        "max_depth": 0.0,
        "history": [],
        "events": ["00:00 — Simulation ready. Choose conditions and press Start."],
    }


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def log_event(state, text):
    state["events"].append(f"{fmt_time(state['time_s'])} — {text}")
    state["events"] = state["events"][-12:]


def rainfall_lps(rain_mm_hr):
    # 1 mm over 1 m² = 1 liter
    return rain_mm_hr * CATCHMENT_AREA_M2 / 3600.0


def depth_m(zone):
    return max(0.0, zone["water_l"] / (ZONE_SURFACE_AREA_M2 * 1000.0))


def hydraulic_flow(depth, blockage):
    # Q = Cd * A_eff * sqrt(2gh)
    if depth <= 0:
        return 0.0
    open_fraction = max(0.0, 1.0 - blockage / 100.0)
    a_eff = ZONE_OPEN_AREA_M2 * open_fraction
    return CD * a_eff * math.sqrt(2 * G * depth) * 1000.0


def update_float(zone, drain_type, state=None):
    old = zone["float_active"]
    if drain_type == "Fixed Grate":
        zone["float_active"] = False
    else:
        if (not zone["float_active"]) and zone["depth_m"] >= FLOAT_TRIGGER_M:
            zone["float_active"] = True
        elif zone["float_active"] and zone["depth_m"] <= FLOAT_RESET_M:
            zone["float_active"] = False

    zone["angle"] = ACTIVE_ANGLE if zone["float_active"] else NORMAL_ANGLE

    if state is not None and old != zone["float_active"]:
        if zone["float_active"]:
            log_event(state, f"Zone {zone['name']} reached 4 cm. Float rose and louvers rotated to {ACTIVE_ANGLE}°.")
        else:
            log_event(state, f"Zone {zone['name']} fell below 2 cm. Louvers returned to {NORMAL_ANGLE}°.")


def add_debris(state, level, distribution, rain, dt):
    base = DEBRIS_RATE[level]
    rain_factor = 0.50 + min(rain, 200) / 200.0
    total_pp_s = (base * rain_factor) / 60.0
    weights = DISTRIBUTIONS[distribution]

    for zone, weight in zip(state["zones"], weights):
        old = zone["blockage"]
        zone["blockage"] = min(MAX_BLOCKAGE, old + total_pp_s * weight * dt)
        for threshold in (30, 50, 70):
            if old < threshold <= zone["blockage"] and threshold not in zone["logged"]:
                zone["logged"].add(threshold)
                log_event(state, f"Zone {zone['name']} blockage reached {threshold}%. Usable inlet area is shrinking.")


def redirect_debris(zone, state, dt):
    if not zone["float_active"] or zone["blockage"] <= 0:
        return 0.0
    moved = min(zone["blockage"], 0.10 * max(zone["flow_lps"], 0.20) * dt)
    zone["blockage"] -= moved
    state["debris_bay"] += moved
    return moved


def step(state, rain, debris, distribution, drain_type, dt=1.0):
    inflow_each = rainfall_lps(rain) / 3.0
    add_debris(state, debris, distribution, rain, dt)

    for z in state["zones"]:
        z["depth_m"] = depth_m(z)
        update_float(z, drain_type, state)
        z["flow_lps"] = hydraulic_flow(z["depth_m"], z["blockage"])

    if drain_type == "HF-SMDG":
        moved_zones = []
        for z in state["zones"]:
            moved = redirect_debris(z, state, dt)
            if moved > 0:
                moved_zones.append(z["name"])
        if moved_zones and int(state["time_s"]) % 10 == 0:
            log_event(state, f"Active Zone(s) {', '.join(moved_zones)} are redirecting floating debris toward the bay.")
        for z in state["zones"]:
            z["flow_lps"] = hydraulic_flow(z["depth_m"], z["blockage"])

    for z in state["zones"]:
        incoming = inflow_each * dt
        outgoing = min(z["water_l"] + incoming, z["flow_lps"] * dt)
        z["water_l"] = max(0.0, z["water_l"] + incoming - outgoing)
        state["total_drained"] += outgoing
        z["depth_m"] = depth_m(z)
        update_float(z, drain_type, state)

    state["time_s"] += dt
    avg_depth = sum(z["depth_m"] for z in state["zones"]) / 3
    max_depth = max(z["depth_m"] for z in state["zones"])
    state["max_depth"] = max(state["max_depth"], max_depth)
    if max_depth >= FLOOD_THRESHOLD_M:
        state["seconds_above_flood"] += dt

    state["history"].append({
        "time_s": state["time_s"],
        "average_depth_cm": avg_depth * 100,
        "max_local_depth_cm": max_depth * 100,
        "total_flow_lps": sum(z["flow_lps"] for z in state["zones"]),
    })


def run_scenario(seconds, rain, debris, distribution, drain_type):
    s = initial_state()
    s["events"] = []
    for _ in range(seconds):
        step(s, rain, debris, distribution, drain_type, 1.0)
    return s


def current_explanation(state, drain_type):
    zones = state["zones"]
    active = [z for z in zones if z["float_active"]]
    deepest = max(zones, key=lambda z: z["depth_m"])
    blocked = max(zones, key=lambda z: z["blockage"])

    if drain_type == "Fixed Grate":
        if max(z["depth_m"] for z in zones) >= FLOOD_THRESHOLD_M:
            return f"The fixed grate cannot react. Zone {blocked['name']} is {blocked['blockage']:.0f}% blocked and local water is above the flood threshold."
        if blocked["blockage"] >= 30:
            return f"Debris is covering Zone {blocked['name']}. The grate angle stays fixed while usable opening area decreases."
        return "Rainfall and debris are entering the drain. The fixed grate remains at one angle regardless of local blockage."

    if active:
        names = ", ".join(z["name"] for z in active)
        return f"Zone(s) {names} reached the trigger. Their floats are raised, louvers are at {ACTIVE_ANGLE}°, and debris is being shifted toward the side collection bay."
    if deepest["depth_m"] >= 0.025:
        return f"Water is building most in Zone {deepest['name']}. Its louvers stay at {NORMAL_ANGLE}° until local depth reaches 4 cm."
    if blocked["blockage"] >= 20:
        return f"Debris is accumulating most in Zone {blocked['name']} ({blocked['blockage']:.0f}% blocked), but its float has not triggered yet."
    return f"Normal state: all floats are down and all louvers remain at {NORMAL_ANGLE}°."


def status(state):
    max_d = max(z["depth_m"] for z in state["zones"])
    active = sum(z["float_active"] for z in state["zones"])
    if max_d >= FLOOD_THRESHOLD_M:
        return "FLOOD RISK", "At least one zone is above the 8 cm conceptual threshold."
    if active:
        return "ADAPTIVE RESPONSE", f"{active} louver zone(s) currently active."
    if max(z["blockage"] for z in state["zones"]) >= 30:
        return "BLOCKAGE BUILDING", "Debris is reducing usable inlet area."
    return "NORMAL", "Runoff is draining without an active louver response."


def louver_line(x, y, angle, active):
    length = 42
    r = math.radians(angle)
    x2 = x + length * math.cos(r)
    y2 = y - length * math.sin(r)
    cls = "louver active" if active else "louver"
    return f'<line class="{cls}" x1="{x:.1f}" y1="{y}" x2="{x2:.1f}" y2="{y2:.1f}" />'


def debris_svg(x0, blockage, active, seed):
    count = min(8, int(blockage / 10))
    out = []
    for i in range(count):
        x = x0 + 20 + ((i * 37 + seed * 11) % 135)
        y = 90 + ((i * 19 + seed * 7) % 34)
        cls = "debris moving" if active else "debris"
        out.append(f'<rect class="{cls}" x="{x}" y="{y}" width="13" height="7" rx="2"/>')
    return "".join(out)


def scene_html(state, drain_type):
    zones = state["zones"]
    max_depth = max(z["depth_m"] for z in zones)
    frac = min(max_depth / MAX_DISPLAY_DEPTH_M, 1.0)
    water_h = 90 * frac
    water_y = 155 - water_h
    flood_y = 155 - 90 * min(FLOOD_THRESHOLD_M / MAX_DISPLAY_DEPTH_M, 1.0)
    xs = [65, 260, 455]
    bay_x = 650
    grate_y = 150

    pieces = []
    louvers = []
    floats = []
    labels = []
    arrows = []

    for idx, (z, x0) in enumerate(zip(zones, xs)):
        pieces.append(debris_svg(x0, z["blockage"], z["float_active"], idx))
        for j in range(4):
            louvers.append(louver_line(x0 + 25 + j*32, grate_y, z["angle"], z["float_active"]))
        fy = 232 if not z["float_active"] else 202
        floats.append(
            f'<line class="linkage" x1="{x0+82}" y1="154" x2="{x0+82}" y2="{fy-13}" />'
            f'<circle class="float {"raised" if z["float_active"] else ""}" cx="{x0+82}" cy="{fy}" r="12" />'
        )
        labels.append(
            f'<text class="zone-name" x="{x0+7}" y="182">ZONE {z["name"]}</text>'
            f'<text class="zone-status {"status-active" if z["float_active"] else ""}" x="{x0+7}" y="199">'
            f'{"ACTIVE" if z["float_active"] else "NORMAL"} · {z["angle"]}° · {z["blockage"]:.0f}% blocked</text>'
        )
        if z["float_active"]:
            arrows.append(f'<path class="redirect" d="M {x0+112} 100 C {x0+155} 75, 625 75, 675 100"/>')

    bay_count = min(10, int(state["debris_bay"] / 4))
    bay_bits = []
    for i in range(bay_count):
        x = bay_x + 16 + (i % 3) * 34
        y = 112 + (i // 3) * 17
        bay_bits.append(f'<rect class="baypiece" x="{x}" y="{y}" width="18" height="8" rx="2"/>')

    explanation = escape(current_explanation(state, drain_type))

    return f'''
    <html><head><style>
      body{{margin:0;background:transparent;font-family:Arial,sans-serif;color:#e9f0f8}}
      .panel{{background:#0d1520;border:1px solid #253346;border-radius:14px;padding:12px}}
      .top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
      .badge{{font-size:12px;padding:5px 9px;border:1px solid #2a5164;border-radius:999px;background:#10232d}}
      svg{{width:100%;height:330px;background:#101a27;border:1px solid #223147;border-radius:10px}}
      .road{{fill:#343b46}} .curb{{fill:#68727f}} .water{{fill:#185f82;opacity:.78}}
      .surface{{stroke:#61c5e8;stroke-width:2;stroke-dasharray:7 5}}
      .flood{{stroke:#e6a94f;stroke-width:1.5;stroke-dasharray:6 5}}
      .small{{fill:#9fb1c5;font-size:10px}} .floodtext{{fill:#e8bc75;font-size:10px}}
      .zone{{fill:#142332;stroke:#335169}} .bay{{fill:#2e251b;stroke:#755937}}
      .louver{{stroke:#79d8dd;stroke-width:7;stroke-linecap:round}} .louver.active{{stroke:#f3bf58}}
      .linkage{{stroke:#8796a8;stroke-width:3}} .float{{fill:#5f7186;stroke:#a9bbcc;stroke-width:2}}
      .float.raised{{fill:#f0b34a;stroke:#ffe0a0}} .debris{{fill:#aa7446}} .debris.moving{{fill:#d89a52}}
      .baypiece{{fill:#9d6a3e}} .zone-name{{fill:#eaf1fa;font-size:13px;font-weight:bold}}
      .zone-status{{fill:#98a9bc;font-size:10px}} .status-active{{fill:#ffc85d;font-weight:bold}}
      .redirect{{fill:none;stroke:#efb74f;stroke-width:2;stroke-dasharray:6 5;marker-end:url(#arr)}}
      .flow{{fill:none;stroke:#8bdcf2;stroke-width:3;marker-end:url(#blue)}}
      .caption{{margin-top:10px;border-left:3px solid #56c5df;background:#101e2b;padding:10px 12px;border-radius:6px;font-size:13px;line-height:1.35}}
    </style></head><body>
    <div class="panel">
      <div class="top"><b>Live Drain Simulation</b><span class="badge">CURRENT TEST: {escape(drain_type)}</span></div>
      <svg viewBox="0 0 820 305">
        <defs>
          <marker id="arr" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#efb74f"/></marker>
          <marker id="blue" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#8bdcf2"/></marker>
        </defs>
        <rect class="road" x="0" y="0" width="820" height="72"/><rect class="curb" x="0" y="62" width="820" height="10"/>
        <text class="small" x="18" y="25">ROAD / GUTTER RUNOFF</text><path class="flow" d="M205 35 L600 35"/>
        <text class="small" x="365" y="25">FLOW → DEBRIS BAY</text>
        <rect class="water" x="0" y="{water_y:.1f}" width="820" height="{water_h:.1f}"/>
        <line class="surface" x1="0" y1="{water_y:.1f}" x2="820" y2="{water_y:.1f}"/>
        <line class="flood" x1="0" y1="{flood_y:.1f}" x2="820" y2="{flood_y:.1f}"/>
        <text class="floodtext" x="8" y="{max(83,flood_y-5):.1f}">8 cm flood threshold</text>
        <rect class="zone" x="65" y="105" width="165" height="140" rx="8"/>
        <rect class="zone" x="260" y="105" width="165" height="140" rx="8"/>
        <rect class="zone" x="455" y="105" width="165" height="140" rx="8"/>
        <rect class="bay" x="650" y="105" width="130" height="140" rx="8"/>
        {''.join(pieces)}{''.join(louvers)}{''.join(floats)}{''.join(labels)}{''.join(arrows)}
        <text class="zone-name" x="662" y="182">DEBRIS BAY</text>
        <text class="zone-status" x="662" y="199">{state['debris_bay']:.1f} redirected units</text>{''.join(bay_bits)}
        <path class="flow" d="M147 253 L147 288"/><path class="flow" d="M342 253 L342 288"/><path class="flow" d="M537 253 L537 288"/>
        <text class="small" x="295" y="299">WATER TO STORM DRAIN</text>
      </svg>
      <div class="caption"><b>What's happening:</b> {explanation}</div>
    </div></body></html>
    '''


# -----------------------------
# Session state
# -----------------------------
if "sim_v2" not in st.session_state:
    st.session_state.sim_v2 = initial_state()

# -----------------------------
# Controls
# -----------------------------
st.sidebar.header("Storm & Debris Inputs")
rain = st.sidebar.slider("Rainfall Intensity (mm/hr)", 10, 200, 150, 5)
debris = st.sidebar.select_slider("Debris Level", ["Low", "Medium", "High"], value="High")
distribution = st.sidebar.selectbox("Debris Distribution", list(DISTRIBUTIONS.keys()), index=1)
drain_type = st.sidebar.radio("Drain Type", ["Fixed Grate", "HF-SMDG"], index=1)
speed = st.sidebar.select_slider("Simulation Speed", [0.5, 1, 2, 4], value=4, format_func=lambda x: f"{x}×")
st.sidebar.markdown("---")
st.sidebar.caption("Each zone has its own float. At 4 cm local water depth, that zone rotates from 20° to 50°. It resets below 2 cm.")

st.title("HF-SMDG Simulation — Version 2")
st.caption("Visual conceptual model: rainfall → debris blockage → local water buildup → float activation → louver movement → debris redistribution → drainage.")

b1,b2,b3,b4 = st.columns(4)
with b1:
    if st.button("▶ Start Simulation", use_container_width=True, type="primary"):
        st.session_state.sim_v2["running"] = True
with b2:
    if st.button("⏸ Pause", use_container_width=True):
        st.session_state.sim_v2["running"] = False
with b3:
    if st.button("⏭ Step +1 s", use_container_width=True):
        st.session_state.sim_v2["running"] = False
        step(st.session_state.sim_v2, rain, debris, distribution, drain_type, 1.0)
with b4:
    if st.button("↺ Reset", use_container_width=True):
        st.session_state.sim_v2 = initial_state(); st.rerun()

state = st.session_state.sim_v2
zones = state["zones"]
max_cm = max(z["depth_m"] for z in zones) * 100
flow = sum(z["flow_lps"] for z in zones)
active = sum(z["float_active"] for z in zones)
stat, detail = status(state)

m1,m2,m3,m4,m5 = st.columns(5)
m1.metric("Simulation Time", fmt_time(state["time_s"]))
m2.metric("Max Local Water", f"{max_cm:.2f} cm")
m3.metric("Total Drainage", f"{flow:.2f} L/s")
m4.metric("Active Zones", f"{active} / 3")
m5.metric("Debris Bay", f"{state['debris_bay']:.1f} units")

if stat == "FLOOD RISK": st.error(f"**STATUS: {stat}** — {detail}")
elif stat == "ADAPTIVE RESPONSE": st.warning(f"**STATUS: {stat}** — {detail}")
else: st.info(f"**STATUS: {stat}** — {detail}")

left,right = st.columns([3.25,1.15])
with left:
    components.html(scene_html(state, drain_type), height=435, scrolling=False)
with right:
    st.subheader("Zone Status")
    for z in zones:
        icon = "🟠" if z["float_active"] else "🟢"
        st.markdown(f"**{icon} Zone {z['name']}**  \nWater: **{z['depth_m']*100:.2f} cm**  \nBlockage: **{z['blockage']:.1f}%**  \nLouver: **{z['angle']}° {'ACTIVE' if z['float_active'] else 'NORMAL'}**  \nOpen area: **{100-z['blockage']:.1f}%**")
        st.markdown("---")
    st.caption("Trigger: 4 cm · Reset: 2 cm · Flood threshold: 8 cm")

st.subheader("What the Simulation Is Doing")
for e in reversed(state["events"][-6:]):
    st.write("•", e)

st.subheader("Water Depth Over Time")
if state["history"]:
    hist = pd.DataFrame(state["history"]).set_index("time_s")
    st.line_chart(hist[["average_depth_cm","max_local_depth_cm"]], y_label="Water depth (cm)", x_label="Simulation time (s)")
else:
    st.info("Press Start Simulation or Step +1 s to generate the water-depth history.")

st.subheader("Controlled Comparison: Same Storm, Same Debris")
duration = st.slider("Comparison duration (simulated seconds)", 60, 600, 180, 30)
if st.button("Run Fixed Grate vs HF-SMDG Comparison", use_container_width=True):
    fixed = run_scenario(duration, rain, debris, distribution, "Fixed Grate")
    smart = run_scenario(duration, rain, debris, distribution, "HF-SMDG")
    summary = pd.DataFrame([
        {"System":"Fixed Grate","Max water depth (cm)":fixed["max_depth"]*100,"Final avg blockage (%)":sum(z["blockage"] for z in fixed["zones"])/3,"Total drained (L)":fixed["total_drained"],"Time above flood threshold (s)":fixed["seconds_above_flood"],"Debris redirected (units)":fixed["debris_bay"]},
        {"System":"HF-SMDG","Max water depth (cm)":smart["max_depth"]*100,"Final avg blockage (%)":sum(z["blockage"] for z in smart["zones"])/3,"Total drained (L)":smart["total_drained"],"Time above flood threshold (s)":smart["seconds_above_flood"],"Debris redirected (units)":smart["debris_bay"]},
    ])
    st.dataframe(summary, use_container_width=True, hide_index=True)
    fh = pd.DataFrame(fixed["history"])[["time_s","max_local_depth_cm"]].rename(columns={"max_local_depth_cm":"Fixed Grate"})
    sh = pd.DataFrame(smart["history"])[["time_s","max_local_depth_cm"]].rename(columns={"max_local_depth_cm":"HF-SMDG"})
    st.line_chart(fh.merge(sh,on="time_s").set_index("time_s"), y_label="Maximum local water depth (cm)", x_label="Simulation time (s)")
    st.caption("Identical input conditions are used for both systems; differences come from the drain-geometry rules in the model.")

with st.expander("How the simulation works — and what it does NOT claim"):
    st.markdown(r'''**Model chain:** Rainfall → surface water → debris blockage → smaller effective opening → local water buildup.

For HF-SMDG, a zone reaching **4 cm** raises its float and rotates that zone from **20° to 50°**. The conceptual model then transfers some floating debris toward the side bay and recalculates drainage.

The drainage estimate uses **Q = Cd × A × √(2gh)**, with effective opening area reduced by blockage.

**Important:** this is not CFD and is not field-calibrated. Debris arrival and debris redistribution are simplified transparent model parameters used to demonstrate the invention's operating logic.''')

st.caption("Educational conceptual simulation only. Results are estimates from simplified hydraulic assumptions and are not field-validated engineering predictions.")

if state["running"]:
    time.sleep(0.45 / speed)
    step(state, rain, debris, distribution, drain_type, 1.0)
    st.rerun()
