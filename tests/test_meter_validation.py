"""Run with python3 tests/test_meter_validation.py; no HA or inverter required."""

import ast
import asyncio
import json
import logging
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SensorValidator = runpy.run_path(
    str(ROOT / "custom_components/goodwe/validators.py")
)["SensorValidator"]
METERS = ("meter_e_total_imp", "meter_e_total_exp")
METADATA = {sensor: {"unit": "kWh"} for sensor in METERS}


async def check_coordinator(clock):
    """Exercise the real coordinator methods with fake HA storage and inverter I/O."""
    source = ast.parse((ROOT / "custom_components/goodwe/coordinator.py").read_text())
    cls = next(node for node in source.body
               if isinstance(node, ast.ClassDef) and node.name == "GoodweUpdateCoordinator")
    # Load the actual method bodies without importing HA or its generic base class.
    cls.bases = []
    module = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    ), cls], type_ignores=[])
    namespace = {"callback": lambda function: function,
                 "_LOGGER": logging.getLogger("test_coordinator"),
                 "InverterError": type("InverterError", (Exception,), {}),
                 "RequestFailedException": type("RequestFailedException", (Exception,), {})}
    exec(compile(ast.fix_missing_locations(module), "coordinator.py", "exec"), namespace)
    coordinator = object.__new__(namespace["GoodweUpdateCoordinator"])
    coordinator.validator = SensorValidator()
    coordinator.data = {}
    coordinator._last_data = {}
    coordinator._sensor_metadata = METADATA
    coordinator._meter_save_pending = False
    coordinator._update_polled_entities = AsyncMock()
    coordinator.inverter = SimpleNamespace(read_runtime_data=AsyncMock())
    saved = {sensor: {"value": value, "timestamp": clock.return_value}
             for sensor, value in zip(METERS, (10486.15, 11175.95))}
    coordinator._meter_store = SimpleNamespace(
        async_load=AsyncMock(return_value=json.loads(json.dumps(saved))),
        async_save=AsyncMock(), async_delay_save=Mock()
    )
    await coordinator._async_setup()

    # Bad first reads after restore return trusted readings, not zero/None.
    coordinator.inverter.read_runtime_data.return_value = {
        METERS[0]: 0, METERS[1]: 32441.86
    }
    expected = {sensor: reading["value"] for sensor, reading in saved.items()}
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data == expected
    clock.return_value += 10
    coordinator.inverter.read_runtime_data.return_value = {METERS[0]: None}
    assert await coordinator._async_update_data() == expected
    assert coordinator._meter_store.async_delay_save.call_count == 1

    # A pending write gets the latest accepted values and is not postponed per poll.
    clock.return_value += 10
    expected[METERS[0]] += 0.01
    coordinator.inverter.read_runtime_data.return_value = expected
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data == expected
    callback, delay = coordinator._meter_store.async_delay_save.call_args.args
    assert delay == 60
    persisted = callback()
    assert persisted[METERS[0]]["value"] == expected[METERS[0]]
    assert not coordinator._meter_save_pending
    await coordinator.async_save_meter_counters()
    coordinator._meter_store.async_save.assert_awaited_once_with(persisted)


def check():
    """Replay corrupt reads and verify time bounds, initialization and restore."""
    logging.disable(logging.WARNING)
    with patch("time.time", return_value=1000.0) as clock:
        for sensor, initial, spike in (
            (METERS[0], 10486.15, 10487.15),
            (METERS[1], 11175.95, 32441.86),
        ):
            validator = SensorValidator()

            def read(value):
                return validator.validate_data({sensor: value}, METADATA)

            # Do not publish an unconfirmed first reading.
            assert read(initial) == {}
            clock.return_value += 10
            assert read(initial) == {sensor: initial}

            for value in (0.0, -0.0, initial - 0.01, initial / 2, spike,
                          None, "0", True, float("nan"), float("inf"), -1):
                baseline = dict(validator.meter_counters[sensor])
                clock.return_value += 10
                assert read(value) == {}, value
                assert validator.meter_counters[sensor] == baseline, value
                clock.return_value += 10
                assert read(initial) == {sensor: initial}

            # Quantized ordinary growth remains valid and replaces the baseline.
            clock.return_value += 10
            assert read(initial + 0.01) == {sensor: initial + 0.01}
            assert read(initial + 0.2) == {}

            # Catch up after a real outage; rejected spikes must not poison this.
            clock.return_value += 3600
            assert read(initial + 20) == {sensor: initial + 20}

            # Persisted state retains protection on the very first read after restart.
            restored = SensorValidator()
            restored.meter_counters = json.loads(json.dumps(validator.meter_counters))
            clock.return_value += 10
            assert restored.validate_data({sensor: 0}, METADATA) == {}
            assert restored.validate_data({sensor: spike + 100}, METADATA) == {}
            assert restored.validate_data({sensor: initial + 20.01}, METADATA)

            # A backwards wall clock cannot create a negative growth allowance.
            clock.return_value -= 100
            assert restored.validate_data({sensor: initial + 20.01}, METADATA)

        # A bad initial high/zero reading is replaced before it becomes trusted.
        validator = SensorValidator()
        for value, expected in ((32441.86, {}), (11175.95, {}),
                                (11175.96, {METERS[1]: 11175.96})):
            clock.return_value += 10
            assert validator.validate_data({METERS[1]: value}, METADATA) == expected
        assert validator.validate_data({METERS[0]: 0}, METADATA) == {}
        clock.return_value += 10
        assert validator.validate_data({METERS[0]: 0}, METADATA) == {METERS[0]: 0}
        clock.return_value += 10
        assert validator.validate_data({METERS[0]: 0.01}, METADATA) == {METERS[0]: 0.01}

        # Independent meters, configurable power, and a disabled validator.
        validator = SensorValidator(max_meter_power_kw=5)
        values = {METERS[0]: 100, METERS[1]: 200}
        assert validator.validate_data(values, METADATA) == {}
        clock.return_value += 10
        assert validator.validate_data(values, METADATA) == values
        clock.return_value += 10
        assert validator.validate_data({METERS[0]: 100.1}, METADATA) == {}
        clock.return_value += 3600
        assert validator.validate_data({METERS[0]: 105}, METADATA) == {METERS[0]: 105}
        assert validator.meter_counters[METERS[1]]["value"] == 200
        assert SensorValidator(enable_validation=False).validate_data(values) == values

        for power in (0, -1, float("nan"), float("inf")):
            try:
                SensorValidator(max_meter_power_kw=power)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Invalid power accepted: {power}")

        # Daily production counters can still reset; unrelated sensors still work.
        validator = SensorValidator()
        assert validator.validate_data({"e_day": 10}) == {"e_day": 10}
        assert validator.validate_data({"e_day": 0}) == {"e_day": 0}
        assert validator.validate_data({"active_power": -422}) == {"active_power": -422}
        validator.reset_sensor_tracking(METERS[0])

        asyncio.run(check_coordinator(clock))

    print("Meter validation regression checks passed")


if __name__ == "__main__":
    check()
