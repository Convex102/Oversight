
ASGL-A Popup Runner Patch v3
============================

This patch introduces popup_runner_v3.py which:
- Replaces hard-coded file assumptions
- Uses the universal file picker
- Supports Excel, CSV, and PDF
- Allows Excel → PDF comparison (working papers → PDF accounts)

Integration:
------------
Existing popup runner can delegate to:

    from asgl_a.popup_runner_v3 import run_single, run_comparison

No existing files are removed or overwritten.
