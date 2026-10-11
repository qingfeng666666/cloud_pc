from __future__ import annotations

import subprocess


def capture_screen_base64(width: int = 320, height: int = 180, quality: int = 45) -> dict:
    width = max(64, min(int(width), 640))
    height = max(36, min(int(height), 360))
    quality = max(20, min(int(quality), 90))

    script = rf"""
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$src = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$srcG = [System.Drawing.Graphics]::FromImage($src)
$srcG.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $src.Size)
$thumb = New-Object System.Drawing.Bitmap {width}, {height}
$thumbG = [System.Drawing.Graphics]::FromImage($thumb)
$thumbG.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$thumbG.DrawImage($src, 0, 0, {width}, {height})
$ms = New-Object System.IO.MemoryStream
$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object {{ $_.MimeType -eq 'image/jpeg' }}
$encParams = New-Object System.Drawing.Imaging.EncoderParameters 1
$encParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, {quality}L)
$thumb.Save($ms, $codec, $encParams)
$thumbG.Dispose()
$thumb.Dispose()
$srcG.Dispose()
$src.Dispose()
[Convert]::ToBase64String($ms.ToArray())
"""

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "capture failed").strip()
        return {"ok": False, "error": err}

    data = result.stdout.strip()
    if not data:
        return {"ok": False, "error": "empty capture"}

    return {
        "ok": True,
        "width": width,
        "height": height,
        "format": "jpeg",
        "image_base64": data,
    }
