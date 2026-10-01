#!/usr/bin/env python3
"""Add the Sameer app to a checkout of the official StackChan firmware.

Usage: add_sameer_app.py <path-to-StackChan/firmware> [server_url] [device_token]
Idempotent: running it twice leaves the firmware unchanged the second time.
"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KCONFIG_MENU = '''
menu "Sameer"

config SAMEER_SERVER_URL
    string "Sameer server base URL"
    default "https://sameer-ai-agent-3.onrender.com"
    help
        Base URL of the Sameer Flask server, without a trailing slash.

config SAMEER_DEVICE_TOKEN
    string "Device token sent as X-Device-Token"
    default ""
    help
        Must match SAMEER_DEVICE_TOKEN on the server when the server sets one.

endmenu
'''


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        sys.exit(f"Could not find the expected code in {path}; the StackChan firmware has changed.")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main():
    firmware = Path(sys.argv[1]).resolve()
    main_dir = firmware / "main"

    shutil.copytree(HERE / "app_sameer", main_dir / "apps" / "app_sameer", dirs_exist_ok=True)

    replace_once(
        main_dir / "apps" / "apps.h",
        '#include "app_dance/app_dance.h"',
        '#include "app_dance/app_dance.h"\n#include "app_sameer/app_sameer.h"',
    )
    # Installed last so the launcher indices other apps use for warm reboot stay the same.
    replace_once(
        main_dir / "main.cpp",
        "GetMooncake().installApp(std::make_unique<AppSetup>());",
        "GetMooncake().installApp(std::make_unique<AppSetup>());\n"
        "        GetMooncake().installApp(std::make_unique<AppSameer>());",
    )

    kconfig = main_dir / "Kconfig.projbuild"
    if "SAMEER_SERVER_URL" not in kconfig.read_text(encoding="utf-8"):
        kconfig.write_text(KCONFIG_MENU + kconfig.read_text(encoding="utf-8"), encoding="utf-8")

    overrides = []
    if len(sys.argv) > 2 and sys.argv[2]:
        overrides.append(f'CONFIG_SAMEER_SERVER_URL="{sys.argv[2]}"')
    if len(sys.argv) > 3 and sys.argv[3]:
        overrides.append(f'CONFIG_SAMEER_DEVICE_TOKEN="{sys.argv[3]}"')
    if overrides:
        (firmware / "sdkconfig.defaults.local").write_text("\n".join(overrides) + "\n", encoding="utf-8")

    print(f"Sameer app added to {firmware}")


if __name__ == "__main__":
    main()
