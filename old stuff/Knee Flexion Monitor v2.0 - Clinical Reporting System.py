
"""
Created on Wed Mar 26 10:19:15 2025

@author: jacob
"""
"""
Knee Flexion Monitor v2.0 - Clinical Reporting System
"""
from datetime import datetime
import logging
import signal
import sys
from collections import deque
from flask import Flask, jsonify
from threading import Thread
import asyncio
from bleak import BleakClient
import struct
import nest_asyncio

nest_asyncio.apply()

# =====================
# CONFIGURATION SECTION
# =====================
PATIENT_METADATA = {
    'patient_name': "John Smith",
    'age': 58,
    'nhs_number': "123 456 7890",
    'knee_side': "Left",
    'device_model': "FlexTrack Pro v3.2"
}

SCORING_FUNCTION = lambda ht, ma: ht * ma  # Isolated for easy modification

# ====================
# DATA PROCESSING CORE
# ====================


class FlexionAnalyzer:
    def __init__(self, max_records=15):
        self.session_data = deque(maxlen=max_records)
        self.current = {'angle': 0.0, 'hold_time': 0.0, 'max_angle': 0.0}
        self.active_session = False

    def update(self, angle, hold_time, max_angle):
        """Smart transition detection with validation"""
        try:
            angle = float(angle)
            hold_time = float(hold_time)
            max_angle = float(max_angle)
        except (TypeError, ValueError):
            logging.warning(f"Invalid data: {angle}, {hold_time}, {max_angle}")
            return

        # State machine logic
        if angle > 5.0 and not self.active_session:
            self.active_session = True
            self.current = {'start': datetime.now(), 'max_angle': 0.0}

        elif angle <= 5.0 and self.active_session:
            self.active_session = False
            self._finalize_session(hold_time)

        if self.active_session:
            self.current['max_angle'] = max(self.current['max_angle'], angle)

    def _finalize_session(self, hold_time):
        """Store validated session data"""
        record = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'hold_time_sec': round(hold_time/1000, 1),
            'max_angle': round(self.current['max_angle'], 1),
            'score': round(SCORING_FUNCTION(hold_time/1000, self.current['max_angle']), 1)
        }
        self.session_data.append(record)

# ================
# WEB SERVER SETUP
# ================


app = Flask(__name__)
analyzer = FlexionAnalyzer()


@app.route('/live')
def live_feed():
    return jsonify({
        'knee_side': PATIENT_METADATA['knee_side'],
        'current_angle': analyzer.current.get('angle', 0.0),
        'session_active': analyzer.active_session
    })


@app.route('/session_history')
def session_history():
    return jsonify(list(analyzer.session_data))

# ======================
# REPORT GENERATION
# ======================


def generate_clinical_report():
    """Creates time-stamped clinical document"""
    if not analyzer.session_data:
        return "No valid session data recorded"

    report_content = (
        f"Patient Name: {PATIENT_METADATA['patient_name']}\\n"
        f"NHS Number: {PATIENT_METADATA['nhs_number']}\\n"
        f"Assessed Knee: {PATIENT_MEDATA['knee_side']}\\n"
        f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\\n\\n"
        "Exercise Session Summary:\\n"
        "-------------------------\\n"
        "| Time            | Hold (s) | Max Angle | Score |\\n"
        "|-----------------|----------|-----------|-------|\\n"
    )

    totals = {'hold': 0.0, 'angle': 0.0, 'score': 0.0}
    for session in analyzer.session_data:
        report_content += (
            f"| {session['timestamp']} | {session['hold_time_sec']:>8.1f} | "
            f"{session['max_angle']:>9.1f} | {session['score']:>5.1f} |\\n"
        )
        totals['hold'] += session['hold_time_sec']
        totals['angle'] += session['max_angle']
        totals['score'] += session['score']

    num_sessions = len(analyzer.session_data)
    report_content += (
        f"\\nAverages:\\n- Hold Time: {totals['hold']/num_sessions:.1f}s\\n"
        f"- Max Angle: {totals['angle']/num_sessions:.1f}°\\n"
        f"- Average Score: {totals['score']/num_sessions:.1f}\\n\\n"
        "Device Disclaimer:\\n"
        "Data collected by the FlexTrack Pro system. Manufacturer is not liable\\n"
        "for clinical decisions made using this output. Verify critical values\\n"
        "with manual assessment."
    )

    filename = f"Knee_Report_{PATIENT_METADATA['patient_name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(filename, 'w') as f:
        f.write(report_content)

    return filename

# ======================
# BLE HANDLER
# ======================


async def ble_handler():
    # [Same BLE implementation as previous]
    # Add shutdown hook for report generation
    signal.signal(signal.SIGINT, lambda *args: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))

if __name__ == "__main__":
    # [Same execution logic as previous]
    # Add final report trigger
    try:
        asyncio.run(ble_handler())
    finally:
        report_file = generate_clinical_report()
        print(f"Clinical report saved to: {report_file}")
