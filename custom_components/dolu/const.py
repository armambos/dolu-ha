"""Constantes compartidas de la integración DoLu."""

DOMAIN = "dolu"

# El servicio que anuncia el backend por mDNS, y las claves de su registro TXT
# (ver src/mdns.js en el backend).
ZEROCONF_TYPE = "_dolu._tcp.local."
TXT_FINGERPRINT = "fp"
TXT_INSTALLATION_ID = "id"
TXT_VERSION = "v"

# Lo que se guarda en la entrada de configuración.
CONF_FINGERPRINT = "fingerprint"
CONF_INSTALLATION_ID = "installation_id"
CONF_BACKEND_VERSION = "backend_version"

# El recorte de huella que se enseña para cotejar a ojo: diez pares, exactamente el mismo
# que muestra el panel de administración del backend (shortFingerprint en src/tls.js). Que
# sean los mismos diez pares no es un detalle estético — las dos pantallas se comparan una
# al lado de la otra, carácter por carácter.
SHORT_FINGERPRINT_GROUPS = 10

# Lo que el emparejamiento de la ronda 3 añade a la entrada. El nombre se guarda porque el
# que vale es el que llegó por la conexión verificada, no el del anuncio: si alguien cambia
# MDNS_SERVICE_NAME, la entrada sigue diciendo con qué instalación se emparejó.
CONF_BACKEND_NAME = "backend_name"
CONF_PAIRED_AT = "paired_at"

# Lo que la ronda 4 guarda en la entrada, y que la ronda 5 necesita para deshacer
# exactamente lo que se creó. El usuario se busca SIEMPRE por este id y nunca por nombre:
# el dueño de la casa puede renombrarlo desde Ajustes → Personas.
CONF_USER_ID = "user_id"
CONF_REFRESH_TOKEN_ID = "refresh_token_id"
# El backend guardó las credenciales pero no las usa porque su .env manda (sección 3.5).
CONF_CREDENTIALS_UNUSED = "credentials_unused"
