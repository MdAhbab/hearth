# PyInstaller spec — build from the repo root on the target OS:
#   .venv/bin/pyinstaller packaging/hearth.spec --noconfirm
# Produces dist/Hearth.app on macOS, dist/Hearth/Hearth.exe on Windows,
# dist/Hearth/Hearth on Linux. PyInstaller does not cross-compile: build on
# each platform you ship for.

import sys

block_cipher = None

a = Analysis(
    ["../src/hearth/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # keyring discovers backends dynamically
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.chainer",
        # google api client pulls these lazily
        "googleapiclient.discovery",
        "google_auth_oauthlib.flow",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Hearth",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="Hearth")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Hearth.app",
        bundle_identifier="com.hearth.assistant",
        info_plist={
            "CFBundleName": "Hearth",
            "CFBundleDisplayName": "Hearth",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "13.0",
            # macOS permission prompts show these explanations:
            "NSCalendarsUsageDescription":
                "Hearth reads your calendar and, only with your approval, "
                "creates or changes events.",
            "NSCalendarsFullAccessUsageDescription":
                "Hearth reads your calendar and, only with your approval, "
                "creates or changes events.",
            "NSAppleEventsUsageDescription":
                "Hearth can read the active browser tab title and URL when "
                "you enable the Browser permission.",
        },
    )
