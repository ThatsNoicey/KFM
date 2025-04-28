import asyncio
import struct
import math
import sqlite3
import logging
import os
from datetime import datetime
from bleak import BleakClient
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image)
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import smtplib
from flask import Flask, render_template, jsonify, request
import time
import threading
import csv
from flask_cors import CORS

# ======================
# CONFIGURATION SECTION
# ======================
BLE_CONFIG = {
    'device_name': 'KneeFlexSensor',
    'address': 'c8:c9:a3:e5:fd:3e',  # Replace with actual address
    'uuids': {
        'angle': '12345678-1234-5678-1234-56789abcdef1',    # REPLACE
        'max_angle': '12345678-1234-5678-1234-56789abcdef3',# REPLACE
        'hold_time': '12345678-1234-5678-1234-56789abcdef2' # REPLACE
    },
    'retries': 5,
    'timeout': 15.0
}

KFMS_PARAMS = {
    'theta_target': 120.0,    # Degrees
    't_target': 10.0,         # Seconds
    'alpha': 0.4,             # ROM weight
    'beta': 0.4,              # Endurance weight
    'gamma': 0.2,             # Control weight
    'baseline_sessions': 3,
    'flex_threshold': 30.0    # Degrees
}

SAFETY_LIMITS = {
    'max_angle': 150.0,       # Degrees
    'max_hold': 30.0       # Seconds
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('kfm_system.log'),
        logging.StreamHandler()
    ]
)
EMAIL_CONFIG = {
    'smtp_server': 'smtp.gmail.com',  # For Gmail
    'smtp_port': 587,  
    'sender_email': 'wo1wearablemedtech@gmail.com',
    'sender_password': 'opmp ztjj nmvt syad',  
    'recipient_email': 'jacob@frusher.co.uk',
    'get_subject': lambda patient_name: f'KFM Report - {patient_name} - {datetime.now().strftime("%Y-%m-%d")}'
}

app = Flask(__name__)
CORS(app)
data_lock = threading.Lock()
current_angle = 0.0
max_angle = 0.0
hold_time = 0.0
threshold = 30.0
sessions = []
session_start = None
patient_id = None

# ======================
# DATA RECORDER
# ======================
class DataRecorder:
    def __init__(self):
        self.ble_client = None
        self.ble_connected = False

    def start_session(self, patient_id):
        global session_start, max_angle, current_angle, hold_time
        with data_lock:
            session_start = time.time()
            max_angle = 0.0
            current_angle = 0.0
            hold_time = 0.0
            logging.info(f"Session started for {patient_id}")

    def add_data_point(self, angle):
        global current_angle, max_angle, hold_time
        with data_lock:
            current_angle = angle
            if angle > max_angle:
                max_angle = angle
            if session_start:
                hold_time = time.time() - session_start

    def stop_session(self):
        global session_start, sessions
        with data_lock:
            if session_start:
                sessions.append({
                    'max': max_angle,
                    'duration': hold_time,
                    'timestamp': datetime.now().isoformat()
                })
                session_start = None
                logging.info(f"Session saved: Max {max_angle}°, {hold_time}s")
recorder = DataRecorder()
# ======================
# CORE SYSTEM CLASS
# ======================
class KFM_Analyzer:
    def __init__(self, patient_name):
        self.patient_name = patient_name
        self.client = None
        self.connected = False
        self.should_run = True
        
        # Initialize session state
        self._reset_session()
        
        # Historical data
        self.session_history = []
        self.kfms_history = []
        
        # Database setup
        self.db_file = f"kfm_data_{self.patient_name}.db"
        self._init_database()
        
        # Ensure reports directory exists
        os.makedirs('reports', exist_ok=True)

    # ======================
    # SESSION MANAGEMENT
    # ======================
    def _reset_session(self):
        """Initialize/reset all session parameters"""
        self.current_session = {
            'start_time': None,
            'max_angle': 0.0,
            'hold_time': 0.0,
            'live_angles': [],
            'session_active': False
        }
    
    def _start_new_session(self):
        """Begin tracking new rehabilitation session"""
        self.current_session.update({
            'start_time': datetime.now(),
            'session_active': True
        })
        logging.info("New session started")

    # ======================
    # SHUTDOWN HANDLING
    # ======================
    async def shutdown(self):
        """Graceful shutdown with email reporting"""
        try:
            logging.info("Starting shutdown sequence...")
        
            if self.current_session.get('session_active'):
                logging.info("Finalizing active session...")
                self._finalize_session()
        
            if self.session_history:
                # Generate and email the report
                pdf_path = self.generate_report()
                if pdf_path and pdf_path.endswith('.pdf'):
                    self._send_email_with_attachment(pdf_path)
                else:
                        logging.warning("No PDF report to email")
        
            if self.connected and self.client:
                await self.client.disconnect()
                logging.info("BLE connection terminated")
            
        except Exception as e:
            logging.error(f"Shutdown error: {str(e)}")
        finally:
            self.connected = False

    # ======================
    # DATABASE OPERATIONS
    # ======================
    def _init_database(self):
        """Initialize SQLite database with failsafe"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                c = conn.cursor()
                c.execute('''CREATE TABLE IF NOT EXISTS sessions
                            (timestamp TEXT PRIMARY KEY,
                             max_angle REAL,
                             hold_time REAL,
                             kfms REAL,
                             stability REAL)''')
                conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Database initialization failed: {e}")
            raise

    def _store_session_db(self, session_data):
        """Safely store session data in database"""
        try:
            with sqlite3.connect(self.db_file) as conn:
                c = conn.cursor()
                c.execute('''INSERT INTO sessions VALUES 
                            (?, ?, ?, ?, ?)''',
                         (session_data['timestamp'],
                          session_data['max_angle'],
                          session_data['hold_time'],
                          session_data['kfms'],
                          session_data['stability']))
                conn.commit()
        except sqlite3.IntegrityError:
            logging.warning("Duplicate session timestamp detected")
        except sqlite3.Error as e:
            logging.error(f"Database write failed: {e}")

    # ======================
    # BLE COMMUNICATIONS
    # ======================

    async def run_ble_loop(self):
        """Modified BLE event loop"""
        while self.should_run:
            if not self.connected:
                await self.connect()
            await asyncio.sleep(1)

    def start_ble_thread(self):
        """Start BLE thread with proper cleanup"""
        def run_loop():
            try:
                asyncio.run(self.run_ble_loop())
            except Exception as e:
                logging.error(f"BLE thread crashed: {str(e)}")

        self.ble_thread = threading.Thread(target=run_loop, daemon=True)
        self.ble_thread.start()
    async def connect(self):
     """Connect to BLE device with exponential backoff"""
     for attempt in range(BLE_CONFIG['retries']):
         try:
             self.client = BleakClient(BLE_CONFIG['address'])
             await self.client.connect(timeout=BLE_CONFIG['timeout'])
             self.connected = True
             
             # Setup notifications
             await self._setup_notifications()
             logging.info("BLE connected and notifications enabled")
             return True
             
         except Exception as e:
             logging.warning(f"Connection attempt {attempt+1} failed: {str(e)}")
             await asyncio.sleep(2 ** attempt)
             
     logging.error("Maximum connection attempts reached")
     return False

    async def _setup_notifications(self):
     """Configure all BLE notifications"""
     try:
         # Validate UUID format first
         from bleak.uuids import normalize_uuid_str
         for uuid_key in ['angle', 'max_angle', 'hold_time']:
             normalize_uuid_str(BLE_CONFIG['uuids'][uuid_key])

         # Configure notifications
         await self.client.start_notify(
             BLE_CONFIG['uuids']['angle'],
             self._angle_callback
         )
         await self.client.start_notify(
             BLE_CONFIG['uuids']['max_angle'],
             self._max_angle_callback
         )
         await self.client.start_notify(
             BLE_CONFIG['uuids']['hold_time'],
             self._hold_time_callback
         )
         
     except ValueError as e:
         logging.error(f"Invalid UUID format: {e}")
         raise
     except Exception as e:
         logging.error(f"Notification setup failed: {e}")
         raise
    # ======================
    # DATA PROCESSING
    # ======================
    def _angle_callback(self, sender, data):
        """Process incoming angle data"""
        try:
            angle = struct.unpack('<f', data)[0]
            logging.info(f"BLE Received Angle: {angle}°")
            with data_lock:
                recorder.add_data_point(angle)
                # Safety check
                if not (0 <= abs(angle) <= SAFETY_LIMITS['max_angle']):
                    logging.error(f"Invalid angle value: {angle}")
                    return
                    
                # Session detection logic
                if angle > KFMS_PARAMS['flex_threshold']:
                    if not self.current_session['session_active']:
                        self._start_new_session()
                    self._update_session(angle)
                elif self.current_session['session_active']:
                    self._finalize_session()
                    
        except struct.error as e:
            logging.error(f"Angle data unpack error: {e}")
    def _led_down_angle(self, angle):
        
        angle = 180 - 2*angle
        
    def _max_angle_callback(self, sender, data):
        """Process max angle updates with validation"""
        try:
            if not self.current_session.get('session_active'):
                logging.debug("Max angle update outside active session")
                return

            max_angle = struct.unpack('<f', data)[0]
            with data_lock:
                if max_angle > recorder.max_angle:
                    recorder.max_angle = max_angle
            # Validate value range
            if not (0 <= max_angle <= SAFETY_LIMITS['max_angle']):
                logging.error(f"Invalid max angle: {max_angle}")
                return
                
            # Update session only if greater than current
            current_max = self.current_session.get('max_angle', 0.0)
            if max_angle > current_max:
                self.current_session['max_angle'] = max_angle
                logging.debug(f"New max angle: {max_angle}°")
                
        except struct.error as e:
            logging.error(f"Max angle unpack error: {e}")
        except KeyError as e:
            logging.critical(f"Session state error: {e}")
            self._reset_session()

    def _hold_time_callback(self, sender, data):
        """Process hold time updates safely"""
        try:
            if not self.current_session.get('session_active'):
                return

            hold_time = struct.unpack('<L', data)[0] / 1000  # ms to seconds
            if 0 <= hold_time <= SAFETY_LIMITS['max_hold']:
                self.current_session['hold_time'] = hold_time
            else:
                logging.warning(f"Invalid hold time: {hold_time}s")
                
        except struct.error as e:
            logging.error(f"Hold time unpack error: {e}")

    def _update_session(self, angle):
        """Update session with new valid data"""
        try:
            self.current_session['live_angles'].append(angle)
            # Keep last 1000 samples for stability calculations
            if len(self.current_session['live_angles']) > 1000:
                self.current_session['live_angles'].pop(0)
        except KeyError:
            self._reset_session()
            logging.error("Session state corrupted, resetting")

    def _finalize_session(self):
        """Complete current session and calculate metrics"""
        try:
            if len(self.current_session['live_angles']) < 10:
                logging.warning("Discarding short session")
                return
                
            # Calculate metrics
            stability = self._calculate_stability_index()
            kfms = self._calculate_kfms()
            
            # Store session
            session_data = {
                'timestamp': self.current_session['start_time'].isoformat(),
                'max_angle': self.current_session['max_angle'],
                'hold_time': self.current_session['hold_time'],
                'stability': stability,
                'kfms': kfms
            }
            
            self.session_history.append(session_data)
            self._store_session_db(session_data)
            self.kfms_history.append(kfms)
            
            logging.info(f"Session finalized: Max {session_data['max_angle']}°")
            self._reset_session()
            
        except KeyError as e:
            logging.error(f"Session finalization error: {e}")
            self._reset_session()

    # ======================
    # ANALYTICS ENGINE
    # ======================
    def _calculate_stability_index(self):
        """Compute movement control metric"""
        angles = self.current_session.get('live_angles', [])
        if len(angles) < 2 or self.current_session.get('max_angle', 0) == 0:
            return 0.0
            
        mean = sum(angles) / len(angles)
        variance = sum((x - mean)**2 for x in angles) / (len(angles)-1)
        sd = math.sqrt(variance)
        
        return (mean / self.current_session['max_angle']) * (1 - (sd / self.current_session['max_angle']))

    def _calculate_kfms(self):
        """Calculate Knee Flexion Monitor Score"""
        try:
            # Base components
            rom = (self.current_session['max_angle'] / KFMS_PARAMS['theta_target']) * KFMS_PARAMS['alpha']
            endurance = (self.current_session['hold_time'] / KFMS_PARAMS['t_target']) * KFMS_PARAMS['beta']
            control = self._calculate_stability_index() * KFMS_PARAMS['gamma']
            
            # Progression factor
            progression = self._calculate_progression_factor(rom + endurance + control)
            
            return min((rom + endurance + control) * math.sqrt(progression) * 100, 100)
        except KeyError as e:
            logging.error(f"KFMS calculation error: {e}")
            return 0.0

    def _calculate_progression_factor(self, current_score):
        """Dynamic difficulty adjustment"""
        try:
            if len(self.kfms_history) < KFMS_PARAMS['baseline_sessions']:
                return 1.0
                
            baseline = sum(self.kfms_history[:KFMS_PARAMS['baseline_sessions']]) / KFMS_PARAMS['baseline_sessions']
            return 1 + ((current_score - baseline) / 100)
        except IndexError:
            return 1.0

    # ======================
    # REPORTING SYSTEM
    # ======================
    def _generate_kfms_plot(self):
        """Generate daily progress plot with error bars from database"""
        try:
            # Connect to patient-specific database
            db_path = f"kfm_data_{self.patient_name}.db"
            with sqlite3.connect(db_path) as conn:
                # Get all historical data including current session
                query = '''SELECT timestamp, kfms FROM sessions'''
                df = pd.read_sql(query, conn)
            
            # Convert timestamps and extract dates
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['date'] = df['timestamp'].dt.date
        
            # Add current session data if exists
            if self.current_session.get('session_active'):
                current_df = pd.DataFrame([{
                    'timestamp': self.current_session['start_time'],
                    'kfms': self._calculate_kfms()
                    }])
                df = pd.concat([df, current_df], ignore_index=True)

            # Calculate daily statistics
            daily_stats = df.groupby('date')['kfms'].agg(['mean', 'std', 'count']).reset_index()
        
            # Create plot
            plt.figure(figsize=(10, 6))
        
            # Plot individual points with actual timestamps
            plt.scatter(
                df['timestamp'], df['kfms'],
                alpha=0.3, color='#3498db', label='Individual Measurements',
                zorder=1
                )
        
        # Plot daily means with error bars at noon for alignment
            plot_dates = [datetime.combine(d, datetime.strptime("12:00", "%H:%M").time()) 
                          for d in daily_stats['date']]
        
        # Handle days with single measurements (no error bar)
            valid_stats = daily_stats[daily_stats['count'] > 1]
            single_stats = daily_stats[daily_stats['count'] == 1]
        
        # Plot error bars for days with multiple measurements
            plt.errorbar(
                plot_dates, valid_stats['mean'], 
                yerr=valid_stats['std'],
                fmt='o--', color='#e74c3c', markersize=10,
                linewidth=2, capsize=6, capthick=2,
                label='Daily Mean ±1 SD (n≥2)', zorder=2
                )
        
        # Plot single-measurement days without error bars
            plt.scatter(
                [datetime.combine(d, datetime.min.time()) for d in single_stats['date']],
                single_stats['mean'],
                color='#e74c3c', marker='*', s=200,
                label='Single Measurements', zorder=3
                )
        
        # Style plot
            plt.title(f'KFMS Progress: {self.patient_name}\n', fontsize=16, fontweight='bold')
            plt.xlabel('\nSession Date', fontsize=12, style='italic')
            plt.ylabel('KFMS Score\n', fontsize=12, style='italic')
            plt.grid(True, alpha=0.3, linestyle='--')
            plt.xticks(rotation=45, fontsize=10)
            plt.yticks(fontsize=10)
            plt.legend(loc='upper left', framealpha=0.9)
            plt.tight_layout()
        
        # Save plot
            plot_path = f"reports/{self.patient_name}_kfms_plot_{datetime.now().strftime('%Y%m%d')}.png"
            plt.savefig(plot_path, dpi=300, bbox_inches='tight')
            plt.close()
        
            return plot_path

        except sqlite3.Error as e:
            logging.error(f"Database error during plotting: {e}")
            return None
        except Exception as e:
            logging.error(f"Plot generation failed: {e}")
            return None
        
    def _send_email_with_attachment(self, file_path):

        try:
            # Create email message
            msg = MIMEMultipart()
            msg['From'] = EMAIL_CONFIG['sender_email']
            msg['To'] = EMAIL_CONFIG['recipient_email']
            msg['Subject'] = EMAIL_CONFIG['get_subject'](self.patient_name)

            body = f"""
            Knee Flexion Monitor Report for: {self.patient_name}
            Generated on: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            """
            msg.attach(MIMEText(body, 'plain'))

        # Attach the PDF
            with open(file_path, 'rb') as f:
                attach = MIMEApplication(f.read(), _subtype='pdf')
                attach.add_header(
                    'Content-Disposition',
                    'attachment',
                    filename=os.path.basename(file_path)
                    )
                msg.attach(attach)

        # Send email
            with smtplib.SMTP(
                    EMAIL_CONFIG['smtp_server'],
                    EMAIL_CONFIG['smtp_port']
                    ) as server:
                server.starttls()
                server.login(
                    EMAIL_CONFIG['sender_email'],
                    EMAIL_CONFIG['sender_password']
                    )
                server.send_message(msg)

            logging.info(f"Email sent to {EMAIL_CONFIG['recipient_email']}")

        except Exception as e:
            logging.error(f"Email failed: {str(e)}")
            
    def generate_report(self):
        """Generate PDF clinical report with error bar plot"""
        if not self.session_history:
            return "No session data available"
        
        try:
            # Create plot
            plot_path = self._generate_kfms_plot()
        
        # Setup PDF document
            styles = getSampleStyleSheet()
            doc = SimpleDocTemplate(
                f"reports/KFMReport_{self.patient_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                pagesize=A4
                )
            elements = []
        
        # Add header
            elements.append(Paragraph(
                f"<b>Knee Flexion Monitor Report</b><br/>"
                f"Patient: {self.patient_name}<br/>"
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                styles['Title']
                ))
            elements.append(Spacer(1, 0.5*inch))
        
        # Add clinical guidance table
            guidance_table = Table([
                ['KFMS Range', 'Classification', 'Rehabilitation Stage'],
                ['0-39', 'High Risk', 'Immediate Intervention'],
                ['40-59', 'Limited Efficacy', 'Focused Training'],
                ['60-79', 'Functional Recovery', 'Strengthening Program'],
                ['80-100', 'Optimal Performance', 'Maintenance Protocol']
                ], colWidths=[1.5*inch, 2*inch, 2.5*inch])
            guidance_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2874a6')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey)
                ]))
            elements.append(guidance_table)
            elements.append(Spacer(1, 0.3*inch))
        
        # Add plot
            elements.append(Paragraph("<b>History</b>", styles['Heading2']))
            elements.append(Image(plot_path, width=6*inch, height=3*inch))
            elements.append(Spacer(1, 0.3*inch))
        
        # Add session data table
            elements.append(Paragraph("<b>Daily Performance</b>", styles['Heading2']))
            table_data = [['Timestamp', 'Hold Time', 'Max Angle', 'KFMS', 'Stability']]
            for session in self.session_history:
                table_data.append([
                    session['timestamp'][:19],
                    f"{session['hold_time']:.1f}s",
                    f"{session['max_angle']:.1f}°",
                    f"{session['kfms']:.1f}",
                    f"{session['stability']:.3f}"
                    ])
            
            session_table = Table(table_data, colWidths=[2*inch, 1*inch, 1.2*inch, 1*inch, 1.2*inch])
            session_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#3498db')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey)
                ]))
            elements.append(session_table)
            elements.append(Spacer(1, 0.3*inch))
        
        # Add averages
            avg_hold = np.mean([s['hold_time'] for s in self.session_history])
            avg_angle = np.mean([s['max_angle'] for s in self.session_history])
            avg_kfms = np.mean([s['kfms'] for s in self.session_history])
            avg_stability = np.mean([s['stability'] for s in self.session_history])
        
            avg_table = Table([
                ['Metric', 'Average Value'],
                ['Hold Time', f"{avg_hold:.1f} ± {np.std([s['hold_time'] for s in self.session_history]):.1f}s"],
                ['Max Angle', f"{avg_angle:.1f} ± {np.std([s['max_angle'] for s in self.session_history]):.1f}°"],
                ['KFMS Score', f"{avg_kfms:.1f} ± {np.std([s['kfms'] for s in self.session_history]):.1f}"],
                ['Stability', f"{avg_stability:.3f} ± {np.std([s['stability'] for s in self.session_history]):.3f}"]
                ])
            avg_table.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2ecc71')),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey)
                ]))
            elements.append(avg_table)
        
        # Add disclaimer
            elements.append(Spacer(1, 0.5*inch))
            elements.append(Paragraph(
                f"LEGAL DISCLAIMER:\n{self._get_disclaimer()}",
                ParagraphStyle(name='Disclaimer', fontSize=8, textColor=colors.grey)
                ))
        
        # Build PDF
            pdf_path = f"reports/KFMReport_{self.patient_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            doc.build(elements)
            return pdf_path  # Just the path, no extra text

        except Exception as e:
            logging.error(f"Report generation error: {e}")
            return f"Error generating report: {str(e)}"

    def _get_disclaimer(self):
        return (
            "The Knee Flexion Monitor (KFM) system is for informational purposes only.\n"
            "It is not a medical device. Consult a healthcare professional for medical advice.\n"
            "No liability is assumed for use or interpretation of this data.\n"
        )
@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/data')
def get_data():
    with data_lock:
        return jsonify({
            'current_angle': current_angle,
            'max_angle': max_angle,
            'hold_time': hold_time,
            'threshold': threshold,
            'sessions': sessions[-10:]
        })

@app.route('/api/start', methods=['POST'])
def start_session():
    global patient_id
    data = request.json
    patient_id = data.get('patient_id')
    
    if not patient_id:
        return jsonify({'error': 'Patient ID required'}), 400
        
    recorder.start_session(patient_id)
    return jsonify({'status': 'Recording started'})

@app.route('/api/stop', methods=['POST'])
def stop_session():
    global session_start, sessions, max_angle, hold_time
    try:
        with data_lock:
            if session_start is None:
                return jsonify({'error': 'No active session'}), 400
            
            duration = round(time.time() - session_start, 2)
            sessions.append({
                'timestamp': datetime.now().isoformat(),
                'max': max_angle,
                'duration': duration
            })
            
            # Print session details to console
            print(f"\n=== Session Recorded ===\n"
                  f"Patient: {patient_id}\n"
                  f"Max Angle: {max_angle}°\n"
                  f"Duration: {duration}s\n"
                  f"Timestamp: {datetime.now().isoformat()}\n"
                  f"========================")
            
            session_start = None
            max_angle = 0.0
            hold_time = 0.0
            
            return jsonify({
                'status': 'Session stopped',
                'session_data': sessions[-1]
            })
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify(error="Endpoint not found"), 404

# ======================
# MAIN SYSTEM LOOP
# ======================
async def main(patient_name):
    analyzer = KFM_Analyzer(patient_name)
    
    try:
        if await analyzer.connect():
            logging.info("System ready - monitoring sessions (Press CTRL+C to exit)...")
            while True:
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        logging.info("\nShutdown requested...")
    finally:
        await analyzer.shutdown()
        logging.info("System shutdown complete")

# At the bottom of your file, modify the __main__ block:
if __name__ == "__main__":
    # Initialize analyzer
    analyzer = KFM_Analyzer("default_patient")
    
    # Start BLE thread
    analyzer.start_ble_thread()
    
    # Create data directories
    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)
    
    # Start Flask with proper cleanup
    try:
        app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        logging.info("Server shutting down...")
    finally:
        analyzer.should_run = False
        if hasattr(analyzer, 'ble_thread'):
            analyzer.ble_thread.join(timeout=5)