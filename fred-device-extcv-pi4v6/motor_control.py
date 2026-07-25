######### Program to control the FrED's DC motor ########

import os
import cv2
import time
import math
import board
import busio
import atexit
import datetime
import digitalio
import contextlib
import numpy as np
import matplotlib.pyplot as plt
import RPi.GPIO as GPIO
import adafruit_mcp3xxx.mcp3008 as MCP
GPIO.setmode(GPIO.BCM)
import FrED_functions
# Shared EMA filter, tuned from the real experiment data (see signal_filter.py).
from signal_filter import EMAFilter, ALPHA_SPOOLER_RPM, rescale_alpha

from time import sleep
#from gpiozero import Motor
#from gpiozero import RotaryEncoder
import spidev  #para nuevo encoder
from adafruit_mcp3xxx.analog_in import AnalogIn


########## GPIO Pin Definitions ##########
#encoder_A = 24         # DC motor encoder input pin 1
#encoder_B = 23         # DC motor encoder input pin 2
SLAVE_SELECT_ENC = 1   #para nuevo encoder
motorPin = 5         # DC motor PWM output pin

########## Initialise GPIO ##########
GPIO.setwarnings(False)
GPIO.setup(motorPin, GPIO.OUT)

########## Initialise DC motor encoder ##########
#encoder = RotaryEncoder(encoder_A, encoder_B, max_steps=0)

# Create the SPI bus
spi = busio.SPI(clock=board.SCK, MISO=board.MISO, MOSI=board.MOSI)

# Create the cs (chip select)
cs = digitalio.DigitalInOut(board.D8)

# Create the mcp object
mcp = MCP.MCP3008(spi, cs)

# Create analog inputs connected to the input pins on the MCP3008.
channel_0 = AnalogIn(mcp, MCP.P0)

# Capture video from the camera
#cap = cv2.VideoCapture(0)  # Use the appropriate camera index (0 or 1) if you have multiple cameras connected

# DC Motor initialisation
ppr = 300.8       # Pulses Per Revolution of the encoder
dcFreq = 2500 #100 #1000     #DC motor PWM frequency
fanFreq = 1000    #Fan PWM frequency

oldtime = 0                    #old DC time
oldpos = 0                     #old DC position
lasttime = 0                   #old stepper time
#Start DC motor
motor_output = GPIO.PWM(motorPin, dcFreq)
motor_output.start(0)   #initializing the PWM
frame_count = 0

#para nuevo encoder
GPIO.setup(SLAVE_SELECT_ENC, GPIO.OUT)
GPIO.output(SLAVE_SELECT_ENC, GPIO.HIGH)

##################### para nuevo encoder #####################
def initialize_encoder():
    global spi_enc
    spi_enc = spidev.SpiDev()
    spi_enc.open(0, 0)
    spi_enc.max_speed_hz = 50000

    # Initialize encoder
    GPIO.output(SLAVE_SELECT_ENC, GPIO.LOW)
    spi_enc.xfer2([0x88, 0x03])
    GPIO.output(SLAVE_SELECT_ENC, GPIO.HIGH)

    clear_encoder_count()


def clear_encoder_count():
    GPIO.output(SLAVE_SELECT_ENC, GPIO.LOW)
    spi_enc.xfer2([0x98, 0x00, 0x00, 0x00, 0x00])
    GPIO.output(SLAVE_SELECT_ENC, GPIO.HIGH)

    time.sleep(0.0001)

    GPIO.output(SLAVE_SELECT_ENC, GPIO.LOW)
    spi_enc.xfer2([0xE0])
    GPIO.output(SLAVE_SELECT_ENC, GPIO.HIGH)


def read_encoder():
    GPIO.output(SLAVE_SELECT_ENC, GPIO.LOW)
    spi_enc.xfer2([0x60])
    count_1 = spi_enc.xfer2([0x00])
    count_2 = spi_enc.xfer2([0x00])
    count_3 = spi_enc.xfer2([0x00])
    count_4 = spi_enc.xfer2([0x00])
    GPIO.output(SLAVE_SELECT_ENC, GPIO.HIGH)

    count_value = (
        (count_1[0] << 24) +
        (count_2[0] << 16) +
        (count_3[0] << 8) +
        count_4[0]
    )
    return count_value

############################################################

########### variables ###########
rpm_reference = 25  # desired motor speed
previous_time = 0
previous_PIDtime = 0
previous_steps = 0
previous_rpm = 0
previous_PIDerror = 0 #rpm_reference #se iguala a la referencia para 
                                  #tener derivada del error cero y evitar "picos" artificales 
tm = 0.02           # Sample time
match_time = 0.020
PWM_motor = 0       #DC motor PWM
error_sum = 0

u = 0
        
muestra = 1

########### Initialing lists ##########
time_data = []
rpm_data = []
rpm_raw_data = []
rpm_ref_data = []
motor_input_data = []
PWM_motor_data = []
motor_voltage_data = []

plt.ion()
fig, ax = plt.subplots()
line1, = ax.plot(time_data, rpm_data, label='Motor speed (rmp)')
line2, = ax.plot(time_data, rpm_ref_data, label='rpm reference (rpm)')
ax.legend()
ax.set_title('DC Motor without PID')

###############################################
########## declarations of functions ########## 

def ploting ():
    # Update the plot
    line1.set_xdata(time_data)
    line1.set_ydata(rpm_data)
    line2.set_xdata(time_data)
    line2.set_ydata(rpm_ref_data)
    #line2.set_ydata(motor_input_data)
    ax.relim()
    ax.autoscale_view()
    plt.draw()
    plt.pause(0.01)

def plotD ():
    plt.plot(time_data, rpm_data)
    plt.show()

########## starting to count time ##########
tstart = time.perf_counter()   #start internal clock
initial_time = time.time()

initialize_encoder()  #inicializamos el encoder
previous_position = 4294967269
first_sample=True

# --- EMA filter for the RPM (same TYPE as FrED_functions.filter, but the
# coefficient is derived from the recorded experiment data in signal_filter.py).
# ALPHA_SPOOLER_RPM is tuned for 50 Hz; rescale it to this script's actual rate
# (1/tm) so the smoothing/time-constant is preserved if you change tm.
rpm_ema = EMAFilter(rescale_alpha(ALPHA_SPOOLER_RPM, 50.0, 1.0 / tm))


try:
    #Loop Execution
    while True:
        current_time = time.perf_counter() - tstart
        
        current_position = read_encoder()
        
        if first_sample==True:
            current_position=4294967265
            first_sample=False            
        
        
        rpm_raw = FrED_functions.motor_speed(current_time, previous_time, previous_position, current_position)

        previous_time = current_time
        previous_position = current_position        

        #previous_position = read_encoder()
        #previous_time = 0

        rpm = rpm_ema.update(rpm_raw)   # EMA filter (signal_filter.EMAFilter)
        previous_rpm = rpm
        
        motor_input, error_i, PIDerror = FrED_functions.PID (rpm_reference, rpm, previous_PIDerror, 
                                          error_sum, current_time, previous_PIDtime)
        #motor_input, error_i = FrED_functions.PI (rpm_reference, rpm, error_sum, 
        #                                           current_time, previous_PIDtime)
        #motor_input, error_i = FrED_functions.STSM (rpm_reference, rpm, u, error_sum, 
        #                                           current_time, previous_PIDtime)
        previous_PIDtime = current_time
        previous_PIDerror = PIDerror
        error_sum = error_i


        opcion_LS = 11
        motor_input = FrED_functions.least_square (current_time, opcion_LS)
        #motor_input = 29
        PWM_motor = FrED_functions.linearization (motor_input)
        PWM_motor = max(min(PWM_motor, 100), 0)
        #PWM_motor = 50
        #PWM_motor = 0
        #motor_output.ChangeDutyCycle(motor_input) #sending PWM to the motor
        #if PWM_motor_data == []:
        #    PWM_motor = 0
        motor_output.ChangeDutyCycle(PWM_motor)

        #ploting()
        #plotD()

        if current_time>=muestra:
            #print("DC Speed = {:0.2f} rpm".format(rpm))
            #print("DC Speed =",rpm)
            #print("time =",current_time)
            #print("Speed =",rpm)
            #print("PWM =",PWM_motor)
            #print("pasos =",encoder.steps)
            #print("enc =", current_position)   #4294967269, 
            print(f"{current_time}\t{rpm}\t{motor_input}")
            muestra = muestra + 1
        #print("time =",current_time)
        #timeD = time.time() - initial_time
        #print("timeD =",timeD)

        ########### convertion PWM to votage ##########
        motor_voltage = (12 * PWM_motor) / 100

        ########### save data to the lists #########
        time_data.append(round(current_time, 2))
        rpm_data.append(round(rpm, 2))
        rpm_raw_data.append(round(rpm_raw, 2))
        rpm_ref_data.append(rpm_reference)
        motor_input_data.append(round(motor_input,2))
        PWM_motor_data.append(round(PWM_motor,2))
        motor_voltage_data.append(round(motor_voltage,2))

        ########## to have a consistent sample time ##########
        wait = max(0, match_time - current_time)
        time.sleep(wait)
        match_time = match_time + tm
except KeyboardInterrupt:
    print ("\nCode Stopped\n")
    ########## save data in a txt file ##########
    FrED_functions.save_data(time_data, rpm_data, motor_voltage_data,
                              motor_input_data, PWM_motor_data, rpm_raw_data)
    print ("Data saved in FrED_data.txt file\n\n")
    #plotD ()
    
finally:
    GPIO.cleanup()
    #cap.release()
    #cv2.destroyAllWindows()

