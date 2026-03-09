#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import serial
import time
import threading
import logging

logger = logging.getLogger("hardware_bridge")

class HardwareBridge:
    """
    Bridges Bot logic to the RP2040 (Pico) via Serial.
    Executes light patterns for trade events.
    """
    def __init__(self, port='/dev/ttyACM0', baud=115200):
        self.port = port
        self.baud = baud
        self.ser = None
        self._lock = threading.Lock()
        self._connected = False

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            # Break into REPL
            self.ser.write(b'\x03\x03')
            time.sleep(0.1)
            self.ser.read_all()
            
            # Inject helper function onto the board
            setup_code = """
from machine import Pin
import time
led = Pin(25, Pin.OUT)
def play(p):
    if p == 'ENTRY':
        led.value(1)
    elif p == 'EXIT':
        for _ in range(3): led.value(1); time.sleep(0.2); led.value(0); time.sleep(0.2)
    elif p == 'TP':
        for _ in range(20): led.toggle(); time.sleep(0.05)
        led.value(0)
    elif p == 'SL':
        for _ in range(5):
            for i in range(50): led.value(1); time.sleep(0.005); led.value(0); time.sleep(0.015)
            time.sleep(0.2)
    elif p == 'START':
        for _ in range(10): led.toggle(); time.sleep(0.05)
        led.value(0)
    elif p == 'OFF':
        led.value(0)
"""
            self.ser.write(setup_code.encode() + b'\r\n')
            self._connected = True
            logger.info(f"Connected to RP2040 on {self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to hardware: {e}")
            self._connected = False

    def signal(self, pattern):
        """Send a pattern signal to the board in a separate thread (non-blocking)."""
        if not self._connected:
            self.connect()
        
        if self._connected:
            threading.Thread(target=self._send, args=(pattern,), daemon=True).start()

    def _send(self, pattern):
        with self._lock:
            try:
                cmd = f"play('{pattern}')\r\n"
                self.ser.write(cmd.encode())
                time.sleep(0.1)
                self.ser.read_all() # Clear buffer
            except Exception as e:
                logger.error(f"Hardware communication lost: {e}")
                self._connected = False

# Singleton instance
bridge = HardwareBridge()
