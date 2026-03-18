# Sensorwert-Validierung

Diese Datei ist die zentrale Dokumentation der aktuellen Validierungslogik im Projekt. Sie ersetzt die getrennten Beschreibungen aus `VALIDATION_FEATURE.md` und `VALIDIERUNGSABLAUF.md`.

Die Beschreibung orientiert sich am tatsächlich implementierten Verhalten in:

- `custom_components/goodwe/validators.py`
- `custom_components/goodwe/coordinator.py`
- `custom_components/goodwe/config_flow.py`
- `custom_components/goodwe/diagnostics.py`
- ergänzend für Tageswerte: `custom_components/goodwe/sensor.py`

## Ziel

Die Validierung soll verhindern, dass unrealistische oder kaputte Inverterwerte die Home-Assistant-Statistiken beschädigen.

Abgefangen werden vor allem:

- offensichtliche Modbus-/Register-Fehlerwerte
- Werte außerhalb plausibler Bereiche
- unzulässige Rückgänge bei Totalzählern
- `NaN` und `Inf`

## Beteiligte Dateien

### `custom_components/goodwe/validators.py`

Enthält die Kernlogik:

- `SensorValidator`
- Bereichsprüfung
- Modbus-Fehlerwert-Heuristik
- Monotonieprüfung
- Statistik über verworfene Werte

### `custom_components/goodwe/coordinator.py`

Bindet die Validierung in den Update-Ablauf ein:

- liest Rohdaten vom Inverter
- validiert alle Werte
- setzt bei Ablehnung den letzten gültigen Wert wieder ein
- enthält die Korrektur in `total_sensor_value()`, damit echte `0`-Werte nicht verloren gehen

### `custom_components/goodwe/const.py`

Definiert die Konfigurationswerte:

- `CONF_ENABLE_VALIDATION`
- `CONF_CUSTOM_RANGES`
- `DEFAULT_ENABLE_VALIDATION = True`

### `custom_components/goodwe/config_flow.py`

Stellt die Optionen in Home Assistant bereit:

- Validierung ein/aus

### `custom_components/goodwe/diagnostics.py`

Liefert Diagnosedaten zur Validierung:

- Anzahl verworfener Werte pro Sensor
- letzte Ablehnungen mit Grund

### `custom_components/goodwe/sensor.py`

Behandelt Tageszähler gesondert:

- `e_day`
- `e_load_day`

Diese Werte werden bei Bedarf um Mitternacht auf `0` gesetzt.

## Konfiguration

Die Validierung ist standardmäßig aktiviert.

### Optionen in Home Assistant

Pfad:

`Einstellungen -> Geräte & Dienste -> GoodWe -> Konfigurieren`

Verfügbare Optionen:

- `Enable sensor value validation`
  Standard: `True`

### Erweiterte Konfiguration

Zusätzliche benutzerdefinierte Bereiche sind intern möglich über:

```python
CONF_CUSTOM_RANGES = {
    "sensor_id": (min_value, max_value)
}
```

Diese Option ist aktuell nicht direkt im UI als eigene Feldliste umgesetzt.

## Ablauf pro Update

Bei jedem Update im Coordinator läuft der Ablauf so:

1. `read_runtime_data()` liest die Rohdaten vom Inverter.
2. `validator.validate_data(raw_data, sensor_metadata)` prüft jeden einzelnen Sensorwert.
3. Gültige Werte werden übernommen.
4. Verworfene Werte werden nicht übernommen.
5. Wenn für einen verworfenen Wert bereits ein letzter gültiger Wert existiert, wird dieser alte Wert wieder eingesetzt.
6. Wenn noch kein alter gültiger Wert existiert, fehlt der Sensor in diesem Update-Ergebnis.

Praktische Folge:

- Ein kaputter neuer Wert ist in Home Assistant oft nicht direkt sichtbar.
- Stattdessen bleibt häufig der letzte gültige Wert stehen.

## Exakte Prüfreihenfolge pro Sensorwert

Jeder Wert wird in genau dieser Reihenfolge geprüft:

### 1. `None`

- `None` wird akzeptiert.
- Die weitere Behandlung erfolgt später im Coordinator.

### 2. Nicht-numerische Werte

Direkt akzeptiert werden insbesondere:

- Strings
- Enum-Werte
- Textwerte
- Bool-Werte

Für diese Werte gibt es keine Bereichs- oder Monotonieprüfung.

### 3. Modbus-Fehlerwert-Heuristik

Folgende Werte werden als kaputte Registerwerte behandelt:

- `0xFFFF`
- `0x7FFF`
- `0x8000`
- `-32768`
- `65535`

Zusätzlich werden Werte verworfen, wenn sie weniger als `0.01` von einem dieser Werte abweichen.

Wichtig:

- Das ist keine allgemeingültige Modbus-TCP-Regel.
- Es ist eine Heuristik für typische Sentinel- oder Fehlerwerte in 16-Bit-Registern.
- Echte Modbus-Protokollfehler werden normalerweise per Exception Response oder Timeout signalisiert, nicht durch diese Zahlen im Register.

### 4. Finite-Prüfung

Verworfen werden:

- `NaN`
- `Inf`
- `-Inf`

### 5. Einheit bestimmen

Die Einheit wird zuerst aus den Sensor-Metadaten genommen.

Falls keine Metadaten verfügbar sind, wird sie aus der Sensor-ID abgeleitet:

- `voltage` oder ID beginnt mit `v` -> `V`
- `current` oder ID beginnt mit `i` -> `A`
- `power`, `consumption`, ID beginnt mit `p` oder enthält `_p` -> `W`
- `energy` oder ID beginnt mit `e_` -> `kWh`
- `temp` oder `temperature` -> `C`
- `freq` oder ID beginnt mit `fgrid` -> `Hz`
- `soc` oder `%` -> `%`

Sonderfall:

- Sensor-IDs mit `function` oder `_bit` bekommen absichtlich keine Einheit zugeordnet.

### 6. Bereichsprüfung

Die Bereichsprüfung selbst hat wieder eine feste Priorität.

#### 6.1 Negative `kWh`

Wenn die Einheit `kWh` ist:

- jeder Wert `< 0` wird immer verworfen

Diese Regel greift vor allen anderen Bereichsregeln.

#### 6.2 Benutzerdefinierter Bereich

Wenn für die Sensor-ID ein Eintrag in `custom_ranges` existiert:

- nur dieser Bereich zählt
- bei Treffer ist die Bereichsprüfung bestanden
- bei Verstoß wird verworfen

Danach werden keine sensor-spezifischen oder allgemeinen Standardbereiche mehr geprüft.

#### 6.3 Sensor-spezifische Bereiche

Wenn kein `custom_range` greift, gelten für diese Sensoren feste Spezialbereiche:

| Sensor-ID | Bereich |
| --- | --- |
| `vgrid` | `180..280` |
| `vgrid2` | `180..280` |
| `vgrid3` | `180..280` |
| `vbattery1` | `40..600` |
| `vpv1` | `0..1000` |
| `vpv2` | `0..1000` |
| `vpv3` | `0..1000` |
| `vpv4` | `0..1000` |
| `fgrid` | `49..61` |
| `fgrid2` | `49..61` |
| `fgrid3` | `49..61` |
| `battery_soc` | `0..100` |
| `e_day` | `0..200` |
| `e_load_day` | `0..500` |

Wenn ein Sensor in dieser Liste ist:

- innerhalb des Bereichs -> gültig
- außerhalb des Bereichs -> verworfen

Danach wird kein Standardbereich nach Einheit mehr geprüft.

#### 6.4 Standardbereiche nach Einheit

Nur wenn weder `custom_range` noch sensor-spezifischer Bereich gegriffen hat, gelten diese Standardbereiche:

| Einheit | Bereich |
| --- | --- |
| `V` | `0..1000` |
| `A` | `-150..150` |
| `W` | `-50000..50000` |
| `kWh` | `0..100000` |
| `VA` | `-50000..50000` |
| `var` | `-50000..50000` |
| `C` | `-40..100` |
| `Hz` | `45..65` |
| `%` | `0..100` |
| `h` | `0..1000000` |

Wenn keine Einheit bekannt ist, gibt es keine Standard-Bereichsprüfung.

### 7. Monotonieprüfung

Diese Prüfung gilt nur für:

- `e_total`
- `e_bat_charge_total`
- `e_bat_discharge_total`
- `meter_e_total_exp`
- `meter_e_total_imp`
- `h_total`

Regel:

- Diese Sensoren sollen nur steigen.

Erlaubte Toleranz:

- `max(1 % des letzten Werts, 0.1)`

Das bedeutet:

- ein kleiner Rückgang innerhalb der Toleranz wird akzeptiert
- ein größerer Rückgang wird genauer geprüft

Sonderfälle, die trotz Rückgang akzeptiert werden:

1. Neuer Wert `< 1.0`
   Dann wird ein möglicher Inverter-Reset angenommen und der neue Wert als neue Basis akzeptiert.

2. Neuer Wert ist kleiner als `50 %` des letzten Werts
   Auch dann wird ein möglicher Reset angenommen und der neue Wert wird akzeptiert.

Nur wenn beides nicht zutrifft, wird der Rückgang verworfen.

Wichtig:

- Diese Reset-Logik akzeptiert bewusst auch große Sprünge nach unten.
- Das ist aktuell so implementiert.

### 8. Aktive Prüfungen

Die Validierung arbeitet jetzt nur noch mit:

- Modbus-Fehlerwert-Heuristik
- Finite-Prüfung
- Bereichsprüfung
- Monotonieprüfung für definierte Totalzähler

## Welche Regeln für welche Werte gelten

| Wertetyp / Sensor | Angewendete Regeln |
| --- | --- |
| `None` | direkt akzeptiert |
| String / Enum / Text / Bool | direkt akzeptiert |
| jeder numerische Wert | Modbus-Heuristik, Finite-Prüfung, Bereichsprüfung, evtl. Monotonie |
| jeder `kWh`-Wert | zusätzliche Regel: niemals negativ |
| Sensor mit `custom_range` | `custom_range` überschreibt andere Bereichsregeln |
| `vgrid`, `vgrid2`, `vgrid3` | sensor-spezifischer Bereich `180..280` |
| `vbattery1` | sensor-spezifischer Bereich `40..600` |
| `vpv1` bis `vpv4` | sensor-spezifischer Bereich `0..1000` |
| `fgrid`, `fgrid2`, `fgrid3` | sensor-spezifischer Bereich `49..61` |
| `battery_soc` | sensor-spezifischer Bereich `0..100` |
| `e_day` | sensor-spezifischer Bereich `0..200` |
| `e_load_day` | sensor-spezifischer Bereich `0..500` |
| `e_total`, `e_bat_charge_total`, `e_bat_discharge_total`, `meter_e_total_exp`, `meter_e_total_imp`, `h_total` | zusätzliche Monotonieprüfung |
| typische Leistungswerte wie `ppv`, `active_power`, `house_consumption` | Standardbereich `W` |
| Sensoren ohne bekannte Einheit | keine einheitsbasierte Standard-Bereichsprüfung |
| Sensor-IDs mit `function` oder `_bit` | keine Einheitszuordnung, damit keine einheitsbasierte Standard-Bereichsprüfung |

## Verhalten bei Ablehnung

Wenn ein Wert verworfen wird:

1. Er wird nicht in `validated_data` übernommen.
2. Die Ablehnung wird in den Validierungsstatistiken gespeichert.
3. Es wird eine Warnung geloggt.
4. Falls ein letzter gültiger Wert vorhanden ist, setzt der Coordinator genau diesen alten Wert wieder ein.
5. Falls kein letzter gültiger Wert vorhanden ist, fehlt der Sensorwert im aktuellen Ergebnis.

### Logging

Für die Analyse ist diese Logger-Konfiguration sinnvoll:

```yaml
logger:
  logs:
    custom_components.goodwe: debug
```

Typische Meldungen:

- Warnung bei verworfenem Wert
- Debug-Meldung beim Rückgriff auf den letzten gültigen Wert

## Diagnosedaten

Die Diagnosedaten enthalten einen Abschnitt `validation`, zum Beispiel:

```json
{
  "validation": {
    "enabled": true,
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

Damit lässt sich nachvollziehen:

- welcher Sensor wie oft verworfen wurde
- welcher konkrete Wert verworfen wurde
- aus welchem Grund die Ablehnung passiert ist

## Sonderfälle

### Korrektur in `total_sensor_value()`

Früher war die Logik sinngemäß:

```python
return val if val else self._last_data.get(sensor)
```

Das war problematisch, weil echte `0`-Werte wie ein fehlender Wert behandelt wurden.

Aktuell ist die Logik:

```python
return val if (val is not None and val != "") else self._last_data.get(sensor)
```

Damit bleibt ein echter `0`-Wert erhalten.

### Tageszähler um Mitternacht

Für diese Sensoren gibt es eine Sonderbehandlung:

- `e_day`
- `e_load_day`

Wenn der Inverter nachts offline ist, werden sie um Mitternacht auf `0` gesetzt.

Dabei wird zusätzlich:

- die Verlaufshistorie im Validator gelöscht
- der zuletzt bekannte Wert auf `0` gesetzt

Das verhindert, dass Tageswerte nachts stehen bleiben und erst morgens beim Aufwachen des Inverters zurückgesetzt werden.

## Empfehlungen für den Betrieb

- Nach Änderungen zunächst die Diagnosedaten beobachten.
- Bei unerwarteten Ablehnungen die Logs prüfen.
- Bei einzelnen problematischen Sensoren sind gezielte `custom_ranges` sinnvoller als globale Lockerungen.

## Kurzfassung

- Die Validierung ist standardmäßig aktiv.
- Nicht-numerische Werte werden praktisch nicht validiert.
- Numerische Werte laufen durch Modbus-Heuristik, Finite-Prüfung, Bereichsprüfung und optional Monotonieprüfung.
- Verworfene Werte werden in der Regel durch den letzten gültigen Wert ersetzt.
- Tageswerte haben bewusst Sonderregeln.
