"""
txdot_direction_gui.py  -  a small window for txdot_direction.py. Start it with RUN_ME.bat or
    python txdot_direction_gui.py
"""
import contextlib, io, os, queue, sys, threading, traceback
import tkinter as tk
from tkinter import filedialog, ttk

import txdot_direction as td

FORMATS = {'Shapefile, zipped (ArcGIS)': 'shp', 'GeoPackage (ArcGIS Pro, QGIS)': 'gpkg',
           'GeoParquet (Python, R)': 'parquet'}


class QueueWriter(io.TextIOBase):
    def __init__(self, q): self.q = q
    def write(self, s): self.q.put(s); return len(s)


class App(tk.Tk):
    def __init__(self, start_file=None):
        super().__init__()
        self.title(f"TxDOT Roadway Inventory - travel direction  v{td.__version__}")
        self.q = queue.Queue(); self.busy = False
        pad = dict(padx=10, pady=4)

        f = ttk.LabelFrame(self, text="1. Input: TxDOT yearly .zip or TxDOT_Roadway_Linework_wAssets.shp")
        f.pack(fill='x', **pad)
        self.inp = tk.StringVar(value=start_file or '')
        ttk.Entry(f, textvariable=self.inp).pack(side='left', fill='x', expand=True, padx=6, pady=6)
        ttk.Button(f, text="Browse...", command=self.pick_input).pack(side='right', padx=6)

        f = ttk.LabelFrame(self, text="2. Output folder"); f.pack(fill='x', **pad)
        self.out = tk.StringVar()
        ttk.Entry(f, textvariable=self.out).pack(side='left', fill='x', expand=True, padx=6, pady=6)
        ttk.Button(f, text="Browse...", command=self.pick_output).pack(side='right', padx=6)
        if start_file: self.out.set(default_output(start_file))

        f = ttk.LabelFrame(self, text="3. Options"); f.pack(fill='x', **pad)
        self.scope = tk.StringVar(value='all')
        ttk.Radiobutton(f, text="Whole inventory (all roads, frontage roads, ramps)", value='all',
                        variable=self.scope).grid(row=0, column=0, columnspan=2, sticky='w', padx=6)
        ttk.Radiobutton(f, text="Interstates and freeways only, main lanes (F_SYSTEM 1-2, REC 1)", value='f12',
                        variable=self.scope).grid(row=1, column=0, columnspan=2, sticky='w', padx=6)
        ttk.Label(f, text="Output format").grid(row=2, column=0, sticky='w', padx=6, pady=(8, 2))
        self.fmt = tk.StringVar(value=list(FORMATS)[0])
        ttk.Combobox(f, textvariable=self.fmt, values=list(FORMATS), state='readonly', width=34)\
            .grid(row=2, column=1, sticky='w', pady=(8, 2))
        self.key = tk.BooleanVar(value=True); self.tg = tk.BooleanVar(value=True)
        self.ramps = tk.BooleanVar(value=True); self.kg = tk.BooleanVar(value=False)
        checks = [(self.key, "Keep only key fields (smaller file; untick to keep all ~130 fields)"),
                  (self.tg, "Also write a map copy whose lines point the way traffic moves"),
                  (self.ramps, "Give ramps/connectors the drawn direction (assumed; about 90% correct)"),
                  (self.kg, "Keep KG centerlines that sit between LG and RG (normally dropped)")]
        for i, (var, text) in enumerate(checks):
            ttk.Checkbutton(f, text=text, variable=var).grid(row=3 + i, column=0, columnspan=2, sticky='w', padx=6)

        b = ttk.Frame(self); b.pack(fill='x', **pad)
        self.run_btn = ttk.Button(b, text="Run", command=self.start); self.run_btn.pack(side='left')
        ttk.Button(b, text="Open output folder", command=self.open_output).pack(side='left', padx=8)
        self.status = ttk.Label(b, text="Ready."); self.status.pack(side='left', padx=8)

        self.log = tk.Text(self, height=14, wrap='word', font=('Consolas', 9))
        self.log.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.write_log("Pick the TxDOT file, choose options, then press Run. "
                       "The whole 2025 inventory takes about 3-5 minutes.\n")
        self.update_idletasks(); self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.after(200, self.poll)

    def pick_input(self):
        p = filedialog.askopenfilename(title="TxDOT Roadway Inventory",
                                       filetypes=[("TxDOT zip or shapefile", "*.zip *.shp"), ("All files", "*.*")])
        if p:
            self.inp.set(p)
            if not self.out.get(): self.out.set(default_output(p))

    def pick_output(self):
        p = filedialog.askdirectory(title="Output folder")
        if p: self.out.set(p)

    def open_output(self):
        p = self.out.get()
        if p and os.path.isdir(p): os.startfile(p) if hasattr(os, 'startfile') else None

    def start(self):
        if self.busy: return
        inp, out = self.inp.get().strip(), self.out.get().strip()
        if not os.path.isfile(inp): self.write_log("Please choose an input file first.\n"); return
        if not out: out = default_output(inp); self.out.set(out)
        f12 = self.scope.get() == 'f12'
        kw = dict(fsystem=[1, 2] if f12 else None, rec=[1] if f12 else None, fmt=FORMATS[self.fmt.get()],
                  fields=td.KEY_FIELDS if self.key.get() else None, travel_geometry=self.tg.get(),
                  assume_connectors=self.ramps.get(), keep_kg=self.kg.get(),
                  name=output_name(inp, f12))
        self.busy = True; self.run_btn.state(['disabled']); self.status.config(text="Running...")
        self.write_log(f"\n--- run: {inp}\n")
        threading.Thread(target=self.work, args=(inp, out, kw), daemon=True).start()

    def work(self, inp, out, kw):
        w = QueueWriter(self.q)
        try:
            with contextlib.redirect_stdout(w), contextlib.redirect_stderr(w):
                td.run(inp, out, **kw)
            self.q.put(('done', "Finished."))
        except SystemExit as e:
            self.q.put(f"\nSTOPPED: {e}\n"); self.q.put(('done', "Stopped, see message."))
        except Exception:
            self.q.put("\nERROR:\n" + traceback.format_exc()); self.q.put(('done', "Error, see message."))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    self.busy = False; self.run_btn.state(['!disabled']); self.status.config(text=item[1])
                else:
                    self.write_log(item)
        except queue.Empty:
            pass
        self.after(200, self.poll)

    def write_log(self, s):
        self.log.insert('end', s); self.log.see('end')


def default_output(inp):
    return os.path.join(os.path.dirname(os.path.abspath(inp)), 'direction_output')


def output_name(inp, f12):
    stem = os.path.splitext(os.path.basename(inp))[0]
    stem = 'TxDOT_Roadway_' + stem if stem[:4].isdigit() else stem      # 2025.zip -> TxDOT_Roadway_2025
    return stem + ('_F12' if f12 else '_ALL') + '_direction'


if __name__ == '__main__':
    try:  # sharp text on high-resolution Windows screens
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
