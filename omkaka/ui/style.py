"""A compact, local-only visual system for the research workspace."""
from html import escape

import streamlit as st


def apply_style():
    st.html("""<style>
    :root { --ink:#19233b; --muted:#66758b; --line:#e5eaf2; --accent:#6054d6; }
    .stApp { background:#f6f7fb; color:var(--ink); }
    [data-testid="stHeader"] { background:transparent; }
    [data-testid="stMainBlockContainer"] { max-width:1450px; padding:1.8rem 2rem 4rem; }
    h1,h2,h3,h4 { color:#19233b; letter-spacing:-.035em; }
    h1 { font-size:2.5rem!important; font-weight:700!important; }
    h2 { font-size:1.8rem!important; font-weight:650!important; padding-top:0!important; padding-bottom:.3rem!important; }
    h3 { font-size:1.3rem!important; } h4 { font-size:1.05rem!important; }
    p,li { line-height:1.65; }
    [data-testid="stCaptionContainer"] { color:#66758b; }
    [data-testid="stSidebar"] { background:#142038; border-right:none; min-width:245px!important; max-width:245px!important; }
    [data-testid="stSidebar"] * { color:#e9edf6; }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top:0; }
    [data-testid="stSidebarUserContent"] { padding:1.2rem 1rem!important; }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#9ba9bf; }
    [data-testid="stSidebar"] [role="radiogroup"] { gap:7px; }
    [data-testid="stSidebar"] [role="radiogroup"] label { padding:11px 14px; border-radius:9px; width:100%; margin:0; }
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) { background:#2b3955; box-shadow:inset 3px 0 #9f95ff; }
    [data-testid="stSidebar"] [role="radiogroup"] label:hover { background:#22314b; }
    [data-testid="stSidebar"] [role="radiogroup"] label > div:first-child { display:none; }
    [data-testid="stSidebar"] input[type="radio"] { appearance:none!important; width:0!important; height:0!important; margin:0!important; }
    [data-testid="stSidebar"] [role="radiogroup"] > div { width:100%; }
    [data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child { display:none; }
    [data-testid="stSidebar"] [data-testid="stRadioOption"] p { font-size:.86rem; white-space:nowrap; }
    [data-testid="stMetric"] { background:white; border:1px solid var(--line); padding:18px 16px; border-radius:12px; min-height:104px; }
    [data-testid="stMetricLabel"] p { color:#66758b; font-size:.82rem; font-weight:500; }
    [data-testid="stMetricLabel"] p { white-space:normal!important; overflow:visible!important; }
    [data-testid="stMetricValue"] { font-size:clamp(1.1rem,1.8vw,1.65rem); font-weight:650; letter-spacing:-.035em; }
    [data-testid="stVerticalBlockBorderWrapper"] > div { border-color:var(--line)!important; border-radius:12px!important; background:white; }
    [data-testid="stExpander"] { background:white; border:1px solid var(--line); border-radius:10px; }
    [data-testid="stForm"] { background:white; border:1px solid var(--line); border-radius:12px; padding:22px; }
    [data-testid="stDataFrame"] { border-radius:10px; overflow:hidden; }
    [data-testid="stAlert"] { border-radius:10px; font-size:.88rem; }
    [data-testid="stAlert"] p { font-size:.86rem; }
    .stButton button, .stDownloadButton button, .stLinkButton a { border-radius:8px; font-weight:600; min-height:40px; }
    .stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background:#6054d6; border-color:#6054d6; color:white; }
    [data-testid="stTabs"] [role="tablist"] { gap:30px; border-bottom:1px solid var(--line); }
    [data-testid="stTabs"] [role="tab"] { padding:10px 0; font-weight:600; }
    .ok-brand { display:flex; align-items:center; gap:11px; margin:12px 0 30px; }
    .ok-logo { background:#7569e7; padding:10px; min-width:42px; height:42px; border-radius:12px; font-size:18px; font-weight:700; color:white; line-height:22px; }
    .ok-brand-name { font-size:15px; font-weight:650; letter-spacing:-.03em; }
    .ok-brand small { display:block; color:#93a2bb!important; font-size:11px; letter-spacing:.13em; margin-top:3px; }
    .ok-eyebrow { color:#7b8497; font-size:11px; font-weight:650; letter-spacing:.16em; text-transform:uppercase; margin:9px 0 12px; }
    .ok-top { display:flex; justify-content:space-between; align-items:center; gap:15px; border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:10px; }
    .ok-top span { font-size:12px; color:#66758b; }
    .ok-pill { display:inline-block; padding:5px 10px; border-radius:6px; background:#eef0fc; color:#5b50be!important; font-size:11px; font-weight:650; letter-spacing:.035em; }
    .ok-pill.green { background:#e9f5f0; color:#28795b!important; }
    .ok-pill.amber { background:#fff3df; color:#97620c!important; }
    .ok-hero { background:#fff; border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin-bottom:8px; }
    .ok-hero h2 { margin:12px 0 4px; font-size:2rem!important; line-height:1.2; }
    .ok-hero p { color:#66758b; margin:8px 0 0; font-size:14px; }
    .ok-hero .ok-ticker { display:inline-block; background:#eeebff; border-radius:9px; color:#6656c8; padding:10px 13px; font-weight:700; font-size:18px; }
    .ok-card { background:white; border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:14px; }
    .ok-card h3 { margin:0 0 8px; font-size:1rem!important; letter-spacing:-.02em; }
    .ok-card p { margin:0; color:#66758b; font-size:13px; }
    .ok-step { display:flex; gap:14px; padding:15px 0; border-bottom:1px solid #edf0f6; }
    .ok-step:last-child { border:0; }
    .ok-step b { color:#263552; font-size:13px; } .ok-step small { display:block; color:#718096; line-height:1.5; margin-top:4px; }
    .ok-step-number { min-width:27px; height:27px; line-height:27px; text-align:center; border-radius:50%; background:#f0edfe; color:#6858c5; font-size:12px; font-weight:700; }
    .ok-footer { color:#8a95a8; font-size:11px; padding-top:24px; margin-top:30px; border-top:1px solid var(--line); }
    @media(max-width:900px) { [data-testid="stMainBlockContainer"] { padding:2rem 1.2rem; } .ok-hero { padding:22px; } .ok-hero h2 { font-size:1.8rem!important; } }
    </style>""")


def header(eyebrow, title, description):
    st.html(f'<div class="ok-eyebrow">{escape(eyebrow)}</div>')
    st.header(title)
    st.caption(description)


def card(title, body):
    st.html(f'<div class="ok-card"><h3>{escape(title)}</h3><p>{escape(body)}</p></div>')
