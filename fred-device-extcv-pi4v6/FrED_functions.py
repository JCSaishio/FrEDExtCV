###############################################
########## declarations of functions ##########
import math


############################################
########## Functions for DC motor ##########
############################################

#def motor_speed (current_time, previous_time, previous_steps, encoder_steps):
#    rpm_raw = ((encoder_steps - previous_steps) * 60) / ((current_time - previous_time) * 1176)
#    #previous_steps = encoder.steps
#    return rpm_raw 

#def motor_speed(current_time, previous_time, previous_position, current_position):   #para nuevo encoder
#    PULSES_PER_REVOLUTION = 4704

#    delta_time = current_time - previous_time
#    if delta_time <= 0:
#        return 0.0

#    delta_position = current_position - previous_position
#    rpm_raw = -(delta_position / PULSES_PER_REVOLUTION) * (60 / delta_time)

#    return rpm_raw

def motor_speed(current_time, previous_time, previous_position, current_position):   #para nuevo encoder

    rpm_raw = -((current_position - previous_position) * 60) / ((current_time-previous_time)*4704)

    return rpm_raw

def filter (rpm_raw, previous_rpm):
    alpha = 0.3
    rpm = alpha * rpm_raw + (1 - alpha) * previous_rpm
    return rpm 

def PID (rpm_reference, rpm, previous_PIDerror, error_sum, current_time, previous_PIDtime):
    kp = 0.3106 #0.6066 #0.4152#second #0.6066 #0.4050 #0.4152 #0.1537
    ki = 9.703 #11.0243 #10.1415#second #5.2503 #4.8298 #10.1415 #0.4597
    kd = 0.02044 #0.03 #0.0335#second #0.03 #0.0335 #0.02

    delta_time = current_time- previous_PIDtime
    error = rpm_reference-rpm
    error_d = (error - previous_PIDerror) / delta_time
    error_i = (error * delta_time) + error_sum

    pid = kp * error + ki * error_i + kd * error_d

    PIDerror = error
    return pid, error_i, PIDerror

def PI (rpm_reference, rpm, error_sum, current_time, previous_PIDtime):
    kp = 0.36 # 0.1472 #second #0.3666 #0.1370 #0.1472
    ki = 10.01 # 9.0160 #second #4.7703 #4.2938 #9.0160

    delta_time = current_time- previous_PIDtime
    error = rpm_reference-rpm
    error_i = (error * delta_time) + error_sum

    pi = kp * error + ki * error_i
    return pi, error_i

def STSM (rpm_reference, rpm, u, error_sum, current_time, previous_PIDtime):
    ### Super twisting Sliding modes ###

    gain1 = 1     #gain for the sliding surface
    alpha1 = -10  #alpha gain one
    alpha2 = 10   #alpha gain two

    delta_time = current_time- previous_PIDtime
    error = rpm_reference-rpm
            
    surface = gain1 * error
    du = -alpha2 * sign(surface)
    u = u + (du * delta_time)

    stsm = -alpha1 * math.sqrt( abs(surface) ) * sign(surface) + u

    return stsm

def save_data (time_data, rpm_data, motor_voltage_data, motor_input_data,
                PWM_motor_data, rpm_raw_data):
    with open("FrED_data.txt","a") as archivo:
        archivo.write("time\trpm\tvolt\tinp\tpwm\traw\n")
        size = len(time_data)
        for i in range(size-1):
            a = time_data[i]
            b = rpm_data[i]
            c = motor_voltage_data[i]
            d = motor_input_data[i]
            e = PWM_motor_data[i]
            f = rpm_raw_data[i]
            archivo.write(f"{a}\t{b}\t{c}\t{d}\t{e}\t{f}\n")

def linearization (motor_input):
    m = 0.4323 #0.44
    b = 22.84
    PWM_motor = (1/m) * motor_input - (b/m)
    #PWM_motor = 0.45 * motor_input + b
    #PWM_motor = 0.57 * motor_input

    #b0 = 0.03
    #b1 = 0.03
    #b2 = -5
    #PWM_motor = (b0 * (motor_input * motor_input)) + (b1 * motor_input) + b2
    return PWM_motor

def sign (value):    #function to get the sign function of a value
    if value == 0:
        result = 0
    if value < 0:
        result = -1
    if value > 0:
        result = 1
    return result 

def least_square (current_time, opcion_LS):
        match opcion_LS:
            case 1:
                ########TEST 1 TO IDENTIFICATION SYSTEM #############
                if current_time < 10:
                    output = 60 #100
                if current_time < 20 and current_time >= 10:
                    output = 45 #75
                if current_time < 30 and current_time >= 20:
                    output = 30 #50
                if current_time < 40 and current_time >= 30:
                    output = 15 #25     
                if current_time < 70 and current_time >= 40:
                    output = 15-current_time+40 #25-current_time+40 
                if current_time < 180 and current_time >= 70:
                    output = current_time - 70  
                if current_time < 285 and current_time >= 180:
                    output = 60-current_time+180 #100-current_time+180
                if current_time >= 285:
                    output = 60 #100
                 
            case 2:
                ########TEST 2 TO IDENTIFICATION SYSTEM #############
                if current_time < 10:
                    output = 27 #25
                if current_time < 25 and current_time >= 10:
                    output = 35 #35
                if current_time < 35 and current_time >= 25:
                    output = 45 #50
                if current_time < 50 and current_time >= 35:
                    output = 50 #70     
                if current_time < 60 and current_time >= 50:
                    output = 40 #90 
                if current_time < 70 and current_time >= 60:
                    output = 57 #100  
                if current_time < 100 and current_time >= 70:
                    output = 57-current_time+70 #100-current_time+180
                if current_time >= 100:
                    output = 27 + current_time-100
                if current_time >= 130:
                    output = 40 #100
            
            case 3:
                ########TEST 3 TO IDENTIFICATION SYSTEM #############
                if current_time < 10:
                    output = 60 #100
                if current_time < 20 and current_time >= 10:
                    output = 45 #75
                if current_time < 30 and current_time >= 20:
                    output = 30 #50
                if current_time < 40 and current_time >= 30:
                    output = 15 #25     
                if current_time < 43 and current_time >= 40:
                    output = 0 
                if current_time >= 43:
                    output = 60 #100  
            
            case 4:
                ########TEST 4 TO IDENTIFICATION SYSTEM #############
                if current_time < 10:
                    output = 57 #100
                if current_time < 40 and current_time >= 10:
                    output = 57-current_time+10
                if current_time >= 40:
                    output = 27 + current_time-40

            case 5:
                ########TEST 5 TO IDENTIFICATION SYSTEM #############
                if current_time < 10:
                    output = 57
                if current_time < 20 and current_time >= 10:
                    output = 45
                if current_time < 30 and current_time >= 20:
                    output = 35
                if current_time < 40 and current_time >= 30:
                    output = 25
                if current_time >= 40:
                    output = 57 
            
            case 6:
                ########TEST 6 TO IDENTIFICATION SYSTEM #############
                if current_time > 0:
                    output = 23 + current_time
            
            case 7:
                ########TEST 7 TO IDENTIFICATION SYSTEM #############
                if current_time < 5:
                    output = 57
                if current_time >= 5:
                    output = 100-current_time+5                

            case 8:         
                ####### TEST 8 to IDENTIFICATION SYSTEM ###########
                if current_time > 0:
                    output = 57
                else :
                    output = 0
            case 9:         
                ####### TEST 9 to IDENTIFICATION SYSTEM ###########
                if current_time > 0:
                    output = 50
                if current_time > 0.5:
                    output = 25
                if current_time > 1:
                    output = 50
                if current_time > 1.5:
                    output = 25
                if current_time > 2:
                    output = 50
                if current_time > 2.5:
                    output = 25
                if current_time > 3:
                    output = 50
                if current_time > 3.5:
                    output = 25
                if current_time > 4:
                    output = 50
                if current_time > 4.5:
                    output = 25
                if current_time > 5:
                    output = 50
            case 10:         
                ####### TEST 10 to IDENTIFICATION SYSTEM ###########
                if current_time > 0:
                    output = 50
                if current_time > 1:
                    output = 25
                if current_time > 2:
                    output = 50
                if current_time > 3:
                    output = 25
                if current_time > 4:
                    output = 50
                if current_time > 5:
                    output = 25
                if current_time > 6:
                    output = 50
                if current_time > 7:
                    output = 25
                if current_time > 8:
                    output = 50
                if current_time > 9:
                    output = 25
                if current_time > 10:
                    output = 50
            case 11:         
                ####### TEST 11 to IDENTIFICATION SYSTEM ###########
                if current_time > 0:
                    output = 50
                if current_time > 5:
                    output = 25
                if current_time > 10:
                    output = 50
                if current_time > 15:
                    output = 25
                if current_time > 20:
                    output = 50
                if current_time > 25:
                    output = 25
                if current_time > 30:
                    output = 50
                if current_time > 35:
                    output = 25
                if current_time > 40:
                    output = 50
                if current_time > 45:
                    output = 25
                if current_time > 50:
                    output = 50
            case 12:         
                ####### TEST 12 to IDENTIFICATION SYSTEM ###########
                if current_time > 0:
                    output = 25
                if current_time > 5:
                    output = 35
                if current_time > 10:
                    output = 45
                if current_time > 15:
                    output = 55
                if current_time > 20:
                    output = 50
                if current_time > 25:
                    output = 45
                if current_time > 30:
                    output = 40
                if current_time > 35:
                    output = 35
                if current_time > 40:
                    output = 30
                if current_time > 45:
                    output = 25
                if current_time > 50:
                    output = 50
        output = max(min(output, 57), 23)  #23 y 57 son el rango de trabajo
        
        return output

def ploting (time_data):
    # Update the plot
    line1.set_xdata(time_data)
    line1.set_ydata(rpm_data)
    line2.set_xdata(time_data)
    line2.set_ydata(rpm_ref_data)
    ax.relim()
    ax.autoscale_view()
    plt.draw()
    plt.pause(0.01)

def plotD ():
    plt.plot(time_data, rpm_data)
    plt.show()





############################################
########## Functions for Extruder ##########
############################################

def get_temperature(voltage: float) -> float:
    """Get the average temperature from the voltage using Steinhart-Hart 
    equation"""
    REFERENCE_TEMPERATURE = 298.15 # K
    RESISTANCE_AT_REFERENCE = 100000 # Ω
    BETA_COEFFICIENT = 3977 # K
    VOLTAGE_SUPPLY = 3.3 # V
    RESISTOR = 10000 # Ω
    READINGS_TO_AVERAGE = 10
    if voltage < 0.0001:  # Prevenir división por cero
        return 0
    resistance = ((VOLTAGE_SUPPLY - voltage) * RESISTOR )/ voltage
    ln = math.log(resistance / RESISTANCE_AT_REFERENCE)
    temperature = (1 / ((ln / BETA_COEFFICIENT) + (1 / REFERENCE_TEMPERATURE))) - 273.15
    average_temperature = 0

    return temperature

def temp_filter (raw_temperature, previous_temp):
    alpha = 0.10
    temp = alpha * raw_temperature + (1 - alpha) * previous_temp
    return temp


def save_data_temp (time_data, stepper_rpm_data,
                    temperature_raw_data, temperature_data, heater_pwm_data, temp_reference_data):
    with open("FrED_data_temp.txt","a") as archivo:
        archivo.write("time\trpm\ttmp\tTMP\tPWM\t\n")
        size = len(time_data)
        for i in range(size-1):
            a = time_data[i]
            b = stepper_rpm_data[i]
            c = temperature_raw_data[i]
            d = temperature_data[i]
            e = heater_pwm_data[i]
            f = temp_reference_data[i]   #letter i is already in use
            
            archivo.write(f"{a}\t{b}\t{c}\t{d}\t{e}\t{f}\n")

def extruder_PID (temp_reference, temperature, previous_PIDerror, error_sum, current_time, previous_PIDtime):
    kp = 1 #0.6066 #0.4050 #0.4152 #0.1537
    ki = 0.004 #5.2503 #4.8298 #10.1415 #0.4597
    kd = 1.8 #0.03 #0.0335 #0.02

    delta_time = current_time- previous_PIDtime
    error = temp_reference-temperature
    error_d = (error - previous_PIDerror) / delta_time
    error_i = (error * delta_time) + error_sum

    pid = kp * error + ki * error_i + kd * error_d

    PIDerror = error
    return pid, error_i, PIDerror
