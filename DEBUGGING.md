# Debugging-Anleitung für unrealistische Werte

## Problem: Negative oder extrem hohe kWh-Werte

Das Problem, das Sie sehen (-5.082.368 kWh), wird jetzt durch folgende Verbesserungen behoben:

### 1. **Verbesserte Validierung**

Die Validierung wurde verschärft:
- ✅ Negative kWh-Werte werden **sofort abgelehnt** und geloggt
- ✅ Ablehnungen werden als **WARNING** im Log angezeigt (nicht nur DEBUG)
- ✅ Detaillierte Ablehnungsgründe werden gespeichert

### 2. **Debug-Script verwenden**

Führen Sie das neue Debug-Script aus, um zu sehen, welche Werte vom Inverter kommen:

```bash
cd /home/sde/home-assistant-goodwe-inverter

# 1. Passen Sie die IP-Adresse an
nano debug_values.py  # Ändern Sie IP_ADDRESS und ggf. PROTOCOL

# 2. Script ausführen
python3 debug_values.py
```

**Das Script zeigt:**
- 🔴 Kritische Probleme (z.B. negative kWh-Werte)
- ⚠️ Verdächtige Werte (außerhalb normaler Bereiche)
- ✓ Normale Werte

### 3. **Home Assistant Logs prüfen**

Nach einem Neustart von Home Assistant:

```bash
# Live-Ansicht der Logs (Strg+C zum Beenden)
tail -f /config/home-assistant.log | grep -i goodwe

# Nur Warnings und Errors
tail -f /config/home-assistant.log | grep -E "WARNING|ERROR" | grep -i goodwe

# Nach bestimmten Sensoren suchen
grep "e_bat_charge_total" /config/home-assistant.log
```

**Sie sollten jetzt sehen:**
```
WARNING custom_components.goodwe.validators: Rejected negative kWh value for e_bat_charge_total: -5082368.0
WARNING custom_components.goodwe.validators: Rejected unrealistic value for sensor e_bat_charge_total: -5082368.0 (reason: Negative energy value)
```

### 4. **Diagnostics herunterladen**

1. **Einstellungen** → **Geräte & Dienste** → **GoodWe**
2. Klicken Sie auf Ihr Inverter-Gerät
3. Klicken Sie **"Diagnose herunterladen"**

Im JSON suchen nach:
```json
"validation": {
  "enabled": true,
  "rejected_count": {
    "e_bat_charge_total": 123,  ← Wie oft wurde dieser Sensor abgelehnt?
    ...
  },
  "recent_failures": [
    {
      "sensor_id": "e_bat_charge_total",
      "value": -5082368.0,
      "reason": "Negative energy value (kWh cannot be negative)"
    }
  ]
}
```

### 5. **Mögliche Ursachen identifizieren**

#### a) **Werte kommen falsch vom Inverter** (Modbus-Problem)
Wenn `debug_values.py` die falschen Werte zeigt:
- → Modbus-Kommunikationsproblem
- → Firmware-Bug im Inverter
- → Byte-Order-Problem bei TCP

**Lösung:** Die Validierung fängt diese ab

#### b) **Werte werden in Home Assistant falsch berechnet**
Wenn `debug_values.py` korrekte Werte zeigt:
- → Problem in einer Template-Sensor-Konfiguration
- → Riemann Sum oder Utility Meter falsch konfiguriert
- → Andere Integration greift ein

**Lösung:** Prüfen Sie `configuration.yaml` nach Custom-Sensoren

### 6. **Sofortmaßnahmen**

1. **Home Assistant neu starten** (damit die verbesserte Validierung aktiv wird)

2. **Debug-Logging aktivieren** in `configuration.yaml`:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.goodwe: debug
       goodwe: debug
   ```

3. **Statistiken zurücksetzen** (wenn bereits beschädigt):
   ```bash
   # In Home Assistant Developer Tools → Services:
   Service: recorder.purge
   Data:
     keep_days: 7
     repack: true
   ```

   Oder für einzelne Sensoren:
   - Gehen Sie zu **Developer Tools** → **Statistics**
   - Suchen Sie nach dem problematischen Sensor
   - Klicken Sie auf **"FIX ISSUE"** oder löschen Sie fehlerhafte Einträge

### 7. **Validierung prüfen**

In Home Assistant:
1. **Einstellungen** → **Geräte & Dienste** → **GoodWe** → **KONFIGURIEREN**
2. Prüfen Sie:
   - ✅ **Enable sensor value validation**: ON

### 8. **Test-Szenario**

Nach dem Neustart sollten Sie sehen:

**VORHER (Log):**
```
2026-02-06 08:00:00 DEBUG: Sensor e_bat_charge_total = -5082368.0
```

**NACHHER (Log):**
```
2026-02-06 08:00:00 WARNING: Rejected negative kWh value for e_bat_charge_total: -5082368.0
2026-02-06 08:00:00 DEBUG: Using last known value for e_bat_charge_total after validation rejection
```

**Im Graph:**
- Statt -5 Millionen kWh → Letzter gültiger Wert wird beibehalten
- Keine Spikes mehr in den Statistiken

## Erwartete Verbesserungen

Nach der Implementierung:
- ✅ Negative kWh-Werte werden abgelehnt
- ✅ Warnings im Log für alle ungültigen Werte
- ✅ Statistiken bleiben sauber
- ✅ Letzter gültiger Wert wird verwendet, bis wieder gültige Daten kommen

## Wenn das Problem weiterhin besteht

1. **Führen Sie `debug_values.py` aus** und teilen Sie die Ausgabe
2. **Zeigen Sie die Diagnostik-Daten** (`validation` Sektion)
3. **Posten Sie relevante Log-Zeilen** (mit Timestamps)

Dann können wir gezielt die Ursache identifizieren.
