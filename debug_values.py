#!/usr/bin/env python3
"""Debug script to analyze and record raw sensor values from a GoodWe inverter."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Setup logging
logging.basicConfig(
    format="%(asctime)-15s %(levelname)s: %(message)s",
    stream=sys.stderr,
    level=logging.INFO,
)

# Inverter configuration - adjust these values or override them via CLI
IP_ADDRESS = "192.168.1.49"
PORT = 502  # 8899 for UDP, 502 for Modbus/TCP
PROTOCOL = "TCP"  # "UDP" or "TCP"
FAMILY = "ET"  # ET, ES, DT or None to detect automatically
TIMEOUT = 1
RETRIES = 3

# Recording configuration
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_OUTPUT_FILE = "debug_values_raw.jsonl"
DEFAULT_METADATA_FILE = "debug_values_metadata.json"
KEY_SENSORS = [
    "ppv",
    "house_consumption",
    "active_power",
    "battery_soc",
    "e_day",
    "e_total",
    "e_bat_charge_total",
    "e_bat_discharge_total",
    "meter_e_total_exp",
    "meter_e_total_imp",
]

MODBUS_ERROR_VALUES = {65535, 32767, -32768, 0xFFFF, 0x7FFF, 0x8000}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Read raw GoodWe runtime data once or continuously and append it "
            "to a JSONL file for later analysis."
        )
    )
    parser.add_argument("--host", default=IP_ADDRESS, help="Inverter IP address")
    parser.add_argument(
        "--protocol",
        default=PROTOCOL,
        choices=["TCP", "UDP"],
        help="Connection protocol",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=PORT,
        help="Port to use. If omitted, protocol default is used.",
    )
    parser.add_argument(
        "--family",
        default=FAMILY,
        help="Inverter family such as ET, ES, DT or none",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT,
        help="Read timeout in seconds",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=RETRIES,
        help="Number of retries per read",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds for continuous recording",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Number of samples to record. 0 means run forever.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help="Path to JSONL output file",
    )
    parser.add_argument(
        "--metadata-output",
        default=DEFAULT_METADATA_FILE,
        help="Path to metadata JSON file",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Read only one sample and exit",
    )
    return parser.parse_args()


def to_jsonable(value: Any) -> Any:
    """Convert runtime values into JSON-safe types."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def format_value(value: Any, sensor: Any) -> str:
    """Format value with unit and mark obvious issues."""
    formatted = f"{value} {sensor.unit}"
    issues = detect_issues(value, sensor.unit)
    if issues:
        formatted += " " + " ".join(issues)
    return formatted


def detect_issues(value: Any, unit: str | None) -> list[str]:
    """Return a list of obvious issues for a raw value."""
    issues: list[str] = []

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return issues

    if value in MODBUS_ERROR_VALUES:
        issues.append("⚠️ MODBUS ERROR CODE")

    if isinstance(value, float) and not math.isfinite(value):
        issues.append("⚠️ NON-FINITE")
        return issues

    if unit == "V" and (value < 0 or value > 1000):
        issues.append("⚠️ OUT OF RANGE (0-1000V)")
    elif unit == "A" and (value < -150 or value > 150):
        issues.append("⚠️ OUT OF RANGE (-150-150A)")
    elif unit == "W" and (value < -50000 or value > 50000):
        issues.append("⚠️ OUT OF RANGE (-50k-50kW)")
    elif unit == "kWh" and value < 0:
        issues.append("🔴 NEGATIVE ENERGY VALUE")
    elif unit == "kWh" and value > 100000:
        issues.append("⚠️ OUT OF RANGE (>100000kWh)")
    elif unit == "C" and (value < -40 or value > 100):
        issues.append("⚠️ OUT OF RANGE (-40-100C)")
    elif unit == "Hz" and (value < 45 or value > 65):
        issues.append("⚠️ OUT OF RANGE (45-65Hz)")
    elif unit == "%" and (value < 0 or value > 100):
        issues.append("⚠️ OUT OF RANGE (0-100%)")

    return issues


def build_sensor_metadata(inverter: Any) -> dict[str, dict[str, Any]]:
    """Build a compact metadata map for all sensors."""
    metadata: dict[str, dict[str, Any]] = {}
    for sensor in inverter.sensors():
        metadata[sensor.id_] = {
            "name": sensor.name,
            "unit": sensor.unit,
            "kind": str(sensor.kind),
        }
    return metadata


def summarize_sample(
    response: dict[str, Any],
    sensor_metadata: dict[str, dict[str, Any]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split a sample into problematic, suspicious and normal sensors."""
    problematic: list[tuple[str, str, str]] = []
    suspicious: list[tuple[str, str, str]] = []
    normal: list[tuple[str, str, str]] = []

    for sensor_id, meta in sensor_metadata.items():
        if sensor_id not in response:
            continue
        value = response[sensor_id]
        formatted = format_value(value, type("Sensor", (), {"unit": meta["unit"]})())
        label = f"{sensor_id:30} ({meta['name'][:40]:40})"
        entry = (sensor_id, label, formatted)
        if "🔴" in formatted:
            problematic.append(entry)
        elif "⚠️" in formatted:
            suspicious.append(entry)
        else:
            normal.append(entry)

    return problematic, suspicious, normal


def write_metadata(
    metadata_path: Path,
    inverter: Any,
    sensor_metadata: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    """Write one metadata file for later correlation."""
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "connection": {
            "host": args.host,
            "protocol": args.protocol,
            "port": args.port,
            "family": args.family,
            "timeout": args.timeout,
            "retries": args.retries,
            "interval_seconds": args.interval,
        },
        "inverter": {
            "model_name": getattr(inverter, "model_name", None),
            "serial_number": getattr(inverter, "serial_number", None),
            "firmware": getattr(inverter, "firmware", None),
            "rated_power": getattr(inverter, "rated_power", None),
        },
        "sensors": sensor_metadata,
    }
    metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_sample(
    output_path: Path,
    sample_index: int,
    response: dict[str, Any],
    sensor_metadata: dict[str, dict[str, Any]],
) -> None:
    """Append one raw sample as JSONL."""
    issues: dict[str, list[str]] = {}
    for sensor_id, value in response.items():
        unit = sensor_metadata.get(sensor_id, {}).get("unit")
        detected = detect_issues(value, unit)
        if detected:
            issues[sensor_id] = detected

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sample_index": sample_index,
        "raw_data": {sensor_id: to_jsonable(value) for sensor_id, value in response.items()},
        "issues": issues,
    }

    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def print_header(inverter: Any, output_path: Path, metadata_path: Path, args: argparse.Namespace) -> None:
    """Print startup information."""
    print(f"\n{'=' * 80}")
    print("GoodWe Inverter Debug Recorder")
    print(f"{'=' * 80}\n")
    print(f"Connected to inverter at {args.host}:{args.port} ({args.protocol})")
    print(f"Model:       {getattr(inverter, 'model_name', 'unknown')}")
    print(f"Serial:      {getattr(inverter, 'serial_number', 'unknown')}")
    print(f"Firmware:    {getattr(inverter, 'firmware', 'unknown')}")
    print(f"Rated Power: {getattr(inverter, 'rated_power', 'unknown')}W")
    print(f"Output:      {output_path}")
    print(f"Metadata:    {metadata_path}")
    if args.once:
        print("Mode:        single sample")
    elif args.samples:
        print(f"Mode:        {args.samples} samples")
        print(f"Interval:    {args.interval:.1f}s")
    else:
        print("Mode:        continuous")
        print(f"Interval:    {args.interval:.1f}s")
    print(f"\n{'=' * 80}\n")


def print_sample_summary(
    sample_index: int,
    problematic: list[tuple[str, str, str]],
    suspicious: list[tuple[str, str, str]],
    normal: list[tuple[str, str, str]],
) -> None:
    """Print a compact sample summary to stdout."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Sample #{sample_index}")
    print(
        f"  Critical: {len(problematic)}  "
        f"Suspicious: {len(suspicious)}  "
        f"Normal: {len(normal)}"
    )

    if problematic:
        for _, label, formatted in problematic[:10]:
            print(f"  CRITICAL   {label}: {formatted}")

    if suspicious:
        for _, label, formatted in suspicious[:10]:
            print(f"  SUSPICIOUS {label}: {formatted}")

    shown_key_sensor = False
    for sensor_id, label, formatted in normal:
        if sensor_id in KEY_SENSORS:
            print(f"  KEY        {label}: {formatted}")
            shown_key_sensor = True
    if not shown_key_sensor:
        print("  KEY        no configured key sensors present in this sample")

    print()


async def record_samples(inverter: Any, args: argparse.Namespace) -> None:
    """Record one or more samples and write them to disk."""
    output_path = Path(args.output).expanduser().resolve()
    metadata_path = Path(args.metadata_output).expanduser().resolve()
    sensor_metadata = build_sensor_metadata(inverter)
    write_metadata(metadata_path, inverter, sensor_metadata, args)
    print_header(inverter, output_path, metadata_path, args)

    target_samples = 1 if args.once else args.samples
    sample_index = 0

    while True:
        sample_index += 1
        response = await inverter.read_runtime_data()
        append_sample(output_path, sample_index, response, sensor_metadata)
        problematic, suspicious, normal = summarize_sample(response, sensor_metadata)
        print_sample_summary(sample_index, problematic, suspicious, normal)

        if args.once:
            break
        if target_samples and sample_index >= target_samples:
            break

        await asyncio.sleep(args.interval)


async def main() -> None:
    """Main debug function."""
    args = parse_args()
    import goodwe

    if not args.port:
        args.port = 502 if args.protocol == "TCP" else 8899
    if args.protocol == "UDP" and args.port == PORT:
        args.port = 8899
    if args.once:
        args.samples = 1

    family = None if str(args.family).lower() == "none" else args.family

    try:
        inverter = await goodwe.connect(
            host=args.host,
            port=args.port,
            family=family,
            timeout=args.timeout,
            retries=args.retries,
        )
        await record_samples(inverter, args)
    except goodwe.InverterError as error:
        print(f"❌ Error connecting to inverter: {error}")
        print("\nTroubleshooting:")
        print(f"  - Check IP address: {args.host}")
        print(f"  - Check port: {args.port}")
        print(f"  - Check protocol: {args.protocol}")
        print("  - Ensure inverter is powered on and reachable")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as error:
        print(f"❌ Unexpected error: {error}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
