"""
Knee Flexion Monitor v4.1 - Clinical System with Full Testing Suite
"""

from datetime import datetime
import logging
import signal
import sys
import os
import sqlite3
from collections import deque
from threading import Thread, Lock
from flask import Flask, jsonify
import asyncio
from bleak import BleakClient, BleakError
import struct
import nest_asyncio
import random
import time
import math
import numpy as np
import hashlib
from contextlib import contextmanager

nest_asyncio.apply()

# =====================
# CONFIGURATION SECTION 
# =====================

MAX_VALID_ANGLE = 140.0  # Max reasonable knee flexion angle
MAX_HOLD_TIME = 30.0  # Max hold time in seconds (adjust as needed)
first_valid_session_detected = False
ANGLE_THRESHOLD = 30

PATIENT_METADATA = {
    'patient_name': "John Smith",
    'age': 58,
    'nhs_number': "123 456 7890",
    'knee_side': "Left",
    'device_model': "FlexTrack Pro v3.2",
    'therapist_id': "THR-4562"
}

KFMS_PARAMS = {
    'theta_target': 120,   # Target flexion angle (degrees)
    't_target': 10,        # Target hold time (seconds)
    'alpha': 0.45,          # ROM component weight
    'beta': 0.45,           # Endurance component weight
    'gamma': 0.1,          # Control component weight
    'baseline_sessions': 3 # Number of sessions for baseline
}

BLE_CONFIG = {
    'address': "c8:c9:a3:e5:fd:3e",
    'retries': 3,
    'timeout': 15.0,
    'uuids': {
        'angle': "12345678-1234-5678-1234-56789abcdef1",
        'hold_time': "12345678-1234-5678-1234-56789abcdef2",
        'max_angle': "12345678-1234-5678-1234-56789abcdef3"
    }
}

DATABASE_CONFIG = {
    'path': "kfm_records.db",
    'backup_dir': "db_backups",
    'backup_interval': 3600  # 1 hour in seconds
}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("kfm_system.log"),
        logging.StreamHandler()
    ]
)

# ====================
# DATABASE MANAGEMENT
# ====================
class DatabaseManager:
    """Handles database operations with transaction support and backup"""
    
    def __init__(self):
        self.conn = None
        self.lock = Lock()
        self.last_backup = 0
        self._initialize_db()
        
    def _initialize_db(self):
        """Initialize database schema and connections"""
        with self._transaction() as cursor:
            cursor.execute('''
                           CREATE TABLE IF NOT EXISTS sessions (
                               session_id TEXT PRIMARY KEY,
                               patient_id TEXT,
                               start_time DATETIME,
                               duration REAL,
                               max_angle REAL,
                               kfms_score REAL,
                               stability_index REAL,
                               raw_data_checksum TEXT,
                               FOREIGN KEY(patient_id) REFERENCES patients(nhs_number)
                               )
                           ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS angle_data (
                    timestamp DATETIME,
                    session_id TEXT,
                    angle REAL,
                    PRIMARY KEY (timestamp, session_id),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_sessions_time 
                ON sessions(start_time)
            ''')
            
        logging.info("Database initialization complete")

    @contextmanager
    def _transaction(self):
        """Provides transactional scope around database operations"""
        with self.lock:
            self.conn = sqlite3.connect(DATABASE_CONFIG['path'])
            cursor = self.conn.cursor()
            try:
                yield cursor
                self.conn.commit()
            except Exception as e:
                self.conn.rollback()
                logging.error(f"Database error: {str(e)}")
                raise
            finally:
                self.conn.close()

    def store_session(self, session_data, angle_readings):
        """Stores complete session data with integrity checks"""
        try:
            data_hash = hashlib.sha256(
                str(angle_readings).encode()
            ).hexdigest()
            
            with self._transaction() as cursor:
                cursor.execute('''
                    INSERT INTO sessions VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                ''', (
                    session_data['session_id'],
                    PATIENT_METADATA['nhs_number'],
                    session_data['start_time'],
                    session_data['duration'],
                    session_data['max_angle'],
                    session_data['kfms_score'],
                    session_data['stability_index'],
                    data_hash
                ))
                
                cursor.executemany('''
                    INSERT INTO angle_data VALUES (?, ?, ?)
                ''', [
                    (ts, session_data['session_id'], angle)
                    for ts, angle in angle_readings
                ])
                
            self._check_backup()
            return True
        except sqlite3.Error as e:
            logging.error(f"Data storage failed: {str(e)}")
            return False

    def _check_backup(self):
        """Perform periodic database backups"""
        now = time.time()
        if (now - self.last_backup) > DATABASE_CONFIG['backup_interval']:
            self._create_backup()
            self.last_backup = now

    def _create_backup(self):
        """Create timestamped database backup"""
        os.makedirs(DATABASE_CONFIG['backup_dir'], exist_ok=True)
        backup_path = os.path.join(
            DATABASE_CONFIG['backup_dir'],
            f"kfm_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
        )
        
        try:
            with open(DATABASE_CONFIG['path'], 'rb') as src:
                with open(backup_path, 'wb') as dst:
                    dst.write(src.read())
            logging.info(f"Database backup created: {backup_path}")
        except IOError as e:
            logging.error(f"Backup failed: {str(e)}")

# ======================
# BLE COMMUNICATION
# ======================
class BLEManager:
    """Handles BLE communication for angle data with session tracking"""
    
    def __init__(self):
        self.client = None
        self.connected = False
        self.callback = None
        self.current_angle = 0.0
        self.max_angle = 0.0
        self.hold_time = 0.0
        self.last_update_time = None
        self.session_data = []

    async def connect(self):
        """Establish BLE connection with retries"""
        self._reset_session()
        for attempt in range(BLE_CONFIG['retries']):
            try:
                self.client = BleakClient(
                    BLE_CONFIG['address'],
                    timeout=BLE_CONFIG['timeout']
                )
                await self.client.connect()
                self.connected = True
                await self._enable_notifications()
                logging.info("BLE connection established")
                return True
            except BleakError as e:
                logging.warning(f"Connection attempt {attempt+1} failed: {str(e)}")
                await asyncio.sleep(2**attempt)
        logging.error("BLE connection failed after retries")
        return False

    def _reset_session(self):
        """Reset tracking variables for new session"""
        self.current_angle = 0.0
        self.max_angle = 0.0
        self.hold_time = 0.0
        self.last_update_time = None

    async def _enable_notifications(self):
        """Enable notifications for angle data"""
        try:
            uuid = BLE_CONFIG['uuids']['angle']
            await self.client.start_notify(uuid, self._angle_callback)
            logging.info(f"Notifications enabled for UUID: {uuid}")
        except BleakError as e:
            logging.error(f"Notification setup failed: {str(e)}")
            raise

    def _angle_callback(self, sender, data):
        """Process incoming angle data"""
        try:
            # Unpack the float value properly
            new_angle = struct.unpack('<f', data)[0]  # Little-endian float
            
            # Update tracking
            self.current_angle = new_angle
            if abs(new_angle) > abs(self.max_angle):
                self.max_angle = new_angle
                self.hold_time = 0.0
                self.last_update_time = time.time()
            
            # Update hold time
            current_time = time.time()
            if self.last_update_time:
                time_diff = current_time - self.last_update_time
                if abs(new_angle - self.current_angle) < 0.1:  # Threshold
                    self.hold_time += time_diff
            
            self.last_update_time = current_time
            
            # Session detection
            if new_angle == 0 and self.max_angle != 0:
                self._store_session()
                self._reset_session()
            
            # Callback handling
            if self.callback:
                if callable(self.callback):
                    self.callback(new_angle)  # Pass just the angle value
                else:
                    logging.warning("Callback is not callable")
                    
        except Exception as e:
            logging.error(f"Error processing angle data: {e}")
            if hasattr(data, 'hex'):
                logging.error(f"Raw data: {data.hex()}")

    def _store_session(self):
        """Store completed session data"""
        self.session_data.append({
            'max_angle': self.max_angle,
            'hold_time': self.hold_time,
            'timestamp': datetime.datetime.now().isoformat()  # Fixed datetime usage
        })
        logging.info(f"Session stored: Max={self.max_angle}°, Hold={self.hold_time:.2f}s")

    async def safe_read(self, uuid):
        """Read characteristic with error handling"""
        if not self.connected:
            return None
        try:
            return await self.client.read_gatt_char(uuid)
        except BleakError as e:
            logging.error(f"Read failed for {uuid}: {str(e)}")
            return None

    async def disconnect(self):
        """Graceful disconnection"""
        if self.connected and self.client:
            if self.max_angle != 0:
                self._store_session()
            await self.client.disconnect()
            self.connected = False
            logging.info("BLE disconnected")
            
# ======================
# CORE FUNCTIONALITY 
# ======================
def is_valid_session(max_angle, hold_time):
    """
    Checks if a session is valid based on max angle and hold time.
    Returns True if the session should be recorded, False if it should be ignored.
    """
    global first_valid_session_detected
    
    # Wait for the first valid session (angle must first drop below threshold)
    if not first_valid_session_detected:
        if max_angle < ANGLE_THRESHOLD:
            return False  # Ignore until we see a valid session start
        first_valid_session_detected = True  # Start recording valid sessions after this point
    
    # Filter out sessions that have unreasonable values
    if hold_time > MAX_HOLD_TIME:
        print("Ignoring session: Hold time too high.")
        return False
    if max_angle > MAX_VALID_ANGLE:
        print("Ignoring session: Max angle too high.")
        return False

    return True

class FlexionAnalyzer:
    def __init__(self, db_manager):
        self.db = db_manager
        self.current_session = None
        self.session_history = []
        self.live_angles = []
        self.emulation_mode = False
        
    def start_session(self):
        """Initialize new therapy session"""
        self.current_session = {
            'session_id': hashlib.sha256(
                str(time.time()).encode()
            ).hexdigest()[:12],
            'start_time': datetime.now(),
            'angle_data': [],
            'max_angle': 0.0,
            'hold_start': None
        }
        logging.info(f"Session started: {self.current_session['session_id']}")

    def update(self, angle):
        """Process new angle reading"""
        if not self.current_session:
            return
            
        timestamp = datetime.now()
        self.live_angles.append(angle)
        self.current_session['angle_data'].append((timestamp, angle))
        
        if angle > self.current_session['max_angle']:
            self.current_session['max_angle'] = angle
            
        if angle >= KFMS_PARAMS['theta_target']:
            if not self.current_session['hold_start']:
                self.current_session['hold_start'] = time.time()
        else:
            self.current_session['hold_start'] = None

    def finalize_session(self):
        """Complete current session and store data"""
        if not self.current_session:
            return
        
        duration = (datetime.now() - self.current_session['start_time']).total_seconds()
        hold_time = self._calculate_hold_time()
        stability = self._calculate_stability()  # Calculate stability
        kfms = self._calculate_kfms(hold_time)
    
        session_record = {
            'session_id': self.current_session['session_id'],
            'start_time': self.current_session['start_time'],
            'duration': duration,
            'max_angle': self.current_session['max_angle'],
            'kfms_score': kfms,
            'stability_index': stability  # Add stability to record
            }
    
        if self.db.store_session(session_record, self.current_session['angle_data']):
            self.session_history.append(session_record)
            logging.info(f"Session stored: {self.current_session['session_id']}")
        else:
            logging.error("Failed to store session data")
            
            self.current_session = None
            self.live_angles = []
    
    def _calculate_hold_time(self):
        """Calculate total time in target position"""
        if not self.current_session['hold_start']:
            return 0.0
        return time.time() - self.current_session['hold_start']

    def _calculate_kfms(self, hold_time):
        """Calculate Knee Flexion Monitor Score"""
        rom_component = (self.current_session['max_angle']/KFMS_PARAMS['theta_target']) * KFMS_PARAMS['alpha']
        endurance_component = (hold_time/KFMS_PARAMS['t_target']) * KFMS_PARAMS['beta']
        control_component = self._calculate_stability() * KFMS_PARAMS['gamma']
        return min((rom_component + endurance_component + control_component) * 100, 100)

    def _calculate_stability(self):
        """Calculate movement stability metric"""
        if len(self.live_angles) < 2:
            return 0.0
            
        angles = np.array(self.live_angles)
        return np.mean(angles)/self.current_session['max_angle'] * (1 - np.std(angles)/self.current_session['max_angle'])

    def generate_clinical_report(self):
        """Generates comprehensive PDF-style report"""
        if not self.session_history:
            return "No session data available"
        
        avg_hold = sum(s['duration'] for s in self.session_history)/len(self.session_history)
        avg_angle = sum(s['max_angle'] for s in self.session_history)/len(self.session_history)
        avg_kfms = sum(s['kfms_score'] for s in self.session_history)/len(self.session_history)
        avg_stability = sum(s['stability_index'] for s in self.session_history)/len(self.session_history)
            
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
            "| Timestamp           | Duration  | Max Angle  | KFMS  |\n"
            "|---------------------|-----------|------------|-------|\n"
        )
        
        for session in self.session_history:
            report_content += (
                f"| {session['start_time'].strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{session['duration']:>8.1f}s | {session['max_angle']:>9.1f}° | "
                f"{session['kfms_score']:>5.1f} |\n"
            )
        report_content += (
        "|---------------------|-----------|------------|-------|\n"
        f"| AVERAGES            |{avg_hold:>9.1f}s | {avg_angle:>9.1f}° | {avg_kfms:>5.1f} |\n\n "
        ) 
        
        report_content += (
            f"\n\nLEGAL DISCLAIMER:\n"
            f"The Knee Flexion Monitor (KFM) system is intended for informational purposes only.\n"
            f"Consult a medical professional before making any healthcare decisions.\n"
        )
        
        os.makedirs("reports", exist_ok=True)
        filename = f"reports/KFMS_Report_{PATIENT_METADATA['patient_name']}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        with open(filename, 'w') as f:
            f.write(report_content)
            
        return filename

# ======================
# TESTING & EMULATION
# ======================
async def mock_data_generator(analyzer):
    """Generates realistic rehabilitation sessions for testing"""
    print("\n=== Starting KFMS Simulation ===")
    
    for session in range(1, 11):
        target = random.gauss(mu=75, sigma=15)
        target = min(max(target, 0), 150)
        hold = random.gauss(mu=5000, sigma=2000)
        hold = min(max(hold, 0), 10000)
        
        print(f"Session {session}: Target {target:.1f}°, Hold {hold/1000:.1f}s")
        
        # Session initiation
        analyzer.start_session()
        
        # Flexion phase
        for angle in np.linspace(0, target, num=10):
            analyzer.update(float(angle))
            await asyncio.sleep(0.1)
            
        # Hold phase
        start = time.time()
        while (time.time() - start) < (hold/1000):
            analyzer.update(float(target))
            await asyncio.sleep(0.2)
            
        # Return phase
        for angle in np.linspace(target, 0, num=10):
            analyzer.update(float(angle))
            await asyncio.sleep(0.1)
            
        analyzer.finalize_session()
        await asyncio.sleep(1)
    
    print("=== Simulation Complete ===")

# ======================
# SYSTEM INITIALIZATION
# ======================
app = Flask(__name__)
db_manager = DatabaseManager()
analyzer = FlexionAnalyzer(db_manager)
ble_manager = BLEManager()

@app.route('/api/live')
def live_data():
    return jsonify({
        'active': bool(analyzer.current_session),
        'current_angle': analyzer.live_angles[-1] if analyzer.live_angles else 0.0,
        'max_angle': analyzer.current_session['max_angle'] if analyzer.current_session else 0.0
    })

@app.route('/api/sessions')
def session_history():
    return jsonify(analyzer.session_history)

async def main_loop():
    """Main data acquisition and processing loop"""
        
    def angle_handler(angle):
        if not analyzer.current_session and angle > 5.0:
            analyzer.start_session()
        elif analyzer.current_session and angle <= 5.0:
            analyzer.finalize_session()
            
        if analyzer.current_session:
            analyzer.update(angle)
        
    ble_manager = BLEManager()
    ble_manager.callback = angle_handler
    
    try:
        while True:
            await asyncio.sleep(1)
            await ble_manager.safe_read(BLE_CONFIG['uuids']['angle'])
    except KeyboardInterrupt:
        pass
    finally:
        await ble_manager.disconnect()
        if analyzer.current_session:
            analyzer.finalize_session()

def signal_handler(sig, frame):
    """Handle system shutdown signals"""
    logging.info("Shutting down system...")
    asyncio.run(ble_manager.disconnect())
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Start Flask API
    flask_thread = Thread(target=lambda: app.run(
        host='0.0.0.0', 
        port=5000, 
        use_reloader=False
    ))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Run appropriate mode
    if "--test" in sys.argv:
        logging.info("Starting in TEST MODE")
        try:
            asyncio.run(mock_data_generator(analyzer))
        except KeyboardInterrupt:
            pass
        finally:
            report_file = analyzer.generate_clinical_report()
            logging.info(f"\nClinical Report Generated: {report_file}")
    else:
        logging.info("Starting in LIVE MODE")
        asyncio.run(main_loop())