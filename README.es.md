# DoLu para Home Assistant

*[Read this in English](README.md)*

Integración personalizada que conecta Home Assistant con el backend de DoLu.

Su único cometido es quitar el último paso manual de la instalación: crear a mano un token
de acceso de larga duración en Home Assistant y copiarlo al `.env` del backend. En su lugar,
el backend se anuncia en la red, Home Assistant lo descubre, y un código de un solo uso los
empareja.

Los paneles siguen llegando a Home Assistant por MQTT, igual que siempre. Esta integración
no los toca, y **no crea entidades ni dispositivos**: el backend ya publica por MQTT el
dispositivo "DoLu backend" con su sensor de enlace, y dos dispositivos con ese nombre solo
servirían para no saber cuál mirar.

## Qué hace, en una línea

Crea en Home Assistant un usuario administrador llamado **DoLu**, genera un token de larga
duración suyo, y se lo entrega al backend por un canal cifrado y verificado. Tú no copias
nada.

## Requisitos

- Home Assistant **2026.3** o posterior (antes de esa versión, las integraciones
  personalizadas no pueden traer sus propias imágenes de marca).
- Backend de DoLu **0.3.36** o posterior.
- Home Assistant y el backend en la misma red local. El mDNS no cruza routers; si están
  separados, el alta manual por IP sigue funcionando.

## Instalación

### HACS

Añádelo como repositorio personalizado (HACS → los tres puntos → Repositorios
personalizados), con la categoría *Integración*, y descárgalo. Luego **reinicia Home
Assistant**: hasta que no reinicies, Home Assistant no sabe que existe y no puede descubrir
nada.

### A mano

Copia `custom_components/dolu/` dentro del directorio de configuración de Home Assistant
(donde está `configuration.yaml`) y reinicia.

## Uso

1. En Home Assistant, el backend aparece en **Ajustes → Dispositivos y servicios** como
   descubierto. Si no aparece, añádelo a mano por su dirección IP.
2. Genera un código de emparejamiento en el panel de DoLu, en **Gestión → Home Assistant**.
   Se muestra una sola vez y dura diez minutos.
3. Teclea el código en Home Assistant.
4. Compara la huella que aparece entonces con la que muestra el panel de DoLu, par por par,
   y confirma.

Al confirmar se crea el usuario **DoLu** y se entrega su token. A partir de ahí el backend
habla con Home Assistant por su cuenta.

### Por qué el orden es ese

No es arbitrario. Lo que se acaba entregando es un acceso de administrador a la casa, así
que quien tiene que autenticar es Home Assistant, y eso decide el orden:

- **La huella se fija antes de hablar.** La primera petición ya exige que el certificado sea
  el que anunció el backend. Alguien que copie ese anuncio no llega ni a la primera
  pantalla: su certificado es otro.
- **El código no se envía nunca.** Sirve para que el backend *demuestre* que lo conoce. Si
  no lo demuestra, Home Assistant se para sin haber enviado nada.
- **La huella se enseña al final, no al principio**, y es la del certificado que sirvió de
  verdad la conexión. Compararla con el panel es la última barrera, la que queda si alguien
  llegara a ver el código por encima de tu hombro. Por eso son los mismos diez pares que
  muestra DoLu: se leen una pantalla al lado de la otra.

Un detalle que conviene saber: si tecleas mal el código, Home Assistant se da cuenta **sin
preguntarle al backend**, así que ese error **no gasta** ninguno de los cinco intentos que
bloquean el código. El tope de cinco está para quien le manda pruebas al backend, no para
quien se equivoca escribiendo.

## Dónde ver y revocar el acceso de DoLu

Esto es lo más importante de este README, porque es lo que te deja deshacer todo sin
depender de nadie.

### Dónde NO está

**No busques el token en tu perfil.** La lista de *Tokens de acceso de larga duración* de
Ajustes → tu avatar → Seguridad muestra únicamente los tokens **del usuario con el que has
iniciado sesión**, y el de DoLu cuelga de un usuario aparte. Ahí no va a aparecer nunca, y
no es que falte: es que no es tuyo.

### Dónde sí está

**Ajustes → Personas → Usuarios**. Verás un usuario llamado **DoLu**:

- Es **administrador**, porque escribir estados y disparar eventos en Home Assistant lo
  exige. No hay un permiso intermedio que sirva.
- Tiene marcado **"Solo acceso local"**, porque el backend siempre está en tu red.
- **No puede iniciar sesión**: nace sin contraseña ni credenciales. Existe solo para colgar
  de él el token.

### Cómo se revoca

| Lo que quieres | Qué haces |
| --- | --- |
| Cortar el acceso ya | Borra el usuario **DoLu** en Ajustes → Personas. El token muere con él |
| Cortarlo sin borrarlo | Desactiva el usuario. Home Assistant borra todos sus tokens |
| Quitarlo todo, limpio | Borra la integración en Ajustes → Dispositivos y servicios. Revoca el token, borra el usuario y avisa al backend para que olvide el enlace |

Si revocas el acceso sin borrar la integración, Home Assistant se da cuenta solo: la
integración se marca como que necesita atención y te ofrece volver a emparejar. El panel de
DoLu lo dirá también, y desde los dos sitios se llega al mismo sitio.

### Si el acceso deja de funcionar sin que hayas tocado nada

Hay **dos** causas posibles y desde fuera se parecen:

1. **El token se revocó** — alguien borró o desactivó el usuario DoLu, o le quitó el rol de
   administrador. Se arregla volviendo a emparejar.
2. **El backend dejó de hablar desde una dirección local.** El usuario DoLu tiene marcado
   "Solo acceso local", así que una VPN mal enrutada, un NAT que presente una dirección
   pública o un proxy inverso delante de Home Assistant producen **exactamente el mismo
   rechazo**. Volver a emparejar no arregla esto: funcionaría hasta el siguiente arranque.
   Se arregla en la red, o desmarcando "Solo acceso local" en el usuario DoLu.

## Privacidad y alcance

- El token vive en el backend, en un archivo con permisos `0600`, y no se escribe en ningún
  registro ni se muestra en ninguna pantalla.
- La integración **no envía nada fuera de tu red**: solo habla con el backend, por su
  dirección local y con su certificado fijado.
- No crea entidades, dispositivos ni automatizaciones.

## Desarrollo

Las imágenes de marca viven en `custom_components/dolu/brand/`: `icon.png` e `icon@2x.png`
con la D oscura, para tema claro, y `dark_icon.png` y `dark_icon@2x.png` con la D blanca,
para tema oscuro. Home Assistant sirve las variantes oscuras desde el directorio local igual
que las claras, y si alguna faltara caería a `icon.png`.

Para probar contra un Home Assistant de usar y tirar (un contenedor con su propio directorio
de configuración), copiar la carpeta de la integración y reiniciarlo:

```bash
rsync -av --delete --exclude __pycache__ \
    custom_components/dolu/ USUARIO@HOST:RUTA_CONFIG/custom_components/dolu/
ssh USUARIO@HOST docker restart NOMBRE_CONTENEDOR
```

Dos detalles que muerden:

- El `--delete` apunta solo a la carpeta de la integración. Contra el directorio de
  configuración entero, borraría Home Assistant.
- El `--exclude __pycache__` no es cosmético: Home Assistant escribe ahí desde dentro del
  contenedor, donde corre como root, y sin excluirlo el `--delete` falla al intentar borrar
  archivos que no son suyos.

Cada cambio necesita reiniciar Home Assistant —los módulos de una integración se importan
una sola vez—, pero el nivel de registro sí se puede subir en caliente, desde Herramientas
para desarrolladores → Acciones:

```yaml
action: logger.set_level
data:
  custom_components.dolu: debug
```

Y un aviso que ahorra una tarde: **el navegador cachea las traducciones**. Una clave añadida
en una versión recién desplegada no aparece hasta una recarga forzada (`Ctrl+Shift+R`), y
una clave que falta no da error — se pinta vacía o en crudo. Si un texto sale raro, recarga
antes de darlo por roto. Los fallos de formato sí se ven: Home Assistant los recoge en su
registro como `frontend.js.modern`.

### Probar el emparejamiento

`scripts/e2e_pair_test.py` ejercita `custom_components/dolu/pairing.py` —el mismo archivo que
corre dentro del flujo— contra un backend real, desde dentro del contenedor de Home
Assistant, que es donde están `aiohttp` y `homeassistant`:

```bash
docker exec NOMBRE_CONTENEDOR python3 /config/e2e_pair_test.py \
    --host DIRECCION_DEL_BACKEND --puerto 3000 --fp HUELLA --codigo XXXX-XXXX
```

Comprueba, además de las dos rutas, lo que se rompe en silencio: que `scrypt(código, salt)` y
el transcript del HMAC den lo mismo en Python y en Node. Si las dos puntas se separan ahí, el
síntoma es "código incorrecto" con el código correcto.

Para la barrera del certificado hace falta un impostor de verdad —un segundo HTTPS con
certificado propio que se anuncie con la huella del backend copiada—, porque que el
emparejamiento funcione no demuestra que el pinning sirva.

## Licencia

MIT. Ver [LICENSE](LICENSE).
