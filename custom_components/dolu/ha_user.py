"""El usuario administrador "DoLu" y su token de larga duración.

Esto es lo que la ronda 4 añade: en vez de que una persona cree a mano un token en su perfil
y lo copie al `.env` del backend, la integración lo crea aquí y lo entrega por el canal ya
construido en la ronda 3.

**Variante A** (decisión 1 del análisis): un usuario **normal** marcado como administrador,
no un usuario de sistema. La razón pesa más que la comodidad: `config/auth/delete` se niega
a borrar usuarios de sistema (`cannot_modify_system_generated`), así que con un usuario de
sistema, si algo fallara al borrar la integración, quedaría un **administrador huérfano que
no se puede eliminar desde la interfaz**. Con un usuario normal aparece en
Ajustes → Personas → Usuarios y se borra con dos clics, lo que además mata su token.

El usuario nace **sin credenciales**: no puede iniciar sesión, solo existe para colgar de él
el token. Y con `local_only=True`, porque el backend siempre habla desde la LAN.

Todo lo que usa este módulo está verificado contra el Home Assistant de pruebas
(core 2026.9): `async_create_user`, `async_get_user`, `async_create_refresh_token` y
`async_remove_user` son corrutinas; `async_create_access_token`, `async_get_refresh_token` y
`async_remove_refresh_token` no lo son. `hass.auth` es API interna y no forma parte del
contrato para integraciones: puede cambiar entre versiones sin aviso (sección 1.6).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN, User
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# El nombre del usuario y el del cliente del token. El usuario lo puede renombrar el dueño
# de la casa —por eso nunca se busca por nombre, siempre por user_id—, pero el client_name
# es la clave con la que Home Assistant impide que haya dos tokens de larga duración del
# mismo cliente para el mismo usuario, así que este sí tiene que ser estable.
USER_NAME = "DoLu"
CLIENT_NAME = "DoLu"

# Diez años, como el que crearía una persona desde su perfil. Ni el refresh token ni el de
# larga duración caducan por sí solos; lo que caduca es el access token emitido, y esto es
# lo que dura.
TOKEN_LIFETIME = timedelta(days=3650)


class HaUserError(Exception):
    """No se pudo crear o reutilizar el usuario, o su token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _our_tokens(user: User) -> list:
    """Los tokens de larga duración que este cliente dejó colgados de ese usuario."""
    return [
        token
        for token in list(user.refresh_tokens.values())
        if token.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
        and token.client_name == CLIENT_NAME
    ]


async def _async_find_user_with_our_token(hass: HomeAssistant) -> User | None:
    """El usuario que tiene un token nuestro, si queda alguno.

    Se mira el token y no el nombre a propósito: el nombre lo puede cambiar el dueño de la
    casa, el `client_name` del token lo ponemos nosotros y no lo toca nadie desde la
    interfaz.
    """
    for user in await hass.auth.async_get_users():
        if _our_tokens(user):
            return user
    return None


async def async_ensure_user(hass: HomeAssistant, user_id: str | None) -> User:
    """El usuario de DoLu: el de siempre si sigue existiendo, uno nuevo si no.

    Se busca **por `user_id`** y nunca por nombre. Buscar "el usuario que se llama DoLu" es
    frágil de un modo que muerde tarde: el dueño de la casa puede renombrarlo desde la
    interfaz, y entonces un re-emparejamiento crearía un segundo administrador en vez de
    reutilizar el primero, dejando dos.

    Que el usuario ya no exista es un caso normal, no un error: es lo que pasa cuando
    alguien lo borra desde Ajustes → Personas para revocar el acceso y después vuelve a
    emparejar.
    """
    if user_id:
        user = await hass.auth.async_get_user(user_id)
        if user is not None:
            _LOGGER.debug("Reutilizando el usuario DoLu existente (%s)", user_id)
            return user
        _LOGGER.info(
            "El usuario de DoLu (%s) ya no existe; se busca otro o se crea uno nuevo. "
            "Es lo esperable si se borró desde Ajustes → Personas.",
            user_id,
        )

    # Sin id que seguir, todavía queda un rastro fiable: **nuestro propio token**. Si un
    # emparejamiento anterior dejó un token de larga duración con nuestro client_name, el
    # usuario que lo tiene colgado es el usuario de DoLu, se llame como se llame ahora.
    #
    # Esto es lo que salva el re-emparejamiento cuando la entrada de configuración ya no
    # está —se borró y se vuelve a emparejar—, que es justo el caso en el que no hay ningún
    # user_id guardado que leer. Sin esta búsqueda se crearía un segundo administrador y el
    # primero quedaría huérfano en la casa de alguien.
    existing = await _async_find_user_with_our_token(hass)
    if existing is not None:
        _LOGGER.info(
            "Encontrado el usuario de un emparejamiento anterior por su token (%s); se reutiliza",
            existing.id,
        )
        return existing

    user = await hass.auth.async_create_user(
        USER_NAME,
        group_ids=[GROUP_ID_ADMIN],
        # El backend está en la LAN. Si algún día deja de estarlo —una VPN mal enrutada, un
        # proxy inverso que presente una IP pública— el síntoma será 401 en REST y
        # auth_invalid en el WebSocket, indistinguible de un token revocado (sección 1.5).
        local_only=True,
    )
    _LOGGER.debug("Usuario DoLu creado (%s)", user.id)
    return user


def _revoke_our_tokens(hass: HomeAssistant, user: User) -> int:
    """Revoca los tokens de larga duración que este cliente dejó en ese usuario.

    Hace falta para poder re-emparejar: Home Assistant solo admite **un** token de larga
    duración por `client_name` y usuario, y crear el segundo sin quitar el primero lanza
    `ValueError("DoLu already exists")`, un error que no le dice nada a nadie.

    Se barren todos los que cuadren en vez de fiarse solo del id guardado, porque el id
    guardado puede faltar o estar viejo —una entrada creada antes de que se guardara, o un
    emparejamiento a medias— y entonces el token que estorba seguiría ahí sin que nadie lo
    supiera.
    """
    # _our_tokens ya copia la lista antes de devolverla: se está modificando la colección
    # que se recorre.
    tokens = _our_tokens(user)
    for token in tokens:
        hass.auth.async_remove_refresh_token(token)

    if tokens:
        _LOGGER.debug("Revocados %s token(s) anteriores de DoLu", len(tokens))
    return len(tokens)


async def async_issue_token(
    hass: HomeAssistant, user: User
) -> tuple[str, str]:
    """Crea el token de larga duración y devuelve `(token, refresh_token_id)`.

    El `refresh_token_id` es lo que permitirá revocar **exactamente este** token más
    adelante, sin tocar nada más del usuario. Se guarda en la entrada de configuración y
    viaja también al backend.
    """
    _revoke_our_tokens(hass, user)

    try:
        refresh_token = await hass.auth.async_create_refresh_token(
            user,
            client_name=CLIENT_NAME,
            client_icon=None,
            token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
            access_token_expiration=TOKEN_LIFETIME,
        )
    except ValueError as err:
        # No debería ocurrir: se acaban de revocar los que estorbaban. Si pasa, decirlo con
        # el motivo de Home Assistant delante vale más que un "no se pudo".
        _LOGGER.error("Home Assistant rechazó crear el token de DoLu: %s", err)
        raise HaUserError("token_refused") from err

    access_token = hass.auth.async_create_access_token(refresh_token)
    return access_token, refresh_token.id


def async_revoke_token(hass: HomeAssistant, refresh_token_id: str | None) -> bool:
    """Revoca un token concreto por su id. Devuelve si había algo que revocar.

    Lo usa el ciclo de vida de la ronda 5; aquí queda porque es la pareja natural de
    `async_issue_token` y separarlas invita a que una de las dos se quede sin la otra.
    """
    if not refresh_token_id:
        return False
    token = hass.auth.async_get_refresh_token(refresh_token_id)
    if token is None:
        return False
    hass.auth.async_remove_refresh_token(token)
    return True
