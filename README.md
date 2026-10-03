Satellite Radio

A desktop "radio" for satellites that needs no hardware. Move the slider, and when it lands on the real frequency of an active satellite transmitter the app shows SIGNAL FOUND and plays a recent real recording of that satellite, made by amateur ground stations around the world (SatNOGS network).

This is not live radio. Without a receiver you hear recordings of recent satellite passes, not what is on the air right now. See Hearing live.

Features
Slider tuning over three bands: 137 MHz (weather), 145 MHz (2 m) and 437 MHz (70 cm), in 5 kHz steps, with ◀ / ▶ fine-tune buttons.
Real transmitter data from the SatNOGS DB (only active transmitters). When several transmitters share one frequency (e.g. ISS on 437.800 MHz: telemetry, SSTV, voice repeater) the voice one is preferred.
Search by name (ISS, SO-50, FO-29, NOAA, ...) across every active transmitter, on any frequency.
Presets for the ISS FM repeater (437.800 MHz) and SO-50 (436.795 MHz).
Speech chance % shown for the tuned satellite: the newest recordings are analysed in the background and the percentage of them that contain speech-like audio is shown live, e.g. Speech chance: 30% [3/10 recordings, best score 24.1%].
Find voice: scores the recent recordings of the tuned satellite for speech, lists them best-first and plays the most speech-like one.
Scan all voice sats: does the same across all FM/SSB transmitters in the 2 m and 70 cm bands and ranks the results.
Audio clean-up before playback: band-pass for the speech band, noise reduction, AGC and a soft limiter, so quiet recordings are audible. Switchable (CLEAN_AUDIO).
Open page opens the SatNOGS observation page (waterfall, station location, etc.) for the recording being played.
Polite to the SatNOGS servers: rate-limited requests, automatic retry on HTTP 429, queued requests are cancelled when you tune elsewhere.
Requirements
Python 3.9+
VLC media player installed. Its bitness must match your Python (64-bit Python needs 64-bit VLC).
Python packages:
bash
python -m pip install requests customtkinter python-vlc soundfile numpy

soundfile and numpy are needed for the speech chance, Find voice, Scan all voice sats and the audio clean-up. Without soundfile the app still plays recordings, but without those features.

Run
bash
python satellite_radio.py

On start the app downloads the transmitter list and satellite names from db.satnogs.org (can take a few seconds).

How to use
Pick a band and move the slider, or use ◀ / ▶. After you stop for a moment the radio locks on the nearest transmitter within 15 kHz and shows its name, frequency, mode and description. Otherwise it shows No signal (static).
The newest recordings of that transmitter are listed and the newest one plays automatically. Click any entry to play it.
Use the search box to jump straight to a satellite, or the preset buttons for ISS / SO-50.
Find voice / Scan all voice sats rank recordings by how speech-like they are. Items in the list show the score of each recording.
Volume goes from 0 to 200 %.
Reading the speech chance
Colour	Meaning
green	30 % or more of the analysed recordings contain speech-like audio
orange	1-29 %
red	0 %

The percentage is an estimate from the last ~10 recordings, not a guarantee. The score is a heuristic (energy distribution and pauses), not a speech recogniser: steady tones can score high and very weak speech can be missed. The label also shows the best single score so you can judge borderline cases.

Configuration

Constants at the top of satellite_radio.py:

Constant	Default	Meaning
BANDS	137-138, 144-146, 435-438 MHz	Slider bands
STEP	0.005 MHz	Slider step (5 kHz)
TOL	0.015 MHz	Distance within which the radio locks on a signal
VOICE_MODES	FM, FMN, USB, LSB, SSB	Modes treated as voice in Scan all
VOICE_BANDS	144-146, 435-438 MHz	Bands scanned by Scan all
MAX_SATS	30	Max transmitters per Scan all
OBS_PER_SAT	3	Recordings analysed per transmitter in Scan all
MIN_GAP	1.0 s	Minimum gap between SatNOGS API calls
SPEECH_HIT	0.10	Score at/above which a recording counts as "has speech"
EST_N	10	Recordings analysed for the live speech chance
CLEAN_AUDIO	True	False = only normalise the volume, no filtering
How it works
Transmitters: db.satnogs.org/api/transmitters/ (active, alive, with a downlink frequency). Names come from /api/satellites/.
Recordings: network.satnogs.org/api/observations/ filtered by transmitter and status=good; the newest 20 with audio are kept.
Speech score: each recording is cut into 32 ms frames; a frame counts as "speech" when most of its energy sits between 300 and 3000 Hz, well above that recording's quiet floor and above the high-frequency noise. The score is the fraction of such frames.
Playback: the recording is downloaded, cleaned (down-sampled to ~16 kHz, band-passed, noise-reduced, levelled) and played with VLC.
Limitations
Mostly noise. Voice repeater satellites only transmit while someone is talking, and many recordings are of passes where nobody was. Zero-percent satellites are normal. SO-50 additionally needs a 74.4 Hz tone to arm its repeater.
Recordings are made by stations all over the world, so the people you hear are usually far away from the station that recorded them.
The ISS FM repeater is not always switched on.
The SatNOGS "good" flag means a signal was visible, not that there is speech.
Network only: needs internet access to SatNOGS (DB, Network API and its audio storage).
Troubleshooting
Problem	Fix
Too Many Requests / HTTP 429	SatNOGS rate limit. The app waits and retries by itself; if it keeps happening wait a minute and avoid sweeping the slider quickly or repeating Scan all. Lower MAX_SATS / OBS_PER_SAT.
No sound at all	Check the status line. VLC could not play the file means a VLC problem (bitness mismatch, missing install). Test with a normal WAV in VLC and check the Windows volume mixer.
Crackling / hiss only	The recording probably has no speech; check the speech score and the waterfall via Open page. Try CLEAN_AUDIO = False to compare.
Install soundfile first	python -m pip install soundfile
Wrong transmitter (e.g. telemetry)	Search it by name and click the entry, or pick the preset button.
Database error	No internet access or the SatNOGS DB is down; restart later.
Hearing live

Because the recordings are from the past, real-time listening needs another source:

WebSDR receivers on the internet (tune 145.800 MHz FM when the ISS is in range of that receiver).
ARISS live webcast of scheduled school contacts with ISS astronauts: https://live.ariss.org/
Your own receiver (an RTL-SDR dongle or an FM handheld with a simple antenna).
Legal note

The program is receive-only: it only plays recordings. Listening is generally unrestricted, but transmitting on amateur satellite frequencies requires an amateur radio licence in your country.

Credits
Transmitter data and recordings: SatNOGS (Libre Space Foundation), via the SatNOGS DB and SatNOGS Network public APIs. Please respect their servers and terms of use.
UI: CustomTkinter. Playback: VLC via python-vlc.
