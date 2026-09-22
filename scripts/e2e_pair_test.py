"""Prueba de punta a punta del emparejamiento, con el código real de la integración.

Ejercita `custom_components/dolu/pairing.py` —el mismo archivo que corre dentro del flujo de
configuración— contra un backend de DoLu de verdad. Lo que comprueba no es que "funcione"
en abstracto, sino las dos cosas que se rompen en silencio:

1. **Que Python y Node deriven la misma clave.** El emparejamiento entero cuelga de que
   `scrypt(código, salt)` y el transcript del HMAC sean idénticos byte a byte en las dos
   puntas. Si se separan, el síntoma es "código incorrecto" con el código correcto, que es
   de los errores que cuestan un día entero.
2. **Que las barreras aborten.** Con el impostor levantado (`scripts/impostor.mjs` del
   backend), que fijar la huella pare de verdad la conexión.

Se ejecuta dentro del contenedor de Home Assistant, que es donde están aiohttp y
homeassistant, y desde donde la integración va a hablar de verdad:

    docker exec ha-pruebas python3 /config/dolu-pruebas/e2e_pair_test.py \\
        --host 10.2.1.14 --puerto 3100 --fp <huella> --codigo XXXX-XXXX

Argumentos opcionales:
    --vector code:salt:key_hex:proof_hex   Vector calculado por Node, para el punto 1.
    --impostor-puerto 3200                 Si está levantado, prueba la barrera 1.
    --solo-fallos                          No gasta el código: omite el emparejamiento bueno.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiohttp

# pairing.py no tiene imports relativos a propósito, así que se puede cargar suelto, sin
# montar el paquete entero ni arrancar Home Assistant.
RUTA_PAIRING = Path(__file__).resolve().parent.parent / "custom_components" / "dolu" / "pairing.py"
if not RUTA_PAIRING.exists():
    RUTA_PAIRING = Path("/config/custom_components/dolu/pairing.py")

_spec = importlib.util.spec_from_file_location("dolu_pairing", RUTA_PAIRING)
pairing = importlib.util.module_from_spec(_spec)
# En sys.modules ANTES de ejecutarlo: @dataclass(slots=True) reconstruye la clase y para
# ello busca su módulo por nombre. Si no está registrado, falla con un AttributeError que
# no dice nada de lo que pasa de verdad.
sys.modules["dolu_pairing"] = pairing
_spec.loader.exec_module(pairing)


class HassStub:
    """Lo único que `pairing.py` le pide a Home Assistant: correr algo fuera del bucle."""

    def __init__(self, executor: ThreadPoolExecutor) -> None:
        self._executor = executor

    async def async_add_executor_job(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, func, *args)


fallos = 0


def check(nombre: str, ok: bool, extra: str = "") -> None:
    global fallos
    print(f"{'✔' if ok else '✘'} {nombre}{f' — {extra}' if extra else ''}")
    if not ok:
        fallos += 1


def seccion(titulo: str) -> None:
    print(f"\n── {titulo}")


async def espera_fallo(nombre: str, coro, motivo_esperado: str) -> None:
    try:
        await coro
    except pairing.PairingError as err:
        check(nombre, err.reason == motivo_esperado, f"motivo: {err.reason}")
    except Exception as err:  # noqa: BLE001
        check(nombre, False, f"excepción inesperada: {type(err).__name__}: {err}")
    else:
        check(nombre, False, "no falló, y tenía que fallar")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--puerto", type=int, default=3100)
    parser.add_argument("--fp", required=True, help="Huella anunciada del backend")
    parser.add_argument("--codigo", help="Código de emparejamiento recién generado")
    parser.add_argument("--vector", help="code:salt:key_hex:proof_hex calculado por Node")
    parser.add_argument("--impostor-puerto", type=int)
    parser.add_argument("--solo-fallos", action="store_true")
    args = parser.parse_args()

    executor = ThreadPoolExecutor(max_workers=2)
    hass = HassStub(executor)

    # ------------------------------------------------------------------
    seccion("Interoperabilidad con el backend (Node ↔ Python)")

    if args.vector:
        code, salt, key_hex, proof_hex = args.vector.split(":")
        key = await pairing.derive_key(hass, code, salt)
        check(
            "scrypt(código, salt) da la misma clave que Node",
            key.hex() == key_hex,
            f"python={key.hex()[:16]}… node={key_hex[:16]}…",
        )
        proof = pairing.backend_proof(key, "NONCE-HA-FIJO", "NONCE-BE-FIJO", args.fp)
        check(
            "El transcript del HMAC es el mismo que el de Node",
            proof == proof_hex,
            f"python={proof[:16]}… node={proof_hex[:16]}…",
        )
    else:
        print("  (sin --vector: se omite la comparación con Node)")

    check(
        "normalize_code quita guion, espacios y minúsculas",
        pairing.normalize_code(" 3gst-bas2 ") == "3GSTBAS2",
        pairing.normalize_code(" 3gst-bas2 "),
    )
    check(
        "normalize_fingerprint deja el formato AA:BB:…",
        pairing.normalize_fingerprint("aabbcc") == "AA:BB:CC",
    )

    # ------------------------------------------------------------------
    seccion("Lectura del certificado servido (camino del alta manual)")

    leida = await pairing.read_served_fingerprint(hass, args.host, args.puerto)
    check(
        "La huella leída del handshake es la que anuncia el backend",
        leida == pairing.normalize_fingerprint(args.fp),
        leida,
    )

    await espera_fallo(
        "Un puerto donde no hay nada da cannot_connect",
        pairing.read_served_fingerprint(hass, args.host, 9),
        "cannot_connect",
    )

    async with aiohttp.ClientSession() as session:
        def cliente(fp: str, puerto: int | None = None) -> pairing.PairingClient:
            return pairing.PairingClient(
                hass, session, args.host, puerto or args.puerto, fp
            )

        # --------------------------------------------------------------
        seccion("Barrera 1 — fijar el certificado antes de hablar")

        # Una huella que no es la del backend: el handshake tiene que fallar antes de que
        # viaje nada. Es el mismo mecanismo que para al impostor que copia el TXT.
        falsa = "AA:" + ":".join(["BB"] * 31)
        await espera_fallo(
            "Con una huella que no cuadra, la conexión ni se completa",
            cliente(falsa).hello("XXXX-XXXX"),
            "fingerprint_mismatch",
        )

        if args.impostor_puerto:
            # El impostor real, con certificado propio, pero fijando la huella del backend
            # de verdad: exactamente el ataque de copiar el TXT del anuncio.
            await espera_fallo(
                "El impostor que copia la huella anunciada no pasa del TLS",
                cliente(args.fp, args.impostor_puerto).hello(args.codigo or "XXXX-XXXX"),
                "fingerprint_mismatch",
            )
        else:
            print("  (sin --impostor-puerto: se omite la prueba con el impostor real)")

        # --------------------------------------------------------------
        seccion("Barrera 3 — el código")

        if args.codigo:
            await espera_fallo(
                "Un código equivocado se detecta aquí y no se envía nada",
                cliente(args.fp).hello("AAAA-BBBB"),
                "invalid_code",
            )

            if not args.solo_fallos:
                cli = cliente(args.fp)
                hello = await cli.hello(args.codigo)
                check("hello con el código bueno verifica proof_be", True, f"nombre: {hello.name}")
                check(
                    "El nombre y la identidad llegan por el canal verificado, no por el TXT",
                    bool(hello.name and hello.installation_id),
                    f"{hello.name} / {hello.installation_id}",
                )
                check(
                    "La huella que dice servir es la que se fijó",
                    hello.fingerprint == pairing.normalize_fingerprint(args.fp),
                )

                resultado = await cli.complete(
                    {"round": 3, "test": True, "ha_url": "http://e2e-test:8123", "note": "e2e_pair_test.py"}
                )
                check("complete acepta la prueba de Home Assistant", bool(resultado.get("ok")), str(resultado))

                # Y el código es de un solo uso: el mismo ya no vale.
                await espera_fallo(
                    "El código queda quemado después de usarlo",
                    cliente(args.fp).hello(args.codigo),
                    "code_used",
                )
        else:
            print("  (sin --codigo: se omiten las pruebas del código)")

    executor.shutdown(wait=False)
    print("")
    print(f"{'TODO BIEN' if fallos == 0 else f'{fallos} COMPROBACIONES FALLIDAS'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
