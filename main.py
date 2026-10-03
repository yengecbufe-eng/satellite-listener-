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

Install:  python -m pip install requests customtkinter python-vlc soundfile numpy   (VLC must be installed)
"""
import os
import tempfile
import threading
import time
from datetime import datetime

import numpy as np
import requests
import customtkinter as ctk
import vlc

try:
    import soundfile as sf     # used to look for speech inside recordings
except ImportError:
    sf = None

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

# --- scan-all-voice-satellites settings ---
VOICE_MODES = {"FM", "FMN", "USB", "LSB", "SSB"}
VOICE_BANDS = [(144.0, 146.0), (435.0, 438.0)]
MAX_SATS = 30          # max transmitters per scan
OBS_PER_SAT = 3        # newest recordings analysed per transmitter
MIN_GAP = 1.0          # seconds between SatNOGS API calls (avoids HTTP 429)
SPEECH_HIT = 0.10      # voice score at/above which a recording counts as "has speech"
EST_N = 10             # recordings analysed for the live speech-chance estimate


def voice_score(path):
    """Rough 0..1 estimate of how much of a recording sounds like speech.

    Speech keeps most of its energy between 300 and 3000 Hz and has pauses
    between words, while FM noise is spread towards high frequencies.
    This is a heuristic, not a speech recognizer: it can be fooled by steady
    tones (e.g. NOAA APT) and may miss very weak speech.
    """
    x, fs = sf.read(path, always_2d=True)
    x = x.mean(axis=1)
    n = int(0.032 * fs)                       # 32 ms frames
    frames = len(x) // n
    if frames < 20:
        return 0.0
    X = x[:frames * n].reshape(frames, n) * np.hanning(n)
    S = np.abs(np.fft.rfft(X, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / fs)
    low = S[:, (f >= 300) & (f <= 3000)].sum(axis=1)
    high = S[:, (f >= 5000) & (f <= 0.9 * fs / 2)].sum(axis=1)
    floor = np.percentile(low, 20) + 1e-12
    active = (low > 6 * floor) & (low > 2 * high)
    return float(active.mean())


CLEAN_AUDIO = True     # False = only normalise, no filtering at all


def _moving_avg(a, w):
    w = max(1, int(w))
    c = np.cumsum(np.insert(a, 0, 0.0))
    out = (c[w:] - c[:-w]) / w
    return np.pad(out, (w // 2, len(a) - len(out) - w // 2), mode="edge")


def enhance(x, fs):
    """Speech enhancement for narrow-band FM recordings (numpy only). Returns (audio, fs).

    1. down-sample to ~16 kHz (speech lives below 4 kHz)
    2. noise profile from the quietest 15 % of the frames (not from the whole file)
    3. spectral subtraction (Wiener-like gain), smoothed in time and frequency so there is
       no 'bubbling'; frames that look like speech (energy 300-3000 Hz >> noise, low > high)
       get a much higher gain floor than pure-noise frames, with a hangover so word ends
       are not chopped
    4. smooth 300-3400 Hz band-pass (telephone band)
    5. slow AGC (quiet words up, loud noise bursts down) + soft limiter
    """
    from numpy.lib.stride_tricks import sliding_window_view
    x = np.asarray(x, dtype=np.float64)
    k = max(1, int(fs // 16000))
    if k > 1:
        m = len(x) // k * k
        x = x[:m].reshape(-1, k).mean(axis=1)
        fs = fs // k
    n, hop = 512, 256
    win = np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n))       # sqrt-Hann: perfect OLA
    pad = np.pad(x, (n, n))
    fr = sliding_window_view(pad, n)[::hop]
    frames = len(fr)
    S = np.fft.rfft(fr * win, axis=1)
    P = np.abs(S) ** 2
    f = np.fft.rfftfreq(n, 1 / fs)
    sp = (f >= 300) & (f <= 3000)
    hi = (f >= 5000) & (f <= 0.9 * fs / 2)
    e = P[:, (f >= 300) & (f <= 3400)].sum(axis=1)
    noise = P[e <= np.percentile(e, 15)].mean(axis=0)
    low = P[:, sp].sum(axis=1)
    high = P[:, hi].sum(axis=1) if hi.any() else np.zeros(frames)
    vad = (low > 4 * noise[sp].sum()) & (low > 2 * high)
    vad = np.convolve(vad.astype(float), np.ones(9), mode="same") > 0      # hangover
    g = np.sqrt(np.clip(1 - 1.8 * noise[None, :] / (P + 1e-12), 0, 1))
    g = np.maximum(g, np.where(vad, 0.30, 0.08)[:, None])
    g = (np.roll(g, 1, 0) + g + np.roll(g, -1, 0)) / 3
    g = (np.roll(g, 1, 1) + g + np.roll(g, -1, 1)) / 3
    bp = 1 / (1 + (300 / np.maximum(f, 1.0)) ** 4) / (1 + (f / 3400) ** 4)
    Y = np.fft.irfft(S * g * bp[None, :], n, axis=1) * win
    out = np.zeros((frames + 1) * hop)
    out[:frames * hop] += Y[:, :hop].reshape(-1)
    out[hop:(frames + 1) * hop] += Y[:, hop:].reshape(-1)
    y = out[n:n + len(x)]
    env = np.sqrt(_moving_avg(y ** 2, 0.1 * fs))
    ref = np.percentile(env, 90)
    if ref > 1e-9:
        gain = np.clip(ref / (env + 0.05 * ref), 0.6, 5.0)
        y = y * _moving_avg(gain, 0.2 * fs)
    m = np.percentile(np.abs(y), 99.5)
    if m > 1e-9:
        y = np.tanh(y / m * 0.9) * 0.7
    return y, fs


def make_audible(path):
    """Reads a recording, applies enhance() and writes a WAV. Returns (new_path, peak, rms)."""
    x, fs = sf.read(path, always_2d=True)
    x = x.mean(axis=1)
    x = x - x.mean()
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    y = x
    if CLEAN_AUDIO and len(x) >= 8192 and rms > 1e-9:
        y, fs = enhance(x, fs)
    else:
        m = np.percentile(np.abs(y), 99.5) if len(y) else 0.0
        if m > 1e-9:
            y = np.clip(y / m * 0.6, -1, 1)
    out_path = path.rsplit(".", 1)[0] + "_n.wav"
    sf.write(out_path, y.astype(np.float32), fs)
    return out_path, peak, rms


class Cancelled(Exception):
    """A queued request is no longer needed (the user tuned somewhere else)."""


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
        self.scan_id = 0
        self.level_txt = ""
        self.http_lock = threading.Lock()
        self.last_req = 0.0
        self.block_until = 0.0
        self.score_cache = {}      # recording id -> voice score
        self.est_id = 0
        self.search_btns = {}      # transmitter uuid -> (button, base text)

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
        self.lbl_chance = ctk.CTkLabel(self, text="", font=("Consolas", 16, "bold"))
        self.lbl_chance.pack(pady=(0, 4))

        presets = ctk.CTkFrame(self, fg_color="transparent")
        presets.pack(pady=(2, 4))
        ctk.CTkLabel(presets, text="Voice satellites:").pack(side="left", padx=6)
        ctk.CTkButton(presets, text="ISS repeater 437.800", width=170,
                      command=lambda: self.jump(437.800)).pack(side="left", padx=4)
        ctk.CTkButton(presets, text="SO-50 436.795", width=150,
                      command=lambda: self.jump(436.795)).pack(side="left", padx=4)

        self.all_tx = []          # every active transmitter, any frequency (for name search)
        self.search = ctk.CTkEntry(self, width=620,
                                   placeholder_text="Search any satellite by name (e.g. NOAA, ISS, CatSat, Tevel)")
        self.search.pack(pady=(4, 0))
        self.search.bind("<KeyRelease>", self.on_search)

        self.listbox = ctk.CTkScrollableFrame(self, width=620, height=210)
        self.listbox.pack(padx=10, pady=4, fill="both", expand=True)

        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(pady=6)
        ctk.CTkButton(row2, text="■ Stop", width=90, command=self.stop).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="Find voice", width=110, fg_color="#2e7d32", hover_color="#1b5e20",
                      command=self.find_voice).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="Scan all voice sats", width=150, fg_color="#1565c0",
                      hover_color="#0d47a1", command=self.voice_scan_all).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="Open page", width=110, command=self.open_page).pack(side="left", padx=4)
        ctk.CTkLabel(row2, text="Volume").pack(side="left", padx=(14, 4))
        vol = ctk.CTkSlider(row2, from_=0, to=200, width=200, command=self.set_vol)
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
            tx, all_tx = [], []
            for t in data:
                f = t.get("downlink_low")
                if not f or not t.get("alive", True):
                    continue
                f /= 1e6
                d = dict(f=f, uuid=t.get("uuid"), norad=t.get("norad_cat_id"),
                         mode=t.get("mode") or "?", desc=t.get("description") or "")
                all_tx.append(d)
                if 136.0 <= f <= 438.0:
                    tx.append(d)
            self.tx = tx
            self.all_tx = all_tx
            try:
                rs = requests.get(DB_SAT, headers=HEADERS, timeout=40, params={"format": "json"})
                sats = rs.json()
                if isinstance(sats, dict):
                    sats = sats.get("results", [])
                self.sat_names = {s.get("norad_cat_id"): s.get("name") for s in sats}
            except Exception:
                pass
            self.after(0, lambda: self.lbl_info.configure(
                text=f"{len(all_tx)} active transmitters loaded ({len(tx)} in the slider bands).\n"
                     "Move the slider or search a satellite by name."))
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
        near = sorted((t for t in self.tx if abs(t["f"] - v) <= 0.3),
                      key=lambda t: (round(abs(t["f"] - v) / 0.003), self.voice_rank(t)))[:3]
        self.lbl_near.configure(text="Nearby: " + "   ".join(
            f"{t['f']:.4f} {t['mode']} ({self.name_of(t)})" for t in near) if near else "No signals nearby")
        if self.debounce:
            self.after_cancel(self.debounce)
        self.debounce = self.after(700, lambda: self.lock(v))

    @staticmethod
    def voice_rank(t):
        """0 = described as voice/repeater, 1 = voice mode (FM/SSB), 2 = data/telemetry/other."""
        d = (t["desc"] or "").lower()
        if "voice" in d or "repeater" in d or "digitalker" in d:
            return 0
        if (t["mode"] or "").upper() in VOICE_MODES:
            return 1
        return 2

    def pick(self, cands, f):
        """Closest transmitter to f; when several share the frequency (e.g. ISS 437.800:
        telemetry + SSTV + voice repeater) the voice one wins."""
        return min(cands, key=lambda t: (round(abs(t["f"] - f) / 0.003), self.voice_rank(t),
                                         abs(t["f"] - f)))

    def name_of(self, t):
        return self.sat_names.get(t["norad"]) or t["desc"] or f"NORAD {t['norad']}"

    def lock(self, v):
        if not self.tx:
            return
        best = self.pick(self.tx, v)
        if abs(best["f"] - v) > TOL:
            self.stop()
            self.clear_list()
            self.cur_tx = None
            self.lbl_chance.configure(text="")
            self.lbl_info.configure(text=f"{v:.4f} MHz\nNo signal (static)")
            return
        self.cur_tx = best
        self.refresh_chance()
        self.lbl_info.configure(
            text=f"SIGNAL FOUND\n{self.name_of(best)}\n"
                 f"Frequency: {best['f']:.4f} MHz   Mode: {best['mode']}\n{best['desc']}")
        self.status("Searching for recordings...")
        threading.Thread(target=self.fetch_obs, args=(best,), daemon=True).start()

    # ---------------- search by name ----------------
    def on_search(self, _event=None):
        q = self.search.get().strip().lower()
        if len(q) < 2 or not self.all_tx:
            return
        hits = [t for t in self.all_tx
                if q in self.name_of(t).lower() or q in t["desc"].lower()]
        hits.sort(key=lambda t: self.name_of(t))
        self.clear_list()
        for t in hits[:40]:
            base = f"{self.name_of(t)}  |  {t['f']:.4f} MHz  |  {t['mode']}"
            cs = self.chance_str(t)
            btn = ctk.CTkButton(self.listbox, anchor="w", height=32,
                                text=base + (f"  |  {cs}" if cs else ""),
                                command=lambda tt=t: self.tune_to(tt))
            btn.pack(fill="x", pady=2)
            self.search_btns[t["uuid"]] = (btn, base, t)
        self.status(f"{len(hits)} transmitters match" if hits else "No match", "gray")

    def tune_to(self, t):
        """Jump to a transmitter chosen from the search results (any frequency)."""
        self.cur_tx = t
        self.refresh_chance()
        self.lbl_freq.configure(text=f"{t['f']:.4f} MHz")
        self.lbl_info.configure(
            text=f"{self.name_of(t)}\nFrequency: {t['f']:.4f} MHz   Mode: {t['mode']}\n{t['desc']}")
        self.status("Searching for recordings...")
        threading.Thread(target=self.fetch_obs, args=(t,), daemon=True).start()

    def jump(self, mhz):
        cand = [t for t in self.all_tx if abs(t["f"] - mhz) < 0.003]
        if not cand:
            self.status(f"{mhz:.3f} MHz is not in the database right now.", "orange")
            return
        self.tune_to(self.pick(cand, mhz))

    # ---------------- voice finder (single satellite) ----------------
    def find_voice(self):
        """Download the latest recordings of the tuned satellite, score each one for
        speech, list them best-first and play the most speech-like one."""
        if sf is None:
            self.status("Install soundfile first:  python -m pip install soundfile", "orange")
            return
        t = self.cur_tx
        obs = self.cache.get(t["uuid"]) if t else None
        if not obs:
            self.status("Tune to a satellite with recordings first.", "orange")
            return
        self.stop(quiet=True)
        threading.Thread(target=self._scan, args=(t, list(obs[:15])), daemon=True).start()

    def _scan(self, t, obs):
        scored = []
        for i, o in enumerate(obs, 1):
            if self.cur_tx is not t:
                return          # the user tuned somewhere else
            self.after(0, lambda i=i: self.status(f"Scanning for speech... {i}/{len(obs)}"))
            try:
                scored.append((self.score_obs(o), o))
            except Exception:
                pass
        scored.sort(key=lambda s: s[0], reverse=True)
        self.after(0, lambda: self.show_scored(t, scored))

    def show_scored(self, t, scored):
        if self.cur_tx is not t:
            return
        self.clear_list()
        if not scored:
            self.status("Could not analyse any recording.", "orange")
            return
        for score, o in scored:
            st = o.get("station_name") or f"station {o.get('ground_station', '?')}"
            ctk.CTkButton(self.listbox, anchor="w", height=32,
                          text=f"{score * 100:4.1f}% speech-like  |  {self.fmt_time(o.get('start'))}  |  {st}",
                          command=lambda ob=o: self.play(ob)).pack(fill="x", pady=2)
        best_score, best = scored[0]
        if best_score >= 0.10:
            self.play(best)
        else:
            self.status("No speech found in these recordings (probably only noise). "
                        "Try another satellite or check again later.", "orange")

    # ---------------- rate-limited HTTP ----------------
    def http_get(self, url, params=None, timeout=30, gap=MIN_GAP, alive=None):
        """GET with a minimum gap between requests; on HTTP 429 waits (Retry-After) and retries."""
        for attempt in range(5):
            with self.http_lock:
                now = time.time()
                start = max(now, self.last_req + gap, self.block_until)
                self.last_req = start
            if start > now:
                time.sleep(start - now)
            if alive is not None and not alive():
                raise Cancelled()
            r = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
            if r.status_code != 429:
                r.raise_for_status()
                return r
            ra = r.headers.get("Retry-After", "")
            try:
                delay = float(ra)
            except ValueError:
                delay = 5.0 * (attempt + 1)
            delay = min(max(delay, 3.0), 90.0)
            with self.http_lock:
                self.block_until = max(self.block_until, time.time() + delay)
            self.after(0, lambda d=delay: self.status(
                f"SatNOGS rate limit - waiting {d:.0f}s, then retrying...", "orange"))
        raise RuntimeError("SatNOGS keeps answering 429 (too many requests). Wait a minute and try again.")

    # ---------------- scan ALL voice satellites ----------------
    def get_obs(self, t, alive=None):
        """Fetch the latest recordings of a transmitter without touching the UI (cached)."""
        if t["uuid"] not in self.cache:
            r = self.http_get(NET_OBS, alive=alive, params={
                "format": "json", "transmitter_uuid": t["uuid"], "status": "good"})
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
            self.cache[t["uuid"]] = obs[:20]
        return self.cache[t["uuid"]]

    def score_obs(self, o):
        key = self.obs_key(o)
        if key in self.score_cache:
            return self.score_cache[key]
        s = self._score_download(o)
        self.score_cache[key] = s
        self.after(0, self.refresh_chance)
        return s

    def _score_download(self, o):
        path = None
        try:
            r = self.http_get(o["_url"], timeout=90, gap=0.3)
            r.raise_for_status()
            fd, path = tempfile.mkstemp(suffix=".ogg")
            with os.fdopen(fd, "wb") as fh:
                fh.write(r.content)
            return voice_score(path)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def voice_scan_all(self):
        if sf is None:
            self.status("Install soundfile first:  python -m pip install soundfile", "orange")
            return
        if not self.all_tx:
            self.status("Database is still loading...", "orange")
            return
        self.stop(quiet=True)          # also cancels any running scan
        self.scan_id += 1
        threading.Thread(target=self._voice_scan_all, args=(self.scan_id,), daemon=True).start()

    def _voice_scan_all(self, sid):
        seen, cands = set(), []
        for t in self.all_tx:
            if (t["mode"] or "").upper() not in VOICE_MODES:
                continue
            if not any(lo <= t["f"] <= hi for lo, hi in VOICE_BANDS):
                continue
            if t["uuid"] in seen:
                continue
            seen.add(t["uuid"])
            cands.append(t)
        cands = cands[:MAX_SATS]

        results = []        # (score, transmitter, recording)
        for i, t in enumerate(cands, 1):
            if sid != self.scan_id:
                return
            self.after(0, lambda i=i, t=t: self.status(
                f"Scanning {self.name_of(t)}  ({i}/{len(cands)})"))
            try:
                obs = self.get_obs(t)[:OBS_PER_SAT]
            except Exception:
                continue
            for o in obs:
                if sid != self.scan_id:
                    return
                try:
                    results.append((self.score_obs(o), t, o))
                except Exception:
                    pass
        results.sort(key=lambda r: r[0], reverse=True)
        self.after(0, lambda: self.show_voice_results(sid, results))

    def show_voice_results(self, sid, results):
        if sid != self.scan_id:
            return
        self.clear_list()
        if not results:
            self.status("No recordings could be analysed.", "orange")
            return
        for score, t, o in results[:40]:
            ctk.CTkButton(
                self.listbox, anchor="w", height=32,
                text=f"{score * 100:4.1f}%  |  {self.name_of(t)}  |  {t['f']:.4f} MHz  |  "
                     f"{self.chance_str(t)}  |  {self.fmt_time(o.get('start'))}",
                command=lambda tt=t, ob=o: self.play_from_scan(tt, ob)).pack(fill="x", pady=2)
        best_score, bt, bo = results[0]
        if best_score >= 0.10:
            self.play_from_scan(bt, bo)
        else:
            self.status("No speech found in any satellite right now (mostly noise). "
                        "Try again later.", "orange")

    def play_from_scan(self, t, o):
        self.cur_tx = t
        self.lbl_freq.configure(text=f"{t['f']:.4f} MHz")
        self.lbl_info.configure(
            text=f"{self.name_of(t)}\nFrequency: {t['f']:.4f} MHz   Mode: {t['mode']}\n{t['desc']}")
        self.play(o)
        self.refresh_chance()
        if sf is not None:
            self.estimate(t)

    # ---------------- live speech-chance estimate ----------------
    @staticmethod
    def obs_key(o):
        return o.get("id") or o["_url"]

    def chance(self, t):
        """(recordings with speech, recordings analysed, recordings to analyse) for a transmitter."""
        obs = self.cache.get(t["uuid"], [])[:EST_N]
        scores = [self.score_cache[self.obs_key(o)] for o in obs if self.obs_key(o) in self.score_cache]
        hits = sum(1 for sc in scores if sc >= SPEECH_HIT)
        return hits, len(scores), len(obs)

    def best_score(self, t):
        obs = self.cache.get(t["uuid"], [])[:EST_N]
        sc = [self.score_cache[self.obs_key(o)] for o in obs if self.obs_key(o) in self.score_cache]
        return max(sc) if sc else 0.0

    def chance_str(self, t):
        hits, n, _ = self.chance(t)
        return f"{100.0 * hits / n:.0f}% speech chance" if n else ""

    def refresh_chance(self):
        """Update the big '%' label for the tuned satellite and the % in the search list."""
        t = self.cur_tx
        if t is not None:
            hits, n, total = self.chance(t)
            if n == 0:
                self.lbl_chance.configure(text="Speech chance: estimating...", text_color="gray")
            else:
                pct = 100.0 * hits / n
                color = "lightgreen" if pct >= 30 else ("orange" if pct > 0 else "tomato")
                tail = f"  (analysing {n}/{total}...)" if n < total else ""
                self.lbl_chance.configure(
                    text=f"Speech chance: {pct:.0f}%   [{hits}/{n} recordings, best score {self.best_score(t) * 100:.1f}%]{tail}",
                    text_color=color)
        for uuid, (btn, base, tt) in list(self.search_btns.items()):
            cs = self.chance_str(tt)
            if cs:
                try:
                    btn.configure(text=f"{base}  |  {cs}")
                except Exception:
                    pass

    def estimate(self, t):
        self.est_id += 1
        threading.Thread(target=self._estimate, args=(t, self.est_id), daemon=True).start()

    def _estimate(self, t, eid):
        try:
            obs = self.get_obs(t, alive=lambda: self.cur_tx is t and eid == self.est_id)[:EST_N]
        except Exception:
            return
        for o in obs:
            if eid != self.est_id or self.cur_tx is not t:
                return          # tuned somewhere else
            try:
                self.score_obs(o)
            except Exception:
                pass
        self.after(0, self.refresh_chance)

    # ---------------- recordings ----------------
    def fetch_obs(self, t):
        try:
            obs = self.get_obs(t, alive=lambda: self.cur_tx is t)
            self.after(0, lambda: self.show_obs(t, obs))
        except Cancelled:
            return
        except Exception as e:
            self.after(0, lambda m=str(e): self.status(f"Error: {m}", "tomato"))

    def clear_list(self):
        self.search_btns = {}
        for w in self.listbox.winfo_children():
            w.destroy()

    def show_obs(self, t, obs):
        if self.cur_tx is not t:
            return      # the slider has moved somewhere else in the meantime
        self.clear_list()
        if not obs:
            self.status("No playable recordings for this transmitter.", "orange")
            self.lbl_chance.configure(text="Speech chance: n/a (no recordings)", text_color="gray")
            return
        for o in obs:
            st = o.get("station_name") or f"station {o.get('ground_station', '?')}"
            ctk.CTkButton(self.listbox, anchor="w", height=32,
                          text=f"{self.fmt_time(o.get('start'))}  |  {st}",
                          command=lambda ob=o: self.play(ob)).pack(fill="x", pady=2)
        self.play(obs[0])
        self.refresh_chance()
        if sf is not None:
            self.estimate(t)
        else:
            self.lbl_chance.configure(text="Install soundfile for the speech chance", text_color="orange")

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
            r = self.http_get(o["_url"], timeout=90, gap=0.3)
            r.raise_for_status()
            fd, path = tempfile.mkstemp(suffix=".ogg")
            with os.fdopen(fd, "wb") as f:
                f.write(r.content)
            level = ""
            if sf is not None:
                try:
                    new, peak, rms = make_audible(path)
                    os.remove(path)
                    path = new
                    level = f"  [peak {peak:.3f}, rms {rms:.4f}]"
                except Exception:
                    pass
            self.after(0, lambda: self.start_player(path, o, token, level))
        except Exception as e:
            self.after(0, lambda m=str(e): self.status(f"Download error: {m}", "tomato"))

    def start_player(self, path, o, token, level=""):
        if token != self.token:
            os.remove(path)
            return
        self.level_txt = level
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
            self.status(f"Playing: {self.fmt_time(o.get('start'))} - {st} ({info}){self.level_txt}",
                        "lightgreen")

    def stop(self, quiet=False):
        self.token += 1
        self.scan_id += 1      # cancel any running "scan all" job
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
