AC Voltage Protection System

This project is an Arduino-based AC voltage protector developed using SimulIDE. It measures grid voltage and disconnects the load when overvoltage or undervoltage occurs.

The system uses a Finite State Machine (FSM), so the relay remains OFF after a fault until it is manually reset.

Software required:

SimulIDE

Arduino IDE (to compile the code)

Python 3

Python libraries: pyserial, matplotlib

com0com (for virtual serial ports)

How to run

Download this GitHub repository.

Open the simulation file from project_files/ in SimulIDE.

Load the compiled Arduino .hex firmware.

Set the AC source to 220V RMS, 50Hz. In WaveGen, use 311.13V peak amplitude.

Run the simulation.

How to run the Python Dashboard

The dashboard was the most difficult part for me because SimulIDE and Python cannot directly communicate without configuring their serial connections.

I used com0com to create a virtual serial port pair:

COM6 for SimulIDE

COM5 for Python Dashboard

Baud rate: 9600

First, install the required Python libraries:

pip install pyserial matplotlib

Open PowerShell in the folder containing fsm_dashboard.py and run:

py fsm_dashboard.py

In SimulIDE, open the SerialPort connection using COM6. Then select COM5 in the Python Dashboard and click Connect.

If the dashboard does not receive data, check that the simulation is running, both ports belong to the same com0com pair, and COM5 is not already being used by another program.

Sometimes I had to disconnect and reconnect the dashboard after restarting the simulation. Also, SimulIDE's Serial Terminal should not use the same Arduino TX/RX pins at the same time as the SerialPort.

The dashboard displays live grid voltage, relay status, fault conditions and a voltage graph. It also supports remote reset and changing the voltage protection limits.

Protection settings

Normal voltage: 180V to 250V

Undervoltage fault: below 180V

Overvoltage fault: above 250V

Fault reset: D7 pushbutton or Python Dashboard RESET command

The relay does not automatically reconnect after a fault.

Test results

At approximately 220V, the system operated normally. At around 270V, overvoltage protection disconnected the load. Undervoltage protection was also tested around 170V.

Current sensing was added using a 4.7-ohm shunt resistor, but the measured current was around 0.12A compared with an expected value of approximately 0.22A. The current measurement still needs correction, and overcurrent protection is not included.

Project files

src/ - Arduino source code

schematics/ - Circuit and block diagram

test_evidence/ - Test screenshots and logs

project_files/ - SimulIDE simulation, firmware and Python dashboard

design_document.pdf - One-page demonstration report
