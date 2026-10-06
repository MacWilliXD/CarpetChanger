"""Genera el icono y compila dist/CarpetChanger.exe (portable, un solo archivo).

Uso:  python build.py
Requiere:  pip install -r requirements.txt

Medidas contra falsos positivos de antivirus / SmartScreen:
  * metadatos de versión completos en el .exe (producto, empresa, descripción...);
  * sin compresión UPX (los ejecutables empaquetados con UPX se marcan a menudo);
  * en GitHub Actions el bootloader de PyInstaller se compila desde el código fuente
    (ver .github/workflows/release.yml), en vez de usar el precompilado que comparten
    miles de ejecutables.
"""
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "assets", "icon.ico")
BUILD = os.path.join(HERE, "build")
SOURCE = os.path.join(HERE, "carpetchanger.pyw")

AUTHOR = "MacWilliXD"
DESCRIPTION = "CarpetChanger - cambia entre versiones de un juego renombrando carpetas"
URL = "https://github.com/MacWilliXD/CarpetChanger"


def app_version():
    with open(SOURCE, encoding="utf-8") as f:
        return re.search(r'^__version__ = "([\d.]+)"', f.read(), re.M).group(1)


def make_icon():
    s = 512
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((16, 16, s - 16, s - 16), radius=112, fill=(31, 106, 165))
    # carpeta
    d.rounded_rectangle((96, 120, 250, 190), radius=22, fill=(240, 180, 60))
    d.rounded_rectangle((96, 160, 416, 400), radius=28, fill=(255, 205, 90))
    # flechas de intercambio
    blue = (31, 106, 165)
    d.line((170, 240, 330, 240), fill=blue, width=26)
    d.polygon([(360, 240), (318, 206), (318, 274)], fill=blue)
    d.line((182, 322, 342, 322), fill=blue, width=26)
    d.polygon([(152, 322), (194, 288), (194, 356)], fill=blue)
    img.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def make_version_file(version):
    nums = tuple(int(x) for x in (version.split(".") + ["0"] * 4)[:4])
    path = os.path.join(BUILD, "version_info.txt")
    os.makedirs(BUILD, exist_ok=True)
    strings = {
        "CompanyName": AUTHOR,
        "FileDescription": DESCRIPTION,
        "FileVersion": version,
        "InternalName": "CarpetChanger",
        "LegalCopyright": f"© {AUTHOR}",
        "OriginalFilename": "CarpetChanger.exe",
        "ProductName": "CarpetChanger",
        "ProductVersion": version,
        "Comments": URL,
    }
    entries = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040a04b0', [
          {entries}])]),
    VarFileInfo([VarStruct('Translation', [0x040a, 1200])])
  ]
)
""")
    return path


def build():
    version = app_version()
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--noupx",
        "--name", "CarpetChanger",
        "--icon", ICO,
        "--version-file", make_version_file(version),
        "--add-data", f"{ICO}{os.pathsep}.",
        "--collect-data", "customtkinter",
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", BUILD,
        "--specpath", BUILD,
        SOURCE,
    ])
    print(f"\nListo: {os.path.join(HERE, 'dist', 'CarpetChanger.exe')}  (v{version})")


if __name__ == "__main__":
    make_icon()
    build()
