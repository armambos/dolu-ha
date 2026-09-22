"""La integración DoLu para Home Assistant.

Versión mínima: descubrir el backend de DoLu en la red y emparejarse con él. Los paneles
siguen llegando a Home Assistant por MQTT exactamente igual que antes; esta integración no
los toca ni los sustituye.

No crea entidades ni dispositivos a propósito. El backend ya publica por MQTT el dispositivo
"DoLu backend" con su sensor de enlace, y un segundo dispositivo con ese mismo nombre, creado
por otro camino, solo serviría para que nadie supiera cuál mirar.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

type DoluConfigEntry = ConfigEntry[None]


async def async_setup_entry(hass: HomeAssistant, entry: DoluConfigEntry) -> bool:
    """Cargar la entrada.

    Hoy no hay nada que arrancar: la entrada solo guarda dónde está el backend y con qué
    huella se le reconoce. La conexión de verdad —el emparejamiento y el token— llega en las
    rondas siguientes.
    """
    _LOGGER.debug(
        "Backend DoLu configurado en %s:%s (instalación %s)",
        entry.data.get("host"),
        entry.data.get("port"),
        entry.data.get("installation_id"),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DoluConfigEntry) -> bool:
    """Descargar la entrada. Sin nada arrancado, no hay nada que parar."""
    return True
