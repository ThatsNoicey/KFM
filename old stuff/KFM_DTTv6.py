import asyncio
import struct
import math
import sqlite3
import logging
import os
import threading
from enum import Enum, auto
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
from flask_cors import CORS

# ======================
# CONFIGURATION SECTION
# ======================
BLE_CONFIG = {
    'device_name': 'KneeFlexSensor',
    'address': 'c8:c9:a3:e5:fd:3e',
    'uuids': {
        'angle': '12345678-1234-5678-1234-56789abcdef1',
        'max_angle': '12345678-1234-5678-1234-56789abcdef3',
        'hold_time': '12345678-1234-5678-1234-56789abcdef2'
    },
    'retries': 5,
    'timeout': 15.0
}

KFMS_PARAMS = {
    'theta_target': 120.0,
    't_target': 10.0,
    'alpha': 0.4,
    'beta': 0.4,
    'gamma': 0.2,
    'baseline_sessions': 3,
    'flex_threshold': 30.0
}

SAFETY_LIMITS = {
    'max_angle': 150.0,
    'max_hold': 30.0
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
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'sender_email': 'wo1wearablemedtech@gmail.com',
    'sender_password': 'opmp ztjj nmvt syad',
    'recipient_email': 'jacob@frusher.co.uk',
    'get_subject': lambda patient_name: f'KFM Report - {patient_name} - {datetime.now().strftime("%Y-%m-%d")}'
}

# ======================
# STATE MANAGEMENT
# ======================
class SessionState(Enum):
    INACTIVE = auto()
    STARTING = auto()
    ACTIVE = auto()
    STOPPING = auto()

app = Flask(__name__)
CORS(app)
data_lock = threading.RLock()
analyzer = None  # Will be initialized later

# ======================
# CORE SYSTEM CLASS
# ======================
class KFM_Analyzer:
            
    def __init__(self, patient_name):
        self.patient_name = patient_name
        self.client = None
        self.connected = False
        self.should_run = True
        
        # Session state management
        self.session_state = SessionState.INACTIVE
        self.session_lock = threading.RLock()
        self._reset_session()
        
        # Historical data
        self.session_history = []
        self.kfms_history = []
        
        # Database setup
        self.db_file = f"kfm_data_{self.patient_name}.db"
        self._init_database()
        
        os.makedirs('reports', exist_ok=True)
        

    def _reset_session(self):
        with self.session_lock:  # Add lock acquisition
            self.current_session = {
                'start_time': None,
                'max_angle': 0.0,
                'hold_time': 0.0,
                'live_angles': [],
                'low_angle_start': None
            }
        logging.debug("Session data reset")

    def _set_session_state(self, new_state):
        with self.session_lock:
            old_state = self.session_state
            self.session_state = new_state
            logging.info(f"Session state: {old_state.name} -> {new_state.name}")

    # ======================
    # SESSION STATE METHODS
    # ======================
    def session_is_active(self):
        with self.session_lock:
            return self.session_state == SessionState.ACTIVE

    def session_is_inactive(self):
        with self.session_lock:
            return self.session_state == SessionState.INACTIVE

    def _start_new_session(self, initial_angle: float):
        """Validated session startup sequence"""
        with self.session_lock:
            if self.session_state != SessionState.INACTIVE:
                logging.warning("Session start requested while already active")
                return
    
            logging.info(f"Starting new session with initial angle: {initial_angle}°")
            self._reset_session()
            self.current_session.update({
                'start_time': datetime.now(),
                'max_angle': initial_angle,
                'low_angle_start': None
            })
            self._set_session_state(SessionState.ACTIVE)
    
    def _check_session_health(self):
        """Regularly called safety check for stuck sessions"""
        with self.session_lock:
            if self.session_is_active():
                duration = (datetime.now() - self.current_session['start_time']).total_seconds()
                if duration > SAFETY_LIMITS['max_hold']:
                    logging.error(f"Session over maximum duration ({duration}s), force closing")
                    self._finalize_session()

    def _finalize_session(self):
        """Graceful session termination with validation"""
        with self.session_lock:
            try:
                if self.session_state != SessionState.ACTIVE:
                    logging.warning("Finalize called on non-active session")
                    return
    
                logging.info("Starting session finalization")
                self._set_session_state(SessionState.STOPPING)
    
                # Calculate actual hold time from start time
                if self.current_session['start_time']:
                    hold_time = (datetime.now() - self.current_session['start_time']).total_seconds()
                    hold_time = min(hold_time, SAFETY_LIMITS['max_hold'])
                else:
                    hold_time = 0.0
                    logging.error("Session finalized without valid start time")
    
                # Validate collected data
                valid_session = True
                if len(self.current_session['live_angles']) < 10:
                    logging.warning("Session too short for processing")
                    valid_session = False
                    
                if self.current_session['max_angle'] < KFMS_PARAMS['flex_threshold']:
                    logging.warning("Session max angle below threshold")
                    valid_session = False
    
                session_data = {
                    'timestamp': datetime.now().isoformat(),
                    'max_angle': round(self.current_session['max_angle'], 1),
                    'hold_time': round(hold_time, 1),
                    'stability': 0.0,
                    'kfms': 0.0,
                    'valid': valid_session,
                    'data_points': len(self.current_session['live_angles'])
                }
    
                if valid_session:
                    session_data.update({
                        'stability': round(self._calculate_stability_index(), 3),
                        'kfms': round(self._calculate_kfms(), 1)
                    })
                    logging.info(f"Valid session: {session_data['max_angle']}° for {session_data['hold_time']}s")
                else:
                    logging.warning("Storing invalid session record")
    
                # Store and reset
                self.session_history.append(session_data)
                self._store_session_db(session_data)
                
                if valid_session:
                    self.kfms_history.append(session_data['kfms'])
    
                # Full system reset
                self._reset_session()
                logging.info("Session resources released")
    
            except Exception as e:
                logging.error(f"Finalization failed: {str(e)}", exc_info=True)
                self.session_history.append({
                    'timestamp': datetime.now().isoformat(),
                    'error': str(e)
                })
            finally:
                self._set_session_state(SessionState.INACTIVE)
                logging.debug("Session state set to INACTIVE")

    async def shutdown(self):
        """System shutdown sequence with guaranteed cleanup"""
        shutdown_start = datetime.now()
        logging.info("=== SHUTDOWN INITIATED ===")
        
        try:
            # Phase 1: Session finalization
            if self.session_is_active():
                logging.warning("Force-finalizing active session")
                try:
                    # Bypass normal checks for shutdown
                    with self.session_lock:
                        self._finalize_session()
                except Exception as e:
                    logging.error(f"Emergency finalize failed: {str(e)}")
    
            # Phase 2: Report generation
            report_sent = False
            if self.session_history:
                try:
                    pdf_path = self.generate_report()
                    if pdf_path and os.path.exists(pdf_path):
                        logging.info(f"Generated report: {pdf_path}")
                        self._send_email_with_attachment(pdf_path)
                        report_sent = True
                    else:
                        logging.error("Report generation failed - no valid PDF")
                except Exception as e:
                    logging.error(f"Report subsystem failed: {str(e)}")
            else:
                logging.warning("No session history for reporting")
    
            # Phase 3: Hardware shutdown
            if self.connected:
                logging.info("Disconnecting from BLE...")
                try:
                    await asyncio.wait_for(self.client.disconnect(), timeout=5.0)
                    logging.info("BLE disconnected cleanly")
                except Exception as e:
                    logging.error(f"Force-disconnecting BLE: {str(e)}")
                    self.client._disconnect()
                    
        except Exception as e:
            logging.critical(f"Critical shutdown failure: {str(e)}", exc_info=True)
        finally:
            # Final system halt
            self.connected = False
            self.should_run = False
            shutdown_duration = (datetime.now() - shutdown_start).total_seconds()
            logging.info(f"=== SHUTDOWN COMPLETE ({shutdown_duration:.2f}s) ===")
                
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
        loop = asyncio.new_event_loop()
        def run_loop():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.run_ble_loop())
            
        self.ble_thread = threading.Thread(target=run_loop, daemon=True)
        self.ble_thread.start()
    # ======================
    # BLE COMMUNICATIONS
    # ======================
    async def connect(self):
        for attempt in range(BLE_CONFIG['retries']):
            try:
                self.client = BleakClient(BLE_CONFIG['address'])
                await self.client.connect(timeout=BLE_CONFIG['timeout'])
                self.connected = True
                await self._setup_notifications()
                logging.info("BLE connected")
                return True
            except Exception as e:
                logging.warning(f"Connection attempt {attempt+1} failed: {str(e)}")
                await asyncio.sleep(2 ** attempt)
        logging.error("Maximum connection attempts reached")
        return False

    async def _setup_notifications(self):
        from bleak.uuids import normalize_uuid_str
        for uuid_key in ['angle', 'max_angle', 'hold_time']:
            normalize_uuid_str(BLE_CONFIG['uuids'][uuid_key])

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

    # ======================
    # DATA PROCESSING
    # ======================
    def _angle_callback(self, sender, data):
        """Processes incoming angle data and manages session lifecycle"""
        try:
            angle = struct.unpack('<f', data)[0]
            if not (0 <= angle <= SAFETY_LIMITS['max_angle']):
                logging.warning(f"Invalid angle reading: {angle}°")
                return
    
            with self.session_lock:  # Use session lock instead of data_lock
                now = datetime.now()
                
                # Session state machine
                if angle >= KFMS_PARAMS['flex_threshold']:
                    if self.session_is_inactive():
                        # Validate initial angle meets minimum requirements
                        if angle < 30:  # Your actual threshold
                            logging.debug(f"Rejecting sub-threshold start: {angle}°")
                            return
                        logging.info(f"Session start triggered by angle: {angle}°")
                        self._start_new_session(initial_angle=angle)
                    else:
                        # Reset low angle timer when back above threshold
                        self.current_session['low_angle_start'] = None
                        logging.debug(f"Angle recovered: {angle}°")
    
                    # Always update max angle during active sessions
                    if self.session_is_active() and angle > self.current_session['max_angle']:
                        self.current_session['max_angle'] = angle
                        logging.debug(f"New max angle: {angle}°")
    
                else:  # Angle below threshold
                    if self.session_is_active():
                        if self.current_session['low_angle_start'] is None:
                            # Start low angle timer
                            self.current_session['low_angle_start'] = now
                            logging.info("Low angle detected, starting termination timer")
                        else:
                            # Calculate duration below threshold
                            low_duration = (now - self.current_session['low_angle_start']).total_seconds()
                            logging.debug(f"Continuous low angle for {low_duration:.1f}s")
                            
                            if low_duration >= 5:  # Your configured termination time
                                logging.info(f"Session termination threshold reached ({low_duration:.1f}s)")
                                self._finalize_session()
                    else:
                        # Reset timer if inactive
                        self.current_session['low_angle_start'] = None
    
                # Always store angle with timestamp
                self.current_session['live_angles'].append((angle, now))
                
        except Exception as e:
            logging.error(f"Angle processing failed: {str(e)}", exc_info=True)

    def _max_angle_callback(self, sender, data):
        try:
            max_angle = struct.unpack('<f', data)[0]
            if 0 <= max_angle <= SAFETY_LIMITS['max_angle']:
                with data_lock:
                    if max_angle > self.current_session['max_angle']:
                        self.current_session['max_angle'] = max_angle
        except Exception as e:
            logging.error(f"Max angle error: {e}")

    def _hold_time_callback(self, sender, data):
        try:
            hold_time = struct.unpack('<L', data)[0] / 1000
            if 0 <= hold_time <= SAFETY_LIMITS['max_hold']:
                with data_lock:
                    self.current_session['hold_time'] = hold_time
        except Exception as e:
            logging.error(f"Hold time error: {e}")

    # ======================
    # ANALYTICS & REPORTING
    # ======================
    def _calculate_stability_index(self):
        angles = [a for a, t in self.current_session['live_angles']]
        if len(angles) < 2:
            return 0.0
            
        mean = np.mean(angles)
        sd = np.std(angles)
        return (mean / self.current_session['max_angle']) * (1 - (sd / self.current_session['max_angle']))

    def _calculate_kfms(self):
        try:
            rom = (self.current_session['max_angle'] / KFMS_PARAMS['theta_target']) * KFMS_PARAMS['alpha']
            endurance = (self.current_session['hold_time'] / KFMS_PARAMS['t_target']) * KFMS_PARAMS['beta']
            control = self._calculate_stability_index() * KFMS_PARAMS['gamma']
            progression = 1 + ((sum(self.kfms_history[-3:])/3 - 50)/100) if len(self.kfms_history) >=3 else 1
            return min((rom + endurance + control) * progression * 100, 100)
        except:
            return 0.0
        
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
            if not os.path.exists('reports'):
                os.makedirs('reports')
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

# ======================
# FLASK ENDPOINTS
# ======================
@app.route('/')
def dashboard():
    return render_template('dashboard.html', flex_threshold=KFMS_PARAMS['flex_threshold'])

@app.route('/api/data')
def get_data():
    with data_lock:
        if analyzer is None:
            return jsonify({'error': 'System not initialized'}), 500
            
        return jsonify({
            'current_angle': analyzer.current_session['live_angles'][-1][0] if analyzer.current_session['live_angles'] else 0,
            'max_angle': analyzer.current_session['max_angle'],
            'hold_time': analyzer.current_session['hold_time'],
            'threshold': KFMS_PARAMS['flex_threshold'],
            'sessions': [s for s in analyzer.session_history[-10:]]
        })

@app.route('/api/state')
def get_state():
    return jsonify({
        'state': analyzer.session_state.name,
        'active': analyzer.session_is_active(),
        'duration': analyzer.current_session['hold_time'] if analyzer.session_is_active() else 0
    })

@app.route('/api/start', methods=['POST'])
def start_session():
    analyzer._start_new_session()
    return jsonify({'status': 'Session started'})

@app.route('/api/stop', methods=['POST'])
def stop_session():
    analyzer._finalize_session()
    return jsonify({'status': 'Session stopped'})
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/api/debug')
def debug_info():
    return jsonify({
        'session': analyzer.current_session,
        'ble_connected': analyzer.connected,
        'threads': [t.name for t in threading.enumerate()]
    })
# ======================
# MAIN SYSTEM INIT
# ======================
async def ble_main(analyzer):
    try:
        if await analyzer.connect():
            while analyzer.should_run:
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"BLE error: {e}")
    finally:
        await analyzer.shutdown()

if __name__ == "__main__":
    patient_name = input("Enter patient identifier: ").strip()
    analyzer = KFM_Analyzer(patient_name)
    
    # Start BLE in background thread
    def run_ble():
        asyncio.run(ble_main(analyzer))
        
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()

    # Start Flask
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        analyzer.should_run = False
        ble_thread.join(timeout=5)