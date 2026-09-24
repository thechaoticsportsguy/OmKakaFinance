"""Windows Task Scheduler setup for the daily job (Phase 4).

The task runs run_daily.bat every 30 minutes, all day, every day. The job
itself checks New York time, so your PC's timezone and daylight-saving
changes do not matter. "Run as soon as possible after a missed start" is on,
so a run that was missed while the PC was off happens when it comes back.

By default the task does NOT wake a sleeping PC (it would do so every 30
minutes). Use --wake to allow it; see README for the trade-off.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "OmKakaFinance Daily"


def task_xml(project_root: Path, wake: bool = False) -> str:
    bat = project_root / "run_daily.bat"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>OmKakaFinance daily research: free data sources only, no trading. Runs every 30 minutes; the job decides whether it is time (New York).</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <WakeToRun>{"true" if wake else "false"}</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>"{escape(str(bat))}"</Command>
      <WorkingDirectory>{escape(str(project_root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(project_root: Path, wake: bool = False) -> str:
    xml_path = project_root / "data" / "omkaka_task.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(task_xml(project_root, wake), encoding="utf-16")
    if os.name != "nt":
        return f"Not on Windows: wrote {xml_path} but did not install it."
    out = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
                         capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def uninstall() -> str:
    if os.name != "nt":
        return "Not on Windows."
    out = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def status() -> str:
    if os.name != "nt":
        return "Not on Windows: scheduled task status unavailable."
    out = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"], capture_output=True, text=True)
    return (out.stdout + out.stderr).strip() or "No output from schtasks."
