#!/usr/bin/env python3
"""
EPS → SVG Converter — Server-side EPS parsing and SVG generation.

Parses Encapsulated PostScript files and produces clean, standard SVG.
Handles common AI-generated EPS, CorelDRAW EPS, and hand-coded PostScript.
"""

import os
import re
import math
import io
import json
import logging

from flask import Flask, request, jsonify, send_file

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("eps2svg")

app = Flask(__name__, static_folder="static", static_url_path="")


# ──────────────────────────────────────────────────────────────────────
# EPS Parser — converts EPS to SVG through a PostScript interpreter
# ──────────────────────────────────────────────────────────────────────

class EpsParser:
    """Minimal PostScript interpreter that extracts vector paths from EPS
    and emits clean SVG."""

    # Standard PostScript operators we handle
    PATH_OPS = {
        "moveto", "lineto", "curveto", "rcurveto", "rmoveto", "rlineto",
        "closepath", "newpath", "fill", "stroke", "eofill", "eoclip",
        "gsave", "grestore", "grestoreall",
        "setrgbcolor", "setcmykcolor", "setgray", "setcolorspace",
        "setlinewidth", "setlinecap", "setlinejoin", "setmiterlimit",
        "setdash", "currentpoint",
        "translate", "scale", "rotate", "concat", "setmatrix",
        "show", "showpage", "copypage", "erasepage",
    }

    def __init__(self, content: str):
        self.raw = content
        # Bounding box [llx, lly, urx, ury]
        self.bbox = [0.0, 0.0, 612.0, 792.0]
        # Collected SVG elements
        self.elements: list[str] = []
        self._groups: list[list[str]] = [[]]  # stack of element groups for gsave/grestore
        # Current path being built: list of (cmd, *coords)
        self._current_path: list[tuple] = []
        self._first_point = (0.0, 0.0)
        # Graphics state
        self._rgb = [0.0, 0.0, 0.0]          # current stroke/fill colour
        self._fill_rgb = None                  # None → not filling; [r,g,b] → filling
        self._line_width = 1.0
        self._line_cap = "butt"
        self._line_join = "miter"
        self._miter_limit = 10.0
        self._dash_array: list[float] | None = None
        # State stack for gsave/grestore
        self._state_stack: list[dict] = []
        # Operand stack (PostScript is stack-based)
        self._stack: list = []
        # Procedure name → canonical operator
        self._aliases: dict[str, str] = {}

    # ── preprocessing ────────────────────────────────────────────────

    def _preprocess(self) -> None:
        """Extract header metadata and short-name aliases."""
        raw = self.raw

        # 1. BoundingBox — prefer HiResBoundingBox if present
        hires = re.search(
            r"%%HiResBoundingBox:\s*"
            r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)",
            raw,
        )
        if hires:
            self.bbox = [float(hires.group(i)) for i in range(1, 5)]
            log.info("HiResBoundingBox: %s", self.bbox)
        else:
            bbox = re.search(
                r"%%BoundingBox:\s*"
                r"([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)",
                raw,
            )
            if bbox:
                self.bbox = [float(bbox.group(i)) for i in range(1, 5)]
                log.info("BoundingBox: %s", self.bbox)

        # 2. Procedure aliases  e.g.  /m {moveto} bind def
        # Use a simple scan: match /name { body } (bind)? def
        # body must be a single operator token for us to treat it as alias.
        for m in re.finditer(
            r"/(\w+)\s*\{\s*(\w+)\s*\}\s*(?:bind\s*)?def", raw
        ):
            name, body = m.group(1), m.group(2)
            if body in self.PATH_OPS:
                self._aliases[name] = body

        log.info("Registered %d aliases", len(self._aliases))

    # ── tokeniser ────────────────────────────────────────────────────

    def _tokenize(self) -> list[str]:
        """Split the EPS content into PostScript tokens.

        Handles: numbers, names (/…), strings (…), procedures {…},
        arrays […], DSC comments, and regular line comments.
        """
        text = self.raw
        tokens: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            c = text[i]

            # Whitespace
            if c in " \t\n\r":
                i += 1
                continue

            # Line comment  % …
            if c == "%":
                i += 1
                while i < n and text[i] != "\n":
                    i += 1
                continue

            # Number (integer or real, possibly negative)
            if c.isdigit() or (
                c == "-"
                and i + 1 < n
                and (text[i + 1].isdigit() or text[i + 1] == ".")
            ) or c == ".":
                start = i
                if c == "-":
                    i += 1
                # consume digits, dot, exponent
                while i < n and (
                    text[i].isdigit()
                    or text[i] == "."
                    or text[i] in ("e", "E")
                    or (text[i] in ("-", "+") and i > start and text[i - 1] in ("e", "E"))
                ):
                    i += 1
                tokens.append(text[start:i])
                continue

            # String  ( … )
            if c == "(":
                depth = 1
                i += 1
                start = i
                while i < n and depth > 0:
                    if text[i] == "\\":
                        i += 1  # skip escaped char
                    elif text[i] == "(":
                        depth += 1
                    elif text[i] == ")":
                        depth -= 1
                    i += 1
                # store the raw string token (we skip it in execution)
                tokens.append(text[start - 1 : i])
                continue

            # Procedure  { … }  — we skip nested bodies
            if c == "{":
                depth = 1
                i += 1
                while i < n and depth > 0:
                    if text[i] == "\\":
                        i += 1
                    elif text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                tokens.append("{...}")  # opaque placeholder
                continue

            # Array  [ and ]
            if c in "[]":
                tokens.append(c)
                i += 1
                continue

            # Name  /name
            if c == "/":
                start = i
                i += 1
                while i < n and text[i] not in " \t\n\r()[]{}%":
                    i += 1
                tokens.append(text[start:i])
                continue

            # Regular token (operator, keyword)
            start = i
            while i < n and text[i] not in " \t\n\r()[]{}%":
                i += 1
            if i > start:
                t = text[start:i]
                # Skip binary garbage (non-ASCII heavy tokens)
                if not re.search(r"[^\x20-\x7e]", t):
                    tokens.append(t)

        return tokens

    # ── operand stack ────────────────────────────────────────────────

    def _push(self, val):
        self._stack.append(val)

    def _pop(self):
        return self._stack.pop() if self._stack else 0

    def _pop_num(self) -> float:
        v = self._pop()
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    def _pop_n_nums(self, n: int) -> list[float]:
        """Pop *n* numbers from the stack (preserving source order)."""
        out = [self._pop_num() for _ in range(n)]
        out.reverse()
        return out

    # ── Y-axis flip for SVG ─────────────────────────────────────────

    def _y(self, eps_y: float) -> float:
        """Convert EPS y (bottom-left origin) to SVG y (top-left)."""
        return self.bbox[3] - eps_y + self.bbox[1]

    # ── path finalisation ───────────────────────────────────────────

    def _emit_path(self) -> None:
        """Convert the accumulated path into an SVG <path> element."""
        if not self._current_path:
            return

        d_parts: list[str] = []
        for item in self._current_path:
            cmd = item[0]
            if cmd == "M":
                x, y = item[1], item[2]
                d_parts.append(f"M{x:.3f},{self._y(y):.3f}")
            elif cmd == "L":
                x, y = item[1], item[2]
                d_parts.append(f"L{x:.3f},{self._y(y):.3f}")
            elif cmd == "C":
                x1, y1, x2, y2, x3, y3 = item[1:]
                d_parts.append(
                    f"C{x1:.3f},{self._y(y1):.3f} "
                    f"{x2:.3f},{self._y(y2):.3f} "
                    f"{x3:.3f},{self._y(y3):.3f}"
                )
            elif cmd == "Z":
                d_parts.append("Z")

        d = " ".join(d_parts)
        r, g, b = self._rgb
        stroke_hex = f"#{min(255, int(r * 255)):02x}{min(255, int(g * 255)):02x}{min(255, int(b * 255)):02x}"

        fill = "none"
        if self._fill_rgb is not None:
            fr, fg, fb = self._fill_rgb
            fill = f"#{min(255, int(fr * 255)):02x}{min(255, int(fg * 255)):02x}{min(255, int(fb * 255)):02x}"

        attrs = [
            f'd="{d}"',
            f'fill="{fill}"',
            f'stroke="{stroke_hex}"',
            f'stroke-width="{self._line_width:.2f}"',
            f'stroke-linecap="{self._line_cap}"',
            f'stroke-linejoin="{self._line_join}"',
        ]
        if self._dash_array:
            dashes = ",".join(f"{v:.2f}" for v in self._dash_array)
            attrs.append(f'stroke-dasharray="{dashes}"')

        self._groups[-1].append(f'<path {" ".join(attrs)}/>')
        self._current_path = []
        self._fill_rgb = None

    # ── operator dispatch ────────────────────────────────────────────

    def _execute(self, op: str) -> None:
        """Execute a single PostScript operator."""
        # Resolve alias
        op = self._aliases.get(op, op)

        # ── path construction ────────────────────────────────────
        if op in ("moveto",):
            y, x = self._pop_num(), self._pop_num()
            if self._current_path:
                self._emit_path()
            self._current_path = [("M", x, y)]
            self._first_point = (x, y)

        elif op in ("rmoveto",):
            dy, dx = self._pop_num(), self._pop_num()
            if self._current_path:
                self._emit_path()
            cx, cy = (self._current_path[-1][1], self._current_path[-1][2]) if self._current_path else (0.0, 0.0)
            self._current_path = [("M", cx + dx, cy + dy)]
            self._first_point = (cx + dx, cy + dy)

        elif op in ("lineto",):
            y, x = self._pop_num(), self._pop_num()
            self._current_path.append(("L", x, y))

        elif op in ("rlineto",):
            dy, dx = self._pop_num(), self._pop_num()
            if self._current_path:
                cx, cy = self._current_path[-1][1], self._current_path[-1][2]
            else:
                cx, cy = 0.0, 0.0
            self._current_path.append(("L", cx + dx, cy + dy))

        elif op in ("curveto",):
            y3, x3, y2, x2, y1, x1 = self._pop_n_nums(6)
            self._current_path.append(("C", x1, y1, x2, y2, x3, y3))

        elif op in ("rcurveto",):
            dy3, dx3, dy2, dx2, dy1, dx1 = self._pop_n_nums(6)
            if self._current_path:
                cx, cy = self._current_path[-1][1], self._current_path[-1][2]
            else:
                cx, cy = 0.0, 0.0
            self._current_path.append(
                ("C", cx + dx1, cy + dy1, cx + dx2, cy + dy2, cx + dx3, cy + dy3)
            )

        elif op == "closepath":
            if self._current_path:
                self._current_path.append(("Z",))
                # Move to first point of this subpath
                self._current_path.append(("M", self._first_point[0], self._first_point[1]))

        elif op == "newpath":
            if self._current_path:
                self._emit_path()
            self._current_path = []

        # ── painting ─────────────────────────────────────────────
        elif op in ("stroke",):
            self._fill_rgb = None
            self._emit_path()

        elif op in ("fill", "eofill"):
            self._fill_rgb = self._rgb[:]
            self._emit_path()

        elif op == "eoclip":
            pass  # clipping not supported

        # ── graphics state ───────────────────────────────────────
        elif op == "gsave":
            self._state_stack.append(
                {
                    "rgb": self._rgb[:],
                    "fill_rgb": self._fill_rgb[:] if self._fill_rgb else None,
                    "line_width": self._line_width,
                    "line_cap": self._line_cap,
                    "line_join": self._line_join,
                    "miter_limit": self._miter_limit,
                    "dash_array": self._dash_array[:] if self._dash_array else None,
                }
            )
            self._groups.append([])

        elif op in ("grestore",):
            if self._current_path:
                self._emit_path()
            if self._state_stack:
                st = self._state_stack.pop()
                self._rgb = st["rgb"]
                self._fill_rgb = st["fill_rgb"]
                self._line_width = st["line_width"]
                self._line_cap = st["line_cap"]
                self._line_join = st["line_join"]
                self._miter_limit = st["miter_limit"]
                self._dash_array = st["dash_array"]
            # Close the current group
            if len(self._groups) > 1:
                closed = self._groups.pop()
                self._groups[-1].extend(closed)

        elif op == "grestoreall":
            if self._current_path:
                self._emit_path()
            while len(self._state_stack) > 0:
                self._state_stack.pop()
            while len(self._groups) > 1:
                closed = self._groups.pop()
                self._groups[-1].extend(closed)

        # ── colour ───────────────────────────────────────────────
        elif op == "setrgbcolor":
            r, g, b = self._pop_n_nums(3)
            self._rgb = [r, g, b]

        elif op == "setcmykcolor":
            c, m, y_val, k = self._pop_n_nums(4)
            # Simple CMYK → RGB
            r = max(0.0, 1.0 - min(1.0, c + k))
            g = max(0.0, 1.0 - min(1.0, m + k))
            b = max(0.0, 1.0 - min(1.0, y_val + k))
            self._rgb = [r, g, b]

        elif op == "setgray":
            gray = self._pop_num()
            self._rgb = [gray, gray, gray]

        # ── stroke style ─────────────────────────────────────────
        elif op == "setlinewidth":
            self._line_width = abs(self._pop_num())

        elif op == "setlinecap":
            cap_map = {0: "butt", 1: "round", 2: "square"}
            self._line_cap = cap_map.get(int(self._pop_num()), "butt")

        elif op == "setlinejoin":
            join_map = {0: "miter", 1: "round", 2: "bevel"}
            self._line_join = join_map.get(int(self._pop_num()), "miter")

        elif op == "setmiterlimit":
            self._miter_limit = self._pop_num()

        elif op == "setdash":
            # Stack: array offset
            offset = self._pop_num()
            # The array is currently on the stack as '[' marker — skip for now
            self._dash_array = None  # simplified: skip dash pattern parsing

        # ── transformations (simplified) ─────────────────────────
        elif op in ("translate", "scale", "rotate", "concat", "setmatrix"):
            # Pop operands to keep stack clean but don't apply transforms
            # (a full CTM would require much more complexity)
            if op == "translate":
                self._pop_n_nums(2)  # x y
            elif op == "scale":
                self._pop_n_nums(2)  # sx sy
            elif op == "rotate":
                self._pop_num()  # angle
            elif op == "concat":
                self._pop_n_nums(6)  # matrix
            elif op == "setmatrix":
                self._pop_n_nums(6)  # matrix

        # ── page / text (skip) ───────────────────────────────────
        elif op in ("show", "showpage", "copypage", "erasepage", "currentpoint"):
            pass

    # ── public API ───────────────────────────────────────────────────

    def convert(self) -> str:
        """Run the conversion pipeline; return an SVG string."""
        self._preprocess()
        tokens = self._tokenize()

        for tok in tokens:
            # Skip opaque procedure bodies and name definitions
            if tok == "{...}":
                continue
            if tok.startswith("/"):
                continue
            if tok in ("[", "]"):
                continue
            # Number → push
            try:
                num = float(tok)
                self._push(num)
                continue
            except ValueError:
                pass
            # Operator → execute
            if tok in self.PATH_OPS or tok in self._aliases:
                self._execute(tok)
            # else: unknown token, silently skip

        # Flush any remaining path
        if self._current_path:
            self._fill_rgb = None
            self._emit_path()

        # Collect all elements
        all_el = self._groups[0] if self._groups else []

        w = self.bbox[2] - self.bbox[0]
        h = self.bbox[3] - self.bbox[1]

        self.svg_width = w
        self.svg_height = h

        elements_str = "\n    ".join(all_el) if all_el else (
            '<!-- No vector paths found in EPS -->'
        )

        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg"\n'
            f'     viewBox="{self.bbox[0]:.2f} {self.bbox[1]:.2f} {w:.2f} {h:.2f}"\n'
            f'     width="{w:.2f}" height="{h:.2f}"'
            ">\n"
            f"    {elements_str}\n"
            "</svg>"
        )
        return svg


# ──────────────────────────────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the frontend single-page application."""
    return app.send_static_file("index.html")


@app.route("/api/convert", methods=["POST"])
def api_convert():
    """Accept an EPS file upload, parse it, and return the SVG."""
    if "file" not in request.files:
        return jsonify({"error": "Nessun file ricevuto. Carica un file EPS."}), 400

    file = request.files["file"]
    if not file.filename or file.filename.strip() == "":
        return jsonify({"error": "Nessun file selezionato."}), 400

    fname = file.filename.lower()
    if not (fname.endswith(".eps") or fname.endswith(".epsf") or fname.endswith(".epsi")):
        return jsonify(
            {"error": "Formato non supportato. Carica un file con estensione .eps, .epsf o .epsi."}
        ), 400

    try:
        raw = file.read()
        # EPS files are typically Latin-1; fall back to UTF-8
        try:
            text = raw.decode("latin-1")
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("utf-8", errors="replace")

        log.info("Parsing EPS file: %s (%d bytes)", file.filename, len(raw))

        parser = EpsParser(text)
        svg = parser.convert()

        out_name = os.path.splitext(file.filename)[0] + ".svg"

        return jsonify(
            {
                "success": True,
                "svg": svg,
                "filename": out_name,
                "width": parser.svg_width,
                "height": parser.svg_height,
            }
        )
    except Exception as exc:
        log.exception("Conversion failed")
        return jsonify({"error": f"Errore durante la conversione: {exc}"}), 422


@app.route("/api/download", methods=["POST"])
def api_download():
    """Return SVG content as a downloadable file."""
    data = request.get_json(silent=True)
    if not data or "svg" not in data:
        return jsonify({"error": "Contenuto SVG mancante."}), 400

    svg_content = data["svg"]
    filename = data.get("filename", "converted.svg")

    return send_file(
        io.BytesIO(svg_content.encode("utf-8")),
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name=filename,
    )


# ──────────────────────────────────────────────────────────────────────
# SEO static files
# ──────────────────────────────────────────────────────────────────────

@app.route("/robots.txt")
def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Sitemap: https://cristianporco.it/app/"
        "convertitore-di-file-eps-in-svg-con-parsing-server-side/sitemap.xml\n"
    ), 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        "    <loc>https://cristianporco.it/app/convertitore-di-file-eps-in-svg-con-parsing-server-side/</loc>\n"
        "    <changefreq>monthly</changefreq>\n"
        "    <priority>0.8</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    ), 200, {"Content-Type": "application/xml"}


# ──────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4599))
    log.info("Starting EPS→SVG converter on http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
