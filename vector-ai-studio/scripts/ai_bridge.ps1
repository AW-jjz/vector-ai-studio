param(
    [string]$JsxPath,
    [switch]$ProbeOnly
)

# Windows COM bridge for Illustrator. Standalone file (never inline in a doc).
# -ProbeOnly performs a real COM creation with no JSX execution, used by smoke_ai.py.

$ErrorActionPreference = "Stop"

try {
    $ai = New-Object -ComObject Illustrator.Application
} catch {
    [Console]::Error.WriteLine("ERROR: COM_UNAVAILABLE")
    exit 4
}

if ($ProbeOnly) {
    try { $ai.Quit() } catch { }
    Write-Output "COM_OK"
    exit 0
}

if (-not $JsxPath -or -not (Test-Path -LiteralPath $JsxPath)) {
    [Console]::Error.WriteLine("ERROR: JSX_NOT_FOUND")
    try { $ai.Quit() } catch { }
    exit 4
}

try {
    $jsx = Get-Content -Raw -LiteralPath $JsxPath
    $result = $ai.DoJavaScript($jsx)
    Write-Output $result
    try { $ai.Quit() } catch { }
    exit 0
} catch {
    [Console]::Error.WriteLine("ERROR: DOJAVASCRIPT_FAILED")
    try { $ai.Quit() } catch { }
    exit 4
}
