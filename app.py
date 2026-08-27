from math import sqrt
import time
from html import escape
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

import pandas as pd
import streamlit as st

st.set_page_config(page_title="HF-SMDG V3.4", page_icon="🌧️", layout="wide")

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
    return CD * a_eff * sqrt(2 * G * depth) * 1000.0


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


def _font(size=20, bold=False):
    """
    Portable font helper.
    Uses common fonts when available and falls back to Pillow's default font.
    """
    candidates = []
    if bold:
        candidates.extend([
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ])
    else:
        candidates.extend([
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ])

    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _arrow(draw, start, end, color, width=4):
    """Draw a line with a triangular arrow head without using trigonometry."""
    draw.line([start, end], fill=color, width=width)

    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    length = max((dx * dx + dy * dy) ** 0.5, 1.0)

    ux = dx / length
    uy = dy / length

    # Perpendicular unit vector.
    px = -uy
    py = ux

    head_len = 13
    head_half_width = 7

    base_x = x2 - ux * head_len
    base_y = y2 - uy * head_len

    p1 = (
        base_x + px * head_half_width,
        base_y + py * head_half_width,
    )
    p2 = (
        base_x - px * head_half_width,
        base_y - py * head_half_width,
    )

    draw.polygon([(x2, y2), p1, p2], fill=color)


def _leaf(draw, x, y, scale=1.0, color=(143, 138, 63)):
    w = int(18 * scale)
    h = int(10 * scale)
    draw.ellipse([x, y, x + w, y + h], fill=color, outline=(198, 189, 103), width=1)
    draw.line(
        [(x + 3, y + h // 2), (x + w - 2, y + h // 2)],
        fill=(94, 97, 46),
        width=1,
    )


def _bag(draw, x, y, scale=1.0):
    w = int(14 * scale)
    h = int(18 * scale)
    pts = [
        (x, y + 4),
        (x + 3, y),
        (x + 6, y + 3),
        (x + 10, y),
        (x + w, y + 4),
        (x + w - 2, y + h),
        (x + 2, y + h),
    ]
    draw.polygon(pts, fill=(205, 214, 222), outline=(238, 244, 248))
    draw.arc([x + 2, y, x + 8, y + 8], 190, 350, fill=(238, 244, 248), width=1)


def render_scene_png(state, drain_type):
    """
    Render the center drain visualization to a PNG.

    Using st.image() instead of inline SVG/HTML avoids:
      - iframe flashing
      - st.html SVG sanitization
      - CSS leaking into Streamlit controls
    """
    W, H = 1200, 500
    img = Image.new("RGB", (W, H), (8, 14, 22))
    draw = ImageDraw.Draw(img)

    font_s = _font(15)
    font_sm = _font(13)
    font_m = _font(18, bold=True)
    font_l = _font(23, bold=True)

    # Palette
    border = (39, 57, 76)
    panel = (12, 22, 33)
    road = (55, 63, 75)
    curb = (113, 125, 137)
    water = (24, 103, 139)
    cyan = (120, 219, 224)
    amber = (255, 190, 74)
    text = (235, 245, 252)
    muted = (148, 166, 184)
    bay_fill = (46, 35, 24)

    # Outer panel
    draw.rounded_rectangle([5, 5, W - 5, H - 5], radius=18, fill=panel, outline=border, width=2)

    # Header
    draw.text((22, 17), "LIVE DRAIN SIMULATION", fill=text, font=font_l)
    mode_tag = f"CURRENT TEST: {drain_type}"
    tag_w = draw.textbbox((0, 0), mode_tag, font=font_s)[2] + 28
    draw.rounded_rectangle([W - tag_w - 22, 15, W - 22, 43], radius=13, fill=(16, 36, 45), outline=(40, 86, 107))
    draw.text((W - tag_w - 8, 22), mode_tag, fill=(191, 234, 244), font=font_s)

    # Road
    road_top, road_bottom = 60, 145
    draw.rectangle([22, road_top, W - 22, road_bottom], fill=road)
    draw.line([(100, 102), (W - 110, 102)], fill=(121, 132, 143), width=3)
    # dashed lane effect
    for x in range(115, W - 130, 70):
        draw.line([(x, 102), (x + 34, 102)], fill=(70, 78, 88), width=5)
    draw.rectangle([22, 132, W - 22, 145], fill=curb)
    draw.text((38, 72), "ROAD / GUTTER RUNOFF", fill=(170, 187, 202), font=font_sm)
    _arrow(draw, (360, 78), (785, 78), (133, 222, 241), 4)
    draw.text((475, 59), "SURFACE FLOW -> COLLECTION SIDE", fill=(164, 182, 198), font=font_sm)

    # Dynamic surface water
    zones = state["zones"]
    max_depth = max(z["depth_m"] for z in zones)
    frac = min(max_depth / MAX_DISPLAY_DEPTH_M, 1.0)
    max_water_height = 95
    water_h = int(max_water_height * frac)
    water_y = 235 - water_h
    draw.rectangle([22, water_y, W - 22, 235], fill=water)
    draw.line([(22, water_y), (W - 22, water_y)], fill=(102, 217, 239), width=3)

    # Flood threshold
    threshold_frac = min(FLOOD_THRESHOLD_M / MAX_DISPLAY_DEPTH_M, 1.0)
    flood_y = 235 - int(max_water_height * threshold_frac)
    for x in range(22, W - 22, 22):
        draw.line([(x, flood_y), (x + 12, flood_y)], fill=(243, 185, 78), width=2)
    draw.text((34, flood_y - 22), "8 cm FLOOD THRESHOLD", fill=(242, 200, 117), font=font_sm)

    # Zone geometry
    zone_xs = [95, 350, 605]
    zone_w = 215
    zone_top = 205
    zone_bottom = 420
    bay_x = 865
    bay_w = 210
    is_fixed = drain_type == "Fixed Grate"

    # Debris first so it visually sits around/above the grate.
    for idx, (z, x0) in enumerate(zip(zones, zone_xs)):
        count = min(10, int(z["blockage"] / 7.0))
        for i in range(count):
            px = x0 + 20 + ((i * 47 + idx * 17) % 165)
            py = 170 + ((i * 19 + idx * 11) % 45)
            if i % 4 == 3:
                _bag(draw, px, py, 0.9)
            else:
                _leaf(draw, px, py, 0.9)

    # Zones
    for idx, (z, x0) in enumerate(zip(zones, zone_xs)):
        draw.rounded_rectangle(
            [x0, zone_top, x0 + zone_w, zone_bottom],
            radius=12,
            fill=(18, 33, 48),
            outline=(52, 83, 106),
            width=2,
        )

        # Louvers
        for j in range(4):
            x1 = x0 + 32 + j * 43
            y1 = 270
            # Only two louver states exist in the conceptual model:
            # NORMAL = 20 degrees and ACTIVE = 50 degrees.
            # Precomputed cosine/sine values avoid runtime trig dependencies.
            length = 55
            if z["angle"] >= 40:
                cos_a = 0.6427876097   # cos(50 deg)
                sin_a = 0.7660444431   # sin(50 deg)
            else:
                cos_a = 0.9396926208   # cos(20 deg)
                sin_a = 0.3420201433   # sin(20 deg)

            x2 = x1 + length * cos_a
            y2 = y1 - length * sin_a
            color = (154, 166, 178) if is_fixed else (amber if z["float_active"] else cyan)
            draw.line([(x1, y1), (x2, y2)], fill=color, width=9)

        mode = "FIXED" if is_fixed else ("ACTIVE" if z["float_active"] else "NORMAL")
        mode_color = amber if z["float_active"] and not is_fixed else muted

        draw.text((x0 + 14, 298), f"ZONE {z['name']}", fill=text, font=font_m)
        draw.text(
            (x0 + 14, 326),
            f"{mode} | {z['angle']} deg | {z['blockage']:.0f}% blocked",
            fill=mode_color,
            font=font_sm,
        )

        # Float / actuator
        if is_fixed:
            draw.text((x0 + 45, 378), "NO FLOAT / ACTUATOR", fill=muted, font=font_sm)
        else:
            fy = 350 if z["float_active"] else 382
            draw.line([(x0 + zone_w // 2, 278), (x0 + zone_w // 2, fy - 18)], fill=(127, 146, 166), width=4)
            fill = amber if z["float_active"] else (96, 116, 137)
            outline = (255, 225, 160) if z["float_active"] else (176, 194, 210)
            draw.ellipse(
                [x0 + zone_w // 2 - 15, fy - 15, x0 + zone_w // 2 + 15, fy + 15],
                fill=fill,
                outline=outline,
                width=2,
            )
            fstate = "FLOAT: RAISED" if z["float_active"] else "FLOAT: DOWN"
            draw.text((x0 + 58, 400), fstate, fill=(255, 197, 94) if z["float_active"] else muted, font=font_sm)

        # Water down-arrow
        _arrow(draw, (x0 + zone_w // 2, 430), (x0 + zone_w // 2, 468), (133, 222, 241), 4)

        # Redirection arrow
        if z["float_active"] and not is_fixed:
            start = (x0 + zone_w - 10, 188)
            end = (bay_x + 35, 190)
            _arrow(draw, start, end, amber, 3)

    # Debris bay
    draw.rounded_rectangle(
        [bay_x, zone_top, bay_x + bay_w, zone_bottom],
        radius=12,
        fill=bay_fill,
        outline=(123, 92, 56),
        width=2,
    )
    draw.text((bay_x + 16, 298), "DEBRIS BAY", fill=text, font=font_m)
    draw.text(
        (bay_x + 16, 328),
        f"{state['debris_bay']:.1f} blockage-pp redirected",
        fill=(198, 166, 120),
        font=font_sm,
    )
    draw.text((bay_x + 16, 354), "Debris stays accessible", fill=(198, 166, 120), font=font_sm)
    draw.text((bay_x + 16, 375), "above the drainage pipe", fill=(198, 166, 120), font=font_sm)

    bay_count = min(12, int(state["debris_bay"] / 2.8))
    for i in range(bay_count):
        px = bay_x + 20 + (i % 4) * 40
        py = 225 + (i // 4) * 20
        if i % 4 == 3:
            _bag(draw, px, py, 0.8)
        else:
            _leaf(draw, px, py, 0.8)

    draw.text((435, 474), "WATER -> STORM DRAIN", fill=(158, 181, 201), font=font_sm)

    bio = BytesIO()
    img.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio.getvalue()


# -----------------------------
# Session state
# -----------------------------
if "sim_v34" not in st.session_state:
    st.session_state.sim_v34 = initial_state()

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
        st.session_state.sim_v34["running"] = True
with b2:
    if st.button("⏸ Pause", use_container_width=True):
        st.session_state.sim_v34["running"] = False
with b3:
    if st.button("⏭ Step +1 s", use_container_width=True):
        st.session_state.sim_v34["running"] = False
        step(st.session_state.sim_v34, rain, debris, distribution, drain_type, 1.0)
with b4:
    if st.button("↺ Reset", use_container_width=True):
        st.session_state.sim_v34 = initial_state(); st.rerun()

state = st.session_state.sim_v34
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
    st.image(
        render_scene_png(state, drain_type),
        use_container_width=True,
    )
    st.markdown(
        f'<div class="model-note"><b>WHAT\'S HAPPENING:</b> '
        f'{escape(current_explanation(state, drain_type))}</div>',
        unsafe_allow_html=True,
    )
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

st.caption("HF-SMDG V3.4 · Educational conceptual simulation only · Simplified hydraulic assumptions · Not field-validated engineering performance.")

if state["running"]:
    # Keep the visual refresh rate stable instead of rebuilding the page
    # faster and faster as simulation speed increases.
    REFRESH_INTERVAL_S = 0.40
    time.sleep(REFRESH_INTERVAL_S)

    # Advance simulated time according to the selected speed.
    simulated_dt = REFRESH_INTERVAL_S * float(speed)
    step(state, rain, debris, distribution, drain_type, simulated_dt)

    st.rerun()
