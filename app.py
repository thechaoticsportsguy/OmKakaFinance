"""OmKakaFinance dashboard (Streamlit).

Start it with:  python -m omkaka app     (live)
            or: python -m omkaka demo    (offline demo, fictional data)
"""
import streamlit as st

from omkaka.ui import pages

st.set_page_config(page_title="OmKakaFinance · Research workspace", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
pages.main()
