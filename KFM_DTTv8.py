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
import threading
from flask import jsonify, render_template, Flask, request
import socket

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
    'retries': 30,      
    'timeout': 7.0  
}

KFMS_PARAMS = {
    'target_angle': 120.0,    # Degrees
    'target_duration': 10.0,         # Seconds
    'alpha_weight': 0.4,             # ROM weight
    'beta_weight': 0.4,              # Endurance weight
    'gamma_weight': 0.2,             # Control weight
    'baseline_sessions': 3,         # Progression factor weight
    'flex_threshold': 30.0    # Degrees 
}

SAFETY_LIMITS = {
    'max_angle': 150.0,       # Degrees
    'max_hold_time': 30.0          # Seconds
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
    'recipient_email': 'jf7g23@soton.ac.uk',
    'get_subject': lambda patient_name: f'KFM Report - {patient_name} - {datetime.now().strftime("%Y-%m-%d")}'
}


main_loop = None

# ======================
# CORE SYSTEM CLASS
# ======================
class KFM_Analyzer:
    def __init__(self, patient_name):
        self.patient_name = patient_name
        self.client = None
        self.connected = False
        self.lock = threading.RLock()
        
        self.last_angle = 0.0
        # New attribute to store patient position; default is "sitting"
        self.patient_position = 'sitting'
        
        # Initialize session state
        self._reset_session()
        
        # Historical data
        self.session_history = []
        self.kfms_history = []
        
        # Database setup
        self.db_file = f"kfm_data_{self.patient_name}.db"
        self._init_database()
        
        self.game_states = {
            'rocket': {'phase': 1, 'charge': 0.0, 'launch_success': None}
        }
        
        # Ensure reports directory exists
        os.makedirs('reports', exist_ok=True)

    # ======================
    # SESSION MANAGEMENT
    # ======================
    def _reset_session(self):
        """
        Initialize/reset all session parameters.

        Notes:
            - Clears the current session data.
            - Sets default values for session parameters.
        """
        self.current_session = {
            'start_time': None,
            'max_angle': 0.0,
            'hold_time': 0.0,
            'live_angles': [],
            'session_active': False
        }
    
    """
    Begin tracking a new rehabilitation session.

    Args:
        None

    Returns:
        None

    Notes:
        - Updates the `current_session` dictionary with the start time and sets `session_active` to True.
        - Logs the start of a new session.
    """
    def _start_new_session(self):
        """Begin tracking new rehabilitation session"""
        with self.lock:
            self.current_session.update({
                'start_time': datetime.now(),
                'session_active': True
            })
            logging.info("New session started")

    # ======================
    # SHUTDOWN HANDLING
    # ======================
    async def shutdown(self):
        """
        Perform a graceful shutdown of the system, including BLE disconnection and report generation.

        Args:
            None

        Returns:
            None

        Notes:
            - Finalizes any active session.
            - Generates and emails a PDF report if session history exists.
            - Disconnects from the BLE device if connected.
        """
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
    """
    Initialize the SQLite database for storing session data.

    Args:
        None

    Returns:
        None

    Raises:
        sqlite3.Error: If the database initialization fails.

    Notes:
        - Creates a `sessions` table if it does not already exist.
        - The table stores session data such as timestamp, max angle, hold time, KFMS, and stability.
    """
    def _init_database(self):
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
        """
        Store session data in the SQLite database with retries.

        Args:
            session_data (dict): A dictionary containing session data.

        Returns:
            None
        """
        retries = 3
        for attempt in range(retries):
            try:
                with sqlite3.connect(self.db_file) as conn:
                    c = conn.cursor()
                    c.execute('''INSERT INTO sessions VALUES (?, ?, ?, ?, ?)''',
                              (session_data['timestamp'],
                               session_data['max_angle'],
                               session_data['hold_time'],
                               session_data['kfms'],
                               session_data['stability']))
                    conn.commit()
                    logging.info("Session data stored successfully.")
                    return
            except sqlite3.IntegrityError:
                logging.warning("Duplicate session timestamp detected.")
                return
            except sqlite3.Error as e:
                logging.error(f"Database write failed (attempt {attempt+1}): {e}")
                if attempt < retries - 1:
                    logging.info("Retrying database write...")
                    continue
                else:
                    logging.critical("Database write failed after retries.")
                    raise

    # ======================
    # BLE COMMUNICATIONS
    # ======================
    async def connect(self):
        """
        Attempt to connect to the BLE device using exponential backoff.

        Returns:
            bool: True if the connection is successful, False otherwise.
        """
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

        # Fallback if all retries fail
        logging.error("Maximum connection attempts reached. BLE connection failed.")
        self.connected = False
        return False

    """
    Configure BLE notifications for angle, max angle, and hold time.

    Args:
        None

    Returns:
        None

    Raises:
        ValueError: If the UUID format is invalid.
        Exception: If notification setup fails.

    Notes:
        - Validates UUIDs before starting notifications.
        - Registers callback functions for each notification type.
    """
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
        """
        Process incoming angle data from the BLE device.

        Args:
            sender (int): The handle of the characteristic that triggered the callback.
            data (bytes): The raw angle data received from the BLE device.

        Returns:
            None
        """
        try:
            angle = struct.unpack('<f', data)[0]
            logging.debug(f"Received angle data: {angle}°")

            with self.lock:
                # Safety check
                if not (0 <= abs(angle) <= SAFETY_LIMITS['max_angle']):
                    logging.error(f"Invalid angle value: {angle}")
                    return

                # Adjust angle if patient is lying
                if self.patient_position.lower() == 'lying':
                    angle = angle * 2

                # Update last_angle for real-time display
                self.last_angle = angle

                # Session detection logic
                if angle > KFMS_PARAMS['flex_threshold']:
                    if not self.current_session['session_active']:
                        self._start_new_session()
                    self._update_session(angle)
                elif self.current_session['session_active']:
                    self._finalize_session()

        except struct.error as e:
            logging.error(f"Angle data unpack error: {e}")

    """
    Process incoming max angle data from the BLE device.

    Args:
        sender (int): The handle of the characteristic that triggered the callback.
        data (bytes): The raw max angle data received from the BLE device.

    Returns:
        None

    Notes:
        - Unpacks the max angle data and validates its range.
        - Updates the session's max angle if the new value is greater than the current max.
        - Adjusts the max angle if the patient is in the "lying" position.
    """
    def _max_angle_callback(self, sender, data):
        """Process max angle updates with validation"""
        try:
            with self.lock:
                if not self.current_session.get('session_active'):
                    logging.debug("Max angle update outside active session")
                    return
    
                max_angle = struct.unpack('<f', data)[0]
                
                # Validate value range
                if not (0 <= max_angle <= SAFETY_LIMITS['max_angle']):
                    logging.error(f"Invalid max angle: {max_angle}")
                    return
                    
                # Update session only if greater than current
                if self.patient_position.lower() == 'lying':
                    max_angle = max_angle * 2

                
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
        """
        Process incoming hold time data from the BLE device.

        Args:
            sender (int): The handle of the characteristic that triggered the callback.
            data (bytes): The raw hold time data received from the BLE device.

        Returns:
            None

        Notes:
            - Unpacks the hold time data and converts it from milliseconds to seconds.
            - Updates the session's hold time if the session is active or within 1 second of ending.
            - Clamps the hold time to the maximum allowed value.
        """
        try:
            # Log raw data for debugging
            logging.debug(f"Raw hold_time data: {data.hex()}")
            
            # Unpack as 2-byte unsigned short (adjust format as needed)
            hold_time_ms = struct.unpack('<L', data)[0]
            hold_time = hold_time_ms / 1000.0  # Convert ms to seconds
    
            with self.lock:
                # Allow updates for 1 second after session ends
                session_active = self.current_session.get('session_active', False)
                end_time = self.current_session.get('end_time', datetime.now())
                time_since_end = (datetime.now() - end_time).total_seconds()
    
                if session_active or time_since_end < 1.0:
                    # Validate and clamp value
                    hold_time = max(0.0, min(hold_time, SAFETY_LIMITS['max_hold_time']))
                    self.current_session['hold_time'] = hold_time
                    logging.debug(f"Updated hold_time: {hold_time}s")
                else:
                    logging.debug("Ignoring hold_time update (session inactive)")
    
        except struct.error as e:
            logging.error(f"Hold time unpack error: {e}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")

    def _update_session(self, angle):
        """
        Update the current session with new angle data.

        Args:
            angle (float): The latest angle value to add to the session.

        Returns:
            None

        Notes:
            - Appends the angle to the `live_angles` list in the current session.
            - Maintains a maximum of 1000 angles in the list by removing the oldest values.
        """
        with self.lock:
            try:
                self.current_session['live_angles'].append(angle)
                if len(self.current_session['live_angles']) > 1000:
                    self.current_session['live_angles'].pop(0)
            except KeyError:
                self._reset_session()
                logging.error("Session state corrupted, resetting")

    def _finalize_session(self):
        """
        Complete the current session and calculate metrics.

        Args:
            None

        Returns:
            None

        Notes:
            - Discards sessions with fewer than 10 angles.
            - Calculates stability, KFMS, and other metrics for the session.
            - Stores the session data in the database and appends it to the session history.
            - Resets the session state after finalization.
        """
        try:
            with self.lock:
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
        """
        Compute the stability index for the current session.

        Args:
            None

        Returns:
            float: The calculated stability index.

        Notes:
            - Uses the mean and standard deviation of the angles to calculate stability.
            - Returns 0.0 if there are fewer than 2 angles or if the max angle is 0.
        """
        angles = self.current_session.get('live_angles', [])
        if len(angles) < 2 or self.current_session.get('max_angle', 0) == 0:
            return 0.0
            
        mean = sum(angles) / len(angles)
        variance = sum((x - mean)**2 for x in angles) / (len(angles)-1)
        sd = math.sqrt(variance)
        
        return (mean / self.current_session['max_angle']) * (1 - (sd / self.current_session['max_angle']))

    def _calculate_kfms(self):
        """
        Calculate the Knee Flexion Monitor Score (KFMS) for the current session.

        Args:
            None

        Returns:
            float: The calculated KFMS score.

        Notes:
            - Combines ROM, endurance, and control metrics using weighted parameters.
            - Applies a progression factor based on historical KFMS scores.
            - Caps the final score at 100.
        """
        try:
            # Base components
            rom = (self.current_session['max_angle'] / KFMS_PARAMS['target_angle']) * KFMS_PARAMS['alpha_weight']
            endurance = (self.current_session['hold_time'] / KFMS_PARAMS['target_duration']) * KFMS_PARAMS['beta_weight']
            control = self._calculate_stability_index() * KFMS_PARAMS['gamma_weight']
            
            # Progression factor
            progression = self._calculate_progression_factor(rom + endurance + control)
            
            return min((rom + endurance + control) * 100, 100)
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
        """
        Generate a plot of KFMS progress over time.

        Args:
            None

        Returns:
            str: The file path of the saved plot, or None if an error occurs.

        Notes:
            - Retrieves session data from the database and calculates daily statistics.
            - Creates a scatter plot of individual measurements and daily means with error bars.
            - Saves the plot as a PNG file in the `reports` directory.
        """
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
        """
        Send an email with the generated PDF report attached.

        Args:
            file_path (str): The file path of the PDF report to attach.

        Returns:
            None

        Raises:
            Exception: If the email fails to send.

        Notes:
            - Uses the SMTP protocol to send the email.
            - Attaches the PDF report and includes a brief message in the email body.
        """
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
            with smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
                server.starttls()
                server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
                server.send_message(msg)

            logging.info(f"Email sent to {EMAIL_CONFIG['recipient_email']}")

        except Exception as e:
            logging.error(f"Email failed: {str(e)}")
            
    def generate_report(self):
        """
        Generate a PDF clinical report for the patient.

        Args:
            None

        Returns:
            str: The file path of the generated PDF report, or an error message if generation fails.

        Notes:
            - Includes a KFMS progress plot, session data table, and averages in the report.
            - Saves the report in the `reports` directory.
        """
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
                    session['timestamp'][:19] if session['timestamp'] else "N/A",
                    f"{session['hold_time']:.1f}s" if session['hold_time'] is not None else "N/A",
                    f"{session['max_angle']:.1f}°" if session['max_angle'] is not None else "N/A",
                    f"{session['kfms']:.1f}" if session['kfms'] is not None else "N/A",
                    f"{session['stability']:.3f}" if session['stability'] is not None else "N/A"
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
            avg_hold = np.nanmean([s['hold_time'] for s in self.session_history if s['hold_time'] is not None])
            avg_angle = np.nanmean([s['max_angle'] for s in self.session_history if s['max_angle'] is not None])
            avg_kfms = np.nanmean([s['kfms'] for s in self.session_history if s['kfms'] is not None])
            avg_stability = np.nanmean([s['stability'] for s in self.session_history if s['stability'] is not None])
        
            avg_table = Table([
                ['Metric', 'Average Value'],
                ['Hold Time', f"{avg_hold:.1f} ± {np.nanstd([s['hold_time'] for s in self.session_history if s['hold_time'] is not None]):.1f}s"],
                ['Max Angle', f"{avg_angle:.1f} ± {np.nanstd([s['max_angle'] for s in self.session_history if s['max_angle'] is not None]):.1f}°"],
                ['KFMS Score', f"{avg_kfms:.1f} ± {np.nanstd([s['kfms'] for s in self.session_history if s['kfms'] is not None]):.1f}"],
                ['Stability', f"{avg_stability:.3f} ± {np.nanstd([s['stability'] for s in self.session_history if s['stability'] is not None]):.3f}"]
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
            return pdf_path

        except Exception as e:
            logging.error(f"Report generation error: {e}")
            return f"Error generating report: {str(e)}"

    def _get_disclaimer(self):
        return (
            "The Knee Flexion Monitor (KFM) system is for informational purposes only.\n"
            "It is not a medical device. Consult a healthcare professional for medical advice.\n"
            "No liability is assumed for use or interpretation of this data.\n"
        )

def get_ipv4_address():
    """
    Get the IPv4 address of the machine.

    Returns:
        str: The IPv4 address of the machine, or '127.0.0.1' if unable to determine.
    """
    try:
        # Create a socket and connect to a public DNS server to determine the local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # Google's public DNS server
            return s.getsockname()[0]
    except Exception as e:
        logging.error(f"Error determining IPv4 address: {e}")
        return "127.0.0.1"  # Fallback to localhost
# ======================
# FLASK WEB SERVER
# ======================
def run_flask(analyzer):
    """
    Start the Flask web server for the KFM system.

    Args:
        analyzer (KFM_Analyzer): The main system object for managing BLE and session data.

    Returns:
        None

    Notes:
        - Configures endpoints for the dashboard, real-time data, games, and setup.
        - Runs the Flask server on port 5000 (or 5001 if 5000 is unavailable).
    """
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your-secret-key'
    
    @app.route('/')
    def dashboard():
        """Main dashboard route"""
        try:
            return render_template(
                'dashboard.html',
                flex_threshold=KFMS_PARAMS['flex_threshold'],
                safety_limits=SAFETY_LIMITS,
                patient_name=analyzer.patient_name,
                patient_position=analyzer.patient_position,
                bt_connected=analyzer.connected  
            )
        except Exception as e:
            logging.error(f"Template error: {str(e)}")
            return "System configuration error", 500

    @app.route('/current_data')
    def get_current_data():
        """Real-time data endpoint"""
        try:
            with analyzer.lock:
                # Provide fallback values if BLE is not connected
                if analyzer.connected and analyzer.current_session.get('live_angles'):
                    current_angle = analyzer.current_session['live_angles'][-1]
                else:
                    current_angle = analyzer.last_angle or 0.0  # Default to 0.0 if no data

                return jsonify({
                    'current_angle': current_angle,
                    'max_angle': analyzer.current_session.get('max_angle', 0.0),
                    'hold_time': analyzer.current_session.get('hold_time', 0.0),
                    'session_active': analyzer.current_session.get('session_active', False),
                    'bt_connected': analyzer.connected  
                })
        except Exception as e:
            logging.error(f"Current data error: {str(e)}")
            return jsonify({"error": "Data unavailable"}), 503

    @app.route('/game-1')
    def rocket_game():
        """Game rendering endpoint"""
        return render_template('rocket_game.html')

    @app.route('/game-1/data')
    def game1_data():
        """Provide game data"""
        try:
            with analyzer.lock:
                # Provide fallback values if BLE is not connected
                current_angle = analyzer.last_angle if analyzer.connected else 0.0
                return jsonify({
                    'current_angle': current_angle,
                    'error': None
                })
        except Exception as e:
            return jsonify(error=str(e)), 500
        
    @app.route('/game-2')
    def car_game():
        """Game rendering endpoint for the car game"""
        return render_template('car_game.html')

    @app.route('/history')
    def get_history():
        """Session history endpoint"""
        try:
            with analyzer.lock:
                return jsonify(analyzer.session_history.copy())
        except Exception as e:
            logging.error(f"History error: {str(e)}")
            return jsonify({"error": "History unavailable"}), 503

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        """
        Setup endpoint to configure patient name, flex threshold, and patient position.
        GET returns an HTML form; POST processes the submitted data.
        """
        if request.method == 'POST':
            try:
                patient_name = request.form.get('patient_name')
                flex_threshold = float(request.form.get('flex_threshold', KFMS_PARAMS['flex_threshold']))
                patient_position = request.form.get('patient_position')
                
                # Update analyzer and global configuration
                analyzer.patient_name = patient_name
                KFMS_PARAMS['flex_threshold'] = flex_threshold
                analyzer.patient_position = patient_position.lower()  # expected "sitting" or "lying"
                
                logging.info(f"Setup updated: patient_name={patient_name}, flex_threshold={flex_threshold}, patient_position={patient_position}")
                return jsonify({"status": "success", "message": "Configuration updated."})
            except Exception as e:
                logging.error(f"Setup error: {str(e)}")
                return jsonify({"status": "error", "message": str(e)}), 400
        else:
            # Render a setup form (ensure you have a 'setup.html' template)
            return render_template(
                'setup.html',
                patient_name=analyzer.patient_name,
                flex_threshold=KFMS_PARAMS['flex_threshold'],
                patient_position=analyzer.patient_position
            )

    @app.route('/shutdown', methods=['POST'])
    def shutdown_route():
        """
        Shutdown endpoint to trigger the analyzer's shutdown procedure.
        """
        global main_loop
        try:
            if main_loop:
                # Schedule shutdown in the main asyncio event loop
                asyncio.run_coroutine_threadsafe(analyzer.shutdown(), main_loop)
                logging.info("Shutdown initiated via web interface.")
                return jsonify({"status": "success", "message": "Shutdown initiated."})
            else:
                logging.error("Main event loop not available for shutdown.")
                return jsonify({"status": "error", "message": "Event loop not available."}), 500
        except Exception as e:
            logging.error(f"Shutdown endpoint error: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/prescribed_exercises')
    def prescribed_exercises():
        """
        Endpoint to provide prescribed exercises with fixed and random values.
        """
        try:
            # Prescribed exercise data
            exercises = {
                "threshold": 30,  # Fixed threshold
                "angle_aim": "80-100",  # Fixed angle aim range
                "flexions": {
                    "reps": np.random.randint(1, 11),  # Random reps between 1 and 10
                    "sets": np.random.randint(1, 4)   # Random sets between 1 and 3
                }
            }

            # Return the exercises as JSON
            return jsonify({
                "status": "success",
                "exercises": exercises
            })
        except Exception as e:
            logging.error(f"Error generating prescribed exercises: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route('/set_position', methods=['POST'])
    def set_position():
        """
        Endpoint to update the patient's position (e.g., sitting or lying).
        """
        try:
            position = request.json.get('position', '').lower()
            if position not in ['sitting', 'lying']:
                return jsonify({"status": "error", "message": "Invalid position"}), 400

            with analyzer.lock:
                analyzer.patient_position = position
                logging.info(f"Patient position updated to: {position}")

            return jsonify({"status": "success", "message": f"Position set to {position}"})
        except Exception as e:
            logging.error(f"Error setting position: {str(e)}")
            return jsonify({"status": "error", "message": str(e)}), 500

    try:
        logging.info("Starting Flask server on 0.0.0.0:5000")
        app.run(
            host='0.0.0.0', 
            port=5000,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except OSError as e:
        logging.critical(f"Port 5000 unavailable: {str(e)}")
        logging.info("Trying alternate port 5001")
        app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)

# ======================
# MAIN SYSTEM LOOP
# ======================
async def main(patient_name):
    """
    Main system loop for the KFM system.

    Args:
        patient_name (str): The name of the patient.

    Returns:
        None

    Notes:
        - Starts the Flask server in a background thread.
        - Attempts to connect to the BLE device and enters the main monitoring loop.
        - Handles graceful shutdown on keyboard interrupt.
    """
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    analyzer = KFM_Analyzer(patient_name)
    
    ipv4_address = get_ipv4_address()
    logging.info(f"System IPv4 Address: {ipv4_address}")
    print(f"System IPv4 Address: {ipv4_address}")
    
    # Start web server in background thread
    try:
        flask_thread = threading.Thread(
            target=run_flask, 
            args=(analyzer,),
            daemon=True
        )
        flask_thread.start()
        logging.info("Flask server thread started")
        
        # Wait briefly for server initialization
        await asyncio.sleep(1)
        
        # Attempt BLE connection
        if await analyzer.connect():
            logging.info("BLE connected, system operational")
            logging.info("Access dashboard at: http://localhost:5000")
            
            # Main monitoring loop
            while True:
                await asyncio.sleep(1)
                
    except KeyboardInterrupt:
        logging.info("\nShutdown requested")
    finally:
        await analyzer.shutdown()
        logging.info("System shutdown complete")

if __name__ == "__main__":
    patient_name = "test"
    asyncio.run(main(patient_name))
