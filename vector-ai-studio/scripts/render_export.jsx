#target illustrator

// Faithful PNG preview export. Runs INSIDE Illustrator via DoJavaScript
// (ai_bridge.ps1), the same channel land_ai.py uses for the native .ai.
//
// Why this exists: the builtin rasteriser fills each element's bounding box
// with one solid colour, so circles become squares and paths become slabs. A
// preview produced that way cannot be used to judge semantic fidelity or
// composition (design S11 item 3). Illustrator is already a hard dependency of
// this skill -- if it can open the SVG to save a .ai, it can also export a PNG
// that is pixel-identical to what the .ai actually looks like.
//
// Placeholders replaced at runtime by render_png.py:
//   __SVG_PATH__  __OUT_PNG__  __SCALE__  __RESULT_PATH__

(function () {
    var SVG_PATH = "__SVG_PATH__";
    var OUT_PNG = "__OUT_PNG__";
    var SCALE = parseFloat("__SCALE__") || 100;
    var RESULT_PATH = "__RESULT_PATH__";

    function finish(line) {
        if (RESULT_PATH.indexOf("__RESULT_") !== 0) {
            try {
                var rf = new File(RESULT_PATH);
                rf.open("w");
                rf.write(line);
                rf.close();
            } catch (e) { /* result file is best-effort; stdout still carries the line */ }
        }
        return line;
    }

    try {
        var src = new File(SVG_PATH);
        if (!src.exists) { return finish("ERROR: SRC_MISSING"); }

        var doc = app.open(src);
        if (!doc) { return finish("ERROR: OPEN_FAILED"); }

        var outFile = new File(OUT_PNG);
        var opts = new ExportOptionsPNG24();
        opts.antiAliasing = true;
        opts.transparency = true;
        opts.artBoardClipping = true;
        opts.horizontalScale = SCALE;
        opts.verticalScale = SCALE;

        doc.exportFile(outFile, ExportType.PNG24, opts);

        var ok = outFile.exists && outFile.length > 0;
        var bytes = ok ? outFile.length : 0;

        try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e) { /* doc may already be closed */ }

        return finish(ok ? "OK: " + bytes : "ERROR: EXPORT_EMPTY");
    } catch (e) {
        return finish("ERROR: " + e.message);
    }
})();
