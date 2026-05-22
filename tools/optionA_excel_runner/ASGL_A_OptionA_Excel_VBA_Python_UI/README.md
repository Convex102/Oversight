# ASGL-A Option A (Excel VBA -> Python Desktop UI)

This package provides a **professional desktop runner** that Excel can launch via a VBA macro.

## What you get
- **Excel VBA macro** that launches the runner and passes the active workbook path
- **Python desktop runner UI** (CustomTkinter) matching the left panel behaviour
- **Processing / Report UI** that ticks through stages and then opens a **PDF report**
- **Logging + debug output** in `logs/`
- A clean, extendable structure so you can drop in your real ingestion / compare / SHAP logic

---

## 1) Install (dev)
```bash
cd ASGL_A_OptionA_Excel_VBA_Python_UI
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) Run runner manually
```bash
python -m asgl_a.ui.runner_app --file "C:\path\to\Client Working Papers.xlsx" --compare
```

## 3) Trigger from Excel (VBA)
Copy the code from `excel_vba/Run_ASGL_A.bas` into your workbook VBA project.
Update `ASGL_EXE_OR_PYTHON` + `ASGL_ENTRYPOINT` to match your machine.

---

## 4) Build to EXE (recommended for clients)
Use PyInstaller:
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name asgl_runner scripts/build_runner.spec
```

Then point VBA to `dist\asgl_runner.exe`.

---

## Notes
- The PDF + SHAP output here are **demo placeholders** so you can wire in real outputs.
- All pipeline stages are centralized in `asgl_a/core/pipeline.py`.
