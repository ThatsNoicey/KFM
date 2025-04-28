"""
Created on Wed Mar 26 10:21:59 2025

@author: jacob
"""
"""
Knee Flexion Monitor v2.0 - Development Testing Tool
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
import pytest  # New testing dependency

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

SCORING_FUNCTION = lambda ht, ma: ht * ma

# ====================
# CORE FUNCTIONALITY (Unchanged)
# ====================
class FlexionAnalyzer:
    def __init__(self, max_records=15):
        self.session_data = deque(maxlen=max_records)
        self.current = {'angle': 0.0, 'hold_time': 0.0, 'max_angle': 0.0}
        self.active_session = False
        
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
            
        elif angle <= 5.0 and self.active_session:
            self.active_session = False
            self._finalize_session(hold_time)
            
        if self.active_session:
            self.current['max_angle'] = max(self.current['max_angle'], angle)

    def _finalize_session(self, hold_time):
        record = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'hold_time_sec': round(hold_time/1000, 1),
            'max_angle': round(self.current['max_angle'], 1),
            'score': round(SCORING_FUNCTION(hold_time/1000, self.current['max_angle']), 1)
        }
        self.session_data.append(record)

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

def generate_clinical_report():
    if not analyzer.session_data:
        return "No valid session data recorded"
    
    report_content = (
        f"Patient Name: {PATIENT_METADATA['patient_name']}\n"
        f"NHS Number: {PATIENT_METADATA['nhs_number']}\n"
        f"Assessed Knee: {PATIENT_METADATA['knee_side']}\n"
        f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "Exercise Session Summary:\n"
        "-------------------------\n"
        "| Time            | Hold (s) | Max Angle | Score |\n"
        "|-----------------|----------|-----------|-------|\n"
    )
    
    totals = {'hold': 0.0, 'angle': 0.0, 'score': 0.0}
    for session in analyzer.session_data:
        report_content += (
            f"| {session['timestamp']} | {session['hold_time_sec']:>8.1f} | "
            f"{session['max_angle']:>9.1f} | {session['score']:>5.1f} |\n"
        )
        totals['hold'] += session['hold_time_sec']
        totals['angle'] += session['max_angle']
        totals['score'] += session['score']
    
    num_sessions = len(analyzer.session_data)
    report_content += (
        f"\nAverages:\n- Hold Time: {totals['hold']/num_sessions:.1f}s\n"
        f"- Max Angle: {totals['angle']/num_sessions:.1f}°\n"
        f"- Average Score: {totals['score']/num_sessions:.1f}\n\n"
        "Device Disclaimer:\n"
        "Data collected by the FlexTrack Pro system. Manufacturer is not liable\n" 
        "for clinical decisions made using this output. Verify critical values\n"
        "with manual assessment."
    )
    
    filename = f"Knee_Report_{PATIENT_METADATA['patient_name'].replace(' ','_')}_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(filename, 'w') as f:
        f.write(report_content)
    
    return filename

async def ble_handler():
    retries = 0
    max_retries = 5
    
    while retries < max_retries:
        try:
            async with BleakClient(DEVICE_ADDRESS, timeout=15) as client:
                if client.is_connected:
                    print(f"Connected to {DEVICE_ADDRESS}")
                    retries = 0
                    
                    while True:
                        try:
                            angle = await client.read_gatt_char(CHAR_UUIDS['angle'])
                            hold = await client.read_gatt_char(CHAR_UUIDS['hold_time'])
                            max_a = await client.read_gatt_char(CHAR_UUIDS['max_angle'])
                            
                            analyzer.update(
                                struct.unpack('f', angle)[0],
                                struct.unpack('<I', hold)[0],
                                struct.unpack('f', max_a)[0]
                            )
                            
                            await asyncio.sleep(0.2)
                            
                        except Exception as e:
                            logging.error(f"Data error: {str(e)}")
                            break
                            
        except Exception as e:
            logging.error(f"Connection error: {str(e)}")
            retries += 1
            await asyncio.sleep(2**retries)

# ======================
# TESTING INFRASTRUCTURE (New Additions)
# ======================
def test_report_generation():
    """Unit test for document generation"""
    test_analyzer = FlexionAnalyzer()
    test_analyzer.session_data.extend([
        {'timestamp': "2023-10-15 09:00", 'hold_time_sec': 12.5, 'max_angle': 45.0, 'score': 562.5},
        {'timestamp': "2023-10-15 09:15", 'hold_time_sec': 8.2, 'max_angle': 38.7, 'score': 317.3}
    ])
    
    report = generate_clinical_report()
    assert PATIENT_METADATA['nhs_number'] in report
    assert "| 2023-10-15 09:00 |     12.5 |      45.0 | 562.5 |" in report
    assert "Average Score: 439.9" in report

def test_edge_cases():
    """Boundary condition testing"""
    empty_analyzer = FlexionAnalyzer()
    report = empty_analyzer.generate_clinical_report()
    assert "No valid session data recorded" in report
    
    extreme_analyzer = FlexionAnalyzer()
    extreme_analyzer.session_data.append({
        'timestamp': "2023-10-15 09:00", 
        'hold_time_sec': 1200.0,
        'max_angle': 150.0,
        'score': 180000.0
    })
    report = extreme_analyzer.generate_clinical_report()
    assert "150.0" in report

class MockBleDevice:
    """Simulates BLE data for integration testing"""
    def __init__(self):
        self.data = {
            'angle': 0.0,
            'hold_time': 0,
            'max_angle': 0.0
        }
        
    def generate_mock_session(self):
        self.data.update({
            'angle': round(random.uniform(30.0, 90.0), 1),
            'hold_time': random.randint(3000, 15000),
            'max_angle': round(random.uniform(45.0, 95.0), 1)
        })

def test_integration():
    """End-to-end pipeline test"""
    mock_device = MockBleDevice()
    test_analyzer = FlexionAnalyzer()
    
    for _ in range(5):
        mock_device.generate_mock_session()
        test_analyzer.update(
            mock_device.data['angle'],
            mock_device.data['hold_time'],
            mock_device.data['max_angle']
        )
    
    report = test_analyzer.generate_clinical_report()
    assert "5 Exercise Records" in report

# ======================
# EXECUTION CONTROL
# ======================
if __name__ == "__main__":
    if "--test" in sys.argv:
        print("Running test suite...")
        pytest.main([__file__, "-v"])
    else:
        # Original operational flow
        signal.signal(signal.SIGINT, lambda *args: sys.exit(0))
        flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=5000))
        flask_thread.daemon = True
        flask_thread.start()
        
        try:
            asyncio.run(ble_handler())
        finally:
            report_file = generate_clinical_report()
            print(f"Clinical report saved to: {report_file}")