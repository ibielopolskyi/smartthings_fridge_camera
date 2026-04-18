"""Constants for the Samsung FamilyHub Fridge integration."""

DOMAIN = "samsung_familyhub_fridge"
CID = "5Hic3rk1FP"
DEFAULT_TIMEOUT = 10

# Config entry `data` keys
CONF_AUTH_MODE = "auth_mode"
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_LINKED_SMARTTHINGS_ENTRY_ID = "linked_smartthings_entry_id"
CONF_SAMSUNG_IOT_REFRESH_TOKEN = "samsung_iot_refresh_token"
CONF_SAMSUNG_IOT_AUTH_SERVER = "samsung_iot_auth_server"

# Auth mode values
AUTH_MODE_OAUTH = "oauth"   # reuse HA core smartthings OAuth2 credentials
AUTH_MODE_PAT = "pat"       # legacy: raw SmartThings Personal Access Token

# Domain of the HA core SmartThings integration we piggyback on
SMARTTHINGS_DOMAIN = "smartthings"
