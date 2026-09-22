"""El lado de Home Assistant del emparejamiento con el backend de DoLu.

Aquí vive el protocolo de la sección 2.2 del análisis: las dos pruebas con las que cada lado
demuestra que conoce el código, y el cliente HTTPS que habla con el backend **fijando su
certificado** antes de enseñar o enviar nada.

El orden no es negociable, y es la razón de que haya dos pruebas y no una. Lo que se entrega
al final del emparejamiento (en la ronda 4) es un acceso de administrador a la casa, así que
quien tiene que autenticar es Home Assistant: el backend demuestra primero (`proof_be`) y
solo si eso cuadra Home Assistant envía algo. La prueba de vuelta (`proof_ha`) existe por el
motivo simétrico — sin ella, cualquiera en la red podría mandarle al backend la dirección de
un Home Assistant impostor y un token suyo.

Las tres barreras, y dónde se aplica cada una:

1. **Fijar el certificado.** La primera petición ya va con `aiohttp.Fingerprint`, así que un
   impostor que copie la huella real en su anuncio mDNS ni siquiera completa el handshake.
2. **La comparación humana.** La huella que el flujo enseña es la del certificado que sirvió
   *de verdad* la conexión, recortada a los mismos diez pares que muestra el panel del
   backend. Es la última línea si el impostor llega a conocer el código.
3. **El código.** Nada se envía hasta que `proof_be` cuadra.

La derivación de la clave tiene que ser idéntica byte a byte a la del backend
(`src/haPairing.js`): mismo scrypt, mismos parámetros, mismo transcript y mismas etiquetas
de rol. Si una de las dos puntas cambia, el emparejamiento deja de funcionar sin decir por
qué — por eso `scripts/pair-client-test.mjs` del backend y `scripts/interop_test.py` de este
repo calculan lo mismo desde los dos lenguajes y se comparan.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import ssl
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, Fingerprint
from aiohttp.client_exceptions import ServerFingerprintMismatch

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# scrypt con los mismos parámetros que el backend (SCRYPT_PARAMS en src/haPairing.js).
# N=2^14, r=8, p=1: unos 16 MB y decenas de milisegundos. Es lo que hace que el verificador
# que cualquiera de los dos lados entrega al otro no se pueda atacar sin conexión dentro de
# la ventana de diez minutos que vive el código.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_BYTES = 32
# OpenSSL exige maxmem >= 128*r*N (unos 16.8 MB aquí) y su tope por defecto es 32 MB, que
# justo da. Se pide explícitamente para no depender de ese defecto en otra compilación.
SCRYPT_MAXMEM = 64 * 1024 * 1024

# Separación de dominios: las dos pruebas se calculan con la misma clave sobre los mismos
# nonces. Sin estas etiquetas, la prueba del backend serviría como la de Home Assistant y un
# impostor podría devolver la que acaba de recibir para pasar por el otro lado.
ROLE_BACKEND = "dolu-backend"
ROLE_HA = "dolu-ha"

# El backend valida el nonce contra /^[A-Za-z0-9_-]{16,128}$/. token_urlsafe usa exactamente
# ese alfabeto, y 32 bytes dan 43 caracteres.
NONCE_BYTES = 32

PAIR_TIMEOUT_SECONDS = 15


class PairingError(Exception):
    """Fallo del emparejamiento, con el motivo que el flujo enseña.

    El `reason` es una clave de `strings.json`, no un texto: cada forma de fallar tiene su
    propio mensaje porque "no se pudo emparejar" no le dice a nadie qué mirar.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class BackendHello:
    """Lo que el backend contesta al primer paso, ya verificado.

    `name`, `installation_id` y `version` salen de esta respuesta y **no** del registro TXT
    del anuncio: el TXT lo escribe cualquiera en la red, esto llega por una conexión ya
    fijada por huella y firmada con la clave del código.
    """

    nonce_be: str
    salt: str
    name: str
    installation_id: str
    version: str
    fingerprint: str
    # El backend tiene HA_BASE_URL y HA_LONG_LIVED_TOKEN en su .env, así que esas mandan y
    # lo que se le entregue se guardará sin usarse. Llega en el hello, o sea ANTES de que
    # aquí se cree ningún usuario ni ningún token.
    env_precedence: bool = False


def normalize_code(value: str) -> str:
    """El código tal como lo derivó el backend.

    La persona puede teclear el guion que muestra el panel, espacios o minúsculas. El backend
    deriva la clave del código en crudo (ocho caracteres, sin separadores), así que aquí hay
    que quitar exactamente lo mismo que quita `normalizeCode` en src/codes.js.
    """
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def random_nonce() -> str:
    return secrets.token_urlsafe(NONCE_BYTES)


def normalize_fingerprint(value: str) -> str:
    """La huella en el formato canónico "AA:BB:CC:…", que es el que entra en el transcript."""
    hex_only = re.sub(r"[^0-9A-F]", "", (value or "").upper())
    return ":".join(hex_only[i : i + 2] for i in range(0, len(hex_only), 2))


def fingerprint_bytes(value: str) -> bytes:
    """La huella como bytes crudos, que es lo que `aiohttp.Fingerprint` espera."""
    return bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", value or ""))


def _derive_key_blocking(code: str, salt_hex: str) -> bytes:
    return hashlib.scrypt(
        normalize_code(code).encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_KEY_BYTES,
        maxmem=SCRYPT_MAXMEM,
    )


async def derive_key(hass: HomeAssistant, code: str, salt_hex: str) -> bytes:
    """scrypt(código, salt), fuera del bucle de eventos.

    Cuesta decenas de milisegundos a propósito (ver el comentario de SCRYPT_N). Dejarlo en el
    bucle bloquearía Home Assistant entero durante ese rato.
    """
    return await hass.async_add_executor_job(_derive_key_blocking, code, salt_hex)


def _proof(key: bytes, role: str, nonce_ha: str, nonce_be: str, fingerprint: str) -> str:
    # El fingerprint entra en el cálculo (channel binding): ata la prueba a la conexión TLS
    # concreta por la que viaja, así que un intermediario que reenvíe a la máquina real no
    # puede reutilizarla — su certificado es otro y las cuentas no le salen.
    transcript = "|".join([role, nonce_ha, nonce_be, fingerprint])
    return hmac.new(key, transcript.encode("utf-8"), hashlib.sha256).hexdigest()


def backend_proof(key: bytes, nonce_ha: str, nonce_be: str, fingerprint: str) -> str:
    return _proof(key, ROLE_BACKEND, nonce_ha, nonce_be, fingerprint)


def ha_proof(key: bytes, nonce_ha: str, nonce_be: str, fingerprint: str) -> str:
    return _proof(key, ROLE_HA, nonce_ha, nonce_be, fingerprint)


def _unverified_context() -> ssl.SSLContext:
    """Contexto TLS que no valida cadena ni nombre.

    Es correcto aquí y no un atajo: el certificado del backend es autofirmado y lo que lo
    autentica no es ninguna autoridad, es su huella — comprobada aparte, contra la anunciada
    o contra la que la persona coteja en el panel. Solo se usa para *leer* el certificado en
    el alta manual, donde todavía no hay ninguna huella con la que fijar.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def read_served_fingerprint(hass: HomeAssistant, host: str, port: int) -> str:
    """La huella del certificado que sirve ese host, leída del propio handshake.

    Hace falta en el alta manual: ahí no hay anuncio del que sacar la huella, así que se lee
    la que hay (TOFU) y se le enseña a la persona para que la coteje con el panel del backend.
    En el alta por descubrimiento no se usa — ahí la huella viene del anuncio y se *fija*
    antes de hablar, que es más fuerte.
    """
    context = await hass.async_add_executor_job(_unverified_context)
    try:
        async with asyncio.timeout(PAIR_TIMEOUT_SECONDS):
            reader, writer = await asyncio.open_connection(host, port, ssl=context)
    except (OSError, asyncio.TimeoutError, ssl.SSLError) as err:
        _LOGGER.debug("No se pudo leer el certificado de %s:%s: %s", host, port, err)
        raise PairingError("cannot_connect") from err

    try:
        ssl_object = writer.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            # Cerrar limpiamente un TLS puede fallar si el otro lado ya cortó; da igual, el
            # certificado ya se leyó.
            pass

    if not der:
        raise PairingError("cannot_connect")

    return normalize_fingerprint(hashlib.sha256(der).hexdigest())


class PairingClient:
    """Las dos llamadas del emparejamiento, siempre con el certificado fijado.

    Una instancia por conversación: guarda los nonces y la clave para que `complete` use
    exactamente los mismos que `hello`, que es lo que el backend comprueba.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: ClientSession,
        host: str,
        port: int,
        fingerprint: str,
    ) -> None:
        self._hass = hass
        self._session = session
        self._host = host
        self._port = port
        # La huella que se fija. En el descubrimiento es la anunciada; en el alta manual, la
        # que se acaba de leer del handshake. En los dos casos, a partir de aquí la conexión
        # solo se completa si el certificado es EXACTAMENTE este.
        self.fingerprint = normalize_fingerprint(fingerprint)
        self._ssl = Fingerprint(fingerprint_bytes(self.fingerprint))
        self._nonce_ha = random_nonce()
        self._key: bytes | None = None
        self._hello: BackendHello | None = None

    @property
    def base_url(self) -> str:
        return f"https://{self._host}:{self._port}"

    async def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            async with asyncio.timeout(PAIR_TIMEOUT_SECONDS):
                async with self._session.post(
                    f"{self.base_url}{path}", json=payload, ssl=self._ssl
                ) as response:
                    try:
                        body = await response.json(content_type=None)
                    except ValueError:
                        body = {}
                    return response.status, body if isinstance(body, dict) else {}
        except ServerFingerprintMismatch as err:
            # La barrera 1 haciendo su trabajo: alguien anunció la huella del backend real
            # pero no puede presentar su certificado. Merece un mensaje propio — es la única
            # forma de fallar que significa "hay un impostor en la red", no "no se pudo".
            _LOGGER.warning(
                "El certificado de %s no es el que se anunció: alguien está suplantando al "
                "backend de DoLu en la red",
                self.base_url,
            )
            raise PairingError("fingerprint_mismatch") from err
        except (ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.debug("Fallo hablando con %s%s: %s", self.base_url, path, err)
            raise PairingError("cannot_connect") from err

    @staticmethod
    def _code_state_reason(body: dict[str, Any]) -> str:
        """El 409 del backend, traducido al motivo que la persona necesita leer.

        El backend distingue entre no haber código, haberse gastado, haber caducado y estar
        bloqueado (`haPairingCodeStatus` en src/haPairingCodes.js). Los cuatro se arreglan de
        forma distinta, así que se cuentan por separado.
        """
        return {
            "expired": "code_expired",
            "used": "code_used",
            "locked": "code_locked",
        }.get(str(body.get("status") or ""), "no_active_code")

    async def hello(self, code: str) -> BackendHello:
        """Paso 1: el backend demuestra que conoce el código.

        Devuelve los datos de la instalación **ya verificados**. Si la prueba no cuadra no se
        envía absolutamente nada más: o el código tecleado no es el bueno, o del otro lado no
        está el backend que dice ser.
        """
        status, body = await self._post("/ha/pair/hello", {"nonce_ha": self._nonce_ha})

        if status == 409:
            raise PairingError(self._code_state_reason(body))
        if status == 429:
            raise PairingError("too_many_requests")
        if status != 200:
            _LOGGER.debug("hello devolvió %s: %s", status, body)
            raise PairingError("unexpected_response")

        nonce_be = str(body.get("nonce_be") or "")
        salt = str(body.get("salt") or "")
        served = normalize_fingerprint(str(body.get("fingerprint") or ""))
        proof_be = str(body.get("proof_be") or "")

        if not nonce_be or not salt or not proof_be:
            raise PairingError("unexpected_response")

        # El backend dice servir esta huella; nosotros fijamos aquella. Si no son la misma,
        # algo no cuadra aunque el TLS haya pasado, y el transcript se calcularía sobre un
        # valor distinto en cada punta.
        if served != self.fingerprint:
            _LOGGER.warning(
                "El backend dice servir la huella %s pero la conexión se fijó a %s",
                served,
                self.fingerprint,
            )
            raise PairingError("fingerprint_mismatch")

        key = await derive_key(self._hass, code, salt)
        expected = backend_proof(key, self._nonce_ha, nonce_be, self.fingerprint)

        if not hmac.compare_digest(expected, proof_be):
            # El caso corriente es el más simple: el código tecleado no es el que el panel
            # mostró. El otro —que del otro lado no esté el backend real— lo cubre la misma
            # comprobación, y en los dos casos la respuesta correcta es no enviar nada.
            #
            # Nótese que esto NO gasta uno de los cinco intentos del código: el backend nunca
            # llega a ver una prueba mala, porque se detecta aquí. Es a propósito (ver el
            # README): el tope de intentos protege contra quien envía pruebas al backend, no
            # contra quien se equivoca tecleando.
            raise PairingError("invalid_code")

        self._key = key
        self._hello = BackendHello(
            nonce_be=nonce_be,
            salt=salt,
            name=str(body.get("name") or "DoLu"),
            installation_id=str(body.get("installation_id") or ""),
            version=str(body.get("version") or ""),
            fingerprint=served,
            env_precedence=bool(body.get("env_precedence")),
        )
        return self._hello

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Paso 2: Home Assistant demuestra a su vez que conoce el código, y entrega.

        Solo se llega aquí con `hello` verificado, así que para cuando esto se envía ya está
        probado que del otro lado está quien dice ser.
        """
        if self._key is None or self._hello is None:
            raise PairingError("unexpected_response")

        proof = ha_proof(
            self._key, self._nonce_ha, self._hello.nonce_be, self.fingerprint
        )
        status, body = await self._post(
            "/ha/pair/complete",
            {"nonce_ha": self._nonce_ha, "proof_ha": proof, "payload": payload},
        )

        if status == 409:
            # La conversación se perdió por el camino: el código se regeneró, caducó, o
            # pasaron los dos minutos que vive la sesión abierta por el hello.
            raise PairingError(self._code_state_reason(body) if body.get("status") else "session_lost")
        if status == 401:
            # No debería ocurrir: la clave es la misma con la que ya cuadró proof_be.
            _LOGGER.error("El backend rechazó la prueba de Home Assistant: %s", body)
            raise PairingError("invalid_proof")
        if status == 429:
            raise PairingError("too_many_requests")
        if status != 200:
            _LOGGER.debug("complete devolvió %s: %s", status, body)
            raise PairingError("unexpected_response")

        return body
