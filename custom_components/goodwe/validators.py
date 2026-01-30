"""Data validation for GoodWe inverter sensor values."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Common Modbus error values that should be filtered out
MODBUS_ERROR_VALUES = {
    0xFFFF,  # Common read error (16-bit unsigned max)
    0x7FFF,  # Maximum positive value for signed 16-bit
    0x8000,  # Minimum negative value for signed 16-bit (often used as error)
    -32768,  # Python representation of 0x8000 as signed
    65535,   # Python representation of 0xFFFF
}

# Validation ranges for different sensor units
# Format: {unit: (min_value, max_value)}
DEFAULT_RANGES = {
    "V": (0, 1000),          # Voltage: 0-1000V (covers PV and high voltage battery systems)
    "A": (-150, 150),        # Current: -150A to +150A (covers charge/discharge)
    "W": (-50000, 50000),    # Power: -50kW to +50kW (covers most residential/small commercial)
    "kWh": (0, 100000),     # Energy: 0-1000MWh lifetime (should only increase for totals)
    "VA": (-50000, 50000),   # Apparent power: similar to real power
    "var": (-50000, 50000),  # Reactive power: similar to real power
    "C": (-40, 100),         # Temperature: -40°C to 100°C (extended operating range)
    "Hz": (45, 65),          # Frequency: 45-65Hz (covers both 50Hz and 60Hz systems with margin)
    "%": (0, 100),           # Percentage: 0-100%
    "h": (0, 1000000),       # Hours: 0-1M hours (lifetime counters)
}

# Special handling for specific sensor IDs that need different ranges
SENSOR_SPECIFIC_RANGES = {
    # Grid voltages - tighter range
    "vgrid": (180, 280),
    "vgrid2": (180, 280),
    "vgrid3": (180, 280),
    
    # Battery voltage - depends on system but typical range
    "vbattery1": (40, 600),
    
    # PV voltages - can be higher
    "vpv1": (0, 1000),
    "vpv2": (0, 1000),
    "vpv3": (0, 1000),
    "vpv4": (0, 1000),
    
    # Grid frequency - tighter tolerance
    "fgrid": (49, 61),
    "fgrid2": (49, 61),
    "fgrid3": (49, 61),
    
    # Battery SoC
    "battery_soc": (0, 100),
    
    # Daily energy production - reasonable daily max
    "e_day": (0, 200),
    "e_load_day": (0, 500),
}

# Sensors that should only increase (never decrease)
MONOTONIC_INCREASING_SENSORS = {
    "e_total",
    "e_bat_charge_total",
    "e_bat_discharge_total",
    "meter_e_total_exp",
    "meter_e_total_imp",
    "h_total",
}


class ValidationStats:
    """Track validation statistics for diagnostics."""

    def __init__(self) -> None:
        """Initialize validation statistics."""
        self.rejected_count: dict[str, int] = {}
        self.recent_failures: list[dict[str, Any]] = []
        self.max_recent_failures = 50

    def record_rejection(self, sensor_id: str, value: Any, reason: str) -> None:
        """Record a rejected value."""
        self.rejected_count[sensor_id] = self.rejected_count.get(sensor_id, 0) + 1
        
        failure_entry = {
            "sensor_id": sensor_id,
            "value": value,
            "reason": reason,
        }
        
        self.recent_failures.append(failure_entry)
        
        # Keep only recent failures
        if len(self.recent_failures) > self.max_recent_failures:
            self.recent_failures.pop(0)

    def get_stats(self) -> dict[str, Any]:
        """Get validation statistics."""
        return {
            "rejected_count": dict(self.rejected_count),
            "recent_failures": list(self.recent_failures),
        }


class SensorValidator:
    """Validator for sensor values from GoodWe inverter."""

    def __init__(
        self,
        enable_validation: bool = True,
        outlier_sensitivity: float = 5.0,
        custom_ranges: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Initialize sensor validator.
        
        Args:
            enable_validation: Whether to enable validation (default: True)
            outlier_sensitivity: Multiplier for outlier detection (default: 5.0)
                                Higher values = more tolerant of outliers
            custom_ranges: Optional custom ranges per sensor ID
        """
        self.enable_validation = enable_validation
        self.outlier_sensitivity = outlier_sensitivity
        self.custom_ranges = custom_ranges or {}
        self.stats = ValidationStats()
        
        # Track recent values for outlier detection
        self._value_history: dict[str, list[float]] = {}
        self._max_history = 10
        
        # Track last known values for monotonic sensors
        self._last_monotonic_values: dict[str, float] = {}

    def validate_data(
        self,
        data: dict[str, Any],
        sensor_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate all sensor values in data dictionary.
        
        Args:
            data: Dictionary of sensor_id -> value from inverter
            sensor_metadata: Optional metadata about sensors (unit, kind, etc.)
        
        Returns:
            Dictionary with only valid values (invalid ones removed)
        """
        if not self.enable_validation:
            return data
        
        validated_data = {}
        
        for sensor_id, value in data.items():
            if self._validate_value(sensor_id, value, sensor_metadata):
                validated_data[sensor_id] = value
                
                # Update history for numeric values
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._update_history(sensor_id, value)
            else:
                _LOGGER.debug(
                    "Rejected value for sensor %s: %s",
                    sensor_id,
                    value,
                )
        
        return validated_data

    def _validate_value(
        self,
        sensor_id: str,
        value: Any,
        sensor_metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Validate a single sensor value.
        
        Args:
            sensor_id: Sensor identifier
            value: Value to validate
            sensor_metadata: Optional metadata about the sensor
        
        Returns:
            True if value is valid, False otherwise
        """
        # Allow None values (will be handled by coordinator)
        if value is None:
            return True
        
        # Non-numeric values (strings, enums, etc.) are accepted as-is
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return True
        
        # Check for Modbus error values
        if self._is_modbus_error(value):
            self.stats.record_rejection(
                sensor_id, value, "Modbus error value"
            )
            return False
        
        # Check for NaN or Inf
        if not self._is_finite(value):
            self.stats.record_rejection(
                sensor_id, value, "Non-finite value (NaN or Inf)"
            )
            return False
        
        # Get sensor unit from metadata or sensor_id
        unit = self._get_sensor_unit(sensor_id, sensor_metadata)
        
        # Check range validation
        if not self._validate_range(sensor_id, value, unit):
            return False
        
        # Check monotonic increasing constraint
        if not self._validate_monotonic(sensor_id, value):
            return False
        
        # Check for outliers
        if not self._validate_outlier(sensor_id, value):
            return False
        
        return True

    def _is_modbus_error(self, value: float) -> bool:
        """Check if value is a known Modbus error code."""
        # Check exact matches
        if value in MODBUS_ERROR_VALUES:
            return True
        
        # Check if close to error values (floating point comparison)
        for error_val in MODBUS_ERROR_VALUES:
            if abs(value - error_val) < 0.01:
                return True
        
        return False

    def _is_finite(self, value: float) -> bool:
        """Check if value is finite (not NaN or Inf)."""
        try:
            import math
            return math.isfinite(value)
        except (ValueError, TypeError):
            return False

    def _get_sensor_unit(
        self,
        sensor_id: str,
        sensor_metadata: dict[str, Any] | None,
    ) -> str | None:
        """Get sensor unit from metadata or infer from sensor_id."""
        if sensor_metadata and sensor_id in sensor_metadata:
            return sensor_metadata[sensor_id].get("unit")
        
        # Try to infer from common sensor ID patterns
        if "voltage" in sensor_id or sensor_id.startswith("v"):
            return "V"
        if "current" in sensor_id or sensor_id.startswith("i"):
            return "A"
        if "power" in sensor_id or sensor_id.startswith("p"):
            return "W"
        if "energy" in sensor_id or sensor_id.startswith("e_"):
            return "kWh"
        if "temp" in sensor_id or "temperature" in sensor_id:
            return "C"
        if "freq" in sensor_id or sensor_id.startswith("f"):
            return "Hz"
        if "soc" in sensor_id or "%" in sensor_id:
            return "%"
        
        return None

    def _validate_range(
        self,
        sensor_id: str,
        value: float,
        unit: str | None,
    ) -> bool:
        """Validate value is within acceptable range."""
        # Check custom range first
        if sensor_id in self.custom_ranges:
            min_val, max_val = self.custom_ranges[sensor_id]
            if not (min_val <= value <= max_val):
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Outside custom range [{min_val}, {max_val}]",
                )
                return False
            return True
        
        # Check sensor-specific range
        if sensor_id in SENSOR_SPECIFIC_RANGES:
            min_val, max_val = SENSOR_SPECIFIC_RANGES[sensor_id]
            if not (min_val <= value <= max_val):
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Outside sensor-specific range [{min_val}, {max_val}]",
                )
                return False
            return True
        
        # Check unit-based range
        if unit and unit in DEFAULT_RANGES:
            min_val, max_val = DEFAULT_RANGES[unit]
            if not (min_val <= value <= max_val):
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Outside unit range for {unit} [{min_val}, {max_val}]",
                )
                return False
        
        return True

    def _validate_monotonic(self, sensor_id: str, value: float) -> bool:
        """Validate that monotonic increasing sensors only increase."""
        if sensor_id not in MONOTONIC_INCREASING_SENSORS:
            return True
        
        if sensor_id in self._last_monotonic_values:
            last_value = self._last_monotonic_values[sensor_id]
            if value < last_value:
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Monotonic sensor decreased from {last_value} to {value}",
                )
                return False
        
        # Update last known value
        self._last_monotonic_values[sensor_id] = value
        return True

    def _validate_outlier(self, sensor_id: str, value: float) -> bool:
        """Validate value is not an outlier compared to recent values."""
        if sensor_id not in self._value_history:
            return True
        
        history = self._value_history[sensor_id]
        if len(history) < 3:
            # Not enough history to detect outliers
            return True
        
        # Calculate mean and max deviation from recent history
        mean = sum(history) / len(history)
        max_recent = max(history)
        min_recent = min(history)
        
        # If mean is very close to 0, use range-based detection
        if abs(mean) < 0.1:
            range_val = max_recent - min_recent
            threshold = range_val * self.outlier_sensitivity
            
            if abs(value) > threshold + max(abs(max_recent), abs(min_recent)):
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Outlier: value {value} too far from recent range [{min_recent}, {max_recent}]",
                )
                return False
        else:
            # Use mean-based detection
            threshold = abs(mean) * self.outlier_sensitivity
            
            if abs(value - mean) > threshold:
                self.stats.record_rejection(
                    sensor_id,
                    value,
                    f"Outlier: value {value} deviates >5x from mean {mean:.2f}",
                )
                return False
        
        return True

    def _update_history(self, sensor_id: str, value: float) -> None:
        """Update value history for outlier detection."""
        if sensor_id not in self._value_history:
            self._value_history[sensor_id] = []
        
        self._value_history[sensor_id].append(value)
        
        # Keep only recent history
        if len(self._value_history[sensor_id]) > self._max_history:
            self._value_history[sensor_id].pop(0)

    def get_stats(self) -> dict[str, Any]:
        """Get validation statistics for diagnostics."""
        return {
            "enabled": self.enable_validation,
            "outlier_sensitivity": self.outlier_sensitivity,
            "custom_ranges_count": len(self.custom_ranges),
            **self.stats.get_stats(),
        }
