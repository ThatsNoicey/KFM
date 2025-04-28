"""
Knee Flexion Monitor v3.1 - Clinical Scoring System
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
import random
import time
import math
import numpy as np
import os

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

KFMS_PARAMS = {
    'theta_target': 90,   # Target flexion angle (degrees)
    't_target': 10,        # Target hold time (seconds)
    'alpha': 0.4,          # ROM component weight
    'beta': 0.4,           # Endurance component weight
    'gamma': 0.2,          # Control component weight
    'baseline_sessions': 3 # Number of sessions for baseline
}

# BLE Configuration (Configure for your device)
DEVICE_ADDRESS = "00:11:22:33:44:55"
CHAR_UUIDS = {
    'angle': "012345678-1234-5678-1234-56789abcdef1",
    'hold_time': "12345678-1234-5678-1234-56789abcdef2",
    'max_angle': "12345678-1234-5678-1234-56789abcdef3"
}

# ====================
# CORE FUNCTIONALITY
# ====================
class FlexionAnalyzer:
    def __init__(self, max_records=15):
        self.session_data = deque(maxlen=max_records)
        self.current = {'angle': 0.0, 'hold_time': 0.0, 'max_angle': 0.0}
        self.active_session = False
        self.live_angles = []
        self.baseline_kfms = None
        self.kfms_history = []

    def update(self, angle, hold_time, max_angle):
        try:
            angle = float(angle)
            hold_time = float(hold_time)
            max_angle = float(max_angle)
        except (TypeError, ValueError):
            logging.warning(f"Invalid data: {angle}, {hold_time}, {max_angle}")
            return

        if angle > 5.0 and not self.active_session:
            self.active_session = True
            self.current = {'start': datetime.now(), 'max_angle': 0.0}
            self.live_angles = []
            
        elif angle <= 5.0 and self.active_session:
            self.active_session = False
            self._finalize_session(hold_time)
            
        if self.active_session:
            self.current['max_angle'] = max(self.current['max_angle'], angle)
            self.live_angles.append(angle)

    def _calculate_stability_index(self):
        """Computes movement control metric using angular statistics"""
        if len(self.live_angles) < 2 or self.current['max_angle'] == 0:
            return 0.0
            
        mean_angle = sum(self.live_angles)/len(self.live_angles)
        variance = sum((x - mean_angle)**2 for x in self.live_angles)/(len(self.live_angles)-1)
        sd = math.sqrt(variance)
        
        return (mean_angle/self.current['max_angle']) * (1 - (sd/self.current['max_angle']))

    def _calculate_progression_factor(self, current_kfms):
        """Dynamically adjusts score based on improvement from baseline"""
        if len(self.kfms_history) < KFMS_PARAMS['baseline_sessions']:
            return 1.0  # Baseline establishment phase
            
        baseline = sum(self.kfms_history[:KFMS_PARAMS['baseline_sessions']])/KFMS_PARAMS['baseline_sessions']
        return 1 + ((current_kfms - baseline)/100)

    def _finalize_session(self, hold_time):
        """Process completed session and calculate KFMS"""
        hold_time_sec = hold_time/1000
        stability = self._calculate_stability_index()
        
        # Calculate KFMS components
        rom_component = (self.current['max_angle']/KFMS_PARAMS['theta_target']) * KFMS_PARAMS['alpha']
        endurance_component = (hold_time_sec/KFMS_PARAMS['t_target']) * KFMS_PARAMS['beta']
        control_component = stability * KFMS_PARAMS['gamma']
        
        base_kfms = (rom_component + endurance_component + control_component) * 100
        
        # Apply progression factor
        progression_factor = self._calculate_progression_factor(base_kfms)
        final_kfms = min(base_kfms * progression_factor, 100)  # Cap at 100

        # Store session data
        record = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'hold_time_sec': round(hold_time_sec, 1),
            'max_angle': round(self.current['max_angle'], 1),
            'kfms': round(final_kfms, 1),
            'stability_index': round(stability, 3),
            'progression_factor': round(progression_factor, 2)
        }
        self.session_data.append(record)
        self.kfms_history.append(final_kfms)

    def generate_clinical_report(self):
        """Generates comprehensive report with KFMS documentation"""
        if not self.session_data:
            return "No valid session data recorded"
        
        avg_hold = sum(s['hold_time_sec'] for s in self.session_data)/len(self.session_data)
        avg_angle = sum(s['max_angle'] for s in self.session_data)/len(self.session_data)
        avg_kfms = sum(s['kfms'] for s in self.session_data)/len(self.session_data)        
        report_content = (
            f"Knee Flexion Monitor Score (KFMS) Report\n"
            f"=======================================\n"
            f"Patient: {PATIENT_METADATA['patient_name']} ({PATIENT_METADATA['nhs_number']})\n"
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            "Clinical Interpretation Guide\n"
            "----------------------------\n"
            "| KFMS Range | Classification      | Rehabilitation Stage                             |\n"
            "|------------|---------------------|--------------------------------------------------|\n"
            "| 0-39       | High Risk           | Limited mobility, requires intervention          |\n"
            "| 40-59      | Limited Efficacy    | Early recovery phase, needs focused training     |\n"
            "| 60-79      | Functional Recovery | Moderate function, continue strengthening        |\n"
            "| 80-100     | Optimal Performance | Target performance, consider sport-specific rehab|\n\n"
            
            "Session Data\n"
            "------------\n"
            "| Timestamp           | Hold Time  | Max Angle  | KFMS  | Stability |\n"
            "|---------------------|------------|------------|-------|-----------|\n"
        )
        
        for session in self.session_data:
            report_content += (
                f"| {session['timestamp']} | {session['hold_time_sec']:>9.1f}s | "
                f"{session['max_angle']:>9.1f}° | {session['kfms']:>5.1f} | "
                f"{session['stability_index']:>9.3f} |\n"
            )
        report_content += (
        "|---------------------|------------|------------|-------|-----------|\n"
        f"| AVERAGES            | {avg_hold:>9.1f}s | {avg_angle:>9.1f}° | {avg_kfms:>5.1f} | "
        f"{avg_stability:>9.3f} |\n\n"
        )   
        report_content += (
                f"\n\nLEGAL DISCLAIMER:\n"
                f"The Knee Flexion Monitor (KFM) system is intended for informational and tracking purposes only.\n"
                f"It is not a medical device and does not provide clinical diagnoses, treatment recommendations, or medical advice.\n"
                f"Any decisions made based on the data provided by this system are the sole responsibility of the user and their healthcare provider.\n"
                f"The manufacturer assumes no liability for injury, loss, or damages resulting from the use or interpretation of the data provided.\n"
                f"Users are advised to consult a qualified medical professional before making any healthcare decisions.\n"
        )
    # Create reports directory if it doesn't exist
        os.makedirs("reports", exist_ok=True)
    
        filename = f"reports/KFMS_Report_{PATIENT_METADATA['patient_name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(filename, 'w') as f:
            f.write(report_content)
    
        return filename

app = Flask(__name__)
analyzer = FlexionAnalyzer()

@app.route('/live')
def live_feed():
    return jsonify({
        'knee_side': PATIENT_METADATA['knee_side'],
        'current_angle': analyzer.current.get('angle', 0.0),
        'session_active': analyzer.active_session,
        'current_kfms': analyzer.kfms_history[-1] if analyzer.kfms_history else None
    })

@app.route('/session_history')
def session_history():
    return jsonify(list(analyzer.session_data))

async def mock_data_generator():
    """Generates realistic rehabilitation sessions"""
    print("\n=== Starting KFMS Simulation ===")
    
    for session in range(1, 11):
        target = random.uniform(0.0, 140.0)
        hold = random.randint(500, 10000)
        print(f"Session {session}: Target {target:.1f}°, Hold {hold/1000:.1f}s")
        
        # Simulate flexion movement
        for angle in np.linspace(0, target, num=10):
            analyzer.update(angle, 0, target)
            await asyncio.sleep(0.1)
            
        # Hold phase
        start = time.time()
        while (time.time() - start) < (hold/1000):
            analyzer.update(target, hold, target)
            await asyncio.sleep(0.2)
            
        # Return phase
        for angle in np.linspace(target, 0, num=10):
            analyzer.update(angle, hold, target)
            await asyncio.sleep(0.1)
            
        await asyncio.sleep(1)
    
    print("=== Simulation Complete ===")

# Remaining BLE handling and execution code remains identical to previous version
# (Preserve the if __name__ == "__main__" block from earlier implementation)

if __name__ == "__main__":
    if "--test" in sys.argv:
        print("Initializing KFMS Test Mode...")
        signal.signal(signal.SIGINT, lambda *args: sys.exit(0))
        flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=5000))
        flask_thread.daemon = True
        flask_thread.start()
        
        try:
            asyncio.run(mock_data_generator())
        except KeyboardInterrupt:
            pass
        finally:
            report_file = analyzer.generate_clinical_report()
            print(f"\nClinical Report Generated: {report_file}")
    else:
        # Normal BLE operation
        pass
    