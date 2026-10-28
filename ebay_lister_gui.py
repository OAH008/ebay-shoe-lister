"""
ebay_lister_gui.py  -  point-and-click window for ebay_lister.py

Put this file in the SAME folder as ebay_lister.py (and ebay_keys.txt), then
double-click it, or run:   python ebay_lister_gui.py

It uses the functions inside ebay_lister.py directly, so the listing logic,
store settings, preview.html and results_log.csv are exactly the same as the
command-line version. Nothing in ebay_lister.py needs to change.
"""
import os, sys, json, csv, threading, queue, webbrowser, traceback, html
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SETTINGS_FILE = os.path.join(HERE, "gui_settings.json")

try:
    import ebay_lister as el
except Exception as _ex:  # missing file or missing 'requests'
    _r = tk.Tk(); _r.withdraw()
    messagebox.showerror("Can't start",
                         "Couldn't load ebay_lister.py.\n\nMake sure this file sits in the same folder "
                         "as ebay_lister.py and that 'requests' is installed (pip install requests).\n\n"
                         f"Details: {_ex}")
    sys.exit(1)


# ---------------------------------------------------------------- helpers
def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except Exception:
        pass


def open_path(path):
    if not os.path.exists(path):
        messagebox.showinfo("Not found", f"{os.path.basename(path)} doesn't exist yet.")
        return
    if hasattr(os, "startfile"):
        os.startfile(path)
    else:
        webbrowser.open("file://" + os.path.abspath(path))


def _qty(L):
    try:
        return int(L.get("quantity", 1))
    except (TypeError, ValueError):
        return 1


def _size_key(s):
    try:
        return (0, float(str(s).rstrip("Yy")))
    except ValueError:
        return (1, str(s))


def round_summary(listings):
    """Group this round's listings by style code, then by size. Returns (styles, total_shoes)."""
    by = {}
    for L in listings:
        code = L.get("style_code") or "(no style code)"
        s = by.setdefault(code, {"style": code, "model": L.get("model", ""),
                                 "colorway": L.get("manufacturer_colorway") or L.get("color", ""),
                                 "qty": 0, "sizes": {}})
        q = _qty(L)
        s["qty"] += q
        row = s["sizes"].setdefault(str(L.get("us_size", "?")), {"qty": 0, "drafts": []})
        row["qty"] += q
        row["drafts"].append(L["draft"] if q == 1 else f"{L['draft']} ×{q}")
    styles = sorted(by.values(), key=lambda s: (-s["qty"], s["style"]))
    for s in styles:
        s["rows"] = [(size, s["sizes"][size]) for size in sorted(s["sizes"], key=_size_key)]
    return styles, sum(s["qty"] for s in styles)


def make_preview(listings, groups, out, shoes, n_styles):
    """Same page as ebay_lister.make_preview, plus a Qty on every draft and a one-line total."""
    e = html.escape
    rows = []
    for L in listings:
        pics = groups[L["photo_group"] - 1]
        imgs = "".join(f'<img src="file:///{e(p.replace(os.sep, "/"))}" title="{e(os.path.basename(p))}">' for p in pics)
        names = ", ".join(os.path.basename(p) for p in pics)
        q = _qty(L)
        qty = f'<span class="qty{" multi" if q > 1 else ""}">Qty {q}</span>'
        rows.append(f'<div class="r"><b>#{L["draft"]}</b> {e(L["title"])}<br><small>{names}</small> {qty}<br>{imgs}</div>')
    with open(out, "w", encoding="utf-8") as f:
        f.write("<html><head><meta charset='utf-8'><style>body{font-family:Arial}.r{margin:10px 0;padding:8px;"
                "border-bottom:1px solid #ccc}img{height:120px;margin:4px}"
                ".qty{margin-left:12px;font-weight:bold;font-size:15px}"
                ".multi{color:#fff;background:#c62828;padding:1px 8px;border-radius:4px}"
                ".tot{font-size:18px}</style></head><body>"
                f"<p class='tot'>Total shoes: <b>{shoes}</b> &nbsp;|&nbsp; Listings: <b>{len(listings)}</b></p>"
                "<h2>Check: does each draft # show the right shoe?</h2>" + "".join(rows) + "</body></html>")


class QueueWriter:
    """Sends print() output from ebay_lister.py into the GUI log."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s.strip():
            self.q.put(("log", "info", s.rstrip()))

    def flush(self):
        pass


# ---------------------------------------------------------------- app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"eBay Shoe Lister  [{el.CFG['environment']}]")
        self.geometry("820x680")
        self.minsize(680, 560)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        sys.stdout = QueueWriter(self.q)

        s = load_settings()
        self.json_var = tk.StringVar(value=s.get("json", ""))
        self.photos_var = tk.StringVar(value=s.get("photos", ""))
        self.keys_var = tk.StringVar(value=s.get("keys", os.path.join(HERE, "ebay_keys.txt")))
        self.mode_var = tk.StringVar(value="preview")
        self.only_var = tk.StringVar()
        self.first_var = tk.StringVar()
        self.count_var = tk.StringVar(value="20")
        self.info_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")
        self.pct_var = tk.StringVar(value="0%")

        self._build()
        for v in (self.json_var, self.photos_var, self.first_var, self.only_var, self.count_var):
            v.trace_add("write", lambda *a: self.refresh_info())
        self.refresh_info()
        self.after(100, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- layout
    def _build(self):
        pad = {"padx": 8, "pady": 4}

        files = ttk.LabelFrame(self, text="Files")
        files.pack(fill="x", padx=10, pady=(10, 4))
        files.columnconfigure(1, weight=1)
        rows = [("Batch JSON:", self.json_var, self.pick_json),
                ("Photos folder:", self.photos_var, self.pick_photos),
                ("Keys file:", self.keys_var, self.pick_keys)]
        self.inputs = []
        for r, (label, var, cmd) in enumerate(rows):
            ttk.Label(files, text=label).grid(row=r, column=0, sticky="w", **pad)
            e = ttk.Entry(files, textvariable=var)
            e.grid(row=r, column=1, sticky="ew", **pad)
            b = ttk.Button(files, text="Browse…", command=cmd)
            b.grid(row=r, column=2, **pad)
            self.inputs += [e, b]
        ttk.Label(files, textvariable=self.info_var, foreground="#555", justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        opts = ttk.LabelFrame(self, text="Options")
        opts.pack(fill="x", padx=10, pady=4)
        modes = [("preview", "1. Preview  –  check each draft shows the right shoe (nothing sent to eBay)"),
                 ("check", "2. Check  –  eBay validates every listing (nothing listed, no fees)"),
                 ("live", "3. Live  –  lists the items on eBay for real")]
        for r, (val, text) in enumerate(modes):
            rb = ttk.Radiobutton(opts, text=text, value=val, variable=self.mode_var)
            rb.grid(row=r, column=0, columnspan=4, sticky="w", padx=8, pady=2)
            self.inputs.append(rb)
        ttk.Label(opts, text="Only drafts:").grid(row=3, column=0, sticky="w", **pad)
        e1 = ttk.Entry(opts, textvariable=self.only_var, width=22)
        e1.grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(opts, text="(e.g. 101,102 – blank = all)", foreground="#777").grid(row=3, column=2, sticky="w")
        ttk.Label(opts, text="First photo:").grid(row=4, column=0, sticky="w", **pad)
        e2 = ttk.Entry(opts, textvariable=self.first_var, width=22)
        e2.grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(opts, text="(blank = use the JSON's first_photo)", foreground="#777").grid(row=4, column=2, sticky="w")
        ttk.Label(opts, text="Shoes to list:").grid(row=5, column=0, sticky="w", **pad)
        sp = ttk.Spinbox(opts, from_=1, to=999, textvariable=self.count_var, width=8)
        sp.grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(opts, text="(the next N drafts not yet listed, in draft order)", foreground="#777").grid(row=5, column=2, sticky="w")
        self.inputs += [e1, e2, sp]

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=6)
        self.run_btn = ttk.Button(btns, text="▶  Run", command=self.run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■  Stop after current item", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Open results log", command=lambda: open_path(os.path.join(el.HERE, "results_log.csv"))).pack(side="right")
        ttk.Button(btns, text="Open preview", command=lambda: open_path(os.path.join(el.HERE, "preview.html"))).pack(side="right", padx=6)

        prog = ttk.Frame(self)
        prog.pack(fill="x", padx=10)
        self.pbar = ttk.Progressbar(prog, mode="determinate", maximum=1)
        self.pbar.pack(side="left", fill="x", expand=True)
        ttk.Label(prog, textvariable=self.pct_var, width=5, anchor="e").pack(side="left", padx=(6, 0))
        ttk.Label(self, textvariable=self.status_var).pack(fill="x", padx=10, pady=(2, 4))

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("ok", foreground="#1a7f37")
        self.log.tag_config("warn", foreground="#b26b00")
        self.log.tag_config("err", foreground="#c62828")
        self.log.tag_config("head", font=("Consolas", 9, "bold"))

    # ---------- pickers
    def _start_dir(self, path):
        if path and os.path.isdir(path):
            return path
        if path and os.path.isdir(os.path.dirname(path)):
            return os.path.dirname(path)
        return HERE

    def pick_json(self):
        p = filedialog.askopenfilename(title="Choose batch JSON", initialdir=self._start_dir(self.json_var.get()),
                                       filetypes=[("Batch JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.json_var.set(os.path.normpath(p))
            self.first_var.set("")
            self.only_var.set("")

    def pick_photos(self):
        p = filedialog.askdirectory(title="Choose photos folder", initialdir=self._start_dir(self.photos_var.get()))
        if p:
            self.photos_var.set(os.path.normpath(p))

    def pick_keys(self):
        p = filedialog.askopenfilename(title="Choose ebay_keys.txt", initialdir=self._start_dir(self.keys_var.get()),
                                       filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if p:
            self.keys_var.set(os.path.normpath(p))

    # ---------- how many shoes to list
    def get_count(self):
        try:
            n = int(self.count_var.get().strip())
            return n if n >= 1 else None
        except ValueError:
            return None

    def next_batch(self, listings, n):
        """Drop drafts already listed live, then keep the next n in draft order."""
        done = el.already_listed(os.path.join(el.HERE, "results_log.csv"))
        remaining = sorted((L for L in listings if L["draft"] not in done), key=lambda L: L["draft"])
        return remaining[:n], len(listings) - len(remaining)

    # ---------- batch / folder summary under the file boxes
    def refresh_info(self):
        lines = []
        jpath, folder = self.json_var.get().strip(), self.photos_var.get().strip()
        json_first, needed = None, None
        if jpath:
            try:
                with open(jpath, encoding="utf-8") as f:
                    data = json.load(f)
                Ls = data["listings"]
                drafts = [L["draft"] for L in Ls]
                json_first = data.get("first_photo")
                needed = max(L["photo_group"] for L in Ls) * el.PHOTOS_PER_PAIR
                lines.append(f"Batch {data.get('batch', '?')}: {len(Ls)} listing(s), drafts #{min(drafts)}–#{max(drafts)}, "
                             f"first_photo {json_first or '(none)'}")
                n = self.get_count()
                if n:
                    pick, already = self.next_batch(Ls, n)
                    left = len(Ls) - already
                    if pick:
                        lines.append(f"Next run: {len(pick)} shoe(s), drafts #{pick[0]['draft']}–#{pick[-1]['draft']}"
                                     f"  ({left} not yet listed, {already} already listed)")
                    else:
                        lines.append("Next run: nothing left – every draft in this JSON is already listed.")
            except Exception as ex:
                lines.append(f"⚠ Can't read JSON: {ex}")
        if folder:
            if not os.path.isdir(folder):
                lines.append("⚠ Photos folder not found.")
            else:
                files = sorted(f for f in os.listdir(folder) if f.lower().endswith(el.PHOTO_EXT))
                first = self.first_var.get().strip() or json_first
                avail = len(files)
                msg = f"Photos folder: {len(files)} photo(s)"
                if first:
                    stems = [os.path.splitext(f)[0].lower() for f in files]
                    want = os.path.splitext(first)[0].lower()
                    if want in stems:
                        avail = len(files) - stems.index(want)
                        msg += f", {avail} from {first} on"
                    else:
                        msg += f" — ⚠ {first} is not in this folder"
                        avail = None
                if needed and avail is not None:
                    msg += f" (batch needs {needed})" + ("" if avail >= needed else "  ⚠ not enough photos")
                lines.append(msg)
        self.info_var.set("\n".join(lines) or "Pick a batch JSON and a photos folder.")

    # ---------- run
    def collect(self):
        jpath, folder, keys = (v.get().strip() for v in (self.json_var, self.photos_var, self.keys_var))
        mode = self.mode_var.get()
        if not os.path.isfile(jpath):
            messagebox.showwarning("Missing", "Choose a batch JSON file."); return None
        if not os.path.isdir(folder):
            messagebox.showwarning("Missing", "Choose the photos folder."); return None
        if mode != "preview" and not os.path.isfile(keys):
            messagebox.showwarning("Missing", "Choose your ebay_keys.txt file."); return None
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
            listings = data["listings"]
        except Exception as ex:
            messagebox.showerror("Bad JSON", f"Couldn't read {os.path.basename(jpath)}:\n{ex}"); return None
        only = self.only_var.get().strip()
        if only:
            try:
                want = {int(x) for x in only.replace(" ", "").split(",") if x}
            except ValueError:
                messagebox.showwarning("Only drafts", "Use numbers separated by commas, e.g. 101,102"); return None
            listings = [L for L in listings if L["draft"] in want]
            missing = want - {L["draft"] for L in listings}
            if not listings:
                messagebox.showwarning("Only drafts", "None of those draft numbers are in this JSON."); return None
            if missing:
                self.write("warn", "Not in this JSON (ignored): " + ", ".join(f"#{d}" for d in sorted(missing)))
        n = self.get_count()
        if n is None:
            messagebox.showwarning("Shoes to list", "Enter how many shoes to list (a whole number, 1 or more)."); return None
        listings, already = self.next_batch(listings, n)
        if already:
            self.write("info", f"Skipping {already} draft(s) already listed live (see results log).")
        if not listings:
            messagebox.showinfo("Nothing to do", "Every selected draft is already listed (see results log)."); return None
        save_settings({"json": jpath, "photos": folder, "keys": keys})
        return {"listings": listings, "photos": folder, "keys": keys, "mode": mode,
                "first": self.first_var.get().strip() or data.get("first_photo"),
                "json": jpath}

    def run(self):
        if self.worker and self.worker.is_alive():
            return
        p = self.collect()
        if not p:
            return
        if p["mode"] == "live":
            done = el.already_listed(os.path.join(el.HERE, "results_log.csv"))
            todo = [L for L in p["listings"] if L["draft"] not in done]
            skipped = len(p["listings"]) - len(todo)
            if not todo:
                messagebox.showinfo("Nothing to list", "Every selected draft is already listed (see results log).")
                return
            note = f"\n({skipped} already listed will be skipped.)" if skipped else ""
            ans = simpledialog.askstring("Confirm LIVE listing",
                                         f"About to LIST {len(todo)} item(s) on eBay for real.{note}\n\n"
                                         "Type YES to continue:", parent=self)
            if (ans or "").strip() != "YES":
                self.write("warn", "Live run cancelled.")
                return

        self.stop_event.clear()
        self.pbar.configure(value=0, maximum=1)
        self.pct_var.set("0%")
        self.write("head", f"\n=== {p['mode'].upper()}  ·  {os.path.basename(p['json'])}  ·  {len(p['listings'])} listing(s) ===")
        self.set_running(True)
        self.worker = threading.Thread(target=self.job, args=(p,), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.stop_btn.configure(state="disabled")
        self.status_var.set("Stopping after the current item…")

    def set_running(self, running):
        st = "disabled" if running else "normal"
        for w in self.inputs:
            w.configure(state=st)
        self.run_btn.configure(state=st)
        self.stop_btn.configure(state="normal" if running else "disabled")

    # ---------- worker thread (talks to the window only through the queue)
    def emit(self, level, text):
        self.q.put(("log", level, text))

    def progress(self, step, total, text):
        self.q.put(("progress", step, total, text))

    def job(self, p):
        try:
            self.do_job(p)
        except SystemExit as ex:  # ebay_lister uses sys.exit("message") for its errors
            self.emit("err", str(ex.code) if ex.code else "Stopped.")
            self.q.put(("status", "Stopped with an error – see log."))
        except Exception:
            self.emit("err", traceback.format_exc())
            self.q.put(("status", "Stopped with an error – see log."))
        finally:
            self.q.put(("done",))

    def do_job(self, p):
        listings, mode = p["listings"], p["mode"]
        self.progress(0, 1, "Matching photos to drafts…")
        groups = el.photo_groups(p["photos"], max(L["photo_group"] for L in listings), p["first"])

        if mode == "preview":
            out = os.path.join(el.HERE, "preview.html")
            styles, shoes = round_summary(listings)
            make_preview(listings, groups, out, shoes, len(styles))
            self.emit("head", f"This round: {shoes} shoe(s), {len(listings)} listing(s)")
            for s in styles:
                self.emit("info", f"    {s['style']:<14} total x{s['qty']:<3} {s['model']} – {s['colorway']}")
                for size, r in s["rows"]:
                    self.emit("info", f"        Size {size:<6} x{r['qty']:<3} " + ", ".join("#" + str(d) for d in r["drafts"]))
            self.progress(1, 1, "Preview ready – check every draft shows the right shoe, then run Check.")
            self.emit("ok", f"Preview written: {out}")
            webbrowser.open("file:///" + out.replace(os.sep, "/"))
            return

        self.progress(0, 1, "Reading eBay keys and business policies…")
        keys = el.load_keys(p["keys"])
        pol = el.get_policy_ids(keys)
        self.emit("info", "Business policies found.")
        log = os.path.join(el.HERE, "results_log.csv")

        if mode == "live":
            done = el.already_listed(log)
            todo = [L for L in listings if L["draft"] not in done]
            if len(todo) < len(listings):
                self.emit("warn", f"Skipping {len(listings) - len(todo)} draft(s) already listed.")
            listings = todo

        plan = []
        for L in listings:
            paths = groups[L["photo_group"] - 1]
            if mode == "check":
                paths = paths[:1]  # one photo is enough to validate
            plan.append((L, paths))
        total = sum(len(pp) + 1 for _, pp in plan) or 1
        step = 0
        counts = {"Success": 0, "Warning": 0, "Failure": 0}
        call_name = "AddFixedPriceItem" if mode == "live" else "VerifyAddFixedPriceItem"
        verb = "listing" if mode == "live" else "validating"

        posted = []  # (listing, eBay item id) for each live listing that succeeded
        new_log = not os.path.exists(log)
        with open(log, "a", newline="", encoding="utf-8") as lf:
            w = csv.writer(lf)
            if new_log:
                w.writerow(["draft", "mode", "result", "item_id", "messages"])
            for i, (L, paths) in enumerate(plan, 1):
                if self.stop_event.is_set():
                    self.emit("warn", f"Stopped by you. {len(plan) - i + 1} draft(s) not processed.")
                    break
                tag = f"#{L['draft']} ({i}/{len(plan)})"
                try:
                    pics = []
                    for j, path in enumerate(paths, 1):
                        self.progress(step, total, f"{tag}: uploading photo {j}/{len(paths)}  ({os.path.basename(path)})")
                        pics.append(el.upload_photo(keys, path))
                        step += 1
                    self.progress(step, total, f"{tag}: {verb} on eBay…")
                    ack, root, msgs = el.call(keys, call_name, el.item_xml(L, pics, pol))
                    item_id = root.findtext("e:ItemID", "", el.NS)
                except Exception as ex:
                    ack, item_id, msgs = "Failure", "", [str(ex)]
                step = sum(len(pp) + 1 for _, pp in plan[:i])  # keep the bar right even after a failure
                self.progress(step, total, f"{tag}: {ack}")

                level = "ok" if ack == "Success" else "warn" if ack == "Warning" else "err"
                counts[ack if ack in counts else "Failure"] += 1
                self.emit(level, f"#{L['draft']}: {ack} {item_id}".rstrip())
                for m in msgs:
                    self.emit("err" if m.startswith("Error") else "warn", "    " + m)
                w.writerow([L["draft"], mode, ack, item_id, " | ".join(msgs)])
                lf.flush()
                if mode == "live" and ack in ("Success", "Warning") and item_id:
                    posted.append((L, item_id))

        # ---- inventory sheet (live only; runs even if you pressed Stop; never blocks) ----
        if mode == "live":
            sync = getattr(el, "inventory_sync", None)
            if sync is None:
                self.emit("err", f"[inventory] sheet NOT updated ({getattr(el, '_SYNC_ERR', 'inventory_sync.py not found')}).")
            else:
                self.progress(total, total, "Updating the inventory sheet…")
                try:
                    sync.sync(posted)   # its print() output shows in the log
                except Exception as ex:
                    self.emit("err", f"[inventory] sheet update failed: {ex}")

        summary = f"Done: {counts['Success']} success, {counts['Warning']} warning, {counts['Failure']} failed."
        self.emit("head", summary + f"  Results saved to {log}")
        if mode == "check" and counts["Failure"] == 0 and not self.stop_event.is_set():
            summary += "  All passed – ready for Live."
        self.q.put(("status", summary))

    # ---------- UI updates from the queue
    def write(self, level, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.write(msg[1], msg[2])
                elif kind == "progress":
                    _, step, total, text = msg
                    self.pbar.configure(maximum=total, value=step)
                    self.pct_var.set(f"{int(step * 100 / total)}%")
                    self.status_var.set(text)
                elif kind == "status":
                    self.status_var.set(msg[1])
                elif kind == "done":
                    self.set_running(False)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Still running",
                                       "A run is still going. Quit anyway?\n(The item being sent right now may or may not finish.)"):
                return
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
