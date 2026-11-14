import hashlib
import mmap
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET
import zlib

from PIL import Image, ImageChops, ImageDraw, ImageTk


HERE = Path(__file__).resolve().parent
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\7 Days To Die")
GAME_DATA = Path(os.environ["APPDATA"]) / "7DaysToDie"
WORK = Path(os.environ["LOCALAPPDATA"]) / "REA Map Maker" / "Previews"
SIZES = (6144, 8192, 10240)
DEFAULT_SIZE = 8192
NATIVE_HEIGHT_FILES = {"dtm.raw", "dtm_processed.raw"}
SUPPORTED_ASSEMBLY = "cb54022ccc862aa673e0ee55dafe67d842b364c80df6916958d171e959656c58"
OUTPUT = ("biomes.png", "dtm.raw", "dtm_processed.raw", "main.ttw",
          "map_info.xml", "prefabs.xml", "radiation.png", "spawnpoints.xml",
          "splat3.png", "splat3_half.png", "splat3_processed.png",
          "splat4.png", "splat4_half.png", "splat4_processed.png", "checksums.txt")
LAYERS = (
    ("Height map", "height.raw", "*.raw", "16-bit RAW · full size"),
    ("Biome map", "biomes.rgba", "*.png", "biome colors · full size"),
    ("Water mask", "water.mask", "*.png", "white = water · full size"),
    ("Road map", "roads.rgba", "*.png", "splat3.png · full size"),
)
BIOMES = {(0, 64, 0, 255), (186, 0, 255, 255), (255, 228, 119, 255),
          (255, 255, 255, 255), (255, 168, 0, 255)}
SOLID_BIOME_TOLERANCE = 8
ROAD_COLORS = {(0, 0, 0, 0), (255, 0, 0, 255), (0, 255, 0, 255)}
Image.MAX_IMAGE_PIXELS = max(SIZES) ** 2


def validate_name(name):
    if (not name or len(name) > 80 or not re.fullmatch(r"[\w .-]+", name)
            or name.endswith(".") or os.path.isreserved(name)):
        raise ValueError("Enter a valid Windows world name (up to 80 letters, numbers, spaces, dots, hyphens or underscores).")


def solid_biome(bounds):
    return next((biome for biome in BIOMES
                 if all(value - SOLID_BIOME_TOLERANCE <= low <= high <= value + SOLID_BIOME_TOLERANCE
                        for value, (low, high) in zip(biome, bounds))), None)


def prepare_biomes(pixels, size, name):
    internal_size = (size // 8, size // 8)
    colors = pixels.getcolors(len(BIOMES))
    if pixels.size == (size, size) and colors and all(color in BIOMES for _, color in colors):
        return pixels.resize(internal_size, Image.Resampling.NEAREST)

    # A single biome has no boundaries to preserve when its input size differs.
    if pixels.width == pixels.height:
        biome = solid_biome(pixels.getextrema())
        if biome is None and pixels.size == (size, size) and size > 2:
            # Some exports leave a one-pixel canvas edge outside a solid biome.
            biome = solid_biome(pixels.crop((1, 1, size - 1, size - 1)).getextrema())
        if biome is not None:
            return Image.new("RGBA", internal_size, biome)

    if pixels.size != (size, size):
        raise ValueError(f"{name} must be a {size} × {size} PNG for a mixed-biome map.")
    raise ValueError(f"{name} uses colors outside the game's biome palette. "
                     "Use exact, opaque biome colors for a mixed-biome map.")


def prepare_layer(source, destination, size):
    if destination.name == "height.raw":
        if source.stat().st_size != size * size * 2:
            raise ValueError(f"The height map must contain {size} × {size} little-endian 16-bit samples.")
        if source.name.lower() in NATIVE_HEIGHT_FILES:
            shutil.copyfile(source, destination)
            return
        row_bytes = size * 2
        # Match the PNG layers in the game's south-first terrain buffer.
        with source.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as raw:
            with destination.open("wb") as output:
                for end in range(len(raw), 0, -row_bytes):
                    output.write(raw[end - row_bytes:end])
        return
    is_biome = destination.name == "biomes.rgba"
    with Image.open(source) as image:
        if image.format != "PNG" or (not is_biome and image.size != (size, size)):
            raise ValueError(f"{source.name} must be a {size} × {size} PNG.")
        pixels = image.convert("RGBA")
        if is_biome:
            pixels = prepare_biomes(pixels, size, source.name)
        elif destination.name == "roads.rgba":
            colors = pixels.getcolors(len(ROAD_COLORS))
            if colors is None or any(color not in ROAD_COLORS for _, color in colors):
                raise ValueError(f"{source.name} uses colors outside the game's native layer palette.")
        width = pixels.width
        # The native PNG starts in the north. WorldBuilder buffers start in the south.
        with destination.open("wb") as output:
            for end in range(width, 0, -128):
                strip = pixels.crop((0, max(0, end - 128), width, end))
                if destination.name == "water.mask":
                    red, green, blue, alpha = strip.split()
                    rgb = ImageChops.darker(ImageChops.darker(red, green), blue)
                    strip = ImageChops.darker(rgb, alpha).point(lambda value: int(value == 255))
                output.write(strip.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())


def stage_game(game, job, seed, size):
    mod = job / "Mods" / "REA_Map_Maker"
    mod.mkdir(parents=True)
    shutil.copyfile(HERE / "WorldBridge.dll", mod / "WorldBridge.dll")
    (mod / "ModInfo.xml").write_text(
        '<xml><Name value="REA_Map_Maker"/><DisplayName value="REA Map Maker Bridge"/>'
        '<Version value="1.0.0"/><Author value="REA Map Maker"/></xml>', encoding="utf-8")
    config = ET.parse(game / "serverconfig.xml")
    settings = {
        "UserDataFolder": str(job), "GameWorld": "RWG", "WorldGenSeed": seed,
        "WorldGenSize": str(size), "GameName": "MapMakerPreview",
        "ServerName": "Local Map Maker Preview", "ServerVisibility": "0",
        "ServerPassword": secrets.token_urlsafe(24), "ServerPort": str(29000 + secrets.randbelow(15000)),
        "EACEnabled": "false", "TerminalWindowEnabled": "false", "ServerMaxPlayerCount": "1",
        "TelnetEnabled": "false", "WebDashboardEnabled": "false", "ServerAllowCrossplay": "false",
        "ServerDisabledNetworkProtocols": "SteamNetworking",
    }
    for key, value in settings.items():
        node = config.find(f"./property[@name='{key}']")
        if node is None:
            node = ET.SubElement(config.getroot(), "property", name=key)
        node.set("value", value)
    path = job / "server.xml"
    config.write(path, encoding="utf-8", xml_declaration=True)
    return path


def log_excerpt(path):
    if not path.exists():
        return "The game did not create a log."
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 500_000))
        lines = stream.read().decode("utf-8", "replace").splitlines()
    important = [line for line in lines if re.search(r"\b(ERR|EXC)\b|Exception:|Crash!!!", line)]
    return "\n".join((important or lines)[-5:])


def generate(game, job, seed, size, sources, cancel, status):
    if not (game / "7DaysToDie.exe").is_file() or not (game / "serverconfig.xml").is_file():
        raise ValueError("Select the Steam game folder containing 7DaysToDie.exe and serverconfig.xml.")
    assembly = game / "7DaysToDie_Data" / "Managed" / "Assembly-CSharp.dll"
    if not assembly.is_file() or hashlib.sha256(assembly.read_bytes()).hexdigest() != SUPPORTED_ASSEMBLY:
        raise ValueError("This build of Map Maker requires the Windows game build V3.4.0 (b10) it was built against.")
    for (_, filename, _, _), source in zip(LAYERS, sources):
        if source:
            status(f"Preparing {Path(source).name}…")
            prepare_layer(Path(source), job / filename, size)
        if cancel.is_set():
            raise InterruptedError("Generation canceled.")
    config = stage_game(game, job, seed, size)
    log = job / "game.log"
    env = os.environ.copy()
    env["REA_MAP_MAKER_JOB"] = str(job)
    args = [str(game / "7DaysToDie.exe"), "-batchmode", "-nographics", "-dedicated",
            "-noeac", "-nogs", "-crossplatform=None", f"-UserDataFolder={job}",
            f"-configfile={config}", "-logfile", str(log)]
    with subprocess.Popen(args, cwd=game, env=env, creationflags=subprocess.CREATE_NO_WINDOW) as process:
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(1):
                    raise InterruptedError("Generation canceled.")
                error = job / "bridge.error"
                if error.exists():
                    raise RuntimeError(error.read_text(encoding="utf-8")[:2400])
                elapsed = int(time.monotonic() - start)
                if elapsed > 3600:
                    raise TimeoutError("The game did not finish generating within an hour.")
                bridge_ready = (job / "bridge.ready").exists()
                if not bridge_ready and elapsed > 180:
                    raise RuntimeError(f"The game did not load the generation bridge.\n\n{log_excerpt(log)}\n\nLog: {log}")
                phase = "Generating and processing the world" if bridge_ready else "Starting the game engine"
                status(f"{phase}… {elapsed // 60}:{elapsed % 60:02d}")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
    ready = job / "world.ready"
    error = job / "bridge.error"
    if error.exists():
        raise RuntimeError(error.read_text(encoding="utf-8")[:2400])
    if process.returncode != 0 or not ready.exists():
        raise RuntimeError(f"The game exited before completing the world.\n\n{log_excerpt(log)}\n\nLog: {log}")
    world_name = ready.read_text(encoding="utf-8").strip()
    validate_name(world_name)
    world = job / "GeneratedWorlds" / world_name
    verify_world(world, seed, size)
    return world


def verify_world(world, seed, size):
    missing = [name for name in OUTPUT if not (world / name).is_file() or not (world / name).stat().st_size]
    if missing:
        raise RuntimeError("The game did not finish writing: " + ", ".join(missing))
    for name in ("dtm.raw", "dtm_processed.raw"):
        if (world / name).stat().st_size != size * size * 2:
            raise RuntimeError(f"The game wrote an unexpected size for {name}.")
    metadata = ET.parse(world / "map_info.xml")
    dimensions = metadata.find("./property[@name='HeightMapSize']")
    world_seed = metadata.find("./property[@class='Generation']/property[@name='Seed']")
    if (dimensions is None or dimensions.get("value") != f"{size},{size}"
            or world_seed is None or world_seed.get("value") != seed):
        raise RuntimeError("Generated world metadata does not match the requested size and seed.")
    checksums = dict(line.split("=", 1) for line in (world / "checksums.txt").read_text(encoding="utf-8-sig").splitlines() if line)
    for name in OUTPUT[:-1]:
        crc = 0
        with (world / name).open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                crc = zlib.crc32(chunk, crc)
        if checksums.get(name) != str(crc):
            raise RuntimeError(f"{name} does not match the game's checksum list.")


def build_preview(world, size, width=960):
    with Image.open(world / "biomes.png") as image:
        background = image.convert("RGB").resize((width, width), Image.Resampling.NEAREST)
    with (world / "dtm.raw").open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        points = [int((i + 0.5) * size / width) for i in range(width)]
        light = bytearray()
        for top in points:
            y = size - 1 - top
            for x in points:
                height = struct.unpack_from("<H", raw, 2 * (x + size * y))[0]
                light.append(min(255, 105 + height // 400))
    shade = Image.frombytes("L", (width, width), bytes(light)).convert("RGB")
    background = ImageChops.multiply(background, shade)
    with Image.open(world / "splat4_processed.png") as water:
        mask = water.getchannel("B").resize((width, width), Image.Resampling.BOX)
        background.paste("#356ea6", mask=mask.point(lambda value: 255 if value else 0))
    with Image.open(world / "splat3.png") as roads:
        mask = roads.getchannel("A").resize((width, width), Image.Resampling.BOX)
        background.paste("#e1ceb0", mask=mask.point(lambda value: min(235, value * 3)))
    traders = []
    draw = ImageDraw.Draw(background)
    for prefab in ET.parse(world / "prefabs.xml").iter("decoration"):
        name = prefab.get("name", "")
        if not name.casefold().startswith("trader_"):
            continue
        x, _, z = map(int, prefab.attrib["position"].split(","))
        px = (x / size + 0.5) * width
        py = (0.5 - z / size) * width
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill="#ffdf4c", outline="#16110a", width=2)
        traders.append((name.removeprefix("trader_").replace("_", " ").title(), x, z))
    return background, sorted(traders)


def publish(world, destination):
    if destination.exists():
        raise FileExistsError(f"A world named {destination.name} already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build the copy on the destination volume, then expose it in a single rename.
    with tempfile.TemporaryDirectory(prefix=".map-maker-", dir=destination.parent.parent) as temp:
        staged = Path(temp) / destination.name
        shutil.copytree(world, staged)
        staged.rename(destination)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("7 Days to Die · REA Map Maker")
        self.geometry("1200x790")
        self.minsize(900, 660)
        self.game = tk.StringVar(value=str(GAME))
        self.name = tk.StringVar(value="My World")
        self.seed = tk.StringVar()
        self.size = tk.StringVar(value=str(DEFAULT_SIZE))
        self.sources = [tk.StringVar() for _ in LAYERS]
        self.status = tk.StringVar(value="Enter a seed and generate a preview.")
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.closing = False
        self.job = self.world = self.image = self.photo = None
        self.edit_controls = []

        left = ttk.Frame(self, padding=16)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="World setup", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self.field(left, "Game folder", self.game, lambda: filedialog.askdirectory(parent=self))
        self.field(left, "World name", self.name)
        self.field(left, "World seed", self.seed)
        ttk.Label(left, text="World size").pack(anchor="w", pady=(8, 0))
        sizes = ttk.Combobox(left, textvariable=self.size, values=SIZES, state="readonly")
        sizes.pack(fill="x")
        self.edit_controls.append((sizes, "readonly"))
        ttk.Label(left, text="Optional map layers", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 0))
        for (label, _, pattern, description), variable in zip(LAYERS, self.sources):
            self.field(left, f"{label}  ·  {description}", variable,
                       lambda pattern=pattern: filedialog.askopenfilename(parent=self, filetypes=[("Map layer", pattern)]))
        self.go = ttk.Button(left, text="Generate / Regenerate", command=self.start)
        self.go.pack(fill="x", pady=(15, 4))
        self.stop = ttk.Button(left, text="Cancel generation", state="disabled", command=self.cancel.set)
        self.stop.pack(fill="x")
        self.save = ttk.Button(left, text="Save world to 7 Days to Die", state="disabled", command=self.save_world)
        self.save.pack(fill="x", pady=(6, 0))
        ttk.Label(left, textvariable=self.status, wraplength=305).pack(fill="x", pady=8)

        right = ttk.Frame(self, padding=(0, 16, 16, 16))
        right.pack(side="right", fill="both", expand=True)
        ttk.Label(right, text="Map preview   ·   North ↑   ·   Yellow marks traders",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        self.canvas = tk.Canvas(right, bg="#18232a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.redraw)
        self.traders = tk.Listbox(right, height=5)
        self.traders.pack(fill="x", pady=(8, 0))
        for variable in (self.game, self.seed, self.size, *self.sources):
            variable.trace_add("write", self.invalidate)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)

    def field(self, parent, label, variable, choose=None):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(7, 0))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=variable, width=35)
        entry.pack(side="left", fill="x", expand=True)
        self.edit_controls.append((entry, "normal"))
        if choose:
            def browse():
                path = choose()
                if path:
                    variable.set(path)
            button = ttk.Button(row, text="Browse", width=8, command=browse)
            button.pack(side="right")
            self.edit_controls.append((button, "normal"))

    def invalidate(self, *_):
        if self.world is not None:
            self.save.configure(state="disabled")
            self.status.set("Inputs changed. Regenerate to preview and save the updated world.")

    def set_busy(self, busy, generating=False):
        self.busy = busy
        self.go.configure(state="disabled" if busy else "normal")
        self.stop.configure(state="normal" if generating else "disabled")
        self.save.configure(state="disabled" if busy or self.world is None else "normal")
        for control, state in self.edit_controls:
            control.configure(state="disabled" if busy else state)

    def start(self):
        name = self.name.get().strip()
        seed = self.seed.get()
        try:
            validate_name(name)
            if not seed.strip() or any(ord(c) < 32 for c in seed):
                raise ValueError("Enter a non-empty world seed without control characters.")
            if int(self.size.get()) not in SIZES:
                raise ValueError("Select a supported world size.")
        except ValueError as error:
            messagebox.showerror("World setup", str(error), parent=self)
            return
        self.discard_preview()
        self.image = None
        self.redraw()
        self.traders.delete(0, "end")
        self.cancel.clear()
        self.set_busy(True, generating=True)
        self.status.set("Preparing world generation…")
        sources = [v.get().strip() for v in self.sources]
        args = Path(self.game.get()), seed, int(self.size.get()), sources
        threading.Thread(target=self.generate_worker, args=args, daemon=True).start()

    def generate_worker(self, game, seed, size, sources):
        try:
            WORK.mkdir(parents=True, exist_ok=True)
            job = Path(tempfile.mkdtemp(prefix="preview-", dir=WORK))
            world = generate(game, job, seed, size, sources, self.cancel,
                             lambda text: self.messages.put(("status", text)))
            image, traders = build_preview(world, size)
            self.messages.put(("preview", (job, world, image, traders, seed)))
        except InterruptedError:
            if "job" in locals():
                shutil.rmtree(job, ignore_errors=True)
            self.messages.put(("status", "Generation canceled."))
        except Exception as error:
            if "job" in locals() and not (job / "game.log").is_file():
                shutil.rmtree(job, ignore_errors=True)
            self.messages.put(("error", str(error)))
        finally:
            self.messages.put(("idle", None))

    def save_world(self):
        if self.world is None:
            return
        try:
            name = self.name.get().strip()
            validate_name(name)
            destination = GAME_DATA / "GeneratedWorlds" / name
            if (Path(self.game.get()) / "Data" / "Worlds" / name).exists():
                raise ValueError("A world with that name ships with the game. Choose another name.")
        except ValueError as error:
            messagebox.showerror("Save world", str(error), parent=self)
            return
        self.set_busy(True)
        self.status.set("Saving the processed world…")
        threading.Thread(target=self.save_worker, args=(self.world, destination), daemon=True).start()

    def save_worker(self, world, destination):
        try:
            publish(world, destination)
            self.messages.put(("status", f"Saved {destination.name}. Select it under New Game in 7 Days to Die."))
        except Exception as error:
            self.messages.put(("error", str(error)))
        finally:
            self.messages.put(("idle", None))

    def redraw(self, _event=None):
        self.canvas.delete("all")
        if self.image is not None:
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            side = max(1, min(w, h))
            self.photo = ImageTk.PhotoImage(self.image.resize((side, side), Image.Resampling.LANCZOS))
            self.canvas.create_image(w // 2, h // 2, image=self.photo)

    def poll(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status.set(payload)
            elif kind == "error":
                self.status.set("Operation failed.")
                if not self.closing:
                    messagebox.showerror("REA Map Maker", payload, parent=self)
            elif kind == "preview":
                self.job, self.world, self.image, traders, seed = payload
                self.redraw()
                for name, x, z in traders:
                    self.traders.insert("end", f"Trader {name}   ·   X {x:,}   Z {z:,}")
                self.status.set(f"Seed {seed} · {len(traders)} traders. Save this world or change the seed and regenerate.")
            elif kind == "idle":
                self.set_busy(False)
                if self.closing:
                    self.close()
                    return
        self.after(100, self.poll)

    def discard_preview(self):
        if self.job:
            shutil.rmtree(self.job, ignore_errors=True)
        self.job = self.world = None

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel.set()
            return
        self.discard_preview()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
