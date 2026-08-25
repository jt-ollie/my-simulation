import streamlit as st

st.title("Hydro-Flow Smart Micro-Drainage Gate (HF-SMDG) Simulation")

st.sidebar.header("Storm & Environment Inputs")
rain = st.sidebar.slider("Rainfall Intensity (mm/hr)", 10, 200, 120)
trash = st.sidebar.select_slider("Street Plastic Waste Density", ["Low", "Medium", "High"])
power_grid = st.sidebar.radio("Power Grid Status", ["ONLINE", "OFFLINE (Blackout)"])

# Logic for Traditional Grate
if trash == "High":
    trad_clog = min(100, rain * 0.75)
elif trash == "Medium":
    trad_clog = min(100, rain * 0.50)
else:
    trad_clog = min(100, rain * 0.25)

trad_flow = max(0, 100 - trad_clog)

# Logic for HF-SMDG (Passive Buoyancy)
hfsmdg_clog = 15 if trash == "High" else (10 if trash == "Medium" else 5)
hfsmdg_flow = 100 - hfsmdg_clog

col1, col2 = st.columns(2)

with col1:
    st.subheader("Standard Cast-Iron Grate")
    st.metric("Clogging Level", f"{trad_clog:.0f}%")
    st.metric("Water Flow Capacity", f"{trad_flow:.0f}%")
    if trad_clog > 70:
        st.error("STATUS: CRITICAL FLASH FLOODING")

with col2:
    st.subheader("HF-SMDG (Invention)")
    st.metric("Clogging Level", f"{hfsmdg_clog:.0f}%")
    st.metric("Water Flow Capacity", f"{hfsmdg_flow:.0f}%")
    st.success("STATUS: OPTIMAL PASSIVE DRAINAGE")
