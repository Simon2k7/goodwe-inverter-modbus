# Sensor Value Validation Feature

## Overview

This implementation adds comprehensive sensor value validation to the GoodWe Home Assistant integration to prevent unrealistic values from corrupting statistics. This addresses issues where Modbus/TCP communication errors or inverter glitches send impossible readings.

## Implementation Summary

### Files Created

1. **`custom_components/goodwe/validators.py`** (NEW)
   - Core validation logic with `SensorValidator` class
   - Validates sensor values against configurable ranges
   - Detects Modbus error codes (0xFFFF, 0x7FFF, 0x8000)
   - Implements outlier detection using historical values
   - Enforces monotonic increasing constraints for energy counters
   - Tracks validation statistics for diagnostics

### Files Modified

2. **`custom_components/goodwe/coordinator.py`**
   - Integrated `SensorValidator` into data update coordinator
   - Added validation in `_async_update_data()` method
   - Falls back to last known good values when validation fails
   - **Bug fix**: Fixed `total_sensor_value()` to use `is not None` instead of falsy check

3. **`custom_components/goodwe/const.py`**
   - Added validation configuration constants:
     - `CONF_ENABLE_VALIDATION` (default: True)
     - `CONF_OUTLIER_SENSITIVITY` (default: 5.0)
     - `CONF_CUSTOM_RANGES` (for custom per-sensor ranges)
     - `DEFAULT_ENABLE_VALIDATION` = True
     - `DEFAULT_OUTLIER_SENSITIVITY` = 5.0

4. **`custom_components/goodwe/config_flow.py`**
   - Extended options flow with validation settings
   - Added UI fields for enable_validation and outlier_sensitivity
   - Range validation: sensitivity must be 1.0-20.0

5. **`custom_components/goodwe/diagnostics.py`**
   - Added validation statistics to diagnostic output
   - Shows rejected value counts per sensor
   - Displays recent validation failures with reasons

6. **`custom_components/goodwe/strings.json`**
   - Added UI strings for validation options
   - Includes descriptions for user guidance

7. **`custom_components/goodwe/translations/en.json`**
   - Added English translations for validation options
   - Includes helper text for configuration

8. **`README.md`**
   - Added comprehensive "Sensor Value Validation" section
   - Documents what validation does, how to configure it
   - Troubleshooting guide for false positives

## Validation Rules

### Default Value Ranges by Unit

| Unit | Range | Description |
|------|-------|-------------|
| V | 0-1000V | Voltage sensors |
| A | -150-150A | Current sensors (negative = discharge) |
| W | -50000-50000W | Power sensors |
| kWh | 0-1000000kWh | Energy counters (lifetime) |
| C | -40-100°C | Temperature sensors |
| Hz | 45-65Hz | Frequency sensors |
| % | 0-100% | Percentage sensors |
| VA | -50000-50000VA | Apparent power |
| var | -50000-50000var | Reactive power |

### Sensor-Specific Ranges

Tighter ranges for specific sensors:
- Grid voltages: 180-280V
- Grid frequency: 49-61Hz
- Battery SoC: 0-100%
- Daily energy: 0-200kWh per day

### Validation Checks

1. **Modbus Error Detection**: Filters out common error values
2. **Range Validation**: Ensures values are within acceptable bounds
3. **Monotonic Validation**: Energy counters must never decrease
4. **Outlier Detection**: Rejects values deviating >5x from recent average
5. **Finite Check**: Filters out NaN and Inf values

## Configuration Options

### Via Home Assistant UI

Settings → Devices & Services → GoodWe → Configure

- **Enable sensor value validation** (boolean, default: True)
  - Toggle validation on/off
  
- **Outlier detection sensitivity** (float, 1-20, default: 5.0)
  - Controls tolerance for value spikes
  - Lower = stricter (1-3)
  - Higher = more tolerant (10-20)

### Programmatic (Advanced)

Custom ranges can be set via options (not exposed in UI by default):

```python
CONF_CUSTOM_RANGES = {
    "sensor_id": (min_value, max_value)
}
```

## Behavior

### When Validation Rejects a Value

1. Invalid value is **not** stored in current data
2. Last known good value is used instead
3. Rejection is logged at DEBUG level
4. Statistics are updated for diagnostics

### Logging

To see validation activity:

```yaml
logger:
  logs:
    custom_components.goodwe: debug
```

Look for messages like:
- `"Rejected value for sensor X: Y"`
- `"Using last known value for X after validation rejection"`

## Diagnostics

Download diagnostics to see:

```json
{
  "validation": {
    "enabled": true,
    "outlier_sensitivity": 5.0,
    "custom_ranges_count": 0,
    "rejected_count": {
      "vpv1": 3,
      "active_power": 1
    },
    "recent_failures": [
      {
        "sensor_id": "vpv1",
        "value": 65535,
        "reason": "Modbus error value"
      }
    ]
  }
}
```

## Bug Fixes

### Fixed: `total_sensor_value()` Bug

**Problem**: The method used `if val` instead of `if val is not None`, causing legitimate zero values to be rejected.

**Impact**: Total energy sensors returning exactly 0 would incorrectly fall back to last known value.

**Fix**: Changed to explicit `is not None` check.

```python
# Before (buggy):
return val if val else self._last_data.get(sensor)

# After (fixed):
return val if (val is not None and val != "") else self._last_data.get(sensor)
```

## Testing Recommendations

1. **Monitor validation statistics** after deployment
2. **Check debug logs** for rejected values
3. **Review diagnostics** to identify problematic sensors
4. **Adjust sensitivity** if too many false positives
5. **Compare statistics** before/after to verify improvement

## Backward Compatibility

- **Default behavior**: Validation is ENABLED by default
- Existing installations will automatically get validation on next restart
- Can be disabled via configuration if needed
- No breaking changes to existing sensors or entities

## Future Enhancements

Potential improvements:
1. Per-sensor custom range configuration via UI
2. More sophisticated outlier detection algorithms
3. Learning mode to auto-adjust ranges
4. Notifications when many rejections occur
5. Historical validation statistics graphs
