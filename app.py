import math
import time
from html import escape

import pandas as pd
import streamlit as st

st.set_page_config(page_title="HF-SMDG V3.2", page_icon="🌧️", layout="wide")

st.markdown(
    """
    <style>
    .stApp {
        background:
            radial-gradient(circle at 72% 0%, rgba(22, 104, 121, .12), transparent 30%),
            #080d14;
    }
    [data-testid="stSidebar"] {
        background: #111722;
        border-right: 1px solid #273342;
    }
    [data-testid="stMetric"] {
        background: linear-gradient(180deg, #111a26, #0d141e);
        border: 1px solid #27384b;
        border-radius: 12px;
        padding: 12px 14px;
    }
    [data-testid="stMetricLabel"] { color: #93a7bc; }
    [data-testid="stMetricValue"] { color: #edf7ff; }
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 4rem;
        max-width: 1500px;
    }
    .hf-kicker {
        color: #57d8df;
        font-size: .76rem;
        font-weight: 800;
        letter-spacing: .16em;
        text-transform: uppercase;
        margin-bottom: .2rem;
    }
    .hf-sub {
        color: #93a7bc;
        margin-top: -.55rem;
        margin-bottom: 1rem;
    }
    .cause-strip {
        display:grid;
        grid-template-columns:repeat(6,minmax(0,1fr));
        gap:8px;
        margin:8px 0 16px 0;
    }
    .cause-card {
        background:#0d1621;
        border:1px solid #24374a;
        border-radius:10px;
        padding:9px 8px;
        min-height:66px;
    }
    .cause-num {
        color:#5fe3e6;
        font-size:.70rem;
        font-weight:900;
    }
    .cause-title {
        color:#eff7ff;
        font-size:.80rem;
        font-weight:800;
        margin-top:3px;
    }
    .cause-desc {
        color:#8fa3b7;
        font-size:.67rem;
        margin-top:2px;
        line-height:1.25;
    }
    .event-card {
        background:#0d1520;
        border:1px solid #253548;
        border-radius:9px;
        padding:8px 11px;
        margin-bottom:6px;
        color:#dbe8f5;
        font-size:.82rem;
    }
    .model-note {
        color:#94a7ba;
        font-size:.77rem;
        line-height:1.4;
        background:#0d1520;
        border-left:3px solid #51d1d8;
        padding:8px 10px;
        border-radius:6px;
    }
    @media (max-width:1000px) {
        .cause-strip { grid-template-columns:repeat(3,minmax(0,1fr)); }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# Simplified conceptual model
# -----------------------------
G = 9.81
CD = 0.62
ZONE_OPEN_AREA_M2 = 0.0100
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
        "last_redirect_log_s": -999.0,
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
        if (
            moved_zones
            and state["time_s"] - state.get("last_redirect_log_s", -999.0) >= 10.0
        ):
            log_event(
                state,
                f"Active Zone(s) {', '.join(moved_zones)} are redirecting floating debris toward the bay."
            )
            state["last_redirect_log_s"] = state["time_s"]
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


def louver_line(x, y, angle, active, fixed=False):
    length = 42
    r = math.radians(angle)
    x2 = x + length * math.cos(r)
    y2 = y - length * math.sin(r)
    cls = "fixed-bar" if fixed else ("louver active" if active else "louver")
    return f'<line class="{cls}" x1="{x:.1f}" y1="{y}" x2="{x2:.1f}" y2="{y2:.1f}" />'


def leaf_svg(x, y, rotation=0, moving=False):
    cls = "leaf moving" if moving else "leaf"
    return f"""
    <g class="{cls}" transform="translate({x} {y}) rotate({rotation})">
      <path d="M0,0 C7,-7 16,-6 19,0 C14,8 6,9 0,0 Z"/>
      <line x1="3" y1="1" x2="16" y2="-1"/>
    </g>
    """


def bag_svg(x, y, moving=False):
    cls = "bag moving" if moving else "bag"
    return f"""
    <g class="{cls}" transform="translate({x} {y}) scale(.62)">
      <path d="M0,5 L3,0 L8,4 L13,0 L17,5 L15,22 L2,22 Z"/>
      <path d="M4,5 C5,1 7,1 8,5"/>
      <path d="M10,5 C11,1 13,1 14,5"/>
    </g>
    """


def debris_svg(x0, blockage, active, seed):
    count = min(10, int(blockage / 7.0))
    out = []
    for i in range(count):
        x = x0 + 18 + ((i * 39 + seed * 13) % 137)
        y = 87 + ((i * 17 + seed * 9) % 38)
        if i % 4 == 3:
            out.append(bag_svg(x, y, active))
        else:
            out.append(leaf_svg(x, y, ((i * 29) % 46) - 23, active))
    return "".join(out)


def scene_html(state, drain_type):
    zones = state["zones"]
    max_depth = max(z["depth_m"] for z in zones)
    frac = min(max_depth / MAX_DISPLAY_DEPTH_M, 1.0)
    water_h = 92 * frac
    water_y = 158 - water_h
    flood_y = 158 - 92 * min(FLOOD_THRESHOLD_M / MAX_DISPLAY_DEPTH_M, 1.0)

    xs = [62, 258, 454]
    bay_x = 654
    grate_y = 151
    is_fixed = drain_type == "Fixed Grate"

    pieces, louvers, floats, labels, arrows = [], [], [], [], []

    for idx, (z, x0) in enumerate(zip(zones, xs)):
        pieces.append(debris_svg(x0, z["blockage"], z["float_active"], idx))

        for j in range(4):
            louvers.append(
                louver_line(
                    x0 + 24 + j * 32,
                    grate_y,
                    z["angle"],
                    z["float_active"],
                    fixed=is_fixed,
                )
            )

        if is_fixed:
            floats.append(
                f'<text class="no-actuator" x="{x0+82}" y="226" text-anchor="middle">'
                f'NO FLOAT / ACTUATOR</text>'
            )
        else:
            fy = 205 if z["float_active"] else 233
            floats.append(
                f'<line class="linkage" x1="{x0+82}" y1="155" x2="{x0+82}" y2="{fy-14}" />'
                f'<circle class="float {"raised" if z["float_active"] else ""}" cx="{x0+82}" cy="{fy}" r="12" />'
                f'<text class="float-label {"raised-text" if z["float_active"] else ""}" '
                f'x="{x0+82}" y="{fy+29}" text-anchor="middle">'
                f'FLOAT: {"RAISED" if z["float_active"] else "DOWN"}</text>'
            )

        mode = "FIXED" if is_fixed else ("ACTIVE" if z["float_active"] else "NORMAL")
        labels.append(
            f'<text class="zone-name" x="{x0+8}" y="181">ZONE {z["name"]}</text>'
            f'<text class="zone-status {"status-active" if z["float_active"] else ""}" '
            f'x="{x0+8}" y="198">{mode} · {z["angle"]}° · {z["blockage"]:.0f}% blocked</text>'
        )

        if z["float_active"] and not is_fixed:
            arrows.append(
                f'<path class="redirect" d="M {x0+115} 102 C {x0+160} 70, 625 70, 685 102"/>'
            )

    bay_count = min(12, int(state["debris_bay"] / 2.8))
    bay_bits = []
    for i in range(bay_count):
        x = bay_x + 17 + (i % 3) * 34
        y = 116 + (i // 3) * 19
        if i % 4 == 3:
            bay_bits.append(bag_svg(x, y, False))
        else:
            bay_bits.append(leaf_svg(x, y, (i * 27) % 46 - 23, False))

    explanation = escape(current_explanation(state, drain_type))
    system_tag = "PASSIVE ADAPTIVE LOUVERS" if not is_fixed else "NON-ADAPTIVE REFERENCE"

    return f"""
    <style>
      .hfscene-root {{
        margin:0;
        background:transparent;
        color:#eaf4ff;
        font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        width:100%;
      }}
      .hfscene-root .panel {{
        background:linear-gradient(180deg,#0c1520,#09111a);
        border:1px solid #26394c;border-radius:15px;padding:12px;
        box-shadow:0 12px 35px rgba(0,0,0,.18);overflow:hidden;
      }}
      .hfscene-root .top {{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}}
      .hfscene-root .title {{font-size:15px;font-weight:900;letter-spacing:.02em}}
      .hfscene-root .badges {{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}
      .hfscene-root .badge {{
        font-size:10px;font-weight:800;letter-spacing:.04em;padding:5px 8px;border-radius:999px;
        border:1px solid #28556a;background:#10242d;color:#bfeaf4
      }}
      .hfscene-root .badge.secondary {{border-color:#4b415f;background:#211c2b;color:#dfd3ef}}
      .hfscene-root svg {{
        width:100%;height:350px;background:linear-gradient(180deg,#111b28,#0d1722);
        border:1px solid #24384b;border-radius:11px
      }}
      .hfscene-root .road {{fill:#373f4b}} .hfscene-root .curb {{fill:#737d89}}
      .hfscene-root .lane {{stroke:#79838e;stroke-width:2;stroke-dasharray:20 14;opacity:.6}}
      .hfscene-root .water {{fill:url(#waterGrad);opacity:.82}}
      .hfscene-root .surface {{stroke:#66d9ef;stroke-width:2;stroke-dasharray:8 6}}
      .hfscene-root .flood {{stroke:#f3b94e;stroke-width:1.5;stroke-dasharray:6 5}}
      .hfscene-root .small {{fill:#a0b4c8;font-size:10px}} .hfscene-root .floodtext {{fill:#f2c875;font-size:10px;font-weight:800}}
      .hfscene-root .zone {{fill:#122130;stroke:#34536a;stroke-width:1.2}} .hfscene-root .bay {{fill:#2b2218;stroke:#7b5c38;stroke-width:1.2}}
      .hfscene-root .fixed-bar {{stroke:#9aa6b2;stroke-width:8;stroke-linecap:round}}
      .hfscene-root .louver {{stroke:#78dbe0;stroke-width:8;stroke-linecap:round}}
      .hfscene-root .louver.active {{stroke:#ffbe4a;filter:drop-shadow(0 0 5px rgba(255,190,74,.38))}}
      .hfscene-root .linkage {{stroke:#7f92a6;stroke-width:3}}
      .hfscene-root .float {{fill:#607489;stroke:#b0c2d2;stroke-width:2}}
      .hfscene-root .float.raised {{fill:#ffb83d;stroke:#ffe1a0;filter:drop-shadow(0 0 4px rgba(255,184,61,.4))}}
      .hfscene-root .float-label {{fill:#8497aa;font-size:8px;font-weight:800}}
      .hfscene-root .raised-text {{fill:#ffc55e}}
      .hfscene-root .no-actuator {{fill:#8493a2;font-size:7.5px;font-weight:800;letter-spacing:.06em}}
      .hfscene-root .leaf path {{fill:#8f8a3f;stroke:#c6bd67;stroke-width:.8}}
      .hfscene-root .leaf line {{stroke:#5e612e;stroke-width:1}}
      .hfscene-root .bag path {{fill:#c7d0d8;stroke:#eef4f8;stroke-width:1;opacity:.96}}
      .hfscene-root .moving {{animation:debrisPulse 1.2s ease-in-out infinite alternate}}
      @keyframes debrisPulse {{from{{opacity:.72}} to{{opacity:1}}}}
      .hfscene-root .zone-name {{fill:#edf6ff;font-size:13px;font-weight:900}}
      .hfscene-root .zone-status {{fill:#92a6b9;font-size:9px;font-weight:800}} .hfscene-root .status-active {{fill:#ffc85d}}
      .hfscene-root .redirect {{
        fill:none;stroke:#f2b548;stroke-width:2;stroke-dasharray:7 5;marker-end:url(#amber);
        animation:dashMove 1.05s linear infinite
      }}
      @keyframes dashMove {{to{{stroke-dashoffset:-24}}}}
      .hfscene-root .flow {{fill:none;stroke:#85def1;stroke-width:3;marker-end:url(#blue)}}
      .hfscene-root .caption {{
        display:grid;grid-template-columns:115px 1fr;gap:8px;margin-top:10px;
        background:#0e1c28;border:1px solid #223a4d;border-left:4px solid #55d3dd;
        border-radius:7px;padding:10px 11px;font-size:12px;line-height:1.45;min-height:40px
      }}
      .hfscene-root .caption strong {{color:#7ee4ea}} .hfscene-root .caption span {{color:#d7e7f4}}
      .hfscene-root .baynote {{fill:#c6a678;font-size:8px}}
    </style>
    <div class="hfscene-root">
    <div class="panel">
      <div class="top">
        <div class="title">LIVE DRAIN SIMULATION</div>
        <div class="badges">
          <span class="badge">CURRENT TEST: {escape(drain_type)}</span>
          <span class="badge secondary">{system_tag}</span>
        </div>
      </div>
      <svg viewBox="0 0 825 325">
        <defs>
          <linearGradient id="waterGrad" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="#237fa4"/><stop offset="100%" stop-color="#124b69"/>
          </linearGradient>
          <marker id="amber" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L0,6 L9,3 z" fill="#f2b548"/>
          </marker>
          <marker id="blue" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
            <path d="M0,0 L0,6 L9,3 z" fill="#85def1"/>
          </marker>
        </defs>

        <rect class="road" x="0" y="0" width="825" height="73"/>
        <line class="lane" x1="80" y1="38" x2="745" y2="38"/>
        <rect class="curb" x="0" y="63" width="825" height="10"/>
        <text class="small" x="18" y="22">ROAD / GUTTER RUNOFF</text>
        <path class="flow" d="M205 22 L600 22"/>
        <text class="small" x="353" y="13">SURFACE FLOW → COLLECTION SIDE</text>

        <rect class="water" x="0" y="{water_y:.1f}" width="825" height="{water_h:.1f}"/>
        <line class="surface" x1="0" y1="{water_y:.1f}" x2="825" y2="{water_y:.1f}"/>
        <line class="flood" x1="0" y1="{flood_y:.1f}" x2="825" y2="{flood_y:.1f}"/>
        <text class="floodtext" x="8" y="{max(84,flood_y-5):.1f}">8 cm FLOOD THRESHOLD</text>

        <rect class="zone" x="62" y="105" width="166" height="147" rx="8"/>
        <rect class="zone" x="258" y="105" width="166" height="147" rx="8"/>
        <rect class="zone" x="454" y="105" width="166" height="147" rx="8"/>
        <rect class="bay" x="{bay_x}" y="105" width="132" height="147" rx="8"/>

        {''.join(pieces)}{''.join(louvers)}{''.join(floats)}{''.join(labels)}{''.join(arrows)}

        <text class="zone-name" x="{bay_x+10}" y="181">DEBRIS BAY</text>
        <text class="zone-status" x="{bay_x+10}" y="198">{state['debris_bay']:.1f} blockage-pp redirected</text>
        <text class="baynote" x="{bay_x+10}" y="232">debris stays accessible</text>
        <text class="baynote" x="{bay_x+10}" y="243">above the drainage pipe</text>
        {''.join(bay_bits)}

        <path class="flow" d="M145 258 L145 300"/>
        <path class="flow" d="M341 258 L341 300"/>
        <path class="flow" d="M537 258 L537 300"/>
        <text class="small" x="286" y="316">WATER → STORM DRAIN</text>
      </svg>
      <div class="caption"><strong>WHAT'S HAPPENING</strong><span>{explanation}</span></div>
    </div>
    </div>
    """


# -----------------------------
# Session state
# -----------------------------
if "sim_v32" not in st.session_state:
    st.session_state.sim_v32 = initial_state()

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

st.markdown(
    '<div class="hf-kicker">GED104 · CONCEPTUAL SOFTWARE PROTOTYPE</div>',
    unsafe_allow_html=True,
)
st.title("HF-SMDG // Hydro-Flow Smart Micro-Drainage Gate")
st.markdown(
    '<div class="hf-sub">'
    'Segmented, float-actuated drainage louvers that respond to localized debris blockage '
    'without motors or grid electricity.'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="cause-strip">
      <div class="cause-card"><div class="cause-num">01</div><div class="cause-title">Debris arrives</div><div class="cause-desc">Leaves and plastic reduce usable inlet area.</div></div>
      <div class="cause-card"><div class="cause-num">02</div><div class="cause-title">Water backs up</div><div class="cause-desc">Local drainage falls below incoming runoff.</div></div>
      <div class="cause-card"><div class="cause-num">03</div><div class="cause-title">Float rises</div><div class="cause-desc">That zone triggers at 4 cm local water depth.</div></div>
      <div class="cause-card"><div class="cause-num">04</div><div class="cause-title">Louvers rotate</div><div class="cause-desc">The zone changes from 20° to 50°.</div></div>
      <div class="cause-card"><div class="cause-num">05</div><div class="cause-title">Debris shifts</div><div class="cause-desc">Floating blockage is modeled toward the side bay.</div></div>
      <div class="cause-card"><div class="cause-num">06</div><div class="cause-title">Flow recalculates</div><div class="cause-desc">More usable opening can drain more water.</div></div>
    </div>
    """,
    unsafe_allow_html=True,
)

b1,b2,b3,b4 = st.columns(4)
with b1:
    if st.button("▶ Start Simulation", use_container_width=True, type="primary"):
        st.session_state.sim_v32["running"] = True
with b2:
    if st.button("⏸ Pause", use_container_width=True):
        st.session_state.sim_v32["running"] = False
with b3:
    if st.button("⏭ Step +1 s", use_container_width=True):
        st.session_state.sim_v32["running"] = False
        step(st.session_state.sim_v32, rain, debris, distribution, drain_type, 1.0)
with b4:
    if st.button("↺ Reset", use_container_width=True):
        st.session_state.sim_v32 = initial_state(); st.rerun()

state = st.session_state.sim_v32
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
m5.metric("Redirected Debris", f"{state['debris_bay']:.1f} pp", help="Cumulative blockage percentage-points redirected from active zones toward the debris bay.")

if stat == "FLOOD RISK": st.error(f"**STATUS: {stat}** — {detail}")
elif stat == "ADAPTIVE RESPONSE": st.warning(f"**STATUS: {stat}** — {detail}")
else: st.info(f"**STATUS: {stat}** — {detail}")

left,right = st.columns([3.25,1.15])
with left:
    # Inline HTML/SVG avoids recreating an iframe on every Streamlit rerun.
    # It also lets the explanation panel use its natural height.
    st.html(scene_html(state, drain_type))
with right:
    st.subheader("Zone Inspector")
    for z in zones:
        if drain_type == "Fixed Grate":
            icon = "⚪"
            mode = "FIXED"
            float_state = "Not present"
        else:
            icon = "🟠" if z["float_active"] else "🟢"
            mode = "ACTIVE" if z["float_active"] else "NORMAL"
            float_state = "RAISED" if z["float_active"] else "DOWN"

        st.markdown(
            f"**{icon} Zone {z['name']} — {mode}**  \n"
            f"Water depth: **{z['depth_m']*100:.2f} cm**  \n"
            f"Blockage: **{z['blockage']:.1f}%**  \n"
            f"Effective opening: **{100-z['blockage']:.1f}%**  \n"
            f"Louver angle: **{z['angle']}°**  \n"
            f"Float: **{float_state}**"
        )
        if drain_type == "HF-SMDG" and z["float_active"]:
            st.caption("Local trigger reached → this zone is responding.")
        st.markdown("---")
    st.caption("Activation: 4 cm · Reset: 2 cm · Flood threshold: 8 cm")

st.subheader("Live Event Log")
for e in reversed(state["events"][-6:]):
    st.markdown(
        f'<div class="event-card">{escape(e)}</div>',
        unsafe_allow_html=True,
    )

st.subheader("Water Depth Over Time")
if state["history"]:
    hist = pd.DataFrame(state["history"]).set_index("time_s")
    st.line_chart(hist[["average_depth_cm","max_local_depth_cm"]], y_label="Water depth (cm)", x_label="Simulation time (s)")
else:
    st.info("Press Start Simulation or Step +1 s to generate the water-depth history.")

st.subheader("Controlled Comparison // Same Storm, Same Debris")
duration = st.slider("Comparison duration (simulated seconds)", 60, 600, 180, 30)
st.markdown(
    '<div class="model-note">'
    'This test uses identical rainfall, debris level, debris distribution, and duration for both systems. '
    'The only modeled difference is whether the drain stays fixed or uses the HF-SMDG adaptive-zone response.'
    '</div>',
    unsafe_allow_html=True,
)
if st.button("Run Fixed Grate vs HF-SMDG Comparison", use_container_width=True):
    fixed = run_scenario(duration, rain, debris, distribution, "Fixed Grate")
    smart = run_scenario(duration, rain, debris, distribution, "HF-SMDG")
    summary = pd.DataFrame([
        {"System":"Fixed Grate","Max water depth (cm)":fixed["max_depth"]*100,"Final avg blockage (%)":sum(z["blockage"] for z in fixed["zones"])/3,"Total drained (L)":fixed["total_drained"],"Time above flood threshold (s)":fixed["seconds_above_flood"],"Debris redirected (blockage-pp)":fixed["debris_bay"]},
        {"System":"HF-SMDG","Max water depth (cm)":smart["max_depth"]*100,"Final avg blockage (%)":sum(z["blockage"] for z in smart["zones"])/3,"Total drained (L)":smart["total_drained"],"Time above flood threshold (s)":smart["seconds_above_flood"],"Debris redirected (blockage-pp)":smart["debris_bay"]},
    ])
    st.dataframe(summary, use_container_width=True, hide_index=True)

    fixed_peak = fixed["max_depth"] * 100
    smart_peak = smart["max_depth"] * 100

    if smart_peak < fixed_peak:
        comparison_text = (
            f"In this conceptual run, HF-SMDG produced a lower modeled peak water depth "
            f"({smart_peak:.2f} cm) than the fixed grate ({fixed_peak:.2f} cm), while redirecting "
            f"{smart['debris_bay']:.1f} blockage-percentage-points toward the side bay."
        )
    elif smart_peak > fixed_peak:
        comparison_text = (
            f"In this conceptual run, HF-SMDG produced a higher modeled peak water depth "
            f"({smart_peak:.2f} cm) than the fixed grate ({fixed_peak:.2f} cm)."
        )
    else:
        comparison_text = (
            f"In this conceptual run, both systems reached the same modeled peak water depth "
            f"({smart_peak:.2f} cm)."
        )

    st.info(
        comparison_text
        + " These outputs demonstrate model behavior and are not field-test performance claims."
    )
    fh = pd.DataFrame(fixed["history"])[["time_s","max_local_depth_cm"]].rename(columns={"max_local_depth_cm":"Fixed Grate"})
    sh = pd.DataFrame(smart["history"])[["time_s","max_local_depth_cm"]].rename(columns={"max_local_depth_cm":"HF-SMDG"})
    st.line_chart(fh.merge(sh,on="time_s").set_index("time_s"), y_label="Maximum local water depth (cm)", x_label="Simulation time (s)")
    st.caption("Identical input conditions are used for both systems; differences come from the drain-geometry rules in the model.")

with st.expander("How the simulation works — and what it does NOT claim"):
    st.markdown(r'''**Model chain:** Rainfall → surface water → debris blockage → smaller effective opening → local water buildup.

For HF-SMDG, a zone reaching **4 cm** raises its float and rotates that zone from **20° to 50°**. The conceptual model then transfers some floating debris toward the side bay and recalculates drainage.

The drainage estimate uses **Q = Cd × A × √(2gh)**, with effective opening area reduced by blockage.

The **redirected debris** value is recorded as cumulative blockage **percentage-points (pp)** removed from active inlet zones and conceptually placed in the side debris bay. It is not kilograms or a measured real-world mass.

**Important:** this is not CFD and is not field-calibrated. Debris arrival and debris redistribution are simplified transparent model parameters used to demonstrate the invention's operating logic.''')

st.caption("HF-SMDG V3.2 · Educational conceptual simulation only · Simplified hydraulic assumptions · Not field-validated engineering performance.")

if state["running"]:
    # Keep the visual refresh rate stable instead of rebuilding the page
    # faster and faster as simulation speed increases.
    REFRESH_INTERVAL_S = 0.33
    time.sleep(REFRESH_INTERVAL_S)

    # Advance simulated time according to the selected speed.
    simulated_dt = REFRESH_INTERVAL_S * float(speed)
    step(state, rain, debris, distribution, drain_type, simulated_dt)

    st.rerun()
