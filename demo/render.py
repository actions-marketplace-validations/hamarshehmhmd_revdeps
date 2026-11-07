"""Render the revdeps narrated demo: real command output + edge-tts narration.

Usage: python demo/render.py
Needs ffmpeg, edge-tts, Pillow, node/npm, network, and revdeps on PATH via /tmp/rdvenv.
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(ROOT, "demo.mp4")
SDKBIN = "/tmp/rdvenv/bin"
SANDBOX = "/tmp/rddemo"
W, H, FPS = 1280, 720, 24
VOICE = "en-US-AndrewNeural"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BG, FG = (24, 26, 33), (220, 223, 228)
ANSI = {"31": (255, 95, 95), "32": (95, 215, 120), "33": (240, 200, 80), "2": (130, 135, 145), "36": (90, 200, 230), "1": FG}
PROMPT = "dev@laptop $ "

SCENES = [
    {"kind": "title", "title": "revdeps", "sub": "will my release break the projects\nthat depend on me?",
     "say": "This is rev deps, a small open source command line tool for library maintainers. It tells you "
            "which projects that depend on your library your next release would break, before you publish it."},
    {"kind": "bullets", "title": "The problem",
     "bullets": ["Your tests pass. Semver says minor. Users still break.",
                 "People depend on behaviour you never promised",
                 "Rust has crater, R has revdepcheck, Vite has ecosystem-ci",
                 "Most npm and PyPI maintainers have nothing",
                 "You find out from the issue tracker after release"],
     "say": "Your own tests pass, semver says it's a minor release, and you still break people, because they "
            "rely on behaviour you never meant to promise. Rust has crater, R has rev dep check, and Vite "
            "maintains its own ecosystem C I. Most npm and PyPI maintainers have nothing like that, so they "
            "find out from the issue tracker the morning after a release."},
    {"kind": "term", "cwd": "/tmp/rw/escape-html",
     "cmds": ["revdeps discover escape-html -e npm -n 6 --color always"],
     "say": "Step one: discover. rev deps asks the ecosystems dot m s packages A P I for the most downloaded "
            "packages that depend on yours, and keeps the ones with a public repo. With dash dash write, it "
            "turns them into a rev deps dot toml config you can edit."},
    {"kind": "term", "cwd": "EX/npm",
     "cmds": ["diff -u slugkit-1.0.0/index.js slugkit/index.js | tail -n +3 | grep '^[-+]'"],
     "say": "Here is a bundled example. slugkit one point oh is released. The local change strips accents, "
            "which is a fix, and renames the max length option to limit, which feels like a harmless cleanup."},
    {"kind": "term", "cwd": "EX/npm",
     "cmds": ["revdeps run --color always"],
     "say": "rev deps run tests four apps that use slugkit. Each one is installed with the released version "
            "and tested, that's the baseline. Then your packed build is swapped in and the tests run again. "
            "The U R L shortener passed before and fails now, so it's broken, and because its tests write "
            "JUnit, you see the two exact tests and assertion messages. The recipe site gets fixed by your "
            "change, and the legacy C M S was already failing, so it isn't blamed on you."},
    {"kind": "term", "cwd": "EX/pypi",
     "cmds": ["revdeps run --color always"],
     "say": "PyPI works the same way, with a fresh virtual env per dependent. This tinyconf change returns "
            "typed values instead of strings, and the feature flags app calls dot lower on a boolean. "
            "Flaky tests get a retry, so a one off failure shows up as flaky, not broken."},
    {"kind": "term", "cwd": "/tmp/rw/escape-html",
     "cmds": ["git diff -U0 | grep '^[-+] '", "revdeps run --only serve-index-75lb,celebrate -j 2 --color always"],
     "say": "And on real projects: in a checkout of escape-html, the ampersand escape was changed to a numeric "
            "entity. rev deps clones serve-index and celebrate from GitHub, installs them, and runs their "
            "real test suites. serve-index asserts on the old output, so it's flagged as broken."},
    {"kind": "bullets", "title": "What you get",
     "bullets": ["discover · run · baseline vs candidate",
                 "ok / BROKEN / fixed / already failing / flaky / error",
                 "Newly failing test names from JUnit XML",
                 "JSON, Markdown, GitHub step summary + Action",
                 "Exit 1 only when you broke something"],
     "say": "You get discover and run, a clear status for every dependent, the names of newly failing tests, "
            "JSON and Markdown reports, a GitHub Actions step summary, and a composite action. The exit code "
            "is one only when you actually broke something, so it can gate a release."},
    {"kind": "bullets", "title": "Try it",
     "bullets": ["curl -fsSL https://raw.githubusercontent.com/",
                 "   hamarshehmhmd/revdeps/main/install.sh | sh",
                 "revdeps discover --write && revdeps run",
                 "github.com/hamarshehmhmd/revdeps",
                 "GNU AGPL-3.0 (commercial licenses available)"],
     "say": "Install it with the one line installer, run discover, then run. It's licensed under the GNU "
            "A G P L version three or later, with commercial licenses available from the author. "
            "It's on GitHub at hamarsheh m h m d slash rev deps."},
]


async def tts(text, path):
    import edge_tts
    for attempt in range(8):
        try:
            await edge_tts.Communicate(text, VOICE, rate="+4%").save(path)
            return
        except Exception:
            if os.path.exists(path):
                os.remove(path)
            if attempt == 7:
                raise
            await asyncio.sleep(5)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        text=True,
    )
    return float(out.strip())


EXPORTS = {}


def run_cmd(cmd, cwd):
    if cmd.startswith("export ") and "=" in cmd:
        k, v = cmd[7:].split("=", 1)
        EXPORTS[k] = v
        return ""
    env = dict(os.environ, FORCE_COLOR="1", **EXPORTS)
    env.pop("NO_COLOR", None)
    env["PATH"] = SDKBIN + os.pathsep + env["PATH"]
    env["COLUMNS"] = "120"
    p = subprocess.run(
        ["bash", "-c", "shopt -s nullglob; " + cmd],
        cwd=cwd, capture_output=True, text=True, env=env,
    )
    return (p.stdout + p.stderr).rstrip("\n")


def parse_ansi(line):
    spans, color, bold = [], FG, False
    for part in re.split(r"(\x1b\[[0-9;]*m)", line):
        m = re.match(r"\x1b\[([0-9;]*)m", part)
        if m:
            for code in (m.group(1) or "0").split(";"):
                if code == "0":
                    color, bold = FG, False
                elif code == "1":
                    bold = True
                elif code in ANSI:
                    color = ANSI[code]
        elif part:
            spans.append((part, color, bold))
    return spans


class Term:
    def __init__(self):
        self.font = ImageFont.truetype(MONO, 14)
        self.bold = ImageFont.truetype(MONO_B, 14)
        self.cw = self.font.getlength("M")
        self.lh = 20
        self.cols = int((W - 80) / self.cw)

    def wrap(self, spans):
        lines, cur, n = [], [], 0
        for text, c, b in spans:
            while text:
                room = self.cols - n
                if len(text) <= room:
                    cur.append((text, c, b)); n += len(text); text = ""
                else:
                    cut = text.rfind(" ", 0, room + 1)
                    cut = cut if cut > 0 else room
                    cur.append((text[:cut], c, b)); lines.append(cur); cur, n = [], 0
                    text = "    " + text[cut:].lstrip(" ")
        lines.append(cur)
        return lines

    def frame(self, lines, cursor=False):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 14, W - 20, H - 14], 12, fill=(30, 33, 41), outline=(60, 64, 76))
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([40 + i * 24, 30, 54 + i * 24, 44], fill=c)
        d.text((W // 2, 37), "~ — bash", font=ImageFont.truetype(SANS, 15), fill=(150, 155, 165), anchor="mm")
        visible = lines[-30:]
        y = 64
        for ln in visible:
            x = 40
            for text, c, b in ln:
                d.text((x, y), text, font=self.bold if b else self.font, fill=c)
                x += self.cw * len(text)
            y += self.lh
        if cursor:
            last = visible[-1] if visible else []
            x = 40 + self.cw * sum(len(t) for t, _, _ in last)
            d.rectangle([x, y - self.lh + 3, x + self.cw - 2, y - 4], fill=FG)
        return img


def card(scene):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if scene["kind"] == "title":
        d.text((W // 2, 280), scene["title"], font=ImageFont.truetype(MONO_B, 84), fill=(95, 215, 120), anchor="mm")
        d.multiline_text((W // 2, 420), scene["sub"], font=ImageFont.truetype(SANS, 34), fill=FG, anchor="mm",
                         align="center", spacing=12)
        d.text((W // 2, 620), "github.com/hamarshehmhmd/revdeps  ·  discover · run  ·  npm + PyPI  ·  GNU AGPL-3.0",
               font=ImageFont.truetype(SANS, 22), fill=(150, 155, 165), anchor="mm")
    else:
        d.text((90, 90), scene["title"], font=ImageFont.truetype(SANS_B, 52), fill=(95, 215, 120))
        f = ImageFont.truetype(SANS, 29)
        y = 200
        for b in scene["bullets"]:
            if b and not b.startswith("   "):
                d.text((90, y), "•", font=f, fill=(240, 200, 80))
            d.text((130, y), b.strip(), font=f, fill=FG)
            y += 58 if b.startswith("   ") or not b else 62
    return img


class Writer:
    def __init__(self, path, audio):
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", audio,
             "-af", "apad", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
             "-shortest", path],
            stdin=subprocess.PIPE,
        )
        self.n = 0

    def put(self, img, count=1):
        data = img.tobytes()
        for _ in range(count):
            self.p.stdin.write(data)
        self.n += count

    def close(self):
        self.p.stdin.close()
        self.p.wait()
        if self.p.returncode:
            sys.exit("ffmpeg failed")


def render_scene(i, scene, workdir):
    workdir = scene.get("cwd", SANDBOX).replace("EX", os.path.join(SANDBOX, "examples"))
    audio = os.path.join(BUILD, f"s{i}.mp3")
    if not os.path.exists(audio):
        asyncio.run(tts(scene["say"], audio))
    total = int((duration(audio) + 0.7) * FPS)
    path = os.path.join(BUILD, f"s{i}.mp4")
    w = Writer(path, audio)
    if scene["kind"] != "term":
        w.put(card(scene), total)
        w.close()
        return path
    t = Term()
    shown = scene.get("show", scene["cmds"])
    outputs = [run_cmd(c, workdir) for c in scene["cmds"]]
    # Truncate very long check output so it fits the terminal window
    capped = []
    for out in outputs:
        lines = out.splitlines()
        if len(lines) > 26:
            lines = lines[:24] + ["…", lines[-1]]
        capped.append("\n".join(lines))
    outputs = capped
    steps = list(zip(shown, outputs))
    typing = sum(len(s) for s, _ in steps) / 32 * FPS
    pause = max(6, int((total - typing) / max(1, len(steps))) - 4)
    lines = []
    for cmd, out in steps:
        prompt = [(PROMPT, (95, 215, 120), True)]
        for k in range(0, len(cmd) + 1, 2):
            frame = t.frame(lines + t.wrap(prompt + [(cmd[:k], FG, False)]), cursor=True)
            w.put(frame)
        lines += t.wrap(prompt + [(cmd, FG, False)])
        for ol in out.splitlines():
            lines += t.wrap(parse_ansi(ol))
        lines.append([])
        hold = min(pause, max(0, total - w.n))
        w.put(t.frame(lines), hold)
    final = t.frame(lines + t.wrap([(PROMPT, (95, 215, 120), True)]), cursor=True)
    if w.n < total:
        w.put(final, total - w.n)
    w.close()
    return path


def main():
    os.makedirs(BUILD, exist_ok=True)
    if os.path.exists(SANDBOX):
        shutil.rmtree(SANDBOX)
    os.makedirs(SANDBOX)
    shutil.copytree(os.path.join(ROOT, "examples"), os.path.join(SANDBOX, "examples"),
                    ignore=shutil.ignore_patterns(".revdeps", "node_modules"))
    parts = [render_scene(i, s, SANDBOX) for i, s in enumerate(SCENES)]
    with open(os.path.join(BUILD, "list.txt"), "w") as fh:
        fh.writelines(f"file '{p}'\n" for p in parts)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
         os.path.join(BUILD, "list.txt"), "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", OUT],
        check=True,
    )
    with open(os.path.join(BUILD, "narration.txt"), "w") as fh:
        fh.write("\n\n".join(s["say"] for s in SCENES) + "\n")
    print(OUT, round(duration(OUT), 1), "s")


if __name__ == "__main__":
    main()
