# Historial de cambios

## 0.1.2 — 2026-09-22

- El alta manual ya no permite un segundo backend cuando el descubrimiento está marcado
  como **ignorado**. `single_config_entry` no cubría ese caso: Home Assistant excluye a
  propósito las entradas ignoradas cuando el flujo lo inicia una persona, así que con el
  backend ignorado el formulario salía igual y se creaban dos entradas del mismo backend.
  Ahora el flujo lo comprueba por su cuenta, y ese caso tiene mensaje propio, que dice dónde
  quitar la marca de ignorado en vez de soltar un "ya está configurado".
- El descubrimiento tampoco vuelve a ofrecerse cuando ya hay una entrada creada a mano, que
  todavía no tiene identificador de instalación con el que reconocerse.

## 0.1.1 — 2026-09-22

- El icono de marca pasa a ser el de la app: cuatro PNG con fondo transparente, con variante
  clara y oscura (`icon.png`, `icon@2x.png`, `dark_icon.png`, `dark_icon@2x.png`). Se retira
  el generador con el que se compuso el provisional.

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
