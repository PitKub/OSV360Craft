# external/

The installer and the portable ZIP put the bundled tools here, next to `OSV360Craft.exe`:

```text
external/OpenOSV/cli/osvtool.exe   (+ its DLLs)   OpenOSV stitching engine, Apache-2.0 - LICENSE, NOTICE, licenses/ beside it
external/ffmpeg/ffmpeg.exe, ffprobe.exe          FFmpeg, GPL-3.0 - LICENSE.txt and SOURCE.txt beside it
```

Nothing here is committed to git. `build.bat` fills it from `vendor\` (see `scripts\get_openosv.ps1` and
`scripts\get_ffmpeg.ps1`). You can also place your own copies here, or set their paths in OSV360Craft > Settings.
