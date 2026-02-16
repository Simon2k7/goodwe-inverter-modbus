#!/usr/bin/env python3
"""Debug script to analyze raw sensor values from GoodWe inverter.

This script helps identify which values are unrealistic and where they come from.
"""

import asyncio
import goodwe
import logging
import sys
from datetime import datetime

# Setup logging
logging.basicConfig(
    format="%(asctime)-15s %(levelname)s: %(message)s",
    stream=sys.stderr,
    level=logging.INFO,
)

# Inverter configuration - ADJUST THESE VALUES
IP_ADDRESS = "192.168.1.49"  # Change to your inverter IP
PORT = 502  # 8899 for UDP, 502 for Modbus/TCP
PROTOCOL = "TCP"  # "UDP" or "TCP"
FAMILY = "ET"  # One of ET, ES, DT or None to detect automatically
TIMEOUT = 1
RETRIES = 3


def format_value(value, sensor):
    """Format value with unit and check if it's realistic."""
    formatted = f"{value} {sensor.unit}"
    
    # Check for common issues
    issues = []
    
    # Check for Modbus error values
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in [65535, 32767, -32768, 0xFFFF, 0x7FFF, 0x8000]:
            issues.append("⚠️ MODBUS ERROR CODE")
        
        # Check for unrealistic ranges
        if sensor.unit == "V" and (value < 0 or value > 1000):
            issues.append("⚠️ OUT OF RANGE (0-1000V)")
        elif sensor.unit == "A" and (value < -150 or value > 150):
            issues.append("⚠️ OUT OF RANGE (-150-150A)")
        elif sensor.unit == "W" and (value < -50000 or value > 50000):
            issues.append("⚠️ OUT OF RANGE (-50k-50kW)")
        elif sensor.unit == "kWh" and value < 0:
            issues.append("🔴 NEGATIVE ENERGY VALUE (INVALID!)")
        elif sensor.unit == "kWh" and value > 1000000:
            issues.append("⚠️ OUT OF RANGE (>1000MWh)")
        elif sensor.unit == "C" and (value < -40 or value > 100):
            issues.append("⚠️ OUT OF RANGE (-40-100°C)")
        elif sensor.unit == "Hz" and (value < 45 or value > 65):
            issues.append("⚠️ OUT OF RANGE (45-65Hz)")
        elif sensor.unit == "%" and (value < 0 or value > 100):
            issues.append("⚠️ OUT OF RANGE (0-100%)")
    
    if issues:
        formatted += " " + " ".join(issues)
    
    return formatted


async def main():
    """Main debug function."""
    print(f"\n{'='*80}")
    print(f"GoodWe Inverter Debug Tool")
    print(f"{'='*80}\n")
    print(f"Connecting to inverter at {IP_ADDRESS}:{PORT} ({PROTOCOL})...")
    
    try:
        # Connect to inverter
        port = PORT if PROTOCOL == "TCP" else 8899
        inverter = await goodwe.connect(
            host=IP_ADDRESS,
            port=port,
            family=FAMILY,
            timeout=TIMEOUT,
            retries=RETRIES,
        )
        
        print(f"✓ Connected successfully!\n")
        print(f"Inverter Information:")
        print(f"  Model:       {inverter.model_name}")
        print(f"  Serial:      {inverter.serial_number}")
        print(f"  Firmware:    {inverter.firmware}")
        print(f"  Rated Power: {inverter.rated_power}W")
        print(f"\n{'='*80}\n")
        
        # Read runtime data
        print(f"Reading sensor values at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...\n")
        response = await inverter.read_runtime_data()
        
        # Categorize sensors
        problematic = []
        suspicious = []
        normal = []
        
        for sensor in inverter.sensors():
            if sensor.id_ in response:
                value = response[sensor.id_]
                formatted = format_value(value, sensor)
                
                if "🔴" in formatted:
                    problematic.append((sensor, value, formatted))
                elif "⚠️" in formatted:
                    suspicious.append((sensor, value, formatted))
                else:
                    normal.append((sensor, value, formatted))
        
        # Print problematic values first
        if problematic:
            print(f"🔴 CRITICAL ISSUES ({len(problematic)} sensors):")
            print(f"{'-'*80}")
            for sensor, value, formatted in problematic:
                print(f"  {sensor.id_:30} ({sensor.name:40}): {formatted}")
            print()
        
        # Print suspicious values
        if suspicious:
            print(f"⚠️  SUSPICIOUS VALUES ({len(suspicious)} sensors):")
            print(f"{'-'*80}")
            for sensor, value, formatted in suspicious:
                print(f"  {sensor.id_:30} ({sensor.name:40}): {formatted}")
            print()
        
        # Print summary of normal values
        print(f"✓ NORMAL VALUES ({len(normal)} sensors)")
        print(f"{'-'*80}")
        
        # Show key sensors
        key_sensors = [
            "ppv", "house_consumption", "active_power", "battery_soc",
            "e_day", "e_total", "e_bat_charge_total", "e_bat_discharge_total",
            "meter_e_total_exp", "meter_e_total_imp"
        ]
        
        for sensor_id in key_sensors:
            for sensor, value, formatted in normal:
                if sensor.id_ == sensor_id:
                    print(f"  {sensor.id_:30} ({sensor.name:40}): {formatted}")
        
        print(f"\nFor complete list of all {len(normal)} normal sensors, check the log above.")
        
        print(f"\n{'='*80}")
        print(f"Debug Summary:")
        print(f"  Critical Issues:    {len(problematic)}")
        print(f"  Suspicious Values:  {len(suspicious)}")
        print(f"  Normal Values:      {len(normal)}")
        print(f"  Total Sensors:      {len(problematic) + len(suspicious) + len(normal)}")
        print(f"{'='*80}\n")
        
        if problematic or suspicious:
            print("⚠️  Action Required:")
            print("   1. Check if validation is enabled in Home Assistant")
            print("   2. Review the debug log in Home Assistant: /config/home-assistant.log")
            print("   3. Download diagnostics from Settings → Devices & Services → GoodWe")
            print("   4. If issues persist, consider adjusting outlier sensitivity\n")
        else:
            print("✓ All values look good! No unrealistic readings detected.\n")
        
    except goodwe.InverterError as e:
        print(f"❌ Error connecting to inverter: {e}")
        print(f"\nTroubleshooting:")
        print(f"  - Check IP address: {IP_ADDRESS}")
        print(f"  - Check port: {PORT}")
        print(f"  - Check protocol: {PROTOCOL}")
        print(f"  - Ensure inverter is powered on and connected to network")
        print(f"  - Try ping: ping {IP_ADDRESS}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
