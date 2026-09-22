"""La integración DoLu para Home Assistant.

Versión mínima: descubrir el backend de DoLu en la red y emparejarse con él. Los paneles
siguen llegando a Home Assistant por MQTT exactamente igual que antes; esta integración no
los toca ni los sustituye.

No crea entidades ni dispositivos a propósito. El backend ya publica por MQTT el dispositivo
"DoLu backend" con su sensor de enlace, y un segundo dispositivo con ese mismo nombre, creado
por otro camino, solo serviría para que nadie supiera cuál mirar.

RONDA 5 — el ciclo de vida. Lo que esta entrada vigila es **su propio token**, y la razón es
un caso concreto y desagradable: si el dueño de la casa borra el usuario DoLu desde
Ajustes → Personas, el backend se entera en el acto (sus peticiones pasan a dar 401) pero
Home Assistant no se enteraría de nada — la entrada seguiría viéndose sana, cargada y en
verde. Dos pantallas diciendo cosas distintas sobre lo mismo es el peor sitio donde dejar a
alguien.

No hace falta la red para verlo: la integración corre dentro de Home Assistant, así que
comprobar si su token sigue vivo es mirar `hass.auth`. Y no hay que sondear, porque borrar o
desactivar un usuario dispara un evento (`user_removed`, `user_updated`). El temporizador
está solo para el camino que no dispara ninguno: que alguien borre el refresh token suelto.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.auth import EVENT_USER_REMOVED, EVENT_USER_UPDATED
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_FINGERPRINT, CONF_REFRESH_TOKEN_ID, CONF_USER_ID
from .ha_user import async_revoke_token
from .pairing import async_notify_unlink

_LOGGER = logging.getLogger(__name__)

type DoluConfigEntry = ConfigEntry[None]

# Red de seguridad para el único camino que no avisa por evento: borrar el refresh token
# sin tocar al usuario. Diez minutos es de sobra — la comprobación es una consulta en
# memoria, no una petición a nadie, y mientras tanto el backend ya está diciendo lo suyo.
CHECK_INTERVAL = timedelta(minutes=10)


@callback
def _token_is_alive(hass: HomeAssistant, entry: DoluConfigEntry) -> bool:
    """¿Sigue existiendo el token que este emparejamiento creó, y sirve?

    Tres formas de que deje de servir, y las tres se ven desde aquí:

    - El token ya no está (lo borró el dueño, o murió con su usuario).
    - El usuario está desactivado: `async_deactivate_user` borra todos sus refresh tokens,
      pero se comprueba igual porque es barato y porque el orden de las cosas no siempre es
      el que uno espera.
    - El usuario dejó de ser administrador. No revoca el token, pero lo deja inútil para lo
      que el backend necesita —escribir estados y disparar eventos exigen administrador— y
      el síntoma sería un 401 idéntico al de un token revocado.
    """
    refresh_token_id = entry.data.get(CONF_REFRESH_TOKEN_ID)
    if not refresh_token_id:
        # Entrada de antes de la ronda 4: no entregó ningún token, así que no hay ninguno
        # que pueda haberse muerto. No se inventa un problema donde no lo hay.
        return True

    token = hass.auth.async_get_refresh_token(refresh_token_id)
    if token is None:
        _LOGGER.debug("El token de DoLu (%s) ya no existe", refresh_token_id)
        return False

    user = token.user
    if not user.is_active:
        _LOGGER.debug("El usuario de DoLu (%s) está desactivado", user.id)
        return False
    if not user.is_admin:
        _LOGGER.debug("El usuario de DoLu (%s) ya no es administrador", user.id)
        return False

    return True


async def async_setup_entry(hass: HomeAssistant, entry: DoluConfigEntry) -> bool:
    """Cargar la entrada y quedarse vigilando el token que entregó."""
    if not _token_is_alive(hass, entry):
        # Esto es lo que hace que Home Assistant abra el flujo de reautenticación y marque
        # la integración como que necesita atención, en vez de seguir en verde mintiendo.
        raise ConfigEntryAuthFailed(
            "El acceso de DoLu a Home Assistant ya no vale: hay que volver a emparejar"
        )

    _LOGGER.debug(
        "Backend DoLu configurado en %s:%s (instalación %s, usuario %s)",
        entry.data.get("host"),
        entry.data.get("port"),
        entry.data.get("installation_id"),
        entry.data.get(CONF_USER_ID),
    )

    @callback
    def _check(_: Event | None = None) -> None:
        if not _token_is_alive(hass, entry):
            _LOGGER.warning(
                "El acceso de DoLu a Home Assistant dejó de valer; se pide volver a emparejar"
            )
            entry.async_start_reauth(hass)

    # Borrar un usuario dispara user_removed; desactivarlo o quitarle el rol, user_updated.
    # Se escuchan los dos y se comprueba de verdad en vez de fiarse del user_id del evento:
    # la comprobación es la misma que la de arranque y no puede desincronizarse de ella.
    entry.async_on_unload(hass.bus.async_listen(EVENT_USER_REMOVED, _check))
    entry.async_on_unload(hass.bus.async_listen(EVENT_USER_UPDATED, _check))
    entry.async_on_unload(
        async_track_time_interval(hass, lambda _now: _check(), CHECK_INTERVAL)
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DoluConfigEntry) -> bool:
    """Descargar la entrada. Los oyentes se retiran solos (entry.async_on_unload)."""
    return True


async def async_remove_entry(hass: HomeAssistant, entry: DoluConfigEntry) -> None:
    """Se borró la integración: deshacer lo que creó.

    El orden importa, y es este por lo que pasa si algo falla a mitad:

    1. **Revocar el token y borrar el usuario.** Es lo irreversible si se omite: un usuario
       administrador de más en la casa de alguien, que además nadie relacionaría con una
       integración que ya no está. Va primero para que ocurra aunque lo de después falle.
    2. **Avisar al backend**, al mejor esfuerzo. Si está apagado, el aviso se pierde y ya
       está: se despertará con un token muerto, sus peticiones darán 401 y lo dirá en su
       panel, con las dos causas posibles. Es un final aceptable; dejar un administrador
       huérfano no lo es.

    El usuario se busca por el id guardado, nunca por el nombre: el dueño de la casa pudo
    renombrarlo. Y si ya no existe —porque el borrado de la integración vino justo después
    de borrar el usuario a mano— no es un error, es el camino esperado.
    """
    user_id = entry.data.get(CONF_USER_ID)
    refresh_token_id = entry.data.get(CONF_REFRESH_TOKEN_ID)

    if async_revoke_token(hass, refresh_token_id):
        _LOGGER.debug("Token de DoLu revocado (%s)", refresh_token_id)

    if user_id:
        user = await hass.auth.async_get_user(user_id)
        if user is None:
            _LOGGER.debug("El usuario de DoLu (%s) ya no existía", user_id)
        else:
            await hass.auth.async_remove_user(user)
            _LOGGER.info("Usuario de DoLu (%s) borrado con la integración", user_id)

    host = entry.data.get(CONF_HOST)
    port = entry.data.get(CONF_PORT)
    fingerprint = entry.data.get(CONF_FINGERPRINT)

    if host and port and fingerprint and user_id and refresh_token_id:
        await async_notify_unlink(
            hass,
            async_get_clientsession(hass),
            host,
            port,
            fingerprint,
            user_id,
            refresh_token_id,
        )
