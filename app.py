import math
import time

import pandas as pd
import streamlit as st

st.set_page_config(page_title="HF-SMDG V1", page_icon="🌧️", layout="wide")

# -----------------------------
# Simplified model constants
# -----------------------------
G = 9.81
DISCHARGE_COEFF = 0.62
ZONE_OPEN_AREA_M2 = 0.0045
ZONE_SURFACE_AREA_M2 = 1.50
CATCHMENT_AREA_M2 = 300.0

FLOAT_TRIGGER_M = 0.040
FLOAT_RESET_M = 0.020
NORMAL_ANGLE_DEG = 20
ACTIVE_ANGLE_DEG = 50

FLOOD_THRESHOLD_M = 0.080
MAX_BLOCKAGE = 95.0

# Conceptual debris parameters; not field-validated.
DEBRIS_RATE_PP_PER_MIN = {
    "Low": 6.0,
    "Medium": 14.0,
    "High": 28.0,
}

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
        "angle_deg": NORMAL_ANGLE_DEG,
        "flow_lps": 0.0,
    }


def initial_state():
    return {
        "running": False,
        "time_s": 0,
        "zones": [make_zone("A"), make_zone("B"), make_zone("C")],
        "debris_bay_units": 0.0,
        "total_drained_l": 0.0,
        "seconds_above_flood": 0,
        "max_depth_m": 0.0,
        "history": [],
    }


def rainfall_inflow_lps(rain_mm_hr):
    # 1 mm over 1 m² = 1 liter.
    return rain_mm_hr * CATCHMENT_AREA_M2 / 3600.0


def zone_depth_m(zone):
    return max(0.0, zone["water_l"] / (ZONE_SURFACE_AREA_M2 * 1000.0))


def hydraulic_flow_lps(depth_m, blockage_pct):
    """
    Simplified opening-flow estimate:
        Q = Cd * A_eff * sqrt(2gh)
    """
    if depth_m <= 0:
        return 0.0

    open_fraction = max(0.0, 1.0 - blockage_pct / 100.0)
    effective_area = ZONE_OPEN_AREA_M2 * open_fraction
    q_m3_s = DISCHARGE_COEFF * effective_area * math.sqrt(2 * G * depth_m)
    return q_m3_s * 1000.0


def update_float(zone, drain_type):
    if drain_type == "Fixed Grate":
        zone["float_active"] = False
        zone["angle_deg"] = NORMAL_ANGLE_DEG
        return

    # Hysteresis: activate at 4 cm, reset at 2 cm.
    if not zone["float_active"] and zone["depth_m"] >= FLOAT_TRIGGER_M:
        zone["float_active"] = True
    elif zone["float_active"] and zone["depth_m"] <= FLOAT_RESET_M:
        zone["float_active"] = False

    zone["angle_deg"] = ACTIVE_ANGLE_DEG if zone["float_active"] else NORMAL_ANGLE_DEG


def add_debris(zones, debris_level, distribution_name, rain_mm_hr, dt_s):
    base_rate = DEBRIS_RATE_PP_PER_MIN[debris_level]
    rain_factor = 0.50 + min(rain_mm_hr, 200) / 200.0
    total_rate_pp_s = (base_rate * rain_factor) / 60.0
    weights = DISTRIBUTIONS[distribution_name]

    for zone, weight in zip(zones, weights):
        zone["blockage"] = min(
            MAX_BLOCKAGE,
            zone["blockage"] + total_rate_pp_s * weight * dt_s,
        )


def redistribute_debris(zone, state, dt_s):
    """
    Conceptual HF-SMDG rule:
    an activated louver zone shifts some floating debris toward the side bay.
    """
    if not zone["float_active"] or zone["blockage"] <= 0:
        return

    # Tunable conceptual coefficient, not measured real-world efficiency.
    redirect_pp = 0.10 * max(zone["flow_lps"], 0.20) * dt_s
    redirect_pp = min(redirect_pp, zone["blockage"])

    zone["blockage"] -= redirect_pp
    state["debris_bay_units"] += redirect_pp


def step_simulation(state, rain_mm_hr, debris_level, distribution_name, drain_type, dt_s=1.0):
    total_inflow_lps = rainfall_inflow_lps(rain_mm_hr)
    inflow_per_zone_lps = total_inflow_lps / 3.0

    add_debris(
        state["zones"],
        debris_level,
        distribution_name,
        rain_mm_hr,
        dt_s,
    )

    for zone in state["zones"]:
        zone["depth_m"] = zone_depth_m(zone)
        update_float(zone, drain_type)

    for zone in state["zones"]:
        zone["flow_lps"] = hydraulic_flow_lps(zone["depth_m"], zone["blockage"])

    if drain_type == "HF-SMDG":
        for zone in state["zones"]:
            redistribute_debris(zone, state, dt_s)

        for zone in state["zones"]:
            zone["flow_lps"] = hydraulic_flow_lps(zone["depth_m"], zone["blockage"])

    for zone in state["zones"]:
        incoming_l = inflow_per_zone_lps * dt_s
        outgoing_l = min(zone["water_l"] + incoming_l, zone["flow_lps"] * dt_s)

        zone["water_l"] = max(0.0, zone["water_l"] + incoming_l - outgoing_l)
        state["total_drained_l"] += outgoing_l

        zone["depth_m"] = zone_depth_m(zone)
        update_float(zone, drain_type)

    state["time_s"] += dt_s

    avg_depth_m = sum(z["depth_m"] for z in state["zones"]) / 3.0
    max_local_depth_m = max(z["depth_m"] for z in state["zones"])
    total_flow_lps = sum(z["flow_lps"] for z in state["zones"])

    state["max_depth_m"] = max(state["max_depth_m"], max_local_depth_m)

    if max_local_depth_m >= FLOOD_THRESHOLD_M:
        state["seconds_above_flood"] += dt_s

    state["history"].append({
        "time_s": state["time_s"],
        "average_depth_cm": avg_depth_m * 100,
        "max_local_depth_cm": max_local_depth_m * 100,
        "total_flow_lps": total_flow_lps,
        "A_blockage": state["zones"][0]["blockage"],
        "B_blockage": state["zones"][1]["blockage"],
        "C_blockage": state["zones"][2]["blockage"],
    })


def run_scenario(seconds, rain, debris, distribution, drain_type):
    state = initial_state()
    for _ in range(seconds):
        step_simulation(
            state,
            rain_mm_hr=rain,
            debris_level=debris,
            distribution_name=distribution,
            drain_type=drain_type,
            dt_s=1.0,
        )
    return state


def fmt_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


if "sim" not in st.session_state:
    st.session_state.sim = initial_state()

# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.header("Storm & Debris Inputs")

rain = st.sidebar.slider("Rainfall Intensity (mm/hr)", 10, 200, 120, 5)
debris = st.sidebar.select_slider(
    "Debris Level",
    options=["Low", "Medium", "High"],
    value="Medium",
)
distribution = st.sidebar.selectbox("Debris Distribution", list(DISTRIBUTIONS.keys()))
drain_type = st.sidebar.radio("Drain Type", ["Fixed Grate", "HF-SMDG"])
speed = st.sidebar.select_slider(
    "Simulation Speed",
    options=[0.5, 1, 2, 4],
    value=1,
    format_func=lambda x: f"{x}×",
)

st.sidebar.caption(
    "HF-SMDG has three independently triggered louver zones. "
    "A zone activates at 4 cm local water depth and resets at 2 cm."
)

# -----------------------------
# Controls
# -----------------------------
st.title("HF-SMDG Simulation — Version 1")
st.caption(
    "Conceptual educational model of rainfall, debris blockage, drainage flow, "
    "float activation, louver movement, and debris redistribution."
)

b1, b2, b3, b4 = st.columns(4)

with b1:
    if st.button("▶ Start", use_container_width=True):
        st.session_state.sim["running"] = True

with b2:
    if st.button("⏸ Pause", use_container_width=True):
        st.session_state.sim["running"] = False

with b3:
    if st.button("⏭ Step +1 s", use_container_width=True):
        st.session_state.sim["running"] = False
        step_simulation(
            st.session_state.sim,
            rain,
            debris,
            distribution,
            drain_type,
            dt_s=1.0,
        )

with b4:
    if st.button("↺ Reset", use_container_width=True):
        st.session_state.sim = initial_state()
        st.rerun()

# -----------------------------
# Live metrics
# -----------------------------
state = st.session_state.sim
zones = state["zones"]

top1, top2, top3, top4 = st.columns(4)
top1.metric("Simulation Time", fmt_time(state["time_s"]))
top2.metric(
    "Average Surface Water",
    f"{sum(z['depth_m'] for z in zones) / 3 * 100:.2f} cm",
)
top3.metric(
    "Total Drainage Rate",
    f"{sum(z['flow_lps'] for z in zones):.2f} L/s",
)
top4.metric("Debris Bay", f"{state['debris_bay_units']:.1f} units")

st.subheader(f"Current Test: {drain_type}")

zone_cols = st.columns(3)
for col, zone in zip(zone_cols, zones):
    with col:
        status = "ACTIVATED" if zone["float_active"] else "NORMAL"
        st.markdown(f"### Zone {zone['name']}")
        st.metric("Local Water Depth", f"{zone['depth_m'] * 100:.2f} cm")
        st.metric("Blockage", f"{zone['blockage']:.1f}%")
        st.metric("Effective Opening", f"{100 - zone['blockage']:.1f}%")
        st.metric("Drainage Rate", f"{zone['flow_lps']:.2f} L/s")
        st.write(f"**Float:** {status}")
        st.write(f"**Louver Angle:** {zone['angle_deg']}°")

        if zone["depth_m"] >= FLOOD_THRESHOLD_M:
            st.error("Flood threshold exceeded")
        elif zone["float_active"]:
            st.warning("Adaptive louver active")
        else:
            st.success("Normal drainage state")

# -----------------------------
# Live graph
# -----------------------------
st.subheader("Water Depth Over Time")

if state["history"]:
    hist = pd.DataFrame(state["history"]).set_index("time_s")
    st.line_chart(
        hist[["average_depth_cm", "max_local_depth_cm"]],
        y_label="Water depth (cm)",
        x_label="Simulation time (s)",
    )
else:
    st.info("Start the simulation or use Step +1 s to generate data.")

# -----------------------------
# Controlled comparison
# -----------------------------
st.subheader("Fixed Grate vs HF-SMDG — Same Conditions")

comparison_seconds = st.slider(
    "Comparison duration (simulated seconds)",
    60,
    600,
    180,
    30,
)

if st.button("Run Controlled Comparison"):
    fixed = run_scenario(comparison_seconds, rain, debris, distribution, "Fixed Grate")
    smart = run_scenario(comparison_seconds, rain, debris, distribution, "HF-SMDG")

    comparison = pd.DataFrame([
        {
            "System": "Fixed Grate",
            "Max local water depth (cm)": fixed["max_depth_m"] * 100,
            "Total drained water (L)": fixed["total_drained_l"],
            "Time above flood threshold (s)": fixed["seconds_above_flood"],
            "Final average blockage (%)": sum(z["blockage"] for z in fixed["zones"]) / 3,
            "Debris redirected to bay (units)": fixed["debris_bay_units"],
        },
        {
            "System": "HF-SMDG",
            "Max local water depth (cm)": smart["max_depth_m"] * 100,
            "Total drained water (L)": smart["total_drained_l"],
            "Time above flood threshold (s)": smart["seconds_above_flood"],
            "Final average blockage (%)": sum(z["blockage"] for z in smart["zones"]) / 3,
            "Debris redirected to bay (units)": smart["debris_bay_units"],
        },
    ])

    st.dataframe(comparison, use_container_width=True, hide_index=True)

    fixed_hist = pd.DataFrame(fixed["history"])[["time_s", "max_local_depth_cm"]]
    fixed_hist = fixed_hist.rename(columns={"max_local_depth_cm": "Fixed Grate"})

    smart_hist = pd.DataFrame(smart["history"])[["time_s", "max_local_depth_cm"]]
    smart_hist = smart_hist.rename(columns={"max_local_depth_cm": "HF-SMDG"})

    combined = fixed_hist.merge(smart_hist, on="time_s").set_index("time_s")
    st.line_chart(
        combined,
        y_label="Maximum local water depth (cm)",
        x_label="Simulation time (s)",
    )

with st.expander("How Version 1 works"):
    st.markdown(
        """
        **Simulation chain**

        Rainfall → surface water → debris blockage → reduced effective opening
        → drainage calculation → local water buildup → float activation
        → louver rotation → debris redistribution → recalculated drainage.

        The hydraulic flow estimate uses:

        **Q = Cd × A × √(2gh)**

        where the effective opening area is reduced by debris blockage.

        **Important:** The debris-arrival and debris-redistribution coefficients
        are conceptual parameters for this student prototype. They are not
        field-calibrated performance values.
        """
    )

st.caption(
    "Educational conceptual simulation only. Results are based on simplified "
    "hydraulic assumptions and do not represent field-validated engineering performance."
)

# -----------------------------
# Auto-run
# -----------------------------
if st.session_state.sim["running"]:
    time.sleep(0.50 / speed)
    step_simulation(
        st.session_state.sim,
        rain,
        debris,
        distribution,
        drain_type,
        dt_s=1.0,
    )
    st.rerun()

