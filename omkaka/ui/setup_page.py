"""A guided setup and research workflow; no terminal required."""
import streamlit as st

from .. import schedule
from ..config import secret_status
from ..daily import daily_job
from ..pipeline import build_clients
from ..setup import check_connections, missing_credentials, readiness, save_credentials
from .style import card, header


def research_button(conn, settings, key="run_research"):
    if settings.is_demo:
        return
    missing = missing_credentials()
    if st.button("Run today's research", type="primary", key=key, disabled=bool(missing)):
        with st.status("Gathering market data and checking the evidence…", expanded=True) as progress:
            try:
                result = daily_job(conn, settings, lambda: build_clients(conn, settings),
                                   run_kind="manual", log=st.write, retry_failed=True)
                failed = result["action"] in ("failed_final", "retry_later")
                progress.update(label="Research needs attention" if failed else "Research check complete",
                                state="error" if failed else "complete", expanded=failed)
                st.session_state["research_message"] = result
            except Exception:
                progress.update(label="Research stopped safely", state="error")
                st.error("The run could not finish. Check Health & spending for recorded source problems, then retry.")
    result = st.session_state.get("research_message")
    if result:
        msg = result.get("reason") or ("Research saved. Open Today to read the checked result." if result["action"] == "done" else "Research check complete.")
        (st.error if result["action"] in ("failed_final", "retry_later") else st.info)(msg)
    if missing:
        st.caption("Add your connections in Setup & connections to enable research.")


def page_setup(conn, settings):
    header("Workspace / Setup", "Automate your morning research.",
           "Markets already works without keys. Connect these sources for verified daily screening and the morning schedule.")
    if settings.is_demo:
        card("You're exploring the demo", "This workspace is fully offline. Open the live workspace to save your connections and prepare real research.")
        st.link_button("Open live setup", "http://localhost:8501/?view=setup", type="primary")
        st.markdown("#### Your three setup steps")
        card("01 · Add connections", "Your name and email for SEC access, plus your Massive and Finnhub API keys.")
        card("02 · Check and run", "Test the data sources, then run the first real research session.")
        card("03 · Enable mornings", "After the checks pass, turn on the schedule targeting 6:00 a.m. New York time.")
        return

    ready = readiness(conn, settings)
    status = secret_status()
    if st.session_state.pop("clear_connection_inputs", False):
        for key in ("setup_contact", "setup_market", "setup_news"):
            st.session_state[key] = ""
    cols = st.columns(3)
    cols[0].metric("Connections saved", f"{3 - len(ready['missing'])} / 3")
    cols[1].metric("Live connections", "Verified" if ready["verified"] else "Not verified")
    cols[2].metric("First research run", "Complete" if ready["screened"] else "Not completed")
    st.write("")
    left, right = st.columns([1.7, 1], gap="large")
    with left:
        st.subheader("01  Connect your sources")
        st.caption("Saved only in this app's local .env file. Existing values are never displayed. Leave a field blank to keep its saved value.")
        with st.form("connections", clear_on_submit=True):
            contact = st.text_input("SEC contact · your name and email", type="password", placeholder="Your Name you@example.com",
                                    help="Sent to the SEC to identify requests from your research app.", key="setup_contact")
            market = st.text_input("Massive API key", type="password", placeholder="Paste your key", key="setup_market")
            news = st.text_input("Finnhub API key", type="password", placeholder="Paste your key", key="setup_news")
            if st.form_submit_button("Save connections", type="primary"):
                try:
                    n = save_credentials({"SEC_USER_AGENT": contact, "MARKET_DATA_API_KEY": market, "NEWS_API_KEY": news})
                    if n:
                        st.session_state["connections_saved"] = True
                        st.session_state["clear_connection_inputs"] = True
                        st.session_state.pop("connection_results", None)
                        st.rerun()
                    else:
                        st.info("Nothing changed. Enter a value to save or replace it.")
                except (ValueError, OSError) as exc:
                    st.error(str(exc) if isinstance(exc, ValueError) else "Could not save the local settings file. Close the file in other editors and try again.")
        if st.session_state.pop("connections_saved", False):
            st.success("Connections saved privately. Next, check the live connections below.")
        st.subheader("02  Verify and run")
        st.caption("A connection check makes a small request to each provider. The first full research run can take several minutes.")
        if st.button("Check live connections", disabled=bool(ready["missing"])):
            with st.spinner("Checking the data providers…"):
                try:
                    st.session_state["connection_results"] = check_connections(conn, settings)
                    st.rerun()
                except Exception:
                    st.error("Connection checks could not finish. Nothing has been marked ready. Try again.")
        for row in st.session_state.get("connection_results", []):
            text = f"{row['source']} · {row['detail']}"
            (st.success if row["passed"] else st.error if row["required"] else st.info)(text)
        research_button(conn, settings, "setup_research")
        st.subheader("03  Enable the morning schedule")
        st.caption("Research starts from 4:30 a.m., targeting a checked result by 6:00 a.m. New York time. Windows checks every 30 minutes. Keep this computer on, awake, online, and signed in.")
        current = readiness(conn, settings)
        if not current["ready"]:
            st.info("Schedule pending. " + " ".join(current["reasons"]))
        if st.button("Enable 6 a.m. schedule", disabled=not current["ready"]):
            try:
                from ..config import PROJECT_ROOT
                result = schedule.install(PROJECT_ROOT)
                st.success(result)
            except Exception:
                st.error("Windows could not enable the schedule. Check your connections and Windows Task Scheduler, then retry.")
        with st.expander("Windows schedule status"):
            st.text(schedule.status())
    with right:
        card("Where to get your keys", "Use your own accounts. Choose the free plan when offered; this app has no paid AI integration.")
        st.link_button("Massive · account & API key", "https://massive.com/dashboard")
        st.link_button("Finnhub · account & API key", "https://finnhub.io/dashboard")
        st.link_button("SEC · automated access guidelines", "https://www.sec.gov/about/webmaster-frequently-asked-questions")
        st.write("")
        for label, name in (("SEC contact", "SEC_USER_AGENT"), ("Market data key", "MARKET_DATA_API_KEY"), ("News key", "NEWS_API_KEY")):
            card(label, "Saved · verify with a connection check" if status[name] else "Not saved yet")
        card("Reddit is optional", "It stays unavailable until you receive API approval. It will never be shown as neutral sentiment.")
        card("Tomorrow's routine", "Open Today, read the checked brief, review the source documents, and record your own notes. A day with no qualifying candidate is a valid result.")
