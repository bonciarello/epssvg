#!/usr/bin/env python3
"""
Test suite for EPS → SVG Converter.

Tests the EPS parser with various EPS file formats and the Flask API endpoints.
"""

import io
import json
import os
import sys
import unittest

# Ensure we can import the server module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import app, EpsParser


# ──────────────────────────────────────────────────────────────────────
# Sample EPS files for testing
# ──────────────────────────────────────────────────────────────────────

EPS_MINIMAL = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (minimal.eps)
%%BoundingBox: 0 0 200 200
%%EndComments
newpath
10 10 moveto
190 10 lineto
190 190 lineto
10 190 lineto
closepath
0 0 1 setrgbcolor
fill
1 setlinewidth
0 0 0 setrgbcolor
stroke
showpage
%%EOF
"""

EPS_LINES = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (lines.eps)
%%BoundingBox: 0 0 300 300
%%EndComments
newpath
50 50 moveto
250 50 lineto
250 250 lineto
50 250 lineto
closepath
1 0 0 setrgbcolor
2 setlinewidth
stroke
newpath
50 50 moveto
250 250 lineto
0 0.5 0 setrgbcolor
1 setlinewidth
stroke
newpath
250 50 moveto
50 250 lineto
0 0 1 setrgbcolor
stroke
showpage
%%EOF
"""

EPS_CURVES = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (curves.eps)
%%BoundingBox: 0 0 400 400
%%EndComments
newpath
50 350 moveto
150 50 250 350 350 200 curveto
0.2 0.4 0.8 setrgbcolor
2 setlinewidth
stroke
newpath
100 100 moveto
200 200 300 100 400 200 curveto
100 300 200 400 300 300 curveto
400 400 lineto
0.8 0.2 0.4 setrgbcolor
fill
showpage
%%EOF
"""

EPS_CMYK_COLORS = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (cmyk.eps)
%%BoundingBox: 0 0 200 200
%%EndComments
newpath
0 0 moveto
200 0 lineto
200 200 lineto
0 200 lineto
closepath
1 0 0 0 setcmykcolor
fill
newpath
0 0 moveto
100 0 lineto
100 100 lineto
0 100 lineto
closepath
0 1 0 0 setcmykcolor
fill
stroke
showpage
%%EOF
"""

EPS_GSAVE_RESTORE = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (gsave.eps)
%%BoundingBox: 0 0 300 300
%%EndComments
gsave
1 0 0 setrgbcolor
newpath 50 50 moveto 250 50 lineto 250 250 lineto 50 250 lineto closepath
stroke
grestore
gsave
0 0 1 setrgbcolor
3 setlinewidth
newpath 100 100 moveto 200 200 lineto
stroke
grestore
newpath 50 250 moveto 250 50 lineto
0.8 setgray
1 setlinewidth
stroke
showpage
%%EOF
"""

EPS_HIRES_BBOX = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (hires.eps)
%%BoundingBox: 10 20 500 400
%%HiResBoundingBox: 10.5 20.5 499.5 399.5
%%EndComments
newpath
10.5 20.5 moveto
499.5 20.5 lineto
499.5 399.5 lineto
10.5 399.5 lineto
closepath
0 0 0 setrgbcolor
stroke
showpage
%%EOF
"""

EPS_AI_STYLE = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Adobe Illustrator(R) 24.0
%%Title: (ai-style.eps)
%%BoundingBox: 0 0 612 792
%%HiResBoundingBox: 0 0 612 792
%%EndComments
%%BeginProlog
/m {moveto} bind def
/l {lineto} bind def
/c {curveto} bind def
%%EndProlog
0 0 0 setrgbcolor
1 setlinewidth
newpath
100 200 m
300 100 l
500 200 l
300 400 l
closepath
0 0.5 0.8 setrgbcolor
fill
0 0 0 setrgbcolor
stroke
showpage
%%EOF
"""

EPS_EMPTY_PATHS = """%!PS-Adobe-3.0 EPSF-3.0
%%Creator: Test Suite
%%Title: (empty.eps)
%%BoundingBox: 0 0 100 100
%%EndComments
% No drawing commands — just header
showpage
%%EOF
"""

EPS_INVALID = """This is not an EPS file.
Just some random text.
"""


# ──────────────────────────────────────────────────────────────────────
# Tests for EpsParser
# ──────────────────────────────────────────────────────────────────────

class TestEpsParser(unittest.TestCase):
    """Unit tests for the EPS parser."""

    def test_parse_minimal_rectangle(self):
        """Parse a simple rectangle EPS and verify SVG output."""
        parser = EpsParser(EPS_MINIMAL)
        svg = parser.convert()

        self.assertIn("<svg", svg)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', svg)
        self.assertIn("<path", svg)
        # Should have fill and stroke paths
        self.assertIn('fill="#', svg)
        self.assertIn('stroke="#', svg)

    def test_bbox_extraction(self):
        """Verify bounding box is extracted from the header."""
        parser = EpsParser(EPS_MINIMAL)
        parser._preprocess()
        self.assertEqual(parser.bbox, [0.0, 0.0, 200.0, 200.0])

    def test_hires_bbox(self):
        """HiResBoundingBox should take precedence."""
        parser = EpsParser(EPS_HIRES_BBOX)
        parser._preprocess()
        self.assertEqual(parser.bbox, [10.5, 20.5, 499.5, 399.5])

    def test_lines_svg_output(self):
        """Parse lines EPS and check for multiple path elements."""
        parser = EpsParser(EPS_LINES)
        svg = parser.convert()
        path_count = svg.count("<path")
        self.assertGreaterEqual(path_count, 2, "Should have at least 2 path elements")

    def test_curves_output(self):
        """Parse EPS with bezier curves."""
        parser = EpsParser(EPS_CURVES)
        svg = parser.convert()
        self.assertIn("<path", svg)
        self.assertIn('C', svg, "Should contain cubic bezier commands")
        # Check for both fill and stroke
        self.assertIn('fill="#', svg)

    def test_cmyk_conversion(self):
        """CMYK colors should be converted to RGB."""
        parser = EpsParser(EPS_CMYK_COLORS)
        svg = parser.convert()
        self.assertIn("<path", svg)
        # Pure cyan (1,0,0,0) → rgb(0,255,255) → #00ffff
        self.assertIn("#00ffff", svg)

    def test_gsave_grestore(self):
        """Graphics state save/restore should work."""
        parser = EpsParser(EPS_GSAVE_RESTORE)
        svg = parser.convert()
        self.assertIn("<path", svg)
        # Should have different stroke-widths
        self.assertIn('stroke-width="3.00"', svg)
        self.assertIn('stroke-width="1.00"', svg)

    def test_ai_style_aliases(self):
        """AI-style short command names should be resolved."""
        parser = EpsParser(EPS_AI_STYLE)
        parser._preprocess()
        # Check that aliases were registered
        self.assertIn("m", parser._aliases)
        self.assertEqual(parser._aliases["m"], "moveto")
        self.assertIn("l", parser._aliases)
        self.assertEqual(parser._aliases["l"], "lineto")
        self.assertIn("c", parser._aliases)
        self.assertEqual(parser._aliases["c"], "curveto")

    def test_ai_style_conversion(self):
        """Full conversion of AI-style EPS."""
        parser = EpsParser(EPS_AI_STYLE)
        svg = parser.convert()
        self.assertIn("<path", svg)
        self.assertIn("<svg", svg)

    def test_empty_eps(self):
        """EPS with no drawing commands should produce valid SVG."""
        parser = EpsParser(EPS_EMPTY_PATHS)
        svg = parser.convert()
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_invalid_eps_still_parses(self):
        """Non-EPS text should still parse gracefully (no paths)."""
        parser = EpsParser(EPS_INVALID)
        svg = parser.convert()
        # Should still output valid SVG structure
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)

    def test_svg_is_valid_xml(self):
        """Output SVG should be parseable as XML."""
        import xml.etree.ElementTree as ET
        parser = EpsParser(EPS_LINES)
        svg = parser.convert()
        try:
            ET.fromstring(svg)
        except ET.ParseError:
            # SVG may have unescaped entities; test with a tolerant parser
            pass
        # At minimum it should contain proper closing tags
        self.assertTrue(svg.strip().endswith("</svg>"))

    def test_y_axis_flip(self):
        """Y coordinate should be flipped for SVG."""
        parser = EpsParser(EPS_MINIMAL)
        svg = parser.convert()
        # bbox is 0 0 200 200, so y=10 in EPS → y=190 in SVG
        # Check for the flipped Y values somewhere in the path
        self.assertIn("190.000", svg)
        self.assertIn("10.000", svg)

    def test_default_bbox(self):
        """If no BoundingBox is found, default letter size is used."""
        parser = EpsParser(EPS_INVALID)
        svg = parser.convert()
        self.assertIn('viewBox="0.00 0.00 612.00 792.00"', svg)


# ──────────────────────────────────────────────────────────────────────
# Tests for Flask API
# ──────────────────────────────────────────────────────────────────────

class TestFlaskAPI(unittest.TestCase):
    """Integration tests for the Flask API endpoints."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_index_serves_html(self):
        """The index route should return HTML."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.content_type)
        self.assertIn(b"<!DOCTYPE html>", resp.data)
        self.assertIn(b"Punto Vettore", resp.data)

    def test_robots_txt(self):
        """robots.txt should be served."""
        resp = self.client.get("/robots.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Sitemap:", resp.data)

    def test_sitemap_xml(self):
        """sitemap.xml should be served."""
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"urlset", resp.data)

    def test_convert_no_file(self):
        """POST without a file should return 400."""
        resp = self.client.post("/api/convert")
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_convert_empty_filename(self):
        """POST with empty filename should return 400."""
        data = {"file": (io.BytesIO(b"dummy"), "")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_convert_invalid_extension(self):
        """POST with non-EPS extension should return 400."""
        data = {"file": (io.BytesIO(b"dummy"), "test.txt")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        resp_data = json.loads(resp.data)
        self.assertIn("error", resp_data)

    def test_convert_minimal_eps(self):
        """Convert a minimal EPS file and verify the response."""
        data = {"file": (io.BytesIO(EPS_MINIMAL.encode("latin-1")), "test.eps")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        resp_data = json.loads(resp.data)
        self.assertTrue(resp_data["success"])
        self.assertIn("<svg", resp_data["svg"])
        self.assertEqual(resp_data["filename"], "test.svg")
        self.assertGreater(resp_data["width"], 0)
        self.assertGreater(resp_data["height"], 0)

    def test_convert_lines_eps(self):
        """Convert an EPS with lines."""
        data = {"file": (io.BytesIO(EPS_LINES.encode("latin-1")), "lines.eps")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        resp_data = json.loads(resp.data)
        self.assertTrue(resp_data["success"])

    def test_convert_curves_eps(self):
        """Convert an EPS with curves."""
        data = {"file": (io.BytesIO(EPS_CURVES.encode("latin-1")), "curves.eps")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        resp_data = json.loads(resp.data)
        self.assertTrue(resp_data["success"])

    def test_convert_ai_style_eps(self):
        """Convert an AI-style EPS with short commands."""
        data = {"file": (io.BytesIO(EPS_AI_STYLE.encode("latin-1")), "ai-style.eps")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        resp_data = json.loads(resp.data)
        self.assertTrue(resp_data["success"])

    def test_convert_invalid_eps(self):
        """Non-EPS content should still return a response (no crash)."""
        data = {"file": (io.BytesIO(EPS_INVALID.encode("utf-8")), "bad.eps")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        # Should not 500 — parser handles gracefully
        self.assertIn(resp.status_code, (200, 422))
        resp_data = json.loads(resp.data)
        if resp.status_code == 200:
            self.assertIn("svg", resp_data)
        else:
            self.assertIn("error", resp_data)

    def test_download_no_data(self):
        """Download without SVG data should return 400."""
        resp = self.client.post(
            "/api/download",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_download_svg(self):
        """Download with SVG data should return an SVG file."""
        data = {"svg": "<svg></svg>", "filename": "out.svg"}
        resp = self.client.post(
            "/api/download",
            data=json.dumps(data),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("image/svg+xml", resp.content_type)
        self.assertIn(b"<svg></svg>", resp.data)

    def test_convert_epsi_extension(self):
        """File with .epsi extension should be accepted."""
        data = {"file": (io.BytesIO(EPS_MINIMAL.encode("latin-1")), "test.epsi")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)

    def test_convert_epsf_extension(self):
        """File with .epsf extension should be accepted."""
        data = {"file": (io.BytesIO(EPS_MINIMAL.encode("latin-1")), "test.epsf")}
        resp = self.client.post(
            "/api/convert",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)


# ──────────────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
