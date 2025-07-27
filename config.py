import os

MYSQL_HOST = os.getenv("MYSQL_HOST", "maglev.proxy.rlwy.net")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", 57881))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "FXVtuNJpOuvQvuRESiMRXgqlPXRIKEbg")
MYSQL_DB = os.getenv("MYSQL_DATABASE", "railway")
