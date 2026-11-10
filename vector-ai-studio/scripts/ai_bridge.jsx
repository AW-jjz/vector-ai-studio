#target illustrator

// Native .ai landing. Runs INSIDE Illustrator via DoJavaScript (ai_bridge.ps1).
// Placeholders are replaced at runtime by land_ai.py:
//   __SVG_PATH__  __OUT_PATH__  __OUTLINE_TEXT__  __RESULT_PATH__
//   __COMPAT__  __PDF_COMPATIBLE__  __EMBED_ICC__  __CMYK__
//   __BLEED_PT__  __LAYERS_FROM_GROUPS__
// Only saveAs() with IllustratorSaveOptions produces a real native editable .ai.
// Never renames an SVG/PDF, never writes a PDF-compatible container by hand.
//
// Every save option is set EXPLICITLY. The first version of this file passed a
// bare `new IllustratorSaveOptions()` and relied on defaults: pdfCompatible
// happens to default to true and embedICCProfile to false, so the deliverable
// looked fine until a client's prepress asked for CMYK or an older Illustrator
// refused to open it. Defaults are not a configuration.

(function () {
    var SVG_PATH = "__SVG_PATH__";
    var OUT_PATH = "__OUT_PATH__";
    var OUTLINE_TEXT = ("__OUTLINE_TEXT__" === "1");
    var COMPAT = "__COMPAT__";
    var PDF_COMPATIBLE = ("__PDF_COMPATIBLE__" === "1");
    var EMBED_ICC = ("__EMBED_ICC__" === "1");
    var TO_CMYK = ("__CMYK__" === "1");
    var BLEED_PT = parseFloat("__BLEED_PT__") || 0;
    var LAYERS_FROM_GROUPS = ("__LAYERS_FROM_GROUPS__" === "1");
    var RESULT_PATH = "__RESULT_PATH__";

    var notes = [];

    function note(s) { notes.push(s); }

    function finish(line) {
        if (RESULT_PATH.indexOf("__RESULT_") !== 0) {
            try {
                var rf = new File(RESULT_PATH);
                // Pin the encoding: this side reads the line back as UTF-8, and
                // ExtendScript's default encoding varies by version and locale.
                rf.encoding = "UTF-8";
                rf.open("w");
                rf.write(line);
                rf.close();
            } catch (e) { /* result file is best-effort; stdout still carries the line */ }
        }
        return line;
    }

    function readViewBox(path) {
        try {
            var f = new File(path);
            if (!f.open("r")) { return "unknown"; }
            var txt = f.read();
            f.close();
            var m = txt.match(/viewBox\s*=\s*["']([^"']+)["']/);
            return m ? m[1].replace(/\s+/g, "_") : "unknown";
        } catch (e) {
            return "unknown";
        }
    }

    // Set compatibility only when a version name is given explicitly.
    // "latest" means: leave the property alone so the installed Illustrator
    // writes its own native version. Hardcoding a constant such as
    // Compatibility.ILLUSTRATOR19 fails with "Invalid enumeration value" on
    // builds whose enum does not carry that name, and the failure is at save
    // time, i.e. after all the work is done.
    function applyCompat(opts, name) {
        if (!name || name === "latest") {
            note("compat:app_default");
            return;
        }
        try {
            opts.compatibility = Compatibility[name];
            note("compat:" + name);
        } catch (e) {
            note("compat:invalid_ignored:" + name);
        }
    }

    // Plain RGB -> CMYK. No colour management, no ICC intent: this is a
    // arithmetic conversion, which is all a script can honestly do. Any real
    // print workflow must still assign a profile and check the result.
    function rgbToCmyk(r, g, b) {
        var c = new CMYKColor();
        var rr = r / 255, gg = g / 255, bb = b / 255;
        var k = 1 - Math.max(rr, Math.max(gg, bb));
        if (k >= 1) {
            c.cyan = 0; c.magenta = 0; c.yellow = 0; c.black = 100;
            return c;
        }
        c.cyan = (1 - rr - k) / (1 - k) * 100;
        c.magenta = (1 - gg - k) / (1 - k) * 100;
        c.yellow = (1 - bb - k) / (1 - k) * 100;
        c.black = k * 100;
        return c;
    }

    // Convert every object colour in place.
    //
    // The menu command 'doc-color-cmyk' reports success without converting the
    // artwork: the saved file still carried /DeviceRGB. A step that says it
    // worked while the artefact says otherwise is worse than a step that fails,
    // so this walks the objects and converts the colours itself.
    function toCmyk(doc) {
        try { doc.documentColorSpace = DocumentColorSpace.CMYK; }
        catch (e) { note("cmyk:space_failed"); }
        var n = 0;
        for (var i = 0; i < doc.pathItems.length; i++) {
            var p = doc.pathItems[i];
            try {
                if (p.filled && p.fillColor && p.fillColor.typename === "RGBColor") {
                    var f = p.fillColor;
                    p.fillColor = rgbToCmyk(f.red, f.green, f.blue);
                    n++;
                }
            } catch (e) {}
            try {
                if (p.stroked && p.strokeColor && p.strokeColor.typename === "RGBColor") {
                    var s = p.strokeColor;
                    p.strokeColor = rgbToCmyk(s.red, s.green, s.blue);
                    n++;
                }
            } catch (e) {}
        }
        note("cmyk:objects=" + n + (n === 0 ? ":none_converted" : ":naive_no_icc"));
    }

    // Draw four corner crop marks outside the trim box, on a dedicated layer, so
    // a printer has something to align to. These are vector strokes, not images.
    function addTrimMarks(doc, trim) {
        try {
            var size = 18;
            var gap = 3;
            var L = trim[0], T = trim[1], R = trim[2], B = trim[3];
            var layer = doc.layers.add();
            layer.name = "TrimMarks";
            var segs = [
                [[L - gap, T], [L - gap - size, T]],
                [[R + gap, T], [R + gap + size, T]],
                [[L - gap, B], [L - gap - size, B]],
                [[R + gap, B], [R + gap + size, B]],
                [[L, T + gap], [L, T + gap + size]],
                [[R, T + gap], [R, T + gap + size]],
                [[L, B - gap], [L, B - gap - size]],
                [[R, B - gap], [R, B - gap - size]]
            ];
            for (var i = 0; i < segs.length; i++) {
                var p = layer.pathItems.add();
                p.setEntirePath(segs[i]);
                p.filled = false;
                p.stroked = true;
                p.strokeWidth = 0.25;
                // GrayColor 100 = black, unambiguously. The first version
                // assigned a CMYKColor with black=100 and the saved content
                // stream showed "1 1 1 RG" -- i.e. white marks on a white
                // sheet, invisible. Caught by decompressing the PDF stream,
                // not by looking at the file size.
                var ink = new GrayColor();
                ink.gray = 100;
                p.strokeColor = ink;
            }
            note("cropmarks:8");
        } catch (e) {
            note("cropmarks:failed");
        }
    }

    // Turn top-level groups into named layers so a client can toggle parts of
    // the artwork. Without this an SVG's <g> elements arrive as one flat layer.
    function layersFromGroups(doc) {
        try {
            var total = doc.groupItems.length;
            if (total <= 0) { note("layers:no_groups"); return 0; }
            var created = 0;
            for (var i = total - 1; i >= 0; i--) {
                var g = doc.groupItems[i];
                var nm = g.name || ("Group " + (i + 1));
                var lay = doc.layers.add();
                lay.name = nm;
                try { g.move(lay, ElementPlacement.PLACEATEND); created++; }
                catch (e) { try { lay.remove(); } catch (e2) {} }
            }
            note("layers:from_groups=" + created);
            return created;
        } catch (e) {
            note("layers:failed");
            return 0;
        }
    }

    try {
        var src = new File(SVG_PATH);
        if (!src.exists) { return finish("ERROR: SVG_NOT_FOUND"); }

        var doc = app.open(src);
        var raster = doc.rasterItems.length;
        var placed = doc.placedItems.length;
        var paths = doc.pathItems.length;

        if (raster !== 0 || placed !== 0 || paths <= 0) {
            doc.close(SaveOptions.DONOTSAVECHANGES);
            return finish("ERROR: STRUCTURE_ASSERT_FAILED|raster=" + raster +
                          "|placed=" + placed + "|paths=" + paths);
        }

        if (TO_CMYK) { toCmyk(doc); }

        if (OUTLINE_TEXT && doc.textFrames.length > 0) {
            var outlined = 0;
            for (var t = doc.textFrames.length - 1; t >= 0; t--) {
                try { doc.textFrames[t].createOutline(); outlined++; } catch (e) {}
            }
            note("text:outlined=" + outlined);
        } else if (doc.textFrames.length > 0) {
            note("text:live=" + doc.textFrames.length);
        }

        if (LAYERS_FROM_GROUPS) { layersFromGroups(doc); }

        var ab = doc.artboards[0].artboardRect;
        var trim = [ab[0], ab[1], ab[2], ab[3]];
        var W = Math.round(trim[2] - trim[0]);
        var H = Math.round(trim[1] - trim[3]);

        if (BLEED_PT > 0) {
            // Expand the artboard outward; the trim box is preserved for marks.
            var r = doc.artboards[0];
            r.artboardRect = [trim[0] - BLEED_PT, trim[1] + BLEED_PT,
                              trim[2] + BLEED_PT, trim[3] - BLEED_PT];
            addTrimMarks(doc, trim);
            note("bleed_pt=" + BLEED_PT);
        }

        var layers = doc.layers.length;

        var opts = new IllustratorSaveOptions();
        applyCompat(opts, COMPAT);
        opts.pdfCompatible = PDF_COMPATIBLE;
        opts.embedICCProfile = EMBED_ICC;
        opts.embedLinkedFiles = true;
        opts.compressed = true;
        opts.flattenOutput = OutputFlattening.PRESERVEAPPEARANCE;

        var outFile = new File(OUT_PATH);
        doc.saveAs(outFile, opts);
        doc.close(SaveOptions.DONOTSAVECHANGES);

        // Reopen the produced .ai and re-read the object counts (H1).
        var reopened = app.open(outFile);
        var r2 = reopened.rasterItems.length;
        var p2 = reopened.placedItems.length;
        var paths2 = reopened.pathItems.length;
        var layers2 = reopened.layers.length;

        // Read the crop-mark ink back from the reopened document. Parsing the
        // compressed PDF stream to find out what colour a mark is turned out to
        // be unreliable; asking Illustrator is not.
        var markInk = "none";
        try {
            for (var li = 0; li < reopened.layers.length; li++) {
                if (reopened.layers[li].name === "TrimMarks") {
                    var mps = reopened.layers[li].pathItems;
                    if (mps.length > 0) {
                        var sc = mps[0].strokeColor;
                        markInk = sc.typename;
                        if (sc.typename === "GrayColor") { markInk += ":gray=" + sc.gray; }
                        else if (sc.typename === "RGBColor") { markInk += ":rgb=" + sc.red + "," + sc.green + "," + sc.blue; }
                        else if (sc.typename === "CMYKColor") { markInk += ":k=" + sc.black; }
                        markInk += ":n=" + mps.length;
                    }
                    break;
                }
            }
        } catch (e) { markInk = "read_failed"; }
        try { reopened.close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}

        if (r2 !== 0 || p2 !== 0 || paths2 <= 0) {
            return finish("ERROR: REOPEN_ASSERT_FAILED|raster=" + r2 +
                          "|placed=" + p2 + "|paths=" + paths2);
        }

        return finish("OK|" + OUT_PATH +
                      "|open_paths=" + paths +
                      "|reopen_paths=" + paths2 +
                      "|layers=" + layers +
                      "|reopen_layers=" + layers2 +
                      "|raster=0|placed=0" +
                      "|pdf_compatible=" + (PDF_COMPATIBLE ? 1 : 0) +
                      "|embed_icc=" + (EMBED_ICC ? 1 : 0) +
                      "|cmyk=" + (TO_CMYK ? 1 : 0) +
                      "|bleed_pt=" + BLEED_PT +
                      "|trimmarks_ink=" + markInk +
                      "|app_version=" + app.version +
                      "|notes=" + notes.join(",") +
                      "|artboard=" + W + "x" + H +
                      "|viewBox=" + readViewBox(SVG_PATH));
    } catch (e) {
        return finish("ERROR: " + (e.message || String(e)));
    }
})();
