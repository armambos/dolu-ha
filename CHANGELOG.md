# Historial de cambios

## 0.2.1 — 2026-09-22

- Los cuatro estados en los que el backend no tiene código activo —no hay ninguno, el
  último se usó, caducó, o se bloqueó— dejan de hablar del código que la persona acaba de
  teclear y hablan del estado del backend, que es lo que pasa de verdad. Antes, teclear un
  código equivocado después de un emparejamiento correcto contestaba "ese código ya se usó",
  que es cierto sobre el backend y falso sobre lo que la persona hizo. Los cuatro dicen
  ahora el estado y la acción: no hay código activo, genera uno.

## 0.2.0 — 2026-09-22

Ronda 3: el canal de emparejamiento, del lado de Home Assistant.

- El flujo ya no se limita a apuntar dónde está el backend: se empareja con él siguiendo el
  protocolo de la sección 2.2 del análisis. Tres pantallas, en el orden que impone el
  protocolo y no la comodidad: **confirmar** lo que se encontró (sin afirmar nada todavía),
  **teclear el código**, y **cotejar la huella** del certificado que sirvió de verdad la
  conexión. Solo después de eso Home Assistant envía algo.
- **El certificado se fija antes de hablar** (`aiohttp.Fingerprint`): un impostor que copie
  la huella real en su anuncio mDNS no completa ni el handshake. Comprobado con un impostor
  de verdad, no por lectura del código.
- El **nombre, la versión y la identidad** de la instalación se toman de la respuesta
  verificada, no del registro TXT del anuncio, que lo escribe cualquiera.
- El **alta manual** aprende la huella del propio handshake (TOFU), porque ahí no hay
  anuncio del que sacarla; todo el peso recae entonces sobre el código y el cotejo a ojo.
- Cada forma de fallar tiene su mensaje: huella que no cuadra, código equivocado, caducado,
  gastado, bloqueado, sin código activo, demasiados intentos, y conversación perdida. Los
  que se arreglan reintentando dejan reintentar; el de la huella aborta, porque significa
  que hay alguien suplantando al backend en la red.
- En esta ronda `complete` todavía **no** entrega un token: viaja un payload de prueba. El
  usuario administrador y el token de larga duración son la ronda 4.
- `scripts/e2e_pair_test.py` ejercita `pairing.py` —el mismo archivo que corre en el flujo—
  contra un backend real, y compara la derivación de clave y el HMAC con los que calcula
  Node. Es la comprobación que atrapa la avería que no se ve: que las dos puntas dejen de
  derivar la misma clave.

Nota sobre el bloqueo a los cinco intentos: un código mal tecleado se detecta **en Home
Assistant**, al comprobar la prueba del backend, y por eso no gasta ninguno de los cinco
intentos ni llega al backend. El tope protege contra quien envía pruebas al backend, no
contra quien se equivoca tecleando. Se ejercita desde `scripts/pair-client-test.mjs` del
backend.


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
