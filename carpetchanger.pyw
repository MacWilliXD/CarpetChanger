"""CarpetChanger: intercambia qué carpeta ocupa el nombre "activo" de un juego.

Cada juego/aplicación de la barra lateral tiene una carpeta con nombre fijo, la
que el juego lee (p. ej. "Skyrim Special Edition"). Las demás versiones esperan
al lado con su propio nombre ("Skyrim Special Edition Base", ...). Activar una
versión devuelve la actual a su nombre y pone la elegida en el nombre activo.
Solo se renombran carpetas: nunca se copia ni se borra nada.
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

__version__ = "1.0.0"

APP = "CarpetChanger"

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


def F(size, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


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
                       env=dict(os.environ, CC_PATHS="|".join(paths)), capture_output=True, timeout=15,
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
                ctk.CTkButton(row, text="Examinar…", width=100, height=36, font=F(13),
                              command=lambda v=var: self._browse(v)).pack(side="left", padx=(8, 0))
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


def ghost(master, text, command, width=70, color=None):
    return ctk.CTkButton(master, text=text, command=command, width=width, height=32, font=F(12),
                         fg_color="transparent", hover_color=HOVER, text_color=color or DIM)


# --------------------------------------------------------------------------- #
#  Vista de un juego
# --------------------------------------------------------------------------- #
class GameView(ctk.CTkFrame):
    def __init__(self, master, app, game):
        super().__init__(master, fg_color="transparent")
        self.app, self.game = app, game
        self.sig = None

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=game.name, font=F(26, "bold"), text_color=TEXT).pack(side="left")
        ghost(head, "Quitar juego", self.remove_game, 100, RED).pack(side="right")
        ghost(head, "Editar juego", self.edit_game, 100).pack(side="right", padx=4)
        ctk.CTkLabel(self, text=f"Carpeta que usa el juego:  {game.active_path}", font=F(12),
                     text_color=DIM, anchor="w").pack(fill="x", pady=(0, 14))

        self.top = ctk.CTkFrame(self, fg_color="transparent")
        self.top.pack(fill="x")

        sec = ctk.CTkFrame(self, fg_color="transparent")
        sec.pack(fill="x", pady=(22, 6))
        ctk.CTkLabel(sec, text="Otras versiones", font=F(15, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkButton(sec, text="＋  Añadir versión", width=150, height=32, font=F(12, "bold"),
                      command=self.add_variant).pack(side="right")

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
        self._draw_current(cur, warn)
        self._draw_list(cur, exists)
        self.app.render_sidebar()

    def _draw_current(self, cur, warn):
        g = self.game
        if cur:
            card = ctk.CTkFrame(self.top, fg_color=ACTIVE_BG, border_color=GREEN, border_width=2, corner_radius=14)
            card.pack(fill="x")
            left = ctk.CTkFrame(card, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=22, pady=18)
            ctk.CTkLabel(left, text="●  EN USO AHORA", font=F(11, "bold"), text_color=GREEN).pack(anchor="w")
            ctk.CTkLabel(left, text=cur["name"], font=F(28, "bold"), text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(left, text=f"Cuando cambies, se guardará como «{cur['folder']}»", font=F(12),
                         text_color=DIM).pack(anchor="w")
            right = ctk.CTkFrame(card, fg_color="transparent")
            right.pack(side="right", padx=18)
            ghost(right, "Abrir carpeta", lambda: self.open_path(g.active_path), 110, TEXT).pack(pady=2)
            ghost(right, "Editar", lambda: self.edit_variant(cur), 110, TEXT).pack(pady=2)
            if warn == "ambiguous":
                self._warn("Algunas versiones no se encuentran (marcadas en rojo). Puede que se hayan "
                           "renombrado o movido fuera de la app.")
            return

        if warn == "unregistered":
            self._warn(f"La carpeta «{g.active_name}» está en uso pero aún no tiene nombre. Pónselo para "
                       "poder guardarla cuando cambies a otra versión.",
                       title="La versión actual no tiene nombre",
                       button=("Ponerle nombre…", self.register_active))
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
            ctk.CTkButton(card, text=button[0], command=button[1], width=150, height=36,
                          font=F(13, "bold")).pack(side="right", padx=18)

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
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=18, pady=12)
            ctk.CTkLabel(left, text=v["name"], font=F(16, "bold"), text_color=TEXT if ok else RED).pack(anchor="w")
            ctk.CTkLabel(left, text=v["folder"] if ok else f"No se encuentra la carpeta «{v['folder']}»",
                         font=F(12), text_color=DIM if ok else RED).pack(anchor="w")

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=12)
            ghost(right, "Quitar", lambda v=v: self.remove_variant(v), 64, RED).pack(side="right")
            ghost(right, "Editar", lambda v=v: self.edit_variant(v), 64).pack(side="right")
            ghost(right, "Abrir", lambda v=v: self.open_path(g.path(v)), 64).pack(side="right")
            if ok:
                ctk.CTkButton(right, text="Activar", width=110, height=36, font=F(13, "bold"),
                              command=lambda v=v: self.activate(v)).pack(side="right", padx=(0, 10))
            else:
                ctk.CTkButton(right, text="Activar", width=110, height=36, font=F(13, "bold"),
                              state="disabled", fg_color=BORDER).pack(side="right", padx=(0, 10))
            if ok:
                for w in (row, left, *left.winfo_children()):
                    w.bind("<Double-1>", lambda e, v=v: self.activate(v))

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

    def _switch(self, v, cur, restart_explorer=False):
        g = self.game
        self.app.busy(True)
        try:
            if restart_explorer:
                subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                time.sleep(1.5)
            else:
                free_from_explorer([g.active_path, g.path(v)])
            try:
                g.activate(v)
            finally:
                if restart_explorer:
                    subprocess.Popen(["explorer.exe"], creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self.app.busy(False)
            self.refresh(force=True)
            if is_lock_error(e):
                self._handle_lock(v, cur, e, explorer_tried=restart_explorer)
            else:
                messagebox.showerror(APP, explain(e), parent=self.app)
            return
        self.app.busy(False)
        self.refresh(force=True)
        self.app.toast(f"✓  «{v['name']}» está ahora en uso" + (f"  ·  «{cur['name']}» guardada" if cur else ""))

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
        ctk.CTkButton(sb, text="＋  Añadir juego", height=40, font=F(13, "bold"),
                      command=self.add_game).pack(side="bottom", fill="x", padx=16)

        self.game_list = ctk.CTkScrollableFrame(sb, fg_color="transparent")
        self.game_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.main = ctk.CTkFrame(self, fg_color="transparent")
        self.main.grid(row=0, column=1, sticky="nsew", padx=28, pady=(24, 6))
        self.toast_lbl = ctk.CTkLabel(self, text="", font=F(12, "bold"), text_color=GREEN, anchor="w")
        self.toast_lbl.grid(row=1, column=1, sticky="ew", padx=30, pady=(0, 10))

        self.show(0)
        self.bind("<FocusIn>", lambda e: e.widget is self and self.view and self.view.refresh())

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
            active = d.get("active")
            b = ctk.CTkButton(self.game_list, text=d["name"] + (f"\n{active}" if active else ""),
                              anchor="w", height=50, corner_radius=10, font=F(13, "bold" if sel else "normal"),
                              fg_color=SELECTED if sel else "transparent", hover_color=HOVER,
                              text_color=TEXT, command=lambda i=i: self.show(i))
            try:
                b._text_label.configure(justify="left")
            except AttributeError:
                pass
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
        ctk.CTkButton(box, text="＋  Añadir mi primer juego", height=44, font=F(14, "bold"),
                      command=self.add_game).pack(pady=(22, 0))

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


if __name__ == "__main__":
    # Que la app nunca bloquee por estar "situada" dentro de una de las carpetas que renombra.
    os.chdir(app_dir())
    App().mainloop()
