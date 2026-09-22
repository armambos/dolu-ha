# Historial de cambios

## 0.1.0 — 2026-09-22

Ronda 0: el esqueleto de la integración.

- Descubrimiento del backend por mDNS (`_dolu._tcp`), con la identidad de la instalación
  como `unique_id`: un backend que cambia de IP sigue siendo el mismo y se actualiza en vez
  de aparecer como uno nuevo.
- Alta manual por host y puerto, para cuando Home Assistant y el backend no comparten red.
- Una sola entrada de configuración (`single_config_entry`): hay un backend por casa.
- Sin entidades ni dispositivos: el dispositivo "DoLu backend" sigue siendo el que publica
  el backend por MQTT.
- Español e inglés.
- Icono de marca propio en `brand/`, generado con los trazos del logotipo
  (`scripts/make_brand_icons.py`).
- Validación en CI con hassfest y la acción de HACS.
