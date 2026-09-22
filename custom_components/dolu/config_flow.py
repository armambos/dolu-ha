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

from homeassistant.components.onboarding import async_is_onboarded
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BACKEND_NAME,
    CONF_BACKEND_VERSION,
    CONF_CREDENTIALS_UNUSED,
    CONF_FINGERPRINT,
    CONF_INSTALLATION_ID,
    CONF_PAIRED_AT,
    CONF_REFRESH_TOKEN_ID,
    CONF_USER_ID,
    DOMAIN,
    SHORT_FINGERPRINT_GROUPS,
    TXT_FINGERPRINT,
    TXT_INSTALLATION_ID,
    TXT_VERSION,
    ZEROCONF_TYPE,
)
from .ha_user import (
    HaUserError,
    async_ensure_user,
    async_issue_token,
    async_revoke_token,
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
        # El usuario de DoLu de un emparejamiento anterior, si lo hay. Al re-emparejar se
        # reutiliza en vez de crear un segundo administrador. Se busca por id y nunca por
        # nombre, que el dueño lo puede haber renombrado.
        self._reauth_user_id: str | None = None

    def _onboarding_pending(self) -> bool:
        """¿Home Assistant todavía no ha terminado su configuración inicial?

        Emparejar en ese estado tiene una consecuencia que no se deshace fácil:
        `async_create_user` marca como **propietario** al primer usuario si todavía no hay
        ninguno, así que el dueño de la casa acabaría siendo "DoLu". Por eso se impide en vez
        de solo avisar.
        """
        return not async_is_onboarded(self.hass)

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
            if self._onboarding_pending():
                return self.async_abort(reason="onboarding_incomplete")
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
        if self._onboarding_pending():
            return self.async_abort(reason="onboarding_incomplete")

        # Hay un backend por casa, y estas dos comprobaciones son las que lo sostienen.
        # No hay `single_config_entry` en el manifest, y no es un olvido: está quitado a
        # propósito, porque hacía más daño que bien.
        #
        # Con esa clave puesta, la INTERFAZ de Home Assistant se niega antes de arrancar
        # ningún flujo —enseña su propio diálogo, "{integración} supports only one
        # configuration"— y cuenta cualquier entrada, ignoradas incluidas
        # (`single_config_entry` en el panel de integraciones: si `getConfigEntries({domain})`
        # devuelve algo, diálogo y fuera). Resultado: con el descubrimiento marcado como
        # ignorado, la persona quedaba bloqueada con un mensaje que no menciona la marca de
        # ignorado, y el mensaje de aquí abajo —el único que dice dónde quitarla— no llegaba
        # a verse nunca. Comprobado en la interfaz.
        #
        # Sin la clave, el flujo arranca y cada caso dice lo suyo. Lo que la clave aportaba
        # del lado del servidor ya lo cubren estas comprobaciones y, en el descubrimiento,
        # `_abort_if_unique_id_configured`, que sí tiene en cuenta las entradas ignoradas.
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
        """Cotejar la huella del certificado que sirvió la conexión, y entregar el token.

        Esta pantalla es la barrera 2, y es la única que no puede automatizarse: si alguien
        llegara a conocer el código, todo lo demás le saldría bien y solo el cotejo a ojo lo
        distinguiría del backend real. Por eso los diez pares que se muestran son exactamente
        los mismos que muestra el panel de DoLu, carácter por carácter.

        Y es también el último punto en el que no se ha creado nada todavía. Lo que viene
        después —un usuario administrador y un token de diez años— se hace solo cuando la
        persona ha dicho que las huellas coinciden.
        """
        if self._client is None or self._hello is None:
            return self.async_abort(reason="session_lost")

        if user_input is not None:
            return await self._async_deliver()

        # Si el backend ya avisó de que su .env manda, se dice AQUÍ, antes de crear nada, y
        # con su propio texto. Enterarse después de haber creado un administrador y un token
        # que no se van a usar es peor que no enterarse.
        step_id = "verify_unused" if self._hello.env_precedence else "verify"

        return self.async_show_form(
            step_id=step_id,
            description_placeholders={
                "name": self._hello.name,
                "host": f"{self._host}:{self._port}",
                "version": self._hello.version or "?",
                "fingerprint": short_fingerprint(self._client.fingerprint),
            },
        )

    async def async_step_verify_unused(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """El mismo cotejo, cuando el backend ya dijo que no va a usar lo que se le entregue."""
        return await self.async_step_verify(user_input)

    async def _async_deliver(self) -> ConfigFlowResult:
        """Crear el usuario y el token, entregarlos, y dejar la entrada.

        El orden importa por lo que hay que deshacer si algo falla por el camino: primero la
        URL (que puede no existir y no cuesta nada comprobar), después el usuario, después el
        token, y solo al final la entrega. Si la entrega falla, se revoca el token y —si el
        usuario se creó en este mismo intento— se borra también: un administrador huérfano en
        la casa de alguien es exactamente lo que este proyecto no puede permitirse dejar.
        """
        # La dirección por la que el backend alcanzará a Home Assistant. Que no haya ninguna
        # utilizable es un fallo real y no una rareza: una instancia sin URL interna ni
        # externa configurada existe.
        try:
            ha_url = get_url(self.hass, allow_internal=True, allow_ip=True)
        except NoURLAvailableError:
            _LOGGER.error("Home Assistant no tiene ninguna URL utilizable que darle al backend")
            return self.async_abort(reason="no_ha_url")

        # Se vuelve a comprobar aquí, y no solo al entrar al flujo, porque entre una cosa y
        # otra la persona pudo terminar (o no) el onboarding en otra pestaña. Es barato.
        if self._onboarding_pending():
            return self.async_abort(reason="onboarding_incomplete")

        previous_user_id = self._reauth_user_id
        try:
            user = await async_ensure_user(self.hass, previous_user_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("No se pudo crear ni recuperar el usuario de DoLu")
            return self.async_abort(reason="user_failed")

        created_user = user.id != previous_user_id

        try:
            token, refresh_token_id = await async_issue_token(self.hass, user)
        except HaUserError as err:
            if created_user:
                await self.hass.auth.async_remove_user(user)
            return self.async_abort(reason=err.reason)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("No se pudo crear el token de larga duración de DoLu")
            if created_user:
                await self.hass.auth.async_remove_user(user)
            return self.async_abort(reason="token_failed")

        try:
            result = await self._client.complete(
                {
                    "ha_url": ha_url,
                    "token": token,
                    "user_id": user.id,
                    "refresh_token_id": refresh_token_id,
                }
            )
        except PairingError as err:
            # No llegó a entregarse: lo que se acaba de crear no lo va a usar nadie, así que
            # no se deja atrás. La conversación se consumió, se empieza de nuevo.
            async_revoke_token(self.hass, refresh_token_id)
            if created_user:
                await self.hass.auth.async_remove_user(user)
            return self.async_abort(reason=err.reason)

        # "Nunca en silencio" (sección 3.5): el backend dice si va a usar lo que recibió.
        unused = bool(result.get("credentials_stored_unused"))
        if unused:
            _LOGGER.warning(
                "El backend guardó las credenciales pero NO las va a usar: su archivo .env "
                "tiene HA_BASE_URL y HA_LONG_LIVED_TOKEN, y esas mandan"
            )

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
                # Sin estos dos, la ronda 5 no puede deshacer lo que la 4 hizo.
                CONF_USER_ID: user.id,
                CONF_REFRESH_TOKEN_ID: refresh_token_id,
                CONF_CREDENTIALS_UNUSED: unused,
            },
        )
