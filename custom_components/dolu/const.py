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
