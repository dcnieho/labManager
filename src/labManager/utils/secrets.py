from dotenv import dotenv_values

val = None

def load_secrets(dot_env_file):
    global val
    val = dotenv_values(dot_env_file)
