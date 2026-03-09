import pytest
import time
from unittest.mock import MagicMock, patch
import hardware_bridge

@pytest.fixture
def mock_serial():
    """Fixture to mock serial.Serial."""
    with patch("serial.Serial") as mock:
        # Mock instance of Serial
        instance = mock.return_value
        instance.is_open = True
        yield instance

def test_hardware_bridge_connect(mock_serial):
    """Test that connect() sends the correct setup code to the board."""
    bridge = hardware_bridge.HardwareBridge(port="/dev/test_port")
    bridge.connect()
    
    assert bridge._connected is True
    # Verify that it tried to break into REPL and send setup code
    calls = [c[0][0] for c in mock_serial.write.call_args_list]
    assert b"\x03\x03" in calls
    # Check if some part of setup_code was sent (as one of the writes)
    assert any(b"from machine import Pin" in c for c in calls)

def test_hardware_bridge_connect_failure():
    """Test that connect() handles serial.SerialException."""
    import serial
    with patch("serial.Serial", side_effect=serial.SerialException("Port not found")):
        bridge = hardware_bridge.HardwareBridge(port="/dev/invalid")
        bridge.connect()
        assert bridge._connected is False

def test_hardware_bridge_signal_sends_command(mock_serial):
    """Test that signal() eventually sends the 'play' command."""
    bridge = hardware_bridge.HardwareBridge(port="/dev/test_port")
    bridge._connected = True
    bridge.ser = mock_serial
    
    bridge.signal("ENTRY")
    
    # Give the thread a moment to run
    time.sleep(0.2)
    
    # Verify that the correct command was written
    calls = [c[0][0] for c in mock_serial.write.call_args_list]
    assert b"play('ENTRY')\r\n" in calls

def test_hardware_bridge_auto_connect_on_signal(mock_serial):
    """Test that signal() calls connect() if not already connected."""
    bridge = hardware_bridge.HardwareBridge(port="/dev/test_port")
    assert bridge._connected is False
    
    with patch.object(bridge, "connect", wraps=bridge.connect) as mock_connect:
        bridge.signal("EXIT")
        time.sleep(0.1) # Wait for thread
        mock_connect.assert_called_once()

def test_hardware_bridge_send_error_handling(mock_serial):
    """Test that _send handles write errors by disconnecting."""
    bridge = hardware_bridge.HardwareBridge(port="/dev/test_port")
    bridge._connected = True
    bridge.ser = mock_serial
    
    mock_serial.write.side_effect = Exception("Write error")
    
    # Calling _send directly for easier testing (instead of via signal thread)
    bridge._send("TP")
    
    assert bridge._connected is False
