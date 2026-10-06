"""CarpetChanger: intercambia qué carpeta ocupa el nombre "activo" de un juego.

Cada juego/aplicación de la barra lateral tiene una carpeta con nombre fijo, la
que el juego lee (p. ej. "Skyrim Special Edition"). Las demás versiones esperan
al lado con su propio nombre ("Skyrim Special Edition Base", ...). Activar una
versión devuelve la actual a su nombre y pone la elegida en el nombre activo.
Solo se renombran carpetas: nunca se copia ni se borra nada.
"""
import contextlib
import ctypes
import json
import math
import os
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

import customtkinter as ctk

__version__ = "1.2.0"

APP = "CarpetChanger"
REPO_URL = "https://github.com/MacWilliXD/CarpetChanger"
ADD_GAME_TIP = ("Añade otro juego o programa. Elige la carpeta que usa, la que tiene el nombre original "
                "(p. ej. …\\steamapps\\common\\Skyrim Special Edition).")

BG = ("#f3f4f6", "#1b1c1f")
SIDEBAR = ("#e6e8ec", "#131416")
CARD = ("#ffffff", "#25272b")
BORDER = ("#d9dce1", "#34373c")
DIM = ("#6b7280", "#9aa0a6")
TEXT = ("#111827", "#e8eaed")
GREEN = ("#1d8a5b", "#34c27f")
ACTIVE_BG = ("#e4f5ec", "#15302a")
WARN_BG = ("#fff4ce", "#3a3219")
WARN_BORDER = ("#e8c547", "#8a6d1f")
WARN_FG = ("#6b4f00", "#f3d27a")
RED = ("#c0392b", "#ff7b72")
SELECTED = ("#d4d8de", "#2a2d32")
HOVER = ("#dde0e5", "#202226")
CHIP_BG = ("#cdeedb", "#1d4434")
TIP_BG = ("#1f2937", "#eceef1")  # tooltips en tono invertido para que destaquen
TIP_FG = ("#f9fafb", "#111827")
BUTTON = ("#3b8ed0", "#1f6aa5")  # azul del tema "blue" de CustomTkinter
BUTTON_TEXT = ("#dce4ee", "#dce4ee")


def F(size, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def pick(color):
    """Color (claro, oscuro) → el que toca con el tema actual."""
    if isinstance(color, (tuple, list)):
        return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
    return color


def mix(a, b, t):
    """Interpola dos colores #rrggbb."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return canvas.create_polygon(pts, smooth=True, **kw)


# --------------------------------------------------------------------------- #
#  Rutas y configuración (portable: junto al .exe)
# --------------------------------------------------------------------------- #
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource(name):
    """Archivo empaquetado: en el .exe va en la raíz del bundle; en desarrollo, en assets/."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, name)
    return os.path.join(app_dir(), "assets", name)


def pick_config():
    """Junto al ejecutable; si ahí no se puede escribir, en %APPDATA%."""
    d = app_dir()
    try:
        probe = os.path.join(d, ".cc_write_test")
        open(probe, "w").close()
        os.remove(probe)
    except OSError:
        d = os.path.join(os.environ.get("APPDATA", d), APP)
        os.makedirs(d, exist_ok=True)
    return os.path.join(d, "carpetchanger.json")


CONFIG = pick_config()


class CCError(Exception):
    pass


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        os.replace(CONFIG, CONFIG + ".bak")
        data = {}
    data.setdefault("games", [])
    return data


def save_config(data):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG)


def same(a, b):
    return os.path.normcase(a) == os.path.normcase(b)


def is_lock_error(e):
    return isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32)


def explain(e):
    if isinstance(e, CCError):
        return str(e)
    if is_lock_error(e):
        return ("Windows no dejó renombrar la carpeta porque algún programa tiene algo abierto "
                "dentro (un archivo o una subcarpeta como Data).\n\n"
                "Suele ser el juego, Steam u otro launcher, un gestor de mods (MO2, Vortex...), "
                "un editor (Creation Kit, xEdit, VS Code...), una consola o una ventana del "
                "Explorador dentro de esa carpeta.\n\nDetalle: " + str(e))
    return str(e)


# Variables que PyInstaller pone en el entorno del .exe. Si las hereda otro programa que lancemos,
# apuntan a nuestra carpeta temporal (que se borra al cerrar). Con el Explorador es grave: todo lo
# que se abra después desde él las hereda, y CarpetChanger.exe falla al arrancar ("Failed to load
# Python DLL"), igual que cualquier otro programa que use Tcl/Tk.
_LEAKED_VARS = ("_PYI_", "_MEIPASS", "TCL_LIBRARY", "TK_LIBRARY", "PYINSTALLER_")


def clean_env():
    """Copia del entorno sin las variables internas de PyInstaller, para lanzar otros programas."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(_LEAKED_VARS)}


@contextlib.contextmanager
def clean_environ():
    """Como clean_env(), para llamadas que heredan el entorno del proceso (os.startfile)."""
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.upper().startswith(_LEAKED_VARS)}
    try:
        yield
    finally:
        os.environ.update(saved)


def user_default_env():
    """Entorno de un inicio de sesión recién hecho (el que recibe el Explorador al arrancar Windows)."""
    from ctypes import wintypes as W
    try:
        advapi = ctypes.WinDLL("advapi32")
        userenv = ctypes.WinDLL("userenv")
        k32 = ctypes.WinDLL("kernel32")
        advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
        userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.BOOL]
        userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.argtypes = [W.HANDLE]
        token, block = W.HANDLE(), ctypes.c_void_p()
        if not advapi.OpenProcessToken(W.HANDLE(-1), 0x0008 | 0x0002, ctypes.byref(token)):
            raise OSError("OpenProcessToken")
        try:
            if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
                raise OSError("CreateEnvironmentBlock")
            env, addr = {}, block.value
            while True:
                s = ctypes.wstring_at(addr)
                if not s:
                    break
                addr += (len(s) + 1) * 2
                k, sep, v = s.partition("=")
                if k and sep:
                    env[k] = v
            userenv.DestroyEnvironmentBlock(block)
        finally:
            k32.CloseHandle(token)
        if not any(k.upper() == "PATH" for k in env):
            raise OSError("entorno vacío")
        return env
    except Exception:
        return clean_env()


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class _HandleEntry(ctypes.Structure):
    _fields_ = [("object", ctypes.c_void_p), ("pid", ctypes.c_size_t), ("handle", ctypes.c_size_t),
                ("access", ctypes.c_ulong), ("backtrace", ctypes.c_ushort), ("type_index", ctypes.c_ushort),
                ("attributes", ctypes.c_ulong), ("reserved", ctypes.c_ulong)]


def find_lockers(paths, release=()):
    """Devuelve ({pid: nombre.exe}, cerrados) de los procesos con algo abierto dentro de `paths`.

    Los identificadores que pertenezcan a procesos cuyo nombre esté en `release` se cierran
    (así se suelta lo que explorer.exe deja vigilando). Solo ve procesos del usuario sin
    elevar: los de administrador/sistema no se pueden inspeccionar sin permisos.
    """
    release = {r.lower() for r in release}
    import msvcrt
    from ctypes import wintypes as W
    nt = ctypes.WinDLL("ntdll")
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = W.HANDLE
    k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k32.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
    k32.GetFileType.argtypes = [W.HANDLE]
    k32.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    k32.CloseHandle.argtypes = [W.HANDLE]

    prefixes = [os.path.normcase(os.path.abspath(p)) for p in paths]
    me = os.getpid()
    with open(sys.executable, "rb") as probe:  # para saber qué tipo de objeto es "File"
        probe_handle = msvcrt.get_osfhandle(probe.fileno())
        size = 1 << 22
        while True:
            buf = ctypes.create_string_buffer(size)
            st = nt.NtQuerySystemInformation(64, buf, size, ctypes.byref(W.ULONG())) & 0xFFFFFFFF
            if st == 0xC0000004:
                size *= 2
                continue
            if st != 0:
                return {}, 0
            break
    count = ctypes.c_size_t.from_buffer(buf).value
    entries = (_HandleEntry * count).from_buffer(buf, 2 * ctypes.sizeof(ctypes.c_size_t))
    file_type = next((e.type_index for e in entries if e.pid == me and e.handle == probe_handle), None)

    procs, found, closed = {}, {}, 0
    try:
        for e in entries:
            if file_type is not None and e.type_index != file_type:
                continue
            if e.pid in (0, 4) or (e.pid in found and found[e.pid].lower() not in release):
                continue
            if e.pid not in procs:
                procs[e.pid] = k32.OpenProcess(0x0040 | 0x1000, False, e.pid)  # DUP_HANDLE | QUERY_LIMITED
            ph = procs[e.pid]
            if not ph:
                continue
            dup = W.HANDLE()
            if not k32.DuplicateHandle(ph, e.handle, W.HANDLE(-1), ctypes.byref(dup), 0, False, 2):
                continue
            try:
                if k32.GetFileType(dup) != 1:  # solo archivos/carpetas de disco
                    continue
                b = ctypes.create_unicode_buffer(1024)
                if not k32.GetFinalPathNameByHandleW(dup, b, 1024, 0):
                    continue
                p = os.path.normcase(b.value[4:] if b.value.startswith("\\\\?\\") else b.value)
                if any(p == x or p.startswith(x + os.sep) for x in prefixes):
                    if e.pid not in found:
                        nb, n = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
                        k32.QueryFullProcessImageNameW(ph, 0, nb, ctypes.byref(n))
                        found[e.pid] = os.path.basename(nb.value) or f"PID {e.pid}"
                    if found[e.pid].lower() in release:
                        # DUPLICATE_CLOSE_SOURCE: cierra el identificador dentro del otro proceso
                        if k32.DuplicateHandle(ph, e.handle, None, None, 0, False, 1):
                            closed += 1
            finally:
                k32.CloseHandle(dup)
    finally:
        for h in procs.values():
            if h:
                k32.CloseHandle(h)
    return found, closed


_CLOSE_WINDOWS_PS = r"""
$prefixes = $env:CC_PATHS -split '\|' | % { $_.TrimEnd('\').ToLower() }
$sh = New-Object -ComObject Shell.Application
foreach ($w in @($sh.Windows())) {
    try { $p = $w.Document.Folder.Self.Path } catch { continue }
    if (-not $p) { continue }
    $p = $p.TrimEnd('\').ToLower()
    foreach ($x in $prefixes) { if ($p -eq $x -or $p.StartsWith($x + '\')) { $w.Quit(); break } }
}
"""


def free_from_explorer(paths):
    """Cierra las ventanas del Explorador que muestran esas carpetas (o algo dentro) y suelta
    los identificadores que explorer.exe mantiene abiertos en ellas. Devuelve cuántos soltó."""
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _CLOSE_WINDOWS_PS],
                       env=dict(clean_env(), CC_PATHS="|".join(paths)), capture_output=True, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return find_lockers(paths, release=("explorer.exe",))[1]
    except Exception:
        return 0


def set_icon(win):
    ico = resource("icon.ico")
    if os.path.exists(ico):
        try:
            win.iconbitmap(ico)
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- #
#  Lógica de un juego
# --------------------------------------------------------------------------- #
class Game:
    """Envuelve el dict guardado en el JSON:
    {"name", "base", "active_name", "variants": [{"name", "folder"}], "active"}
    """

    def __init__(self, d):
        self.d = d
        d.setdefault("variants", [])
        d.setdefault("active", None)

    name = property(lambda s: s.d["name"])
    base = property(lambda s: s.d["base"])
    active_name = property(lambda s: s.d["active_name"])
    variants = property(lambda s: s.d["variants"])

    @property
    def active_path(self):
        return os.path.join(self.base, self.active_name)

    def path(self, v):
        return os.path.join(self.base, v["folder"])

    def find(self, name):
        return next((v for v in self.variants if v["name"] == name), None)

    def state(self):
        """Mira el disco y devuelve (variante_activa | None, aviso | None).

        Una variante está activa cuando su carpeta guardada no existe y la carpeta
        activa sí. Si el disco no coincide con lo guardado, manda el disco.
        """
        if not os.path.isdir(self.active_path):
            self.d["active"] = None
            return None, "missing"
        missing = [v for v in self.variants if not os.path.isdir(self.path(v))]
        if not missing:
            self.d["active"] = None
            return None, "unregistered"
        if len(missing) == 1:
            self.d["active"] = missing[0]["name"]
            return missing[0], None
        cur = self.find(self.d.get("active"))
        if cur in missing:
            return cur, "ambiguous"
        self.d["active"] = None
        return None, "ambiguous"

    def activate(self, target):
        cur, _ = self.state()
        if cur is target:
            return
        src = self.path(target)
        if not os.path.isdir(src):
            raise CCError(f"No se encuentra la carpeta de «{target['name']}»:\n{src}")

        parked = None
        if os.path.isdir(self.active_path):
            if cur is None:
                raise CCError("La carpeta que está en uso no tiene nombre todavía.\n"
                              "Pónselo primero para no perderla.")
            parked = self.path(cur)
            if os.path.exists(parked):
                raise CCError(f"No puedo guardar «{cur['name']}» porque ya existe:\n{parked}")
            os.rename(self.active_path, parked)

        try:
            os.rename(src, self.active_path)
        except OSError as e:
            if parked:
                try:
                    os.rename(parked, self.active_path)
                except OSError:
                    raise CCError(f"{explain(e)}\n\nAdemás no se pudo deshacer: «{cur['name']}» "
                                  f"quedó guardada en:\n{parked}") from e
            raise
        self.d["active"] = target["name"]


# --------------------------------------------------------------------------- #
#  Formularios
# --------------------------------------------------------------------------- #
class Form(ctk.CTkToplevel):
    """fields: lista de (clave, etiqueta, valor_inicial, examinar_carpeta)."""

    def __init__(self, parent, title, fields, hint=None, ok_text="Guardar"):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.configure(fg_color=BG)
        self.result = None
        self.vars = {}

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)
        ctk.CTkLabel(body, text=title, font=F(18, "bold"), text_color=TEXT).pack(anchor="w", pady=(0, 14))

        self.first = None
        for key, label, initial, browse in fields:
            ctk.CTkLabel(body, text=label, font=F(12), text_color=DIM).pack(anchor="w")
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=(2, 12))
            var = tk.StringVar(value=initial or "")
            ent = ctk.CTkEntry(row, textvariable=var, width=330 if browse else 440, height=36, font=F(13))
            ent.pack(side="left", fill="x", expand=True)
            if browse:
                tip(ctk.CTkButton(row, text="Examinar…", width=100, height=36, font=F(13),
                                  command=lambda v=var: self._browse(v)),
                    "Elige la carpeta con el explorador de archivos.").pack(side="left", padx=(8, 0))
            self.first = self.first or ent
            self.vars[key] = var

        if hint:
            ctk.CTkLabel(body, text=hint, font=F(12), text_color=DIM, wraplength=440,
                         justify="left").pack(anchor="w", pady=(0, 10))

        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(btns, text=ok_text, width=120, height=36, font=F(13, "bold"),
                      command=self._ok).pack(side="right")
        ctk.CTkButton(btns, text="Cancelar", width=100, height=36, font=F(13), fg_color="transparent",
                      border_width=1, border_color=BORDER, text_color=TEXT, hover_color=HOVER,
                      command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.after(120, self._focus)
        self.after(250, lambda: self.winfo_exists() and set_icon(self))
        self.wait_window()

    def _focus(self):
        if not self.winfo_exists():
            return
        self.grab_set()
        self.lift()
        if self.first:
            self.first.focus_set()
            try:
                self.first._entry.select_range(0, "end")
            except (AttributeError, tk.TclError):
                pass

    def _browse(self, var):
        cur = var.get()
        start = cur if os.path.isdir(cur) else os.path.dirname(cur)
        p = filedialog.askdirectory(parent=self, initialdir=start or None, mustexist=False)
        if p:
            var.set(os.path.normpath(p))

    def _ok(self):
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()


def ask_form(parent, title, fields, validate, hint=None, ok_text="Guardar"):
    """Repite el formulario hasta que validate() no lance CCError. Devuelve dict o None."""
    while True:
        res = Form(parent, title, fields, hint, ok_text).result
        if res is None:
            return None
        try:
            validate(res)
            return res
        except CCError as e:
            messagebox.showerror(APP, str(e), parent=parent)
            fields = [(k, l, res[k], b) for k, l, _, b in fields]


def ghost(master, text, command, width=70, color=None, tooltip=None):
    b = ctk.CTkButton(master, text=text, command=command, width=width, height=32, font=F(12),
                      fg_color="transparent", hover_color=HOVER, text_color=color or DIM)
    if tooltip:
        tip(b, tooltip)
    return b


class Tooltip:
    """Globo de ayuda que aparece al dejar el ratón quieto sobre un widget.

    `text` puede ser un texto o una función que lo devuelva (para datos que cambian).
    """
    DELAY_MS = 450

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.win = self._job = None
        for seq, fn in (("<Enter>", self._schedule), ("<Leave>", self._leave), ("<ButtonPress>", self.hide)):
            widget.bind(seq, fn, add="+")
        tk.Misc.bind(widget, "<Destroy>", self.hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        if self.win is None:
            self._job = self.widget.after(self.DELAY_MS, self.show)

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _leave(self, _=None):
        # Los widgets de CustomTkinter están hechos de varias piezas: al pasar de una a otra llega un
        # <Leave> aunque el ratón siga encima. Solo se oculta si de verdad ha salido.
        try:
            self.widget.after(40, self._hide_if_outside)
        except tk.TclError:
            pass

    def _hide_if_outside(self):
        try:
            under = self.widget.winfo_containing(*self.widget.winfo_pointerxy())
        except (tk.TclError, KeyError):
            under = None
        if under is None or not str(under).startswith(str(self.widget)):
            self.hide()

    def show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        self.win = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, justify="left", wraplength=340, padx=10, pady=7, bg=pick(TIP_BG),
                 fg=pick(TIP_FG), font=("Segoe UI", 9), bd=0).pack()
        tw.update_idletasks()
        x, y = self.widget.winfo_pointerxy()
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        sw, sh = tw.winfo_screenwidth(), tw.winfo_screenheight()
        x = min(x + 14, sw - w - 4)
        y = y + 20 if y + 20 + h < sh else y - h - 10
        tw.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def hide(self, _=None):
        self._cancel()
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None


def tip(widget, text):
    """Añade un tooltip a un widget y lo devuelve (para encadenar con .pack())."""
    widget._cc_tooltip = Tooltip(widget, text)
    return widget


class HelpDialog(ctk.CTkToplevel):
    """Guía: para qué sirve la app, cómo funciona y qué hace cada cosa."""

    def __init__(self, parent):
        super().__init__(parent)
        self.withdraw()
        self.title("Cómo funciona CarpetChanger")
        self.geometry("700x640")
        self.minsize(560, 420)
        self.transient(parent)
        self.configure(fg_color=BG)
        self.bind("<Escape>", lambda e: self.destroy())

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=24, pady=(6, 18))
        ctk.CTkButton(foot, text="Entendido", width=120, height=36, font=F(13, "bold"),
                      command=self.destroy).pack(side="right")
        tip(ghost(foot, "Ver en GitHub", self._open_repo, 120, TEXT),
            "Abre la página del proyecto: descargas, novedades y el README completo.").pack(side="left")

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=(24, 8), pady=(20, 0))
        self.body = body

        ctk.CTkLabel(body, text="Cómo funciona CarpetChanger", font=F(22, "bold"),
                     text_color=TEXT).pack(anchor="w")
        self._p("Ten varias versiones de un mismo juego (o programa) y cambia cuál usa con un clic: "
                "por ejemplo una con tu lista de mods, otra limpia y otra para multijugador.")

        self._h("La idea")
        self._p("Muchos juegos solo leen una carpeta con un nombre fijo; Skyrim, por ejemplo, siempre arranca "
                "desde «Skyrim Special Edition». CarpetChanger hace que la versión que quieres usar ocupe ese "
                "nombre y que las demás esperen al lado con el suyo propio:")
        diagram = ctk.CTkFrame(body, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        diagram.pack(fill="x", pady=(4, 6), padx=(0, 12))
        ctk.CTkLabel(diagram, justify="left", anchor="w", text_color=TEXT, font=("Consolas", 12), text=(
            "Antes                                  Después de activar «SkyMP»\n"
            "Skyrim Special Edition       ← en uso  Skyrim Special Edition Modlist\n"
            "Skyrim Special Edition SkyMP           Skyrim Special Edition       ← en uso\n"
            "Skyrim Special Edition Vanilla         Skyrim Special Edition Vanilla")
        ).pack(anchor="w", padx=16, pady=12)
        self._p("Solo se renombran carpetas: nunca se copia, mueve ni borra nada, así que el cambio es "
                "instantáneo aunque el juego ocupe decenas de GB.")

        self._h("Primeros pasos")
        self._steps([
            "Pulsa «＋ Añadir juego» y elige la carpeta que usa el juego, la que tiene el nombre original "
            "(p. ej. …\\steamapps\\common\\Skyrim Special Edition).",
            "Si junto a ella hay otras copias que empiezan por el mismo nombre (p. ej. «Skyrim Special "
            "Edition Vanilla»), la app te propone añadirlas.",
            "Ponle nombre a la versión que está en uso ahora; así sabrá cómo llamar a su carpeta cuando la "
            "guardes.",
            "Pulsa «Activar» en la versión que quieras usar y abre el juego como siempre.",
        ])

        self._h("Qué hace cada cosa")
        for name, text in (
            ("Activar", "Pone esa versión en uso. La que estaba en uso vuelve a su nombre y la elegida pasa a "
                        "llamarse como espera el juego. También vale hacer doble clic sobre la tarjeta."),
            ("Abrir", "Abre la carpeta de esa versión en el Explorador."),
            ("Editar", "Cambia el nombre que ves o el nombre de su carpeta cuando está guardada (si está "
                       "guardada, también se renombra en el disco)."),
            ("Quitar", "La saca de la lista. La carpeta no se borra."),
            ("＋ Añadir versión", "Añade otra copia del juego. Tiene que estar en la misma carpeta que la "
                                 "versión en uso (p. ej. todas dentro de steamapps\\common)."),
            ("Editar / Quitar juego", "Lo mismo para el juego entero. Tampoco toca ninguna carpeta."),
        ):
            self._item(name, text)
        self._p("Consejo: deja el ratón quieto sobre cualquier botón o nombre para ver qué hace.")

        self._h("Si algo no va")
        self._item("«La carpeta está en uso»",
                   "Windows no deja renombrar una carpeta si un programa tiene algo abierto dentro. La app "
                   "cierra sola lo que deja el Explorador y, si aun así no puede, te dice qué programa la usa "
                   "(suele ser el juego, Steam, un gestor de mods como MO2 o Vortex, o un editor).")
        self._item("«La versión actual no tiene nombre»",
                   "La carpeta en uso todavía no tiene nombre de guardado. Pulsa «Ponerle nombre…» y elige uno.")
        self._item("Una versión sale en rojo",
                   "No se encuentra su carpeta: puede que se haya renombrado o movido fuera de la app. Usa "
                   "«Editar» para corregir el nombre de su carpeta o «Quitar» para sacarla de la lista.")
        self._item("Antes de abrir el juego",
                   "Comprueba arriba qué versión está «en uso»: es la que el juego cargará.")

        self._h("Tus datos")
        self._p(f"La configuración se guarda en un archivo junto al programa:\n{CONFIG}\n"
                "La app es portable: puedes moverla a otra carpeta o a un USB (lleva ese archivo con ella).")

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + 40
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        self.after(120, lambda: self.winfo_exists() and (self.lift(), self.focus_force()))
        self.after(250, lambda: self.winfo_exists() and set_icon(self))

    def _h(self, text):
        ctk.CTkLabel(self.body, text=text, font=F(16, "bold"), text_color=TEXT).pack(anchor="w", pady=(18, 4))

    def _p(self, text):
        ctk.CTkLabel(self.body, text=text, font=F(13), text_color=DIM, justify="left", anchor="w",
                     wraplength=600).pack(anchor="w", fill="x", pady=2)

    def _steps(self, steps):
        for n, text in enumerate(steps, 1):
            row = ctk.CTkFrame(self.body, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
            row.pack(fill="x", pady=3, padx=(0, 12))
            ctk.CTkLabel(row, text=str(n), width=32, height=32, corner_radius=16, fg_color=BUTTON,
                         text_color="white", font=F(13, "bold")).pack(side="left", padx=12, pady=10)
            ctk.CTkLabel(row, text=text, font=F(13), text_color=TEXT, justify="left", anchor="w",
                         wraplength=540).pack(side="left", padx=(0, 14), fill="x")

    def _item(self, name, text):
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text=name, font=F(13, "bold"), text_color=TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(row, text=text, font=F(13), text_color=DIM, justify="left", anchor="w",
                     wraplength=600).pack(anchor="w", fill="x")

    def _open_repo(self):
        with clean_environ():
            os.startfile(REPO_URL)


# --------------------------------------------------------------------------- #
#  Vista de un juego
# --------------------------------------------------------------------------- #
class GameView(ctk.CTkFrame):
    SWAP_SECONDS = 0.7    # vuelo de las tarjetas al cambiar de versión
    PULSE_SECONDS = 0.9   # resaltado de las dos tarjetas que cambiaron

    def __init__(self, master, app, game):
        super().__init__(master, fg_color="transparent")
        self.app, self.game = app, game
        self.sig = None
        self.top_card = None
        self.row_cards = {}
        self.activate_btns = {}
        self.recent = None  # (activada, guardada) tras un cambio, para señalarlas unos segundos
        self._chips = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=(0, 46))  # a la derecha queda el «?» de ayuda
        ctk.CTkLabel(head, text=game.name, font=F(26, "bold"), text_color=TEXT).pack(side="left")
        ghost(head, "Quitar juego", self.remove_game, 100, RED,
              "Quita este juego de la lista. No se borra ni se renombra ninguna carpeta.").pack(side="right")
        ghost(head, "Editar juego", self.edit_game, 100, None,
              "Cambia el nombre de este juego en la lista o la carpeta que usa.").pack(side="right", padx=4)
        tip(ctk.CTkLabel(self, text=f"Carpeta que usa el juego:  {game.active_path}", font=F(12),
                         text_color=DIM, anchor="w"),
            "Es la carpeta que abre el juego. La versión «en uso» es la que ocupa este nombre; "
            "las demás esperan al lado con el suyo.").pack(fill="x", pady=(0, 14))

        self.top = ctk.CTkFrame(self, fg_color="transparent")
        self.top.pack(fill="x")

        sec = ctk.CTkFrame(self, fg_color="transparent")
        sec.pack(fill="x", pady=(22, 6))
        self.sec_label = tip(ctk.CTkLabel(sec, text="Otras versiones", font=F(15, "bold"), text_color=TEXT),
                             "Versiones guardadas: esperan con su propio nombre hasta que las actives.")
        self.sec_label.pack(side="left")
        self.add_btn = tip(ctk.CTkButton(sec, text="＋  Añadir versión", width=150, height=32,
                                         font=F(12, "bold"), command=self.add_variant),
                           f"Añade otra copia del juego. Tiene que estar en la misma carpeta que la versión "
                           f"en uso:\n{game.base}")
        self.add_btn.pack(side="right")

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True)

        self.refresh(force=True)

    # -- dibujo ------------------------------------------------------------ #
    def refresh(self, force=False):
        g = self.game
        cur, warn = g.state()
        exists = tuple(os.path.isdir(g.path(v)) for v in g.variants)
        sig = (cur and cur["name"], warn, tuple((v["name"], v["folder"]) for v in g.variants), exists)
        save_config(self.app.data)
        if sig == self.sig and not force:
            return
        self.sig = sig
        for w in self.top.winfo_children() + self.list.winfo_children():
            w.destroy()
        self.top_card, self.row_cards, self.activate_btns, self._chips = None, {}, {}, []
        self._draw_current(cur, warn)
        self._draw_list(cur, exists)
        self.app.render_sidebar()

    def _draw_current(self, cur, warn):
        g = self.game
        if cur:
            card = ctk.CTkFrame(self.top, fg_color=ACTIVE_BG, border_color=GREEN, border_width=2, corner_radius=14)
            card.pack(fill="x")
            self.top_card = card
            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=22, pady=18)
            line = ctk.CTkFrame(left, fg_color="transparent")
            line.pack(anchor="w")
            in_use = (f"Esta es la versión que cargará el juego al abrirlo. Ahora mismo su carpeta se llama "
                      f"«{g.active_name}».")
            tip(ctk.CTkLabel(line, text="●  EN USO AHORA", font=F(11, "bold"), text_color=GREEN),
                in_use).pack(side="left")
            if self.recent and self.recent[0] == cur["name"] and self.recent[1]:
                self._chip(line, f"⇅  sustituye a «{self.recent[1]}»",
                           f"Acabas de cambiar: «{cur['name']}» ha sustituido a «{self.recent[1]}», "
                           f"que ha vuelto a su carpeta.")
            tip(ctk.CTkLabel(left, text=cur["name"], font=F(28, "bold"), text_color=TEXT), in_use).pack(anchor="w")
            tip(ctk.CTkLabel(left, text=f"Cuando cambies, se guardará como «{cur['folder']}»", font=F(12),
                             text_color=DIM),
                f"Al activar otra versión, la carpeta «{g.active_name}» se renombrará a «{cur['folder']}» "
                f"para guardarla. Puedes cambiar ese nombre con «Editar».").pack(anchor="w")
            right = ctk.CTkFrame(card, fg_color="transparent")
            right.pack(side="right", padx=18)
            ghost(right, "Abrir carpeta", lambda: self.open_path(g.active_path), 110, TEXT,
                  "Abre en el Explorador la carpeta de la versión en uso.").pack(pady=2)
            ghost(right, "Editar", lambda: self.edit_variant(cur), 110, TEXT,
                  "Cambia el nombre de esta versión o cómo se llamará su carpeta cuando la guardes.").pack(pady=2)
            if warn == "ambiguous":
                self._warn("Algunas versiones no se encuentran (marcadas en rojo). Puede que se hayan "
                           "renombrado o movido fuera de la app.")
            return

        if warn == "unregistered":
            self._warn(f"La carpeta «{g.active_name}» está en uso pero aún no tiene nombre. Pónselo para "
                       "poder guardarla cuando cambies a otra versión.",
                       title="La versión actual no tiene nombre",
                       button=("Ponerle nombre…", self.register_active,
                               "Dale un nombre a la versión que está en uso para poder guardarla cuando "
                               "cambies a otra."))
        elif warn == "missing":
            self._warn("Todas las versiones están guardadas, así que ahora mismo el juego no encontrará "
                       "su carpeta. Activa una de abajo.", title="Ninguna versión en uso")
        else:
            self._warn("Varias versiones no se encuentran y no sé cuál está en uso. Revisa las marcadas "
                       "en rojo: puede que se hayan renombrado o movido fuera de la app.",
                       title="No sé qué versión está en uso")

    def _warn(self, text, title=None, button=None):
        card = ctk.CTkFrame(self.top, fg_color=WARN_BG, border_color=WARN_BORDER, border_width=1, corner_radius=14)
        card.pack(fill="x", pady=(10, 0) if self.top.winfo_children()[:-1] else 0)
        left = ctk.CTkFrame(card, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=20, pady=14)
        if title:
            ctk.CTkLabel(left, text=title, font=F(16, "bold"), text_color=WARN_FG).pack(anchor="w")
        ctk.CTkLabel(left, text=text, font=F(12), text_color=WARN_FG, wraplength=520,
                     justify="left").pack(anchor="w")
        if button:
            tip(ctk.CTkButton(card, text=button[0], command=button[1], width=150, height=36,
                              font=F(13, "bold")), button[2]).pack(side="right", padx=18)

    def _draw_list(self, cur, exists):
        g = self.game
        others = [(v, ok) for v, ok in zip(g.variants, exists) if v is not cur]
        if not others:
            ctk.CTkLabel(self.list, text="Aún no hay otras versiones.\nAñade la carpeta de otra copia del "
                         "juego con «＋ Añadir versión».", font=F(13), text_color=DIM,
                         justify="center").pack(pady=30)
            return
        for v, ok in others:
            row = ctk.CTkFrame(self.list, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
            row.pack(fill="x", pady=4, padx=(0, 4))
            self.row_cards[v["name"]] = row
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=18, pady=12)
            line = ctk.CTkFrame(left, fg_color="transparent")
            line.pack(anchor="w")
            info = (f"Versión guardada. Su carpeta:\n{g.path(v)}\n\nDoble clic o «Activar» para usarla."
                    if ok else
                    f"No se encuentra su carpeta:\n{g.path(v)}\n\nPuede que se haya renombrado o movido "
                    f"fuera de la app. Corrige el nombre con «Editar» o sácala de la lista con «Quitar».")
            tip(ctk.CTkLabel(line, text=v["name"], font=F(16, "bold"), text_color=TEXT if ok else RED),
                info).pack(side="left")
            if self.recent and self.recent[1] == v["name"]:
                self._chip(line, "↓  antes en uso",
                           f"Estaba en uso hasta el último cambio. Su carpeta ha vuelto a llamarse «{v['folder']}».")
            tip(ctk.CTkLabel(left, text=v["folder"] if ok else f"No se encuentra la carpeta «{v['folder']}»",
                             font=F(12), text_color=DIM if ok else RED), info).pack(anchor="w")

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=12)
            ghost(right, "Quitar", lambda v=v: self.remove_variant(v), 64, RED,
                  "Quita esta versión de la lista. La carpeta NO se borra.").pack(side="right")
            ghost(right, "Editar", lambda v=v: self.edit_variant(v), 64, None,
                  "Cambia el nombre de esta versión o el de su carpeta (también se renombra en el disco)."
                  ).pack(side="right")
            ghost(right, "Abrir", lambda v=v: self.open_path(g.path(v)), 64, None,
                  "Abre su carpeta en el Explorador.").pack(side="right")
            if ok:
                btn = tip(ctk.CTkButton(right, text="Activar", width=110, height=36, font=F(13, "bold"),
                                        command=lambda v=v: self.activate(v)),
                          f"Pon «{v['name']}» en uso: la versión actual vuelve a su nombre y esta pasa a "
                          f"llamarse «{g.active_name}», que es la que abre el juego.")
                btn.pack(side="right", padx=(0, 10))
                self.activate_btns[v["name"]] = btn
            else:
                tip(ctk.CTkButton(right, text="Activar", width=110, height=36, font=F(13, "bold"),
                                  state="disabled", fg_color=BORDER),
                    "No se puede activar porque no se encuentra su carpeta.").pack(side="right", padx=(0, 10))
            if ok:
                for w in (row, left, line, *line.winfo_children(), *left.winfo_children()):
                    w.bind("<Double-1>", lambda e, v=v: self.activate(v))

    def _chip(self, master, text, tooltip=None):
        chip = ctk.CTkLabel(master, text=f"  {text}  ", font=F(11, "bold"), text_color=GREEN,
                            fg_color=CHIP_BG, corner_radius=8, height=22)
        if tooltip:
            tip(chip, tooltip)
        chip.pack(side="left", padx=(12, 0))
        self._chips.append(chip)

    # -- animación del cambio ---------------------------------------------- #
    def _rect(self, w):
        x, y = w.winfo_rootx() - self.winfo_rootx(), w.winfo_rooty() - self.winfo_rooty()
        return x, y, x + w.winfo_width(), y + w.winfo_height()

    def _plan_swap(self, v, cur):
        """Calcula de dónde a dónde se mueve cada tarjeta. None si no se puede animar."""
        if cur is None or not self.top_card or not self.top_card.winfo_exists():
            return None
        self.update_idletasks()
        g = self.game
        old_others = [x for x in g.variants if x is not cur]
        new_others = [x for x in g.variants if x is not v]
        cards = [self.row_cards.get(x["name"]) for x in old_others]
        if any(c is None or not c.winfo_exists() for c in cards):
            return None
        slots = [self._rect(c) for c in cards]
        view = self._rect(getattr(self.list, "_parent_frame", self.list))
        if any(r[1] < view[1] - 1 or r[3] > view[3] + 1 for r in slots):
            return None  # alguna tarjeta queda fuera de la vista (lista desplazada)
        top = self._rect(self.top_card)
        flights = []
        for x in g.variants:
            src = top if x is cur else slots[old_others.index(x)]
            dst = top if x is v else slots[new_others.index(x)]
            flights.append((x, src, dst, 1.0 if x is cur else 0.0, 1.0 if x is v else 0.0))
        # la que sube se dibuja la última, por encima de todas
        flights.sort(key=lambda f: (f[0] is v, f[0] is cur))
        return top, view, flights

    def _animate_swap(self, v, cur, done):
        plan = None
        try:
            plan = self._plan_swap(v, cur)
        except tk.TclError:
            pass
        if not plan:
            self._land(v, cur, done)
            return
        top, view, flights = plan
        s = self._get_widget_scaling()
        y0 = top[1]
        stage = tk.Canvas(self, highlightthickness=0, bd=0, bg=pick(BG))
        stage.place(x=0, y=y0, width=self.winfo_width(), height=view[3] - y0)
        tk.Misc.tkraise(stage)  # Canvas.lift() sube elementos del lienzo, no el widget
        header = (self._rect(self.sec_label), self._rect(self.add_btn))
        fonts = {}

        def font(px, bold=False):
            key = (px, bold)
            if key not in fonts:
                fonts[key] = tkfont.Font(family="Segoe UI", size=-max(px, 1), weight="bold" if bold else "normal")
            return fonts[key]

        def shift(r):
            return r[0], r[1] - y0, r[2], r[3] - y0

        def draw_header():
            lbl, btn = shift(header[0]), shift(header[1])
            stage.create_text(lbl[0], (lbl[1] + lbl[3]) / 2, text="Otras versiones", anchor="w",
                              fill=pick(TEXT), font=font(round(15 * s), True))
            round_rect(stage, *btn, 6 * s, fill=pick(BUTTON), outline="")
            stage.create_text((btn[0] + btn[2]) / 2, (btn[1] + btn[3]) / 2, text="＋  Añadir versión",
                              fill=pick(BUTTON_TEXT), font=font(round(12 * s), True))

        def draw_card(x, r, m):
            """m = 0: tarjeta de la lista · m = 1: tarjeta grande «en uso». Valores intermedios mezclan."""
            x1, y1, x2, y2 = shift(r)
            h = y2 - y1
            # La que sube ya no tiene su carpeta (ahora es la activa), pero existía: no pintarla en rojo.
            exists = x is v or os.path.isdir(self.game.path(x))
            round_rect(stage, x1, y1, x2, y2, (12 + 2 * m) * s, width=(1 + m) * s,
                       fill=mix(pick(CARD), pick(ACTIVE_BG), m), outline=mix(pick(BORDER), pick(GREEN), m))
            pad = x1 + (18 + 4 * m) * s
            big = m >= 0.5
            if big:
                stage.create_text(pad, y1 + h * 0.25, text="●  EN USO AHORA", anchor="w",
                                  fill=pick(GREEN), font=font(round(11 * s), True))
                sub = f"Cuando cambies, se guardará como «{x['folder']}»"
            else:
                sub = x["folder"] if exists else f"No se encuentra la carpeta «{x['folder']}»"
            stage.create_text(pad, y1 + h * (0.34 + 0.18 * m), text=x["name"], anchor="w",
                              fill=pick(TEXT if exists else RED), font=font(round((16 + 12 * m) * s), True))
            stage.create_text(pad, y1 + h * (0.68 + 0.08 * m), text=sub, anchor="w",
                              fill=pick(DIM if exists else RED), font=font(round(12 * s)))
            if big:
                cx = x2 - 73 * s
                for frac, label in ((0.35, "Abrir carpeta"), (0.63, "Editar")):
                    stage.create_text(cx, y1 + h * frac, text=label, fill=pick(TEXT), font=font(round(12 * s)))
            else:
                base, cy = x2 - 12 * s, (y1 + y2) / 2
                for i, (label, color) in enumerate((("Quitar", RED), ("Editar", DIM), ("Abrir", DIM))):
                    stage.create_text(base - (32 + 64 * i) * s, cy, text=label, fill=pick(color),
                                      font=font(round(12 * s)))
                right = base - 202 * s
                round_rect(stage, right - 110 * s, cy - 18 * s, right, cy + 18 * s, 6 * s, outline="",
                           fill=pick(BUTTON if exists else BORDER))
                stage.create_text(right - 55 * s, cy, text="Activar", fill=pick(BUTTON_TEXT),
                                  font=font(round(13 * s), True))

        start = time.perf_counter()
        duration = self.SWAP_SECONDS

        def tick():
            if not stage.winfo_exists():
                return
            t = min((time.perf_counter() - start) / duration, 1.0)
            e = ease_in_out(t)
            stage.delete("all")
            draw_header()
            for x, src, dst, m0, m1 in flights:
                r = tuple(a + (b - a) * e for a, b in zip(src, dst))
                draw_card(x, r, m0 + (m1 - m0) * e)
            if t < 1:
                self.after(12, tick)
            else:
                self._land(v, cur, done, stage)

        tick()

    def _land(self, v, cur, done, stage=None):
        """Dibuja el estado nuevo, retira la animación y resalta las dos tarjetas que cambiaron."""
        self.recent = (v["name"], cur["name"] if cur else None)
        self.refresh(force=True)
        self.update_idletasks()
        if stage is not None:
            stage.destroy()
        done()
        top, row = self.top_card, self.row_cards.get(self.recent[1] or "")
        start = time.perf_counter()

        def pulse():
            if not self.winfo_exists():
                return
            t = min((time.perf_counter() - start) / self.PULSE_SECONDS, 1.0)
            k = math.sin(math.pi * t)
            if top and top.winfo_exists():
                top.configure(border_width=round(2 + 3 * k))
            if row and row.winfo_exists():
                row.configure(border_width=2 if t < 1 else 1,
                              border_color=mix(pick(GREEN), pick(BORDER), ease_in_out(t)) if t < 1 else BORDER)
            if t < 1:
                self.after(16, pulse)

        pulse()
        chips = list(self._chips)
        self.after(4500, lambda: self._clear_recent(chips))

    def _clear_recent(self, chips):
        if not self.winfo_exists():
            return
        self.recent = None
        for c in chips:
            if c.winfo_exists():
                c.destroy()

    # -- acciones ---------------------------------------------------------- #
    def activate(self, v):
        g = self.game
        cur, _ = g.state()
        if v is cur:
            return
        if cur is None and os.path.isdir(g.active_path):
            messagebox.showinfo(APP, "Antes de cambiar, ponle un nombre a la versión que está en uso "
                                "ahora, para poder guardarla.", parent=self.app)
            if not self.register_active():
                return
            cur, _ = g.state()
        self._switch(v, cur)

    def _activate_freeing_explorer(self, v):
        """Renombra soltando antes lo que el Explorador tenga abierto en esas carpetas.

        Camino rápido (~0,3 s): soltar sus identificadores. Si sigue bloqueada, un reintento breve
        (Defender o el indexador suelen tenerla solo un instante). Si aun así no, se cierran también
        las ventanas del Explorador en esas carpetas (más lento, usa PowerShell) y último intento.
        Un intento fallido no deja nada a medias: Game.activate deshace lo que hubiera hecho.
        """
        g = self.game
        paths = [g.active_path, g.path(v)]
        try:
            find_lockers(paths, release=("explorer.exe",))
        except Exception:
            pass
        remedies = [lambda: time.sleep(0.15), lambda: free_from_explorer(paths)]
        while True:
            try:
                g.activate(v)
                return
            except OSError as e:
                if not is_lock_error(e) or not remedies:
                    raise
                remedies.pop(0)()

    def _switch(self, v, cur, restart_explorer=False):
        g = self.game
        btn = self.activate_btns.get(v["name"])
        if btn is not None and btn.winfo_exists():
            btn.configure(text="Cambiando…", state="disabled")
        self.app.busy(True)
        try:
            if restart_explorer:
                subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, env=clean_env(),
                               creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(1.5)
            try:
                if restart_explorer:
                    g.activate(v)
                else:
                    self._activate_freeing_explorer(v)
            finally:
                if restart_explorer:
                    # Entorno de sesión limpio: el Explorador es el padre de todo lo que se abra después.
                    subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")],
                                     env=user_default_env(), cwd=os.environ.get("SystemRoot", r"C:\Windows"),
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self.app.busy(False)
            self.refresh(force=True)
            if is_lock_error(e):
                self._handle_lock(v, cur, e, explorer_tried=restart_explorer)
            else:
                messagebox.showerror(APP, explain(e), parent=self.app)
            return
        self.app.busy(False)
        self._animate_swap(v, cur, lambda: self.app.toast(
            f"✓  «{v['name']}» está ahora en uso" + (f"  ·  «{cur['name']}» guardada" if cur else "")))

    def _handle_lock(self, v, cur, err, explorer_tried):
        """Averigua qué programa bloquea la carpeta y ofrece la salida más sencilla."""
        g = self.game
        self.app.busy(True)
        try:
            found = find_lockers([g.active_path, g.path(v)])[0]
        except Exception:
            found = {}
        self.app.busy(False)
        names = sorted(set(found.values()), key=str.lower)
        others = [n for n in names if n.lower() != "explorer.exe"]

        if names and not others and not explorer_tried:
            if messagebox.askyesno(
                    APP, "El Explorador de Windows sigue sin soltar la carpeta, aunque ya cerré sus "
                         "ventanas y le pedí que la liberara.\n\n"
                         "¿Reinicio el Explorador y vuelvo a intentarlo?\n\n"
                         "Se cerrarán las ventanas de carpetas que tengas abiertas y la barra de tareas "
                         "desaparecerá un segundo. No afecta a ningún otro programa.",
                    icon="warning", parent=self.app):
                self._switch(v, cur, restart_explorer=True)
            return
        if others:
            messagebox.showerror(
                APP, "No se puede cambiar porque estos programas tienen algo abierto en la carpeta:\n\n   • " +
                     "\n   • ".join(others) + "\n\nCiérralos y vuelve a pulsar «Activar»." +
                     ("\n\n(El Explorador de Windows también la usa; si después sigue fallando, la app "
                      "te ofrecerá reiniciarlo.)" if len(others) < len(names) else ""),
                parent=self.app)
            return
        if messagebox.askyesno(APP, explain(err) + "\n\nNo encontré al culpable entre tus programas: puede ser "
                               "uno abierto como administrador. ¿Abrir el Monitor de recursos para buscarlo?",
                               icon="error", parent=self.app):
            self.app.find_locker(g.base)

    def _check_name(self, name, ignore=None):
        if not name:
            raise CCError("El nombre no puede estar vacío.")
        other = self.game.find(name)
        if other and other is not ignore:
            raise CCError(f"Ya hay una versión llamada «{name}».")

    def _check_folder(self, folder, ignore=None):
        if not folder or any(c in folder for c in '\\/:*?"<>|'):
            raise CCError("Nombre de carpeta no válido.")
        if same(folder, self.game.active_name):
            raise CCError("La carpeta guardada no puede llamarse igual que la que usa el juego.")
        if any(same(v["folder"], folder) for v in self.game.variants if v is not ignore):
            raise CCError(f"Otra versión ya usa la carpeta «{folder}».")

    def register_active(self):
        g = self.game

        def validate(r):
            self._check_name(r["name"])
            self._check_folder(r["folder"])
            if os.path.exists(os.path.join(g.base, r["folder"])):
                raise CCError(f"Ya existe una carpeta «{r['folder']}». Elige otro nombre.")

        r = ask_form(self.app, "Ponle nombre a la versión actual",
                     [("name", "Nombre de esta versión:", "Original", False),
                      ("folder", "Nombre de su carpeta cuando esté guardada:", f"{g.active_name} Original", False)],
                     validate, ok_text="Guardar",
                     hint=f"Ahora mismo es «{g.active_name}». Cuando actives otra versión, esta carpeta "
                          "pasará a llamarse como pongas en el segundo campo.")
        if not r:
            return False
        g.variants.append({"name": r["name"], "folder": r["folder"]})
        g.d["active"] = r["name"]
        self.refresh(force=True)
        return True

    def add_variant(self):
        g = self.game
        p = filedialog.askdirectory(parent=self.app, initialdir=g.base if os.path.isdir(g.base) else None,
                                    title="Elige la carpeta de la otra versión")
        if not p:
            return
        p = os.path.normpath(p)
        if not same(os.path.dirname(p), g.base):
            messagebox.showerror(APP, "La versión tiene que estar en la misma carpeta que la que usa el "
                                 f"juego:\n\n{g.base}", parent=self.app)
            return
        folder = os.path.basename(p)
        if same(folder, g.active_name):
            if self.game.state()[0]:
                messagebox.showinfo(APP, "Esa es la carpeta que está en uso ahora.", parent=self.app)
            else:
                self.register_active()
            return
        prefix = g.active_name + " "
        default = folder[len(prefix):] if folder.lower().startswith(prefix.lower()) else folder

        def validate(r):
            self._check_name(r["name"])
            self._check_folder(folder)

        r = ask_form(self.app, "Añadir versión", [("name", "Nombre:", default, False)], validate,
                     hint=f"Carpeta: {folder}", ok_text="Añadir")
        if not r:
            return
        g.variants.append({"name": r["name"], "folder": folder})
        self.refresh(force=True)
        self.app.toast(f"✓  «{r['name']}» añadida")

    def edit_variant(self, v):
        g = self.game
        cur, _ = g.state()

        def validate(r):
            self._check_name(r["name"], ignore=v)
            self._check_folder(r["folder"], ignore=v)
            if not same(r["folder"], v["folder"]) and os.path.exists(os.path.join(g.base, r["folder"])):
                raise CCError(f"Ya existe una carpeta «{r['folder']}».")

        hint = ("Está en uso: el nuevo nombre de carpeta se aplicará cuando la guardes." if v is cur else
                "Si cambias el nombre de la carpeta, también se renombra en el disco.")
        r = ask_form(self.app, "Editar versión",
                     [("name", "Nombre:", v["name"], False),
                      ("folder", "Nombre de su carpeta cuando está guardada:", v["folder"], False)],
                     validate, hint=hint)
        if not r:
            return
        if r["folder"] != v["folder"] and v is not cur and os.path.isdir(g.path(v)):
            try:
                os.rename(g.path(v), os.path.join(g.base, r["folder"]))
            except OSError as e:
                messagebox.showerror(APP, explain(e), parent=self.app)
                return
        if g.d.get("active") == v["name"]:
            g.d["active"] = r["name"]
        v["name"], v["folder"] = r["name"], r["folder"]
        self.refresh(force=True)

    def remove_variant(self, v):
        if messagebox.askyesno(APP, f"¿Quitar «{v['name']}» de la lista?\n\n"
                               "La carpeta NO se borra del disco.", parent=self.app):
            self.game.variants.remove(v)
            self.refresh(force=True)

    def open_path(self, p):
        if os.path.isdir(p):
            with clean_environ():
                os.startfile(p)
        else:
            messagebox.showerror(APP, f"No existe:\n{p}", parent=self.app)

    def edit_game(self):
        r = self.app.ask_game(self.game.name, self.game.active_path, editing=True)
        if r:
            self.game.d.update(r)
            self.app.show(self.app.current)

    def remove_game(self):
        if messagebox.askyesno(APP, f"¿Quitar «{self.game.name}» de la app?\n\n"
                               "No se renombra ni se borra ninguna carpeta.", parent=self.app):
            self.app.data["games"].remove(self.game.d)
            save_config(self.app.data)
            self.app.show(max(self.app.current - 1, 0))


# --------------------------------------------------------------------------- #
#  Ventana principal
# --------------------------------------------------------------------------- #
class App(ctk.CTk):
    def __init__(self):
        self.data = load_config()
        ctk.set_appearance_mode(self.data.get("theme", "dark"))
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title(APP + ("  (administrador)" if is_admin() else ""))
        self.geometry("1000x640")
        self.minsize(820, 540)
        self.configure(fg_color=BG)
        set_icon(self)
        self.after(250, lambda: set_icon(self))
        self.current = 0
        self.view = None
        self._toast_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sb = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=SIDEBAR)
        sb.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sb.pack_propagate(False)
        ctk.CTkLabel(sb, text=APP, font=F(20, "bold"), text_color=TEXT).pack(anchor="w", padx=20, pady=(22, 0))
        ctk.CTkLabel(sb, text=f"Cambia de versión en un clic  ·  v{__version__}", font=F(12),
                     text_color=DIM).pack(anchor="w", padx=20)
        ctk.CTkLabel(sb, text="JUEGOS Y APPS", font=F(11, "bold"),
                     text_color=DIM).pack(anchor="w", padx=20, pady=(26, 4))

        theme = ctk.CTkSegmentedButton(sb, values=["Oscuro", "Claro"], font=F(12), command=self.set_theme)
        theme.set("Claro" if self.data.get("theme") == "light" else "Oscuro")
        theme.pack(side="bottom", fill="x", padx=16, pady=16)
        for b in getattr(theme, "_buttons_dict", {}).values():
            tip(b, "Cambia entre tema oscuro y claro. Se recuerda para la próxima vez.")
        tip(ctk.CTkButton(sb, text="＋  Añadir juego", height=40, font=F(13, "bold"), command=self.add_game),
            ADD_GAME_TIP).pack(side="bottom", fill="x", padx=16)

        self.game_list = ctk.CTkScrollableFrame(sb, fg_color="transparent")
        self.game_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=28, pady=(24, 6))
        self.toast_lbl = ctk.CTkLabel(self, text="", font=F(12, "bold"), text_color=GREEN, anchor="w")
        self.toast_lbl.grid(row=1, column=1, sticky="ew", padx=30, pady=(0, 10))

        # «?» arriba a la derecha: guía de uso (también con F1)
        self.help_btn = tip(ctk.CTkButton(self, text="?", width=34, height=34, corner_radius=17,
                                          font=F(16, "bold"), fg_color=CARD, hover_color=HOVER,
                                          text_color=TEXT, border_width=1, border_color=BORDER,
                                          command=self.open_help),
                            "Ayuda: para qué sirve CarpetChanger y cómo se usa (F1).")
        self.help_btn.place(relx=1.0, x=-20, y=24, anchor="ne")
        self.bind("<F1>", lambda e: self.open_help())
        self._help = None

        self.show(0)
        self.bind("<FocusIn>", lambda e: e.widget is self and self.view and self.view.refresh())

    def open_help(self):
        if self._help is not None and self._help.winfo_exists():
            self._help.lift()
            self._help.focus_force()
            return
        self._help = HelpDialog(self)

    # -- utilidades -------------------------------------------------------- #
    def busy(self, on):
        self.configure(cursor="watch" if on else "")
        self.update_idletasks()

    def toast(self, text, color=GREEN):
        self.toast_lbl.configure(text=text, text_color=color)
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(6000, lambda: self.toast_lbl.configure(text=""))

    def find_locker(self, folder):
        """Abre el Monitor de recursos con la ruta en el portapapeles para buscarla."""
        self.clipboard_clear()
        self.clipboard_append(folder)
        messagebox.showinfo(APP, "Se abrirá el Monitor de recursos (Windows pedirá permiso).\n\n"
                            "1. Ve a la pestaña «CPU».\n"
                            "2. Despliega «Identificadores asociados».\n"
                            "3. Pega (Ctrl+V) en el cuadro de búsqueda: la ruta ya está copiada.\n\n"
                            "Verás qué programas tienen algo abierto ahí; ciérralos y vuelve a intentarlo.",
                            parent=self)
        try:
            with clean_environ():
                os.startfile("resmon.exe")
        except OSError as e:
            messagebox.showerror(APP, str(e), parent=self)

    def set_theme(self, value):
        self.data["theme"] = "light" if value == "Claro" else "dark"
        ctk.set_appearance_mode(self.data["theme"])
        save_config(self.data)

    # -- navegación -------------------------------------------------------- #
    def render_sidebar(self):
        for w in self.game_list.winfo_children():
            w.destroy()
        for i, d in enumerate(self.data["games"]):
            sel = i == self.current
            b = ctk.CTkButton(self.game_list, text=d["name"], anchor="w", height=42, corner_radius=10,
                              font=F(13, "bold" if sel else "normal"),
                              fg_color=SELECTED if sel else "transparent", hover_color=HOVER,
                              text_color=TEXT, command=lambda i=i: self.show(i))
            tip(b, lambda d=d: f"{d['name']}\n"
                               f"En uso: {d.get('active') or 'ninguna versión con nombre'}\n"
                               f"Carpeta: {os.path.join(d['base'], d['active_name'])}")
            b.pack(fill="x", pady=2)

    def show(self, i):
        self.current = i
        for w in self.main.winfo_children():
            w.destroy()
        self.view = None
        if not self.data["games"]:
            self.render_sidebar()
            self._welcome()
            return
        self.current = min(i, len(self.data["games"]) - 1)
        self.view = GameView(self.main, self, Game(self.data["games"][self.current]))
        self.view.pack(fill="both", expand=True)
        self.render_sidebar()

    def _welcome(self):
        box = ctk.CTkFrame(self.main, fg_color="transparent")
        box.place(relx=0.5, rely=0.45, anchor="center")
        ctk.CTkLabel(box, text="Bienvenido a CarpetChanger", font=F(26, "bold"), text_color=TEXT).pack()
        ctk.CTkLabel(box, text="Ten varias versiones de un juego y cambia cuál usa con un clic.",
                     font=F(14), text_color=DIM).pack(pady=(4, 22))
        steps = [("1", "Añade un juego eligiendo la carpeta que usa\n(p. ej. …\\steamapps\\common\\Skyrim Special Edition)."),
                 ("2", "Ponle nombre a la versión que está ahí ahora."),
                 ("3", "Añade las otras copias que tengas al lado\n(se detectan solas si empiezan por el mismo nombre)."),
                 ("4", "Pulsa «Activar» en la que quieras usar.")]
        for n, t in steps:
            row = ctk.CTkFrame(box, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(row, text=n, width=36, height=36, corner_radius=18, fg_color=("#1f6aa5", "#1f6aa5"),
                         text_color="white", font=F(14, "bold")).pack(side="left", padx=14, pady=10)
            ctk.CTkLabel(row, text=t, font=F(13), text_color=TEXT, justify="left",
                         anchor="w").pack(side="left", padx=(0, 16), fill="x")
        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.pack(pady=(22, 0))
        tip(ctk.CTkButton(btns, text="＋  Añadir mi primer juego", height=44, font=F(14, "bold"),
                          command=self.add_game), ADD_GAME_TIP).pack(side="left")
        tip(ghost(btns, "¿Cómo funciona?", self.open_help, 140, TEXT),
            "Abre la guía con la explicación completa.").pack(side="left", padx=(10, 0))

    # -- juegos ------------------------------------------------------------ #
    def ask_game(self, name="", path="", editing=False):
        def validate(r):
            if not r["name"]:
                raise CCError("Ponle un nombre.")
            p = os.path.normpath(r["path"]) if r["path"] else ""
            if not p or not os.path.basename(p) or not os.path.isdir(os.path.dirname(p)):
                raise CCError("Indica la ruta completa de la carpeta que usa el juego; la carpeta que "
                              "la contiene tiene que existir.")

        r = ask_form(self, "Editar juego" if editing else "Añadir juego",
                     [("name", "Nombre (como aparecerá en la lista):", name, False),
                      ("path", "Carpeta que usa el juego:", path, True)],
                     validate, ok_text="Guardar" if editing else "Añadir",
                     hint="Es la carpeta con el nombre que el juego espera, p. ej. "
                          "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Skyrim Special Edition. "
                          "Las otras versiones tienen que estar junto a ella.")
        if not r:
            return None
        p = os.path.normpath(r["path"])
        return {"name": r["name"], "base": os.path.dirname(p), "active_name": os.path.basename(p)}

    def add_game(self):
        p = filedialog.askdirectory(parent=self, title="Elige la carpeta que usa el juego "
                                    "(la que tiene el nombre original)")
        if not p:
            return
        p = os.path.normpath(p)
        r = self.ask_game(os.path.basename(p), p)
        if not r:
            return
        d = dict(r, variants=[], active=None)

        prefix = (d["active_name"] + " ").lower()
        found = sorted(f for f in os.listdir(d["base"])
                       if f.lower().startswith(prefix) and os.path.isdir(os.path.join(d["base"], f)))
        if found and messagebox.askyesno(
                APP, "Encontré estas otras versiones junto a la carpeta del juego:\n\n   • " +
                     "\n   • ".join(found) + "\n\n¿Añadirlas?", parent=self):
            for f in found:
                d["variants"].append({"name": f[len(prefix):], "folder": f})

        self.data["games"].append(d)
        save_config(self.data)
        self.show(len(self.data["games"]) - 1)
        if os.path.isdir(os.path.join(d["base"], d["active_name"])) and self.view.game.state()[1] == "unregistered":
            self.after(150, self.view.register_active)


def self_test(report):
    """Comprueba que el ejecutable trae todo lo necesario y funciona, sin tocar nada del usuario.

    Uso:  CarpetChanger.exe --selftest informe.txt   (código de salida 0 = todo bien)
    Trabaja en una carpeta temporal y con una configuración temporal.
    """
    global CONFIG
    import shutil
    import tempfile
    import traceback

    lines = [f"CarpetChanger {__version__}  ·  Python {sys.version.split()[0]}  ·  "
             f"{'exe' if getattr(sys, 'frozen', False) else 'script'}"]
    base = tempfile.mkdtemp(prefix="cc_selftest_")
    try:
        for f in ("Juego", "Juego A", "Juego B"):
            os.makedirs(os.path.join(base, f, "Data"))
            open(os.path.join(base, f, "Data", f + ".txt"), "w").close()
        g = Game({"name": "Prueba", "base": base, "active_name": "Juego", "variants": [
            {"name": "Original", "folder": "Juego Original"},
            {"name": "A", "folder": "Juego A"}, {"name": "B", "folder": "Juego B"}]})
        assert g.state()[0]["name"] == "Original"
        g.activate(g.find("A"))
        assert g.state()[0]["name"] == "A"
        assert os.path.isfile(os.path.join(base, "Juego", "Data", "Juego A.txt"))
        assert os.path.isfile(os.path.join(base, "Juego Original", "Data", "Juego.txt"))
        g.activate(g.find("B"))
        g.activate(g.find("Original"))
        assert os.path.isfile(os.path.join(base, "Juego", "Data", "Juego.txt"))
        lines.append("ok  intercambio de carpetas")

        with open(os.path.join(base, "Juego A", "Data", "Juego A.txt")):
            assert find_lockers([os.path.join(base, "Juego A")])[0], "no detecta el bloqueo"
        free_from_explorer([os.path.join(base, "Juego B")])
        lines.append("ok  detección y liberación de bloqueos")

        for env in (clean_env(), user_default_env()):
            out = subprocess.run(["cmd", "/c", "set"], env=env, capture_output=True, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW).stdout.upper()
            assert "PATH=" in out, "entorno vacío"
            assert not any(("\n" + v) in ("\n" + out) for v in _LEAKED_VARS), "se filtran variables internas"
        with clean_environ():
            assert not any(k.upper().startswith(_LEAKED_VARS) for k in os.environ)
        lines.append("ok  programas lanzados con entorno limpio")

        assert os.path.isfile(resource("icon.ico")), "falta icon.ico"
        CONFIG = os.path.join(base, "carpetchanger.json")
        save_config({"theme": "dark", "games": [g.d]})
        app = App()
        errors = []
        app.report_callback_exception = lambda *exc: errors.append(traceback.format_exception(*exc))
        app.update()
        assert app.view is not None and app.view.game.state()[0]["name"] == "Original"
        lines.append("ok  interfaz (CustomTkinter, Tcl/Tk, icono)")

        for theme, target, previous in (("Oscuro", "B", "Original"), ("Claro", "Original", "B")):
            app.set_theme(theme)
            app.update()
            app.view.activate(app.view.game.find(target))
            end = time.perf_counter() + 1.5
            while time.perf_counter() < end:
                app.update()
                time.sleep(0.01)
            assert not errors, "".join(errors[0])
            assert app.view.game.state()[0]["name"] == target
            assert app.view.recent == (target, previous), app.view.recent
        lines.append("ok  animación del cambio (temas oscuro y claro)")

        app.open_help()
        app.update()
        assert app._help is not None and app._help.winfo_exists()
        app._help.destroy()
        tooltip = app.view.add_btn._cc_tooltip
        tooltip.show()
        app.update()
        assert tooltip.win is not None and tooltip.win.winfo_exists()
        tooltip.hide()
        assert not errors, "".join(errors[0])
        app.destroy()
        lines.append("ok  ayuda y tooltips")
        lines.append("RESULTADO: OK")
    except Exception:
        lines.append(traceback.format_exc())
        lines.append("RESULTADO: ERROR")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if lines[-1] == "RESULTADO: OK" else 1


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(self_test(os.path.abspath(sys.argv[2])))
    # Que la app nunca bloquee por estar "situada" dentro de una de las carpetas que renombra.
    os.chdir(app_dir())
    App().mainloop()
