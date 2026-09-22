# DoLu para Home Assistant

Integración personalizada que conecta Home Assistant con el backend de DoLu.

Su único cometido es quitar el último paso manual de la instalación: crear a mano un token
de acceso de larga duración en Home Assistant y copiarlo al `.env` del backend. En su lugar,
el backend se anuncia en la red, Home Assistant lo descubre, y un código de un solo uso los
empareja.

Los paneles siguen llegando a Home Assistant por MQTT, igual que siempre. Esta integración
no los toca, y **no crea entidades ni dispositivos**: el backend ya publica por MQTT el
dispositivo "DoLu backend" con su sensor de enlace, y dos dispositivos con ese nombre solo
servirían para no saber cuál mirar.

## Estado

En construcción, por rondas. Lo que hay hoy:

| | |
| --- | --- |
| ✅ **Descubrimiento** | El backend aparece solo en Ajustes → Dispositivos y servicios |
| ✅ **Alta manual** | Por si Home Assistant y el backend no comparten red |
| ✅ **Emparejamiento** | El código de un solo uso, el certificado fijado y el cotejo de la huella |
| ⏳ **Token** | Crear el usuario y el token en Home Assistant y entregárselos al backend |
| ⏳ **Ciclo de vida** | Revocación, borrado y reemparejamiento |

El emparejamiento ya funciona de punta a punta, pero lo que entrega todavía es un contenido
de prueba: el usuario administrador y el token de larga duración son la ronda siguiente.

## Requisitos

- Home Assistant **2026.3** o posterior (antes de esa versión, las integraciones
  personalizadas no pueden traer sus propias imágenes de marca).
- Backend de DoLu **0.3.30** o posterior: es el que se anuncia por mDNS.
- Home Assistant y el backend en la misma red local. El mDNS no cruza routers; si están
  separados, el alta manual por IP sigue funcionando.

## Instalación

### HACS

Añádelo como repositorio personalizado (HACS → los tres puntos → Repositorios
personalizados), con la categoría *Integración*, y descárgalo. Luego reinicia Home
Assistant.

### A mano

Copia `custom_components/dolu/` dentro del directorio de configuración de Home Assistant
(donde está `configuration.yaml`) y reinicia.

## Uso

1. En Home Assistant, el backend aparece en **Ajustes → Dispositivos y servicios** como
   descubierto. Si no aparece, añádelo a mano por su IP.
2. Genera un código de emparejamiento en el panel de DoLu, en **Gestión → Home Assistant**.
   Se muestra una sola vez y dura diez minutos.
3. Teclea el código en Home Assistant.
4. Compara la huella que aparece entonces con la que muestra el panel de DoLu, par por par,
   y confirma.

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

## Desarrollo

Las imágenes de marca viven en `custom_components/dolu/brand/` y son las mismas del icono
de la app: `icon.png` e `icon@2x.png` con la D oscura, para tema claro, y `dark_icon.png` y
`dark_icon@2x.png` con la D blanca, para tema oscuro. Home Assistant sirve las variantes
oscuras desde el directorio local igual que las claras, y si alguna faltara caería a
`icon.png`.

Para probar contra un Home Assistant de usar y tirar, copiar la carpeta de la integración y
reiniciar el contenedor:

```bash
rsync -av --delete --exclude __pycache__ custom_components/dolu/ ha-pruebas:ha-pruebas/custom_components/dolu/
ssh ha-pruebas docker restart ha-pruebas
```

Dos detalles que muerden:

- El `--delete` apunta solo a la carpeta de la integración. Contra el directorio de
  configuración entero, borraría Home Assistant.
- El `--exclude __pycache__` no es cosmético: Home Assistant escribe ahí desde dentro del
  contenedor, donde corre como root, y sin excluirlo el `--delete` falla al intentar borrar
  archivos que no son suyos.

### Probar el emparejamiento

`scripts/e2e_pair_test.py` ejercita `custom_components/dolu/pairing.py` —el mismo archivo que
corre dentro del flujo— contra un backend real, desde dentro del contenedor:

```bash
docker exec ha-pruebas python3 /config/dolu-pruebas/e2e_pair_test.py \
    --host 10.2.1.14 --puerto 3100 --fp <huella> --codigo XXXX-XXXX
```

Comprueba, además de las dos rutas, lo que se rompe en silencio: que `scrypt(código, salt)` y
el transcript del HMAC den lo mismo en Python y en Node. Si las dos puntas se separan ahí, el
síntoma es "código incorrecto" con el código correcto.

Para la barrera del certificado hace falta un impostor de verdad —`scripts/impostor.mjs` en
el repo del backend—, porque que el emparejamiento funcione no demuestra que el pinning
sirva.

Cada cambio necesita reiniciar Home Assistant —los módulos de una integración se importan
una sola vez—, pero el nivel de registro sí se puede subir en caliente, desde Herramientas
para desarrolladores → Acciones:

```yaml
action: logger.set_level
data:
  custom_components.dolu: debug
```
