"""Alta del backend de DoLu: descubrimiento por mDNS y alta manual.

RONDA 0. Lo que hay aquí es el esqueleto: el backend aparece descubierto, se confirma y
queda una entrada de configuración. El emparejamiento —el código de un solo uso y las dos
pruebas contra /ha/pair/hello y /ha/pair/complete— es la ronda 3.

Hay una advertencia que conviene no perder de vista mientras tanto: la huella que se enseña
en la confirmación sale HOY del registro TXT del anuncio mDNS, y un registro TXT lo escribe
cualquiera en la red. Por eso el texto de esa pantalla dice "anunciada" y no promete nada.
En la ronda 3, la huella que se enseñe será la del certificado que sirvió de verdad la
conexión TLS, comprobada con aiohttp.Fingerprint antes de pintar nada; ahí el cotejo pasa a
significar algo.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_BACKEND_VERSION,
    CONF_FINGERPRINT,
    CONF_INSTALLATION_ID,
    DOMAIN,
    SHORT_FINGERPRINT_GROUPS,
    TXT_FINGERPRINT,
    TXT_INSTALLATION_ID,
    TXT_VERSION,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 3000


def short_fingerprint(fingerprint: str) -> str:
    """Los diez primeros pares, como los muestra el panel del backend."""
    return ":".join(fingerprint.split(":")[:SHORT_FINGERPRINT_GROUPS])


class DoluConfigFlow(ConfigFlow, domain=DOMAIN):
    """Alta de un backend DoLu."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._fingerprint: str = ""
        self._installation_id: str = ""
        self._backend_version: str = ""

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """El backend se anunció en la red."""
        properties = discovery_info.properties
        installation_id = properties.get(TXT_INSTALLATION_ID)
        fingerprint = properties.get(TXT_FINGERPRINT)

        if not installation_id or not fingerprint:
            # Un anuncio sin identidad no sirve para nada: ni se puede evitar duplicarlo ni
            # se puede cotejar contra nada.
            return self.async_abort(reason="incomplete_discovery")

        # La identidad es la instalación, no la IP: así un backend que cambia de dirección
        # sigue siendo el mismo, y se actualiza en vez de aparecer como uno nuevo.
        await self.async_set_unique_id(installation_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port}
        )

        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._fingerprint = fingerprint
        self._installation_id = installation_id
        self._backend_version = properties.get(TXT_VERSION, "")

        name = properties.get("name") or "DoLu"
        self.context["title_placeholders"] = {"name": name, "host": self._host}

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirmar el backend descubierto."""
        if user_input is not None:
            return self._create_entry()

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": f"{self._host}:{self._port}",
                "fingerprint": short_fingerprint(self._fingerprint),
                "version": self._backend_version or "?",
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Alta a mano, para cuando el descubrimiento no llega.

        Hace falta desde el primer día y no solo como respaldo: el mDNS no cruza routers, así
        que en cuanto Home Assistant y el backend queden en VLAN o subredes distintas este es
        el único camino.

        RONDA 0: se apunta lo que teclee la persona y nada más. En la ronda 3, este paso
        hablará con el backend para traerse su identidad y su huella, igual que hace el
        descubrimiento.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            self._async_abort_entries_match({CONF_HOST: self._host, CONF_PORT: self._port})
            return self._create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    def _create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title="DoLu",
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_FINGERPRINT: self._fingerprint,
                CONF_INSTALLATION_ID: self._installation_id,
                CONF_BACKEND_VERSION: self._backend_version,
            },
        )
