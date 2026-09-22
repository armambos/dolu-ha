"""Alta del backend de DoLu: descubrimiento por mDNS, alta manual y emparejamiento.

RONDA 3. El flujo ya no se limita a apuntar dónde está el backend: se empareja con él.

El orden de las pantallas no es de comodidad, es el del protocolo (sección 2.2 del análisis):

1. **Confirmar** — se ha encontrado un backend. Aquí todavía no se afirma nada sobre él:
   todo lo que sabemos viene del anuncio, y un anuncio lo escribe cualquiera en la red.
2. **Código** — la persona teclea el código de un solo uso que generó en el panel de DoLu.
   Con él, el backend demuestra que lo conoce (`proof_be`) sobre una conexión cuyo
   certificado ya está fijado. Si eso no cuadra, el flujo no envía nada.
3. **Cotejar la huella** — ya con la respuesta verificada, se muestran el nombre real de la
   instalación y la huella del certificado **que sirvió la conexión**, para compararla con la
   del panel. Es la última barrera, la que queda si el impostor llegara a conocer el código.

Solo después de eso Home Assistant envía su propia prueba y el contenido del emparejamiento.

En esta ronda ese contenido es un payload de prueba, no un token: el usuario administrador y
el token de larga duración son la ronda 4. Lo que se está probando aquí es el canal.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BACKEND_NAME,
    CONF_BACKEND_VERSION,
    CONF_FINGERPRINT,
    CONF_INSTALLATION_ID,
    CONF_PAIRED_AT,
    DOMAIN,
    SHORT_FINGERPRINT_GROUPS,
    TXT_FINGERPRINT,
    TXT_INSTALLATION_ID,
    TXT_VERSION,
    ZEROCONF_TYPE,
)
from .pairing import (
    BackendHello,
    PairingClient,
    PairingError,
    normalize_fingerprint,
    read_served_fingerprint,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 3000

CONF_CODE = "code"

# Fallos que dejan seguir intentando en la misma pantalla: o son cosa del código (se genera
# otro y se reintenta) o son transitorios. Los que no están aquí abortan el flujo, y hay uno
# que importa mucho que aborte: `fingerprint_mismatch` significa que hay alguien suplantando
# al backend en la red, y ahí no se reintenta, se para.
RETRYABLE_ERRORS = {
    "invalid_code",
    "code_expired",
    "code_used",
    "code_locked",
    "no_active_code",
    "cannot_connect",
    "too_many_requests",
    "unexpected_response",
}


def announced_name(service_name: str) -> str:
    """El nombre con el que el backend se anuncia, para la tarjeta de descubrimiento.

    Sale del nombre de instancia del servicio mDNS ("DoLu._dolu._tcp.local."), que es lo que
    el backend pone en MDNS_SERVICE_NAME. Avahi escapa los espacios como "\\032".

    Es solo una etiqueta: el nombre en el que se puede confiar llega por la conexión ya
    fijada por huella al emparejar, no por un anuncio que escribe cualquiera.
    """
    etiqueta = service_name.split(f".{ZEROCONF_TYPE}")[0].replace("\\032", " ").strip()
    return etiqueta or "DoLu"


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
        self._announced_name: str = "DoLu"
        self._client: PairingClient | None = None
        self._hello: BackendHello | None = None

    # ------------------------------------------------------------------
    # Cómo se llega hasta aquí: descubrimiento o alta manual
    # ------------------------------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """El backend se anunció en la red."""
        properties = discovery_info.properties
        installation_id = properties.get(TXT_INSTALLATION_ID)
        fingerprint = properties.get(TXT_FINGERPRINT)

        # Un descubrimiento que se descarta no deja ninguna huella en la interfaz: la tarjeta
        # simplemente no aparece, y desde fuera no hay forma de distinguirlo de que el
        # anuncio no llegara. Por eso se anota aquí, antes de decidir nada.
        _LOGGER.debug(
            "Descubierto %s en %s:%s — instalación %s, huella %s",
            discovery_info.name,
            discovery_info.host,
            discovery_info.port,
            installation_id,
            fingerprint,
        )

        if not installation_id or not fingerprint:
            # Un anuncio sin identidad no sirve para nada: ni se puede evitar duplicarlo ni
            # se puede fijar el certificado antes de hablar.
            return self.async_abort(reason="incomplete_discovery")

        # La identidad es la instalación, no la IP: así un backend que cambia de dirección
        # sigue siendo el mismo, y se actualiza en vez de aparecer como uno nuevo.
        await self.async_set_unique_id(installation_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port}
        )

        # Y si ya hay un backend configurado, este anuncio no se enseña.
        #
        # La comprobación de arriba no basta: solo reconoce al backend que YA está dado de
        # alta, comparando identificadores de instalación, y una entrada creada por el alta
        # manual puede no tener el mismo. Sin esta segunda comprobación, después de un alta
        # manual el backend seguía apareciendo en "Descubierto" como si no estuviera
        # configurado.
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="single_instance_allowed")

        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        self._fingerprint = normalize_fingerprint(fingerprint)
        self._installation_id = installation_id
        self._backend_version = properties.get(TXT_VERSION, "")
        self._announced_name = announced_name(discovery_info.name)

        self.context["title_placeholders"] = {
            "name": self._announced_name,
            "host": self._host,
        }

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Se encontró un backend. Antes de emparejar, hay que ir a por el código."""
        if user_input is not None:
            return await self.async_step_code()

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._announced_name,
                "host": f"{self._host}:{self._port}",
                "version": self._backend_version or "?",
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Alta a mano, para cuando el descubrimiento no llega.

        Hace falta desde el primer día y no solo como respaldo: el mDNS no cruza routers, así
        que en cuanto Home Assistant y el backend queden en VLAN o subredes distintas este es
        el único camino.

        La diferencia con el descubrimiento está en la huella. Allí viene anunciada y se
        *fija* antes de hablar; aquí no hay anuncio, así que se lee la que el backend sirve
        (TOFU) y todo el peso recae sobre las otras dos barreras: el código y el cotejo a ojo
        de la pantalla siguiente.
        """
        # Hay un backend por casa, y esta comprobación es la que de verdad hace falta.
        #
        # single_config_entry en el manifest NO cubre este camino: Home Assistant excluye a
        # propósito las entradas ignoradas cuando el flujo lo inicia una persona
        # (config_entries.py, async_init: `async_has_entries(handler, include_ignore=False)`
        # y `... and source != SOURCE_USER`). Con el backend marcado como ignorado, el alta
        # manual pasaba de largo y se creaban dos entradas del mismo backend. Comprobado
        # contra una instancia real: sin esto salía el formulario; con esto, no.
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="single_instance_allowed")

        # Solo queda el caso de la entrada ignorada, y merece su propio mensaje: decirle a
        # alguien que "ya hay un backend configurado" cuando lo que hay es un descubrimiento
        # que marcó como ignorado no le dice dónde mirar.
        if self._async_current_entries(include_ignore=True):
            return self.async_abort(reason="ignored_entry")

        errors: dict[str, str] = {}

        if user_input is not None:
            self._host = user_input[CONF_HOST]
            self._port = user_input[CONF_PORT]
            self._async_abort_entries_match({CONF_HOST: self._host, CONF_PORT: self._port})

            try:
                self._fingerprint = await read_served_fingerprint(
                    self.hass, self._host, self._port
                )
            except PairingError as err:
                errors["base"] = err.reason
            else:
                return await self.async_step_code()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._host or vol.UNDEFINED): str,
                    vol.Required(CONF_PORT, default=self._port): int,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # El emparejamiento
    # ------------------------------------------------------------------

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """El código de un solo uso, y con él la prueba del backend.

        Nada se envía al backend más allá de un nonce hasta que su prueba cuadra: si el
        código no es el bueno, o del otro lado no está quien dice ser, el flujo se entera
        aquí y no ha entregado nada.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            client = PairingClient(
                self.hass,
                async_get_clientsession(self.hass),
                self._host,
                self._port,
                self._fingerprint,
            )
            try:
                hello = await client.hello(user_input[CONF_CODE])
            except PairingError as err:
                if err.reason not in RETRYABLE_ERRORS:
                    return self.async_abort(reason=err.reason)
                errors["base"] = err.reason
            else:
                self._client = client
                self._hello = hello

                # Recién ahora se sabe con qué instalación se está hablando de verdad: en el
                # alta manual esto es lo primero que la identifica, y en el descubrimiento
                # confirma que el anuncio no mentía sobre el identificador.
                if hello.installation_id:
                    if self._installation_id and hello.installation_id != self._installation_id:
                        _LOGGER.warning(
                            "El backend se identificó como %s pero el anuncio decía %s",
                            hello.installation_id,
                            self._installation_id,
                        )
                        return self.async_abort(reason="identity_mismatch")
                    self._installation_id = hello.installation_id
                    await self.async_set_unique_id(hello.installation_id)
                    self._abort_if_unique_id_configured()

                return await self.async_step_verify()

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders={
                "name": self._announced_name,
                "host": f"{self._host}:{self._port}",
            },
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Cotejar la huella del certificado que sirvió la conexión, y entregar.

        Esta pantalla es la barrera 2, y es la única que no puede automatizarse: si alguien
        llegara a conocer el código, todo lo demás le saldría bien y solo el cotejo a ojo lo
        distinguiría del backend real. Por eso los diez pares que se muestran son exactamente
        los mismos que muestra el panel de DoLu, carácter por carácter.
        """
        if self._client is None or self._hello is None:
            return self.async_abort(reason="session_lost")

        if user_input is not None:
            try:
                await self._client.complete(self._test_payload())
            except PairingError as err:
                # Aquí ya no se reintenta: la conversación se consumió y el código es de un
                # solo uso. Se empieza de nuevo, con un código nuevo.
                return self.async_abort(reason=err.reason)

            return self.async_create_entry(
                title=self._hello.name or "DoLu",
                data={
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    CONF_FINGERPRINT: self._client.fingerprint,
                    CONF_INSTALLATION_ID: self._installation_id,
                    CONF_BACKEND_VERSION: self._hello.version,
                    CONF_BACKEND_NAME: self._hello.name,
                    CONF_PAIRED_AT: dt_util.utcnow().isoformat(),
                },
            )

        return self.async_show_form(
            step_id="verify",
            description_placeholders={
                "name": self._hello.name,
                "host": f"{self._host}:{self._port}",
                "version": self._hello.version or "?",
                "fingerprint": short_fingerprint(self._client.fingerprint),
            },
        )

    def _test_payload(self) -> dict[str, Any]:
        """Lo que viaja en `complete` en esta ronda: una prueba, no un token.

        Lleva la URL de Home Assistant porque en la ronda 4 irá ahí de verdad, y conviene que
        el camino ya esté ejercitado —incluido el caso de que no haya ninguna URL utilizable,
        que es un fallo real y no una rareza.
        """
        try:
            ha_url = get_url(self.hass, allow_internal=True, allow_ip=True)
        except NoURLAvailableError:
            ha_url = None

        return {
            "round": 3,
            "test": True,
            "ha_url": ha_url,
            "note": (
                "Payload de prueba de la ronda 3. En la ronda 4, aquí viajan la URL de Home "
                "Assistant, el token de larga duración y el id del usuario creado."
            ),
        }
