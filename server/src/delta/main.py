from rich import pretty, print, traceback

pretty.install()
traceback.install()

from delta.app.config import app_settings

# from delta.app.tracing.logging import

if __name__ == "__main__":
    print(app_settings.model_dump_json())
