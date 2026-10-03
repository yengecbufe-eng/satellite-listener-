#!/usr/bin/env python3
"""
SATELLITE RADIO - Slider-tuned, no hardware needed
--------------------------------------------------
Move the slider: when it lands on the real frequency of an active satellite
transmitter, the app shows "SIGNAL FOUND" and plays the newest REAL recording
of that satellite from the SatNOGS network.

 - Frequencies: SatNOGS DB (db.satnogs.org) active transmitter list
 - Audio:       recordings made by amateur ground stations around the world
                (SatNOGS Network)
Note: without a receiver this is NOT live, these are recent recordings.

Install:  python -m pip install requests customtkinter python-vlc   (VLC must be installed)
"""
import os
import tempfile
import threading
from datetime import datetime

import requests
import customtkinter as ctk
import vlc

DB_TX = "https://db.satnogs.org/api/transmitters/"
DB_SAT = "https://db.satnogs.org/api/satellites/"
NET_OBS = "https://network.satnogs.org/api/observations/"
HEADERS = {"User-Agent": "SatelliteRadio/2.0"}

BANDS = {
    "137 MHz (weather)": (137.0, 138.0),
    "145 MHz (2m)":      (144.0, 146.0),
    "437 MHz (70cm)":    (435.0, 438.0),
}
STEP = 0.005          # MHz (5 kHz)
TOL = 0.015           # MHz: within this distance the radio "locks" onto a signal


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("SATELLITE RADIO - Slider")
        self.geometry("700x720")

        self.tx = []              # active transmitters
        self.sat_names = {}       # norad id -> satellite name
        self.cache = {}           # transmitter uuid -> list of recordings
        self.cur_tx = None
        self.player = None
        self.tmpfile = None
        self.token = 0
        self.debounce = None
        self.current_obs = None
        self.volume = 80

        ctk.CTkLabel(self, text="SATELLITE RADIO", font=("Arial", 28, "bold")).pack(pady=(14, 0))
        self.lbl_freq = ctk.CTkLabel(self, text="--- MHz", font=("Consolas", 44, "bold"))
        self.lbl_freq.pack(pady=(6, 0))

        self.seg = ctk.CTkSegmentedButton(self, values=list(BANDS), command=self.on_band)
        self.seg.pack(pady=8)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=4)
        ctk.CTkButton(row, text="◀", width=44, command=lambda: self.nudge(-STEP)).pack(side="left", padx=6)
        self.slider = ctk.CTkSlider(row, from_=137.0, to=138.0, width=520, command=self.on_slide)
        self.slider.pack(side="left")
        ctk.CTkButton(row, text="▶", width=44, command=lambda: self.nudge(STEP)).pack(side="left", padx=6)

        self.lbl_near = ctk.CTkLabel(self, text="", text_color="gray", font=("Consolas", 12))
        self.lbl_near.pack(pady=2)

        self.lbl_info = ctk.CTkLabel(self, text="Loading database...", font=("Consolas", 15),
                                     justify="left")
        self.lbl_info.pack(pady=8)

        self.listbox = ctk.CTkScrollableFrame(self, width=620, height=210)
        self.listbox.pack(padx=10, pady=4, fill="both", expand=True)

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(pady=6)
        ctk.CTkButton(row2, text="■ Stop", width=110, command=self.stop).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="Open page", width=110, command=self.open_page).pack(side="left", padx=4)
        ctk.CTkLabel(row2, text="Volume").pack(side="left", padx=(14, 4))
        vol = ctk.CTkSlider(row2, from_=0, to=100, width=200, command=self.set_vol)
        vol.set(self.volume)
        vol.pack(side="left")

        self.lbl_status = ctk.CTkLabel(self, text="", text_color="gray")
        self.lbl_status.pack(pady=(2, 10))

        self.seg.set("137 MHz (weather)")
        self.on_band("137 MHz (weather)")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        threading.Thread(target=self.load_db, daemon=True).start()

    # ---------------- database ----------------
    def load_db(self):
        try:
            r = requests.get(DB_TX, headers=HEADERS, timeout=40,
                             params={"format": "json", "status": "active"})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                data = data.get("results", [])
            tx = []
            for t in data:
                f = t.get("downlink_low")
                if not f or not t.get("alive", True):
                    continue
                f /= 1e6
                if 136.0 <= f <= 438.0:
                    tx.append(dict(f=f, uuid=t.get("uuid"), norad=t.get("norad_cat_id"),
                                   mode=t.get("mode") or "?", desc=t.get("description") or ""))
            self.tx = tx
            try:
                rs = requests.get(DB_SAT, headers=HEADERS, timeout=40, params={"format": "json"})
                sats = rs.json()
                if isinstance(sats, dict):
                    sats = sats.get("results", [])
                self.sat_names = {s.get("norad_cat_id"): s.get("name") for s in sats}
            except Exception:
                pass
            self.after(0, lambda: self.lbl_info.configure(
                text=f"{len(tx)} active satellite transmitters loaded.\nMove the slider to search for signals."))
            self.after(0, lambda: self.on_slide(self.slider.get()))
        except Exception as e:
            self.after(0, lambda m=str(e): self.lbl_info.configure(text=f"Database error: {m}"))

    # ---------------- tuning ----------------
    def on_band(self, name):
        lo, hi = BANDS[name]
        self.slider.configure(from_=lo, to=hi, number_of_steps=int(round((hi - lo) / STEP)))
        self.slider.set((lo + hi) / 2)
        self.on_slide((lo + hi) / 2)

    def nudge(self, d):
        lo, hi = BANDS[self.seg.get()]
        v = min(hi, max(lo, self.slider.get() + d))
        self.slider.set(v)
        self.on_slide(v)

    def on_slide(self, v):
        v = round(float(v) / STEP) * STEP
        self.lbl_freq.configure(text=f"{v:.4f} MHz")
        near = sorted((t for t in self.tx if abs(t["f"] - v) <= 0.3), key=lambda t: abs(t["f"] - v))[:3]
        self.lbl_near.configure(text="Nearby: " + "   ".join(
            f"{t['f']:.4f} ({self.name_of(t)})" for t in near) if near else "No signals nearby")
        if self.debounce:
            self.after_cancel(self.debounce)
        self.debounce = self.after(700, lambda: self.lock(v))

    def name_of(self, t):
        return self.sat_names.get(t["norad"]) or t["desc"] or f"NORAD {t['norad']}"

    def lock(self, v):
        if not self.tx:
            return
        best = min(self.tx, key=lambda t: abs(t["f"] - v))
        if abs(best["f"] - v) > TOL:
            self.stop()
            self.clear_list()
            self.cur_tx = None
            self.lbl_info.configure(text=f"{v:.4f} MHz\nNo signal (static)")
            return
        self.cur_tx = best
        self.lbl_info.configure(
            text=f"SIGNAL FOUND\n{self.name_of(best)}\n"
                 f"Frequency: {best['f']:.4f} MHz   Mode: {best['mode']}\n{best['desc']}")
        self.status("Searching for recordings...")
        threading.Thread(target=self.fetch_obs, args=(best,), daemon=True).start()

    # ---------------- recordings ----------------
    def fetch_obs(self, t):
        try:
            if t["uuid"] not in self.cache:
                r = requests.get(NET_OBS, headers=HEADERS, timeout=30, params={
                    "format": "json", "transmitter_uuid": t["uuid"], "status": "good"})
                r.raise_for_status()
                data = r.json()
                if isinstance(data, dict):
                    data = data.get("results", [])
                obs = []
                for o in data:
                    url = o.get("payload") or o.get("archive_url")
                    if url:
                        o["_url"] = url
                        obs.append(o)
                obs.sort(key=lambda o: o.get("start") or "", reverse=True)
                self.cache[t["uuid"]] = obs[:12]
            obs = self.cache[t["uuid"]]
            self.after(0, lambda: self.show_obs(t, obs))
        except Exception as e:
            self.after(0, lambda m=str(e): self.status(f"Error: {m}", "tomato"))

    def clear_list(self):
        for w in self.listbox.winfo_children():
            w.destroy()

    def show_obs(self, t, obs):
        if self.cur_tx is not t:
            return      # the slider has moved somewhere else in the meantime
        self.clear_list()
        if not obs:
            self.status("No playable recordings for this transmitter.", "orange")
            return
        for o in obs:
            st = o.get("station_name") or f"station {o.get('ground_station', '?')}"
            ctk.CTkButton(self.listbox, anchor="w", height=32,
                          text=f"{self.fmt_time(o.get('start'))}  |  {st}",
                          command=lambda ob=o: self.play(ob)).pack(fill="x", pady=2)
        self.play(obs[0])

    @staticmethod
    def fmt_time(s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return s or "?"

    # ---------------- playback ----------------
    def play(self, o):
        self.stop(quiet=True)
        self.token += 1
        token = self.token
        self.current_obs = o
        self.status("Downloading recording...")
        threading.Thread(target=self.download, args=(o, token), daemon=True).start()

    def download(self, o, token):
        try:
            r = requests.get(o["_url"], headers=HEADERS, timeout=90)
            r.raise_for_status()
            fd, path = tempfile.mkstemp(suffix=".ogg")
            with os.fdopen(fd, "wb") as f:
                f.write(r.content)
            self.after(0, lambda: self.start_player(path, o, token))
        except Exception as e:
            self.after(0, lambda m=str(e): self.status(f"Download error: {m}", "tomato"))

    def start_player(self, path, o, token):
        if token != self.token:
            os.remove(path)
            return
        self.tmpfile = path
        self.player = vlc.MediaPlayer(path)
        self.player.audio_set_mute(False)
        self.player.play()
        # python-vlc: the volume is only applied once playback has started
        self.after(600, lambda: self.apply_volume(token))
        size = os.path.getsize(path)
        self.after(2000, lambda: self.check_state(token, o, size))

    def apply_volume(self, token):
        if token == self.token and self.player:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(int(self.volume))

    def check_state(self, token, o, size):
        if token != self.token or not self.player:
            return
        state = self.player.get_state()
        st = o.get("station_name") or "?"
        info = f"{size // 1024} KB, state: {state}"
        if state == vlc.State.Error:
            self.status(f"VLC could not play the file ({info})", "tomato")
        elif state == vlc.State.Ended:
            self.status(f"Recording is too short or empty ({info})", "orange")
        else:
            self.status(f"Playing: {self.fmt_time(o.get('start'))} - {st} ({info})", "lightgreen")

    def stop(self, quiet=False):
        self.token += 1
        if self.player:
            self.player.stop()
            self.player = None
        if self.tmpfile:
            try:
                os.remove(self.tmpfile)
            except OSError:
                pass
            self.tmpfile = None
        if not quiet:
            self.status("Stopped")

    def set_vol(self, v):
        self.volume = v
        if self.player:
            self.player.audio_set_volume(int(v))

    def open_page(self):
        if self.current_obs:
            import webbrowser
            webbrowser.open(f"https://network.satnogs.org/observations/{self.current_obs['id']}/")

    def status(self, text, color="gray"):
        self.lbl_status.configure(text=text, text_color=color)

    def on_close(self):
        self.stop(quiet=True)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
