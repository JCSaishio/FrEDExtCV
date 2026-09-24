import io
import yaml
import csv

class Database():
    """Class to store the raw data and generate the CSV file"""
    time_readings = []
    
    # extruder_timestamps = []
    temperature_timestamps = []  # For future temperature measurements
    temperature_delta_time = []
    temperature_readings = []
    temperature_setpoint = []
    temperature_error = []
    temperature_pid_output = []
    temperature_kp = []
    temperature_ki = []
    temperature_kd = []
    extruder_rpm = []
    
    camera_timestamps = []  # Timestamps for diameter measurements
    diameter_delta_time = []
    diameter_readings = []
    diameter_setpoint = []

    spooler_timestamps = []  # For future spooler measurements
    spooler_delta_time = []
    spooler_setpoint = []
    spooler_kp = []
    spooler_ki = []
    spooler_kd = []
    spooler_rpm = []

    cooling_timestamps = []
    fan_duty_cycle = []

    @classmethod
    def generate_csv(cls, filename: str) -> None:
        """Generate a CSV file with the data"""
        filename = filename + ".csv"
        with open(filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
        
            # Obtener el tiempo total de ejecución
            total_time = cls.time_readings[-1] if cls.time_readings else 0
            
            # Temperature Table con timestamps reales
            writer.writerow(["TEMPERATURE DATA"])
            writer.writerow(["Timestamp (s)", "Temperature (C)", 
                           "Temperature setpoint (C)", "Temperature error (C)",
                           "Temperature PID output", "Temperature Kp",
                           "Temperature Ki", "Temperature Kd"])
            
            temp_samples = len([x for x in cls.temperature_readings if x != ""])
            for i in range(temp_samples):
                row = [f"{cls.temperature_timestamps[i]:.3f}" if i < len(cls.temperature_timestamps) else "",
                      cls.temperature_readings[i] if i < len(cls.temperature_readings) else "",
                      cls.temperature_setpoint[i] if i < len(cls.temperature_setpoint) else "",
                      cls.temperature_error[i] if i < len(cls.temperature_error) else "",
                      cls.temperature_pid_output[i] if i < len(cls.temperature_pid_output) else "",
                      cls.temperature_kp[i] if i < len(cls.temperature_kp) else "",
                      cls.temperature_ki[i] if i < len(cls.temperature_ki) else "",
                      cls.temperature_kd[i] if i < len(cls.temperature_kd) else ""]
                writer.writerow(row)
            
            # Separadores entre tablas
            writer.writerow([])
            writer.writerow([])
            
            # Diameter Table with actual timestamps
            writer.writerow(["DIAMETER DATA"])
            writer.writerow(["Timestamp (s)", "Diameter (mm)",
                            "Diameter setpoint (mm)", "Fan duty cycle (%)"])
        
            diameter_samples = len(cls.diameter_readings)
            for i in range(diameter_samples):
                row = [f"{cls.camera_timestamps[i]:.3f}" if i < len(cls.camera_timestamps) else "",
                      cls.diameter_readings[i] if i < len(cls.diameter_readings) else "",
                      cls.diameter_setpoint[i] if i < len(cls.diameter_setpoint) else "",
                      cls.fan_duty_cycle[i] if i < len(cls.fan_duty_cycle) else "0"]
                writer.writerow(row)
            
            # Separadores entre tablas
            writer.writerow([])
            writer.writerow([])
            
            # Motor Table
            writer.writerow(["MOTOR DATA"])
            writer.writerow(["Timestamp (s)", "Extruder RPM",
                           "Spooler setpoint (RPM)", "Spooler RPM",
                           "Spooler Kp", "Spooler Ki", "Spooler Kd"])
            
            motor_samples = len([x for x in cls.spooler_rpm if x != ""])
            for i in range(motor_samples):
                row = [f"{cls.spooler_timestamps[i]:.3f}" if i < len(cls.spooler_timestamps) else "",
                      cls.extruder_rpm[i] if i < len(cls.extruder_rpm) else "",
                      cls.spooler_setpoint[i] if i < len(cls.spooler_setpoint) else "",
                      cls.spooler_rpm[i] if i < len(cls.spooler_rpm) else "",
                      cls.spooler_kp[i] if i < len(cls.spooler_kp) else "",  
                      cls.spooler_ki[i] if i < len(cls.spooler_ki) else "",  
                      cls.spooler_kd[i] if i < len(cls.spooler_kd) else ""]  
                writer.writerow(row)
        print(f"CSV file {filename} generated.")

    @classmethod
    def generate_csv_string(cls, starts=None, t0: float = 0.0) -> str:
        """Build the same three-table CSV as :meth:`generate_csv`, but as a
        STRING, optionally windowed and rebased for an experiment.

        ``starts`` is an optional dict mapping a buffer name to the index the
        slice should start at (so only the recording window is exported); any
        buffer not present starts at 0. Timestamp columns have ``t0`` subtracted
        so the experiment clock starts at 0 s.
        """
        starts = starts or {}

        def col(name):
            return getattr(cls, name)[starts.get(name, 0):]

        buf = io.StringIO()
        writer = csv.writer(buf)

        # --- Temperature table ---
        temp_ts = col("temperature_timestamps")
        temp_read = col("temperature_readings")
        temp_set = col("temperature_setpoint")
        temp_err = col("temperature_error")
        temp_out = col("temperature_pid_output")
        temp_kp = col("temperature_kp")
        temp_ki = col("temperature_ki")
        temp_kd = col("temperature_kd")
        writer.writerow(["TEMPERATURE DATA"])
        writer.writerow(["Timestamp (s)", "Temperature (C)",
                         "Temperature setpoint (C)", "Temperature error (C)",
                         "Temperature PID output", "Temperature Kp",
                         "Temperature Ki", "Temperature Kd"])
        for i in range(len([x for x in temp_read if x != ""])):
            writer.writerow([
                f"{temp_ts[i] - t0:.3f}" if i < len(temp_ts) else "",
                temp_read[i] if i < len(temp_read) else "",
                temp_set[i] if i < len(temp_set) else "",
                temp_err[i] if i < len(temp_err) else "",
                temp_out[i] if i < len(temp_out) else "",
                temp_kp[i] if i < len(temp_kp) else "",
                temp_ki[i] if i < len(temp_ki) else "",
                temp_kd[i] if i < len(temp_kd) else "",
            ])

        writer.writerow([])
        writer.writerow([])

        # --- Diameter table ---
        dia_ts = col("camera_timestamps")
        dia_read = col("diameter_readings")
        dia_set = col("diameter_setpoint")
        fan_dc = col("fan_duty_cycle")
        writer.writerow(["DIAMETER DATA"])
        writer.writerow(["Timestamp (s)", "Diameter (mm)",
                         "Diameter setpoint (mm)", "Fan duty cycle (%)"])
        for i in range(len(dia_read)):
            writer.writerow([
                f"{dia_ts[i] - t0:.3f}" if i < len(dia_ts) else "",
                dia_read[i] if i < len(dia_read) else "",
                dia_set[i] if i < len(dia_set) else "",
                fan_dc[i] if i < len(fan_dc) else "0",
            ])

        writer.writerow([])
        writer.writerow([])

        # --- Motor table ---
        mot_ts = col("spooler_timestamps")
        ext_rpm = col("extruder_rpm")
        spo_set = col("spooler_setpoint")
        spo_rpm = col("spooler_rpm")
        spo_kp = col("spooler_kp")
        spo_ki = col("spooler_ki")
        spo_kd = col("spooler_kd")
        writer.writerow(["MOTOR DATA"])
        writer.writerow(["Timestamp (s)", "Extruder RPM",
                         "Spooler setpoint (RPM)", "Spooler RPM",
                         "Spooler Kp", "Spooler Ki", "Spooler Kd"])
        for i in range(len([x for x in spo_rpm if x != ""])):
            writer.writerow([
                f"{mot_ts[i] - t0:.3f}" if i < len(mot_ts) else "",
                ext_rpm[i] if i < len(ext_rpm) else "",
                spo_set[i] if i < len(spo_set) else "",
                spo_rpm[i] if i < len(spo_rpm) else "",
                spo_kp[i] if i < len(spo_kp) else "",
                spo_ki[i] if i < len(spo_ki) else "",
                spo_kd[i] if i < len(spo_kd) else "",
            ])

        return buf.getvalue()

    @staticmethod
    def get_calibration_data(field: str) -> float:
        """Get calibration data from the yaml file"""
        with open("calibration.yaml", "r", encoding="utf-8") as file:
            calibration_data = yaml.unsafe_load(file)
        return calibration_data[field]

    @staticmethod
    def update_calibration_data(field: str, value: str) -> None:
        """Update calibration data in the yaml file"""
        with open("calibration.yaml", "r") as file:
            calibration_data = yaml.unsafe_load(file)
        with open("calibration.yaml", "w") as file:
            calibration_data[field] = float(value)
            yaml.dump(calibration_data, file)

