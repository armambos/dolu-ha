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
| ⏳ **Emparejamiento** | El código de un solo uso y la verificación de la huella |
| ⏳ **Token** | Crear el usuario y el token en Home Assistant y entregárselos al backend |
| ⏳ **Ciclo de vida** | Revocación, borrado y reemparejamiento |

Mientras el emparejamiento no esté, la entrada de configuración no hace nada: solo deja
anotado dónde está el backend.

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

1. Genera un código de emparejamiento en el panel de DoLu, en **Gestión → Home Assistant**.
2. En Home Assistant, el backend aparecerá en **Ajustes → Dispositivos y servicios** como
   descubierto. Si no aparece, añádelo a mano por su IP.
3. Compara la huella del certificado con la que muestra el panel de DoLu, par por par.
4. Teclea el código antes de que caduque.

Los pasos 3 y 4 llegan con el emparejamiento; hoy el alta se limita a confirmar.

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

Cada cambio necesita reiniciar Home Assistant —los módulos de una integración se importan
una sola vez—, pero el nivel de registro sí se puede subir en caliente, desde Herramientas
para desarrolladores → Acciones:

```yaml
action: logger.set_level
data:
  custom_components.dolu: debug
```
