from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox

from asgl_a.core.pipeline import run_pipeline, PipelineConfig, STAGES

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

APP_TITLE = "ASGL-A Processing Runner"

class RunnerApp(ctk.CTk):
    def __init__(self, input_file: str | None = None, compare: bool = True):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("960x560")
        self.minsize(920, 520)

        self.input_file = ctk.StringVar(value=input_file or "")
        self.compare = ctk.BooleanVar(value=compare)
        self.discuss = ctk.BooleanVar(value=False)

        # left + right layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left = self._build_left_panel()
        self.right = self._build_right_panel()

        self._set_initial_state()

    def _build_left_panel(self):
        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.grid(row=0, column=0, padx=(18, 9), pady=18, sticky="nsew")
        frame.grid_rowconfigure(7, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkLabel(frame, text="ASGL-A Processing Runner", font=ctk.CTkFont(size=22, weight="bold"))
        hdr.grid(row=0, column=0, padx=18, pady=(18, 6), sticky="w")

        sub = ctk.CTkLabel(frame, text="Load Financial Pack(s) for review", font=ctk.CTkFont(size=13))
        sub.grid(row=1, column=0, padx=18, pady=(0, 14), sticky="w")

        drop = ctk.CTkFrame(frame, corner_radius=12, fg_color="#F5F7FA")
        drop.grid(row=2, column=0, padx=18, pady=(0, 14), sticky="ew")
        drop.grid_columnconfigure(0, weight=1)

        drop_lbl = ctk.CTkLabel(drop, text="Drag & Drop or click ", font=ctk.CTkFont(size=14))
        drop_lbl.grid(row=0, column=0, padx=16, pady=16, sticky="w")

        browse = ctk.CTkButton(drop, text="Browse Files", command=self._browse, height=34)
        browse.grid(row=0, column=0, padx=230, pady=14, sticky="w")

        self.file_row = ctk.CTkFrame(frame, corner_radius=10, fg_color="transparent")
        self.file_row.grid(row=3, column=0, padx=18, pady=(0, 10), sticky="ew")
        self.file_row.grid_columnconfigure(0, weight=1)

        self.file_check = ctk.CTkCheckBox(self.file_row, text="Client Working Papers.xlsx", onvalue=True, offvalue=False)
        self.file_check.select()
        self.file_check.grid(row=0, column=0, sticky="w")

        self.file_path_lbl = ctk.CTkLabel(self.file_row, text="", font=ctk.CTkFont(size=11), text_color="#6B7280")
        self.file_path_lbl.grid(row=1, column=0, sticky="w", pady=(2,0))

        opts = ctk.CTkFrame(frame, corner_radius=10, fg_color="transparent")
        opts.grid(row=4, column=0, padx=18, pady=(6, 6), sticky="ew")
        opts.grid_columnconfigure(0, weight=1)

        self.cb_compare = ctk.CTkCheckBox(opts, text="Compare Reports", variable=self.compare)
        self.cb_compare.grid(row=0, column=0, sticky="w")

        self.cb_discuss = ctk.CTkCheckBox(opts, text="Discuss anomalies", variable=self.discuss)
        self.cb_discuss.grid(row=1, column=0, sticky="w", pady=(6,0))

        # Run button
        self.run_btn = ctk.CTkButton(frame, text="Run ASGL-A", height=44, corner_radius=10, command=self._run_clicked)
        self.run_btn.grid(row=6, column=0, padx=18, pady=(18, 18), sticky="ew")

        return frame

    def _build_right_panel(self):
        frame = ctk.CTkFrame(self, corner_radius=12)
        frame.grid(row=0, column=1, padx=(9, 18), pady=18, sticky="nsew")
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(frame, corner_radius=12, fg_color="#0F2A4D")
        top.grid(row=0, column=0, padx=0, pady=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkLabel(top, text="ASGL-A", text_color="white", font=ctk.CTkFont(size=24, weight="bold"))
        logo.grid(row=0, column=0, padx=18, pady=18, sticky="w")

        title = ctk.CTkLabel(top, text="Client Working Papers vs Accounts Report", text_color="white",
                             font=ctk.CTkFont(size=16, weight="bold"))
        title.grid(row=0, column=1, padx=18, pady=18, sticky="w")

        self.stage_frame = ctk.CTkFrame(frame, corner_radius=12, fg_color="transparent")
        self.stage_frame.grid(row=1, column=0, padx=18, pady=(18, 10), sticky="ew")
        self.stage_frame.grid_columnconfigure(0, weight=1)

        self.stage_labels = []
        for i, s in enumerate(STAGES[:6]):
            lbl = ctk.CTkLabel(self.stage_frame, text=f"☐ {s}", font=ctk.CTkFont(size=13))
            lbl.grid(row=i, column=0, sticky="w", pady=5)
            self.stage_labels.append(lbl)

        # Decision + SHAP placeholder area
        mid = ctk.CTkFrame(frame, corner_radius=12, fg_color="#F5F7FA")
        mid.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nsew")
        mid.grid_columnconfigure(0, weight=1)

        self.decision_lbl = ctk.CTkLabel(
            mid,
            text="Decision Intelligence:\n\nStatus: —\nSeverity: —",
            font=ctk.CTkFont(size=13, weight="bold"),
            justify="left"
        )
        self.decision_lbl.grid(row=0, column=0, padx=18, pady=(18, 10), sticky="nw")

        self.actions_lbl = ctk.CTkLabel(
            mid,
            text="Recommended Actions:\n\n—",
            font=ctk.CTkFont(size=12),
            justify="left"
        )
        self.actions_lbl.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="nw")

        self.output_lbl = ctk.CTkLabel(
            mid,
            text="Output:\n\nPDF: —\nSHAP: —",
            font=ctk.CTkFont(size=12),
            justify="left"
        )
        self.output_lbl.grid(row=2, column=0, padx=18, pady=(0, 18), sticky="nw")

        self.open_pdf_btn = ctk.CTkButton(mid, text="Open PDF Report", state="disabled", command=self._open_pdf)
        self.open_pdf_btn.grid(row=3, column=0, padx=18, pady=(0,18), sticky="w")

        self._pdf_path = None
        return frame

    def _set_initial_state(self):
        self._set_file_label()
        self._reset_stages()

    def _set_file_label(self):
        p = self.input_file.get().strip()
        if p:
            self.file_check.configure(text=Path(p).name)
            self.file_path_lbl.configure(text=p)
        else:
            self.file_check.configure(text="Client Working Papers.xlsx")
            self.file_path_lbl.configure(text="")

    def _reset_stages(self):
        for lbl, s in zip(self.stage_labels, STAGES[:6]):
            lbl.configure(text=f"☐ {s}")

        self.decision_lbl.configure(text="Decision Intelligence:\n\nStatus: —\nSeverity: —")
        self.actions_lbl.configure(text="Recommended Actions:\n\n—")
        self.output_lbl.configure(text="Output:\n\nPDF: —\nSHAP: —")
        self.open_pdf_btn.configure(state="disabled")
        self._pdf_path = None

    def _browse(self):
        fp = filedialog.askopenfilename(title="Select Excel pack", filetypes=[("Excel files", "*.xlsx *.xlsm *.xls")])
        if fp:
            self.input_file.set(fp)
            self._set_file_label()

    def _run_clicked(self):
        if not self.input_file.get().strip():
            messagebox.showerror("ASGL-A", "Please select an Excel file first.")
            return

        self.run_btn.configure(state="disabled", text="Running…")
        self._reset_stages()

        t = threading.Thread(target=self._run_pipeline_bg, daemon=True)
        t.start()

    def _on_stage(self, idx: int, stage: str):
        # only animate first 6 in the UI list
        if idx < len(self.stage_labels):
            self.stage_labels[idx].configure(text=f"✓ {stage}")

    def _run_pipeline_bg(self):
        cfg = PipelineConfig(
            input_file=self.input_file.get().strip(),
            compare=self.compare.get(),
            output_dir="outputs",
            logs_dir="logs",
            schema_name="Accountancy_COA_v1"
        )
        result = run_pipeline(cfg, on_stage=self._on_stage)

        # Update UI summary
        self.decision_lbl.configure(
            text=f"Decision Intelligence:\n\nStatus: {result.decision_status}\nSeverity: {result.severity}"
        )
        rec = "\n".join([f"• {a}" for a in result.recommended_actions])
        self.actions_lbl.configure(text=f"Recommended Actions:\n\n{rec}")
        self.output_lbl.configure(text=f"Output:\n\nPDF: {result.pdf_path}\nSHAP: {result.shap_png_path}")

        self._pdf_path = result.pdf_path
        self.open_pdf_btn.configure(state="normal")
        self.run_btn.configure(state="normal", text="Run ASGL-A")

    def _open_pdf(self):
        if not self._pdf_path:
            return
        try:
            os.startfile(self._pdf_path)  # Windows
        except Exception:
            # fallback cross-platform
            import webbrowser
            webbrowser.open_new(self._pdf_path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", dest="file", default="", help="Excel pack path")
    ap.add_argument("--compare", action="store_true", help="Enable compare mode")
    args = ap.parse_args()

    app = RunnerApp(input_file=args.file or None, compare=args.compare)
    app.mainloop()

if __name__ == "__main__":
    main()
